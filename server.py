"""
FastAPI Server for AI-Powered Vehicle ANPR Surveillance System.
Provides REST APIs, WebSockets for real-time alerts, MJPEG camera stream, and static dashboard serving.
"""

import os
import asyncio
import io
import csv
from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Query, Response, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import init_db, get_db, Detection, VehicleRegistry, CameraConfig
from camera_worker import CameraWorker

# Initialize FastAPI App
app = FastAPI(title="Indian ANPR Surveillance Server", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket connections for live notifications
active_websockets: List[WebSocket] = []

def broadcast_detection(detection_dict: dict):
    """Pushes a new detection event to all active connected browsers."""
    for ws in list(active_websockets):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(detection_dict), app.state.main_loop)
        except Exception:
            pass

# Initialize Camera Worker
camera_worker = CameraWorker(
    source="test_samples/indian_traffic_test.mp4",
    model_path="models/indian_plate_yolo.pt",
    camera_name="Main Gate 01",
    on_detection_callback=broadcast_detection,
    conf_threshold=0.25,
    loop_video=True
)

@app.on_event("startup")
def on_startup():
    init_db()
    app.state.main_loop = asyncio.get_event_loop()
    camera_worker.start()

@app.on_event("shutdown")
def on_shutdown():
    camera_worker.stop()

# Ensure directories exist
os.makedirs("outputs/plates", exist_ok=True)
os.makedirs("outputs/contexts", exist_ok=True)
os.makedirs("outputs/snapshots", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Mount outputs for image previews
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


# =====================================================================
# Video Streaming Endpoint (MJPEG)
# =====================================================================
@app.get("/video_feed")
def video_feed():
    """MJPEG stream endpoint for live surveillance preview."""
    def frame_generator():
        import time
        while True:
            frame_bytes = camera_worker.get_current_frame()
            if frame_bytes:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            time.sleep(0.04)
    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


# =====================================================================
# Real-Time WebSocket for Live Vehicle Detections
# =====================================================================
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        while True:
            # Heartbeat listener
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
    except Exception:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


# =====================================================================
# Camera Controls (RTSP vs Laptop Webcam vs Video)
# =====================================================================
class CameraSwitchPayload(BaseModel):
    source: str
    camera_name: Optional[str] = ""

@app.post("/api/camera/switch")
def switch_camera_feed(payload: CameraSwitchPayload):
    """Switches active camera feed dynamically (e.g. webcam 0, rtsp URL, video)."""
    camera_worker.switch_source(payload.source, payload.camera_name)
    return {
        "status": "success",
        "new_source": payload.source,
        "camera_name": camera_worker.camera_name
    }

@app.post("/api/upload_video")
async def upload_video_file(file: UploadFile = File(...)):
    """Uploads an MP4/AVI/MKV video file and switches camera worker to process it."""
    os.makedirs("test_samples", exist_ok=True)
    clean_filename = file.filename.replace(" ", "_")
    target_path = os.path.join("test_samples", f"uploaded_{clean_filename}")
    
    with open(target_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)
            
    feed_label = f"Uploaded File: {file.filename}"
    camera_worker.switch_source(target_path, feed_label)
    
    return {
        "status": "success",
        "filename": file.filename,
        "saved_path": target_path,
        "camera_name": feed_label
    }

@app.get("/api/camera/current")
def get_current_camera():
    return {
        "source": str(camera_worker.source),
        "camera_name": camera_worker.camera_name,
        "is_running": camera_worker.is_running
    }


# =====================================================================
# REST APIs
# =====================================================================
@app.get("/api/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Computes overall detection statistics, accuracy, and today's volume."""
    today_start = datetime.combine(date.today(), datetime.min.time())

    total_count = db.query(Detection).count()
    today_count = db.query(Detection).filter(Detection.timestamp >= today_start).count()
    unique_today = db.query(func.count(func.distinct(Detection.plate_number))).filter(Detection.timestamp >= today_start).scalar() or 0
    valid_count = db.query(Detection).filter(Detection.is_valid == True).count()
    flagged_count = db.query(Detection).filter(Detection.security_flag != "CLEAR").count()

    accuracy_rate = round((valid_count / total_count * 100), 1) if total_count > 0 else 100.0

    state_rows = db.query(Detection.state, func.count(Detection.id)).group_by(Detection.state).order_by(desc(func.count(Detection.id))).limit(6).all()
    state_breakdown = [{"state": s or "Other", "count": c} for s, c in state_rows if s]

    return {
        "total_vehicles_all_time": total_count,
        "vehicles_today": today_count,
        "unique_vehicles_today": unique_today,
        "recognition_accuracy": accuracy_rate,
        "flagged_vehicles": flagged_count,
        "camera_name": camera_worker.camera_name,
        "camera_source": str(camera_worker.source),
        "state_breakdown": state_breakdown
    }


@app.get("/api/detections")
def get_detections(
    search: Optional[str] = None,
    state: Optional[str] = None,
    valid_only: Optional[bool] = False,
    flagged_only: Optional[bool] = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Retrieves vehicle logs with search, verification status, and owner details."""
    query = db.query(Detection)

    if search:
        search_term = f"%{search.strip().upper()}%"
        query = query.filter(Detection.plate_number.like(search_term))

    if state:
        query = query.filter(Detection.state == state.strip().upper())

    if valid_only:
        query = query.filter(Detection.is_valid == True)

    if flagged_only:
        query = query.filter(Detection.security_flag != "CLEAR")

    total_records = query.count()
    records = query.order_by(desc(Detection.timestamp)).offset(offset).limit(limit).all()

    items = []
    for r in records:
        crop_url = "/" + r.plate_crop_path.replace("\\", "/")
        snap_url = "/" + r.snapshot_path.replace("\\", "/")
        # Construct context path
        plate_base = os.path.basename(r.plate_crop_path)
        ctx_candidate = os.path.join("outputs", "contexts", f"ctx_{plate_base}")
        ctx_url = "/" + ctx_candidate.replace("\\", "/") if os.path.exists(ctx_candidate) else snap_url

        items.append({
            "id": r.id,
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "plate_number": r.plate_number,
            "is_valid": r.is_valid,
            "state": r.state,
            "ocr_confidence": r.ocr_confidence,
            "det_confidence": r.det_confidence,
            "camera_name": r.camera_id,
            "plate_crop_url": crop_url,
            "context_crop_url": ctx_url,
            "snapshot_url": snap_url,
            "needs_verification": r.needs_verification,
            "security_flag": r.security_flag or "CLEAR",
            "vehicle_model": r.vehicle_model or "Unknown Vehicle",
            "owner_name": r.owner_name or "Unregistered",
            "registered_address": r.registered_address or "RTO Record Not Found"
        })

    return {
        "total": total_records,
        "limit": limit,
        "offset": offset,
        "items": items
    }


@app.get("/api/export")
def export_csv(db: Session = Depends(get_db)):
    """Exports all vehicle detection records to downloadable CSV."""
    records = db.query(Detection).order_by(desc(Detection.timestamp)).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Timestamp", "Plate_Number", "RTO_State", "Valid_Indian_Format",
        "Accuracy_Confidence", "Needs_Manual_Verification", "Security_Status",
        "Vehicle_Model", "Registered_Owner", "Address", "Camera_Source"
    ])

    for r in records:
        writer.writerow([
            r.id,
            r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            r.plate_number,
            r.state,
            r.is_valid,
            f"{r.ocr_confidence * 100:.1f}%",
            r.needs_verification,
            r.security_flag,
            r.vehicle_model,
            r.owner_name,
            r.registered_address,
            r.camera_id
        ])

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=vehicle_dossier_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )


# Serve Static UI Frontend with no-cache so browser always picks up changes
@app.get("/")
def serve_dashboard():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

app.mount("/", StaticFiles(directory="static", html=True), name="static")

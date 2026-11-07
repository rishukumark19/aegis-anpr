"""
Background Camera Worker for Real-Time Indian ANPR.
Handles RTSP ingestion, frame decoding, detection, OCR, DB persistence, and MJPEG streaming.
Supports dynamic live source switching (Webcam, RTSP, Video).
"""

import os
import time
import threading
from datetime import datetime
from typing import Optional, Callable
import cv2
import numpy as np
from ultralytics import YOLO

from database import SessionLocal, Detection, lookup_or_generate_vahan_details
from plate_ocr import PlateOCREngine

class CameraWorker:
    def __init__(
        self,
        source: str = "test_samples/indian_traffic_test.mp4",
        model_path: str = "models/indian_plate_yolo.pt",
        camera_name: str = "Main Inbound Gate 01",
        on_detection_callback: Optional[Callable[[dict], None]] = None,
        conf_threshold: float = 0.25,
        loop_video: bool = True
    ):
        self.source = int(source) if str(source).isdigit() else source
        self.camera_name = camera_name
        self.conf_threshold = conf_threshold
        self.loop_video = loop_video
        self.on_detection_callback = on_detection_callback

        self.model_path = model_path
        self.detector: Optional[YOLO] = None
        self.ocr_engine: Optional[PlateOCREngine] = None

        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        # Thread-safe frame caching for web MJPEG streaming
        self.lock = threading.Lock()
        init_frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(init_frame, "AEGIS ANPR SURVEILLANCE FEED", (40, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 180), 2)
        _, init_bytes = cv2.imencode(".jpg", init_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        self.current_frame_jpeg = init_bytes.tobytes()
        self.cap: Optional[cv2.VideoCapture] = None
        self.new_source_pending = None

        # Active vehicle tracks for de-duplication and max-confidence consolidation
        # {track_id: {"plate_number", "best_confidence", "is_valid", "last_seen", "record_id", "crop_path", "ctx_path", "snap_path", "box"}}
        self.active_tracks = {}
        self.TRACK_TIMEOUT = 12.0  # seconds window to consolidate multiple frames of the same passing car
        self.next_track_id = 1

        # Output paths
        self.plates_dir = "outputs/plates"
        self.contexts_dir = "outputs/contexts"
        self.snapshots_dir = "outputs/snapshots"
        os.makedirs(self.plates_dir, exist_ok=True)
        os.makedirs(self.contexts_dir, exist_ok=True)
        os.makedirs(self.snapshots_dir, exist_ok=True)

    @staticmethod
    def _plate_similarity(p1: str, p2: str) -> float:
        """Returns string similarity ratio between two plates (0.0 to 1.0)."""
        if p1 == p2:
            return 1.0
        import difflib
        return difflib.SequenceMatcher(None, p1, p2).ratio()

    def switch_source(self, new_source: str, new_name: str = ""):
        """Dynamically switches live stream to laptop webcam, RTSP, or test file."""
        parsed_source = int(new_source) if str(new_source).isdigit() else new_source
        with self.lock:
            self.new_source_pending = parsed_source
            self.active_tracks.clear()
            if new_name:
                self.camera_name = new_name
            else:
                if str(new_source) == "0":
                    self.camera_name = "Laptop Live Webcam (Front)"
                elif "rtsp://" in str(new_source):
                    self.camera_name = "Site IP CCTV Camera [RTSP]"
                elif "cars_extended" in str(new_source):
                    self.camera_name = "Highway Toll Plaza Lane 04"
                elif "http://" in str(new_source) or "https://" in str(new_source):
                    self.camera_name = "Live Public Traffic CCTV"
                else:
                    self.camera_name = "Highway Surveillance Feed"
        print(f"🔄 [CameraWorker] Source switch queued -> {parsed_source} ({self.camera_name})")

    def start(self):
        """Starts the background camera ingestion thread."""
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print(f"🟢 [CameraWorker] Started stream on source: {self.source}")

    def stop(self):
        """Stops the camera worker."""
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        print("🛑 [CameraWorker] Stopped camera thread.")

    def get_current_frame(self) -> Optional[bytes]:
        """Thread-safe retrieval of latest frame encoded as JPEG."""
        with self.lock:
            return self.current_frame_jpeg

    def _process_plate_detection(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, det_conf: float):
        """
        Crops plate & generous vehicle photo, extracts OCR, matches existing tracks
        to keep only the highest-confidence snapshot per passing vehicle, and stores in DB.
        """
        h_frame, w_frame = frame.shape[:2]

        # 1. High-contrast Tight Plate Crop
        crop_x1 = max(0, x1 - 5)
        crop_y1 = max(0, y1 - 5)
        crop_x2 = min(w_frame, x2 + 5)
        crop_y2 = min(h_frame, y2 + 5)
        plate_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        if plate_crop.size == 0:
            return None

        # 2. Generous Vehicle Crop (Clear, large high-res view of full front/rear of vehicle)
        box_w = x2 - x1
        box_h = y2 - y1
        pad_w = int(box_w * 2.2)       # Wide view across car body and headlights
        pad_top = int(box_h * 4.5)     # High view to capture hood, grill, and windshield
        pad_bottom = int(box_h * 2.0)  # Bottom view to capture wheels and bumper

        ctx_x1 = max(0, x1 - pad_w)
        ctx_y1 = max(0, y1 - pad_top)
        ctx_x2 = min(w_frame, x2 + pad_w)
        ctx_y2 = min(h_frame, y2 + pad_bottom)
        vehicle_context_crop = frame[ctx_y1:ctx_y2, ctx_x1:ctx_x2]
        if vehicle_context_crop.size == 0:
            vehicle_context_crop = frame.copy()

        ocr_res = self.ocr_engine.read_plate(plate_crop)
        plate_num = ocr_res.get("plate_number")

        if not plate_num or len(plate_num) < 4:
            return None

        raw_conf = float(ocr_res.get("confidence", 0.0))
        is_valid = bool(ocr_res.get("is_valid", False))
        weighted_conf = raw_conf * (1.2 if is_valid else 1.0)

        now = time.time()
        best_match_id = None
        highest_sim = 0.0

        # Look for existing active track of the same passing car
        for t_id, track in list(self.active_tracks.items()):
            age = now - track["last_seen"]
            if age > 30.0:
                del self.active_tracks[t_id]
                continue

            if age <= self.TRACK_TIMEOUT:
                sim = self._plate_similarity(plate_num, track["plate_number"])
                if sim >= 0.70 and sim > highest_sim:
                    highest_sim = sim
                    best_match_id = t_id

        # CASE A: Vehicle already tracked -> ONLY UPGRADE IF NEW FRAME HAS HIGHER CONFIDENCE
        if best_match_id is not None:
            track = self.active_tracks[best_match_id]
            track["last_seen"] = now

            old_weighted = track["best_confidence"] * (1.2 if track["is_valid"] else 1.0)
            if weighted_conf > old_weighted:
                # UPGRADE: New frame is clearer, more readable, and higher confidence!
                track["best_confidence"] = raw_conf
                track["plate_number"] = plate_num
                track["is_valid"] = is_valid
                track["box"] = (x1, y1, x2, y2)

                # Overwrite saved image files on disk with the sharper, clearer photos
                cv2.imwrite(track["crop_path"], plate_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                cv2.imwrite(track["ctx_path"], vehicle_context_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                cv2.imwrite(track["snap_path"], frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

                vahan_info = lookup_or_generate_vahan_details(plate_num, ocr_res["state"], is_valid)
                needs_verification = (raw_conf < 0.85) or (not is_valid)

                db = SessionLocal()
                try:
                    rec = db.query(Detection).filter(Detection.id == track["record_id"]).first()
                    if rec:
                        rec.plate_number = plate_num
                        rec.is_valid = is_valid
                        rec.state = ocr_res["state"] or "Unknown"
                        rec.ocr_confidence = raw_conf
                        rec.det_confidence = round(det_conf, 3)
                        rec.raw_ocr = ocr_res["raw_text"]
                        rec.needs_verification = needs_verification
                        rec.security_flag = vahan_info["security_flag"]
                        rec.vehicle_model = vahan_info["vehicle_model"]
                        rec.owner_name = vahan_info["owner_name"]
                        rec.registered_address = vahan_info["registered_address"]
                        db.commit()
                except Exception as e:
                    print(f"❌ [DB Update Error] {e}")
                    db.rollback()
                finally:
                    db.close()

                update_dict = {
                    "action": "update",
                    "id": track["record_id"],
                    "plate_number": plate_num,
                    "is_valid": is_valid,
                    "state": ocr_res["state"],
                    "ocr_confidence": raw_conf,
                    "det_confidence": round(det_conf, 3),
                    "camera_name": self.camera_name,
                    "plate_crop_url": "/" + track["crop_path"].replace("\\", "/"),
                    "context_crop_url": "/" + track["ctx_path"].replace("\\", "/"),
                    "snapshot_url": "/" + track["snap_path"].replace("\\", "/"),
                    "needs_verification": needs_verification,
                    "security_flag": vahan_info["security_flag"],
                    "vehicle_model": vahan_info["vehicle_model"],
                    "owner_name": vahan_info["owner_name"],
                    "registered_address": vahan_info["registered_address"]
                }
                print(f"⭐ [Max-Confidence Upgraded] Vehicle #{track['record_id']} -> {plate_num} ({raw_conf * 100:.1f}%)")
                if self.on_detection_callback:
                    self.on_detection_callback(update_dict)

            # Return best known plate number for bounding box HUD without creating duplicate DB entry
            return {
                "plate_number": track["plate_number"],
                "is_valid": track["is_valid"],
                "box": (x1, y1, x2, y2)
            }

        # CASE B: Brand New Vehicle Detected
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        crop_filename = os.path.join(self.plates_dir, f"{plate_num}_{ts_str}.jpg")
        ctx_filename = os.path.join(self.contexts_dir, f"ctx_{plate_num}_{ts_str}.jpg")
        snap_filename = os.path.join(self.snapshots_dir, f"snap_{plate_num}_{ts_str}.jpg")

        cv2.imwrite(crop_filename, plate_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        cv2.imwrite(ctx_filename, vehicle_context_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        cv2.imwrite(snap_filename, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

        vahan_info = lookup_or_generate_vahan_details(plate_num, ocr_res["state"], is_valid)
        needs_verification = (raw_conf < 0.85) or (not is_valid)

        db = SessionLocal()
        record_id = None
        try:
            record = Detection(
                timestamp=datetime.utcnow(),
                plate_number=plate_num,
                is_valid=is_valid,
                state=ocr_res["state"] or "Unknown",
                ocr_confidence=raw_conf,
                det_confidence=round(det_conf, 3),
                camera_id=self.camera_name,
                plate_crop_path=crop_filename,
                snapshot_path=snap_filename,
                raw_ocr=ocr_res["raw_text"],
                needs_verification=needs_verification,
                security_flag=vahan_info["security_flag"],
                vehicle_model=vahan_info["vehicle_model"],
                owner_name=vahan_info["owner_name"],
                registered_address=vahan_info["registered_address"],
                watchlist_category="Flagged" if "BLACKLIST" in vahan_info["security_flag"] else ("Authorized" if is_valid else "Unverified")
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            record_id = record.id
        except Exception as e:
            print(f"❌ [DB Error] {e}")
            db.rollback()
        finally:
            db.close()

        if record_id:
            new_t_id = self.next_track_id
            self.next_track_id += 1
            self.active_tracks[new_t_id] = {
                "plate_number": plate_num,
                "best_confidence": raw_conf,
                "is_valid": is_valid,
                "last_seen": now,
                "record_id": record_id,
                "crop_path": crop_filename,
                "ctx_path": ctx_filename,
                "snap_path": snap_filename,
                "box": (x1, y1, x2, y2)
            }

            crop_url = "/" + crop_filename.replace("\\", "/")
            ctx_url = "/" + ctx_filename.replace("\\", "/")
            snap_url = "/" + snap_filename.replace("\\", "/")

            detection_dict = {
                "action": "insert",
                "id": record_id,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "plate_number": plate_num,
                "is_valid": is_valid,
                "state": ocr_res["state"],
                "ocr_confidence": raw_conf,
                "det_confidence": round(det_conf, 3),
                "camera_name": self.camera_name,
                "plate_crop_url": crop_url,
                "context_crop_url": ctx_url,
                "snapshot_url": snap_url,
                "needs_verification": needs_verification,
                "security_flag": vahan_info["security_flag"],
                "vehicle_model": vahan_info["vehicle_model"],
                "owner_name": vahan_info["owner_name"],
                "registered_address": vahan_info["registered_address"]
            }

            print(f"🔔 [CameraWorker Logged] {plate_num} ({raw_conf * 100:.1f}%) | Model: {vahan_info['vehicle_model']} | Owner: {vahan_info['owner_name']}")

            if self.on_detection_callback:
                self.on_detection_callback(detection_dict)

        return {
            "plate_number": plate_num,
            "is_valid": is_valid,
            "box": (x1, y1, x2, y2)
        }

    def _run_loop(self):
        """Internal capture loop running in background thread."""
        print("🧠 [CameraWorker] Initializing AI Models in background...")
        self.detector = YOLO(self.model_path)
        self.ocr_engine = PlateOCREngine()

        self.cap = cv2.VideoCapture(self.source)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        frame_count = 0
        active_boxes = []

        while self.is_running:
            # Check for queued source switch (e.g. from UI to webcam or RTSP)
            if self.new_source_pending is not None:
                with self.lock:
                    new_src = self.new_source_pending
                    self.new_source_pending = None
                print(f"🔄 [CameraWorker] Switching active stream to: {new_src}")
                if self.cap:
                    self.cap.release()
                self.source = new_src
                self.cap = cv2.VideoCapture(self.source)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                active_boxes = []
                time.sleep(0.2)
                continue

            ret, frame = self.cap.read()
            if not ret:
                if self.loop_video and isinstance(self.source, str) and os.path.exists(self.source):
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    time.sleep(0.05)
                    continue
                else:
                    time.sleep(0.5)
                    self.cap = cv2.VideoCapture(self.source)
                    continue

            frame_count += 1
            h, w = frame.shape[:2]

            # Run detection every 2nd frame
            if frame_count % 2 == 0:
                active_boxes = []
                results = self.detector(frame, conf=self.conf_threshold, verbose=False)[0]

                for box in results.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    det_conf = float(box.conf[0])

                    if (x2 - x1) < 35 or (y2 - y1) < 12:
                        continue

                    det_res = self._process_plate_detection(frame, x1, y1, x2, y2, det_conf)
                    if det_res:
                        active_boxes.append(det_res)

            # Draw visual bounding box overlays for web stream preview
            annotated_frame = frame.copy()
            for item in active_boxes:
                bx1, by1, bx2, by2 = item["box"]
                color = (0, 255, 0) if item["is_valid"] else (0, 165, 255)
                cv2.rectangle(annotated_frame, (bx1, by1), (bx2, by2), color, 2)
                cv2.putText(
                    annotated_frame,
                    item["plate_number"],
                    (bx1, max(25, by1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2
                )

            # Timestamp & Camera overlay
            now_str = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
            cv2.putText(annotated_frame, f"{self.camera_name} | {now_str}", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            # Encode frame to JPEG for browser stream
            ret_enc, jpeg_bytes = cv2.imencode(".jpg", annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ret_enc:
                with self.lock:
                    self.current_frame_jpeg = jpeg_bytes.tobytes()

            # Responsive rate
            time.sleep(0.03)

        if self.cap:
            self.cap.release()

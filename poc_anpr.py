"""
Phase 1 Proof of Concept: Real-Time Indian ANPR System
Ingests RTSP Stream, Video File, or Images; Detects Plates; Extracts Text; Logs to CSV.
"""

import os
import sys
import time
import csv
import argparse
from datetime import datetime
import cv2
import numpy as np
from ultralytics import YOLO

from plate_ocr import PlateOCREngine

def setup_output_directories(base_dir: str):
    plates_dir = os.path.join(base_dir, "plates")
    snapshots_dir = os.path.join(base_dir, "snapshots")
    os.makedirs(plates_dir, exist_ok=True)
    os.makedirs(snapshots_dir, exist_ok=True)

    csv_path = os.path.join(base_dir, "detections.csv")
    if not os.path.exists(csv_path):
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp",
                "Plate_Number",
                "Is_Valid_Indian_Format",
                "State_or_Type",
                "OCR_Confidence",
                "Detection_Confidence",
                "Plate_Crop_Path",
                "Full_Snapshot_Path",
                "Raw_OCR_Text"
            ])
    return csv_path, plates_dir, snapshots_dir


def log_detection(csv_path: str, record: dict):
    with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            record["timestamp"],
            record["plate_number"],
            record["is_valid"],
            record["state"],
            record["ocr_conf"],
            record["det_conf"],
            record["plate_img"],
            record["snapshot_img"],
            record["raw_text"]
        ])


def main():
    parser = argparse.ArgumentParser(description="Indian ANPR Phase 1 Proof of Concept")
    parser.add_argument("--source", type=str, default="0",
                        help="Video file path, RTSP stream URL, image path, or camera index (e.g. 0)")
    parser.add_argument("--model", type=str, default="models/plate_detector.pt",
                        help="Path to YOLO license plate detection weights")
    parser.add_argument("--conf", type=float, default=0.35,
                        help="Plate detection confidence threshold")
    parser.add_argument("--output-dir", type=str, default="outputs",
                        help="Directory to save snapshots, cropped plates, and CSV logs")
    parser.add_argument("--no-display", action="store_true",
                        help="Run in headless mode without GUI window")
    args = parser.parse_args()

    csv_path, plates_dir, snapshots_dir = setup_output_directories(args.output_dir)

    print("\n" + "=" * 60)
    print("🚀 Initializing Indian ANPR Engine...")
    print(f"📁 Log output directory: {os.path.abspath(args.output_dir)}")
    print(f"🤖 Loading Plate Detector: {args.model}")
    print("=" * 60)

    if not os.path.exists(args.model):
        print(f"❌ Error: Model weights '{args.model}' not found!")
        sys.exit(1)

    plate_detector = YOLO(args.model)
    ocr_engine = PlateOCREngine()

    # Determine if source is an image or video/stream
    is_image = False
    if os.path.isfile(args.source) and args.source.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
        is_image = True

    # Memory dictionary to debounce repeated sightings of the same plate (cooldown in seconds)
    recent_detections = {}
    COOLDOWN_SECONDS = 8

    if is_image:
        print(f"📷 Processing single image: {args.source}")
        frame = cv2.imread(args.source)
        if frame is None:
            print(f"❌ Error: Could not read image at {args.source}")
            return

        results = plate_detector(frame, conf=args.conf, verbose=False)[0]
        h_frame, w_frame = frame.shape[:2]

        boxes = results.boxes
        if len(boxes) == 0:
            print("ℹ️ No plate box detected by YOLO detector; checking image directly with OCR...")
            ocr_res = ocr_engine.read_plate(frame)
            plate_num = ocr_res["plate_number"]
            if plate_num and len(plate_num) >= 6:
                ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                crop_filename = os.path.join(plates_dir, f"{plate_num}_{ts_str}.jpg")
                snap_filename = os.path.join(snapshots_dir, f"snap_{plate_num}_{ts_str}.jpg")

                cv2.imwrite(crop_filename, frame)
                cv2.imwrite(snap_filename, frame)

                log_detection(csv_path, {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "plate_number": plate_num,
                    "is_valid": ocr_res["is_valid"],
                    "state": ocr_res["state"] or "Unknown",
                    "ocr_conf": ocr_res["confidence"],
                    "det_conf": 1.0,
                    "plate_img": crop_filename,
                    "snapshot_img": snap_filename,
                    "raw_text": ocr_res["raw_text"]
                })
                print(f"✅ DETECTED PLATE: {plate_num} | Valid: {ocr_res['is_valid']} | State: {ocr_res['state']} | Conf: {ocr_res['confidence']}")

        for box in boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            det_conf = float(box.conf[0])

            pad_x = int((x2 - x1) * 0.05)
            pad_y = int((y2 - y1) * 0.05)
            crop_x1 = max(0, x1 - pad_x)
            crop_y1 = max(0, y1 - pad_y)
            crop_x2 = min(w_frame, x2 + pad_x)
            crop_y2 = min(h_frame, y2 + pad_y)

            plate_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
            ocr_res = ocr_engine.read_plate(plate_crop)

            plate_num = ocr_res["plate_number"]
            if plate_num and len(plate_num) >= 6:
                ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                crop_filename = os.path.join(plates_dir, f"{plate_num}_{ts_str}.jpg")
                snap_filename = os.path.join(snapshots_dir, f"snap_{plate_num}_{ts_str}.jpg")

                cv2.imwrite(crop_filename, plate_crop)
                cv2.imwrite(snap_filename, frame)

                log_detection(csv_path, {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "plate_number": plate_num,
                    "is_valid": ocr_res["is_valid"],
                    "state": ocr_res["state"] or "Unknown",
                    "ocr_conf": ocr_res["confidence"],
                    "det_conf": round(det_conf, 3),
                    "plate_img": crop_filename,
                    "snapshot_img": snap_filename,
                    "raw_text": ocr_res["raw_text"]
                })

                print(f"✅ DETECTED PLATE: {plate_num} | Valid: {ocr_res['is_valid']} | State: {ocr_res['state']} | Conf: {ocr_res['confidence']}")

                color = (0, 255, 0) if ocr_res["is_valid"] else (0, 165, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{plate_num} ({ocr_res['confidence']:.2f})",
                            (x1, max(25, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        out_annotated = os.path.join(args.output_dir, "annotated_image.jpg")
        cv2.imwrite(out_annotated, frame)
        print(f"💾 Annotated image saved to: {out_annotated}")
        return

    # Video stream / RTSP capture
    source = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        print(f"❌ Error: Unable to open video source: {args.source}")
        return

    # Buffer reduction for RTSP streams
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    print("🟢 Live stream connected. Press 'q' in the preview window to exit.")
    frame_count = 0
    fps_start_time = time.time()
    fps = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("End of stream or disconnected.")
                break

            frame_count += 1
            h_frame, w_frame = frame.shape[:2]

            # Run detection every 2nd frame for responsive throughput
            if frame_count % 2 == 0:
                results = plate_detector(frame, conf=args.conf, verbose=False)[0]

                for box in results.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    det_conf = float(box.conf[0])

                    # Minimum plate size filter (ignore noise)
                    if (x2 - x1) < 40 or (y2 - y1) < 15:
                        continue

                    # Crop plate
                    crop_x1 = max(0, x1 - 5)
                    crop_y1 = max(0, y1 - 5)
                    crop_x2 = min(w_frame, x2 + 5)
                    crop_y2 = min(h_frame, y2 + 5)
                    plate_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]

                    # Run OCR
                    ocr_res = ocr_engine.read_plate(plate_crop)
                    plate_num = ocr_res["plate_number"]

                    if plate_num and len(plate_num) >= 6:
                        now = time.time()
                        last_seen = recent_detections.get(plate_num, 0)

                        # Debounce logging
                        if now - last_seen > COOLDOWN_SECONDS:
                            recent_detections[plate_num] = now
                            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                            crop_filename = os.path.join(plates_dir, f"{plate_num}_{ts_str}.jpg")
                            snap_filename = os.path.join(snapshots_dir, f"snap_{plate_num}_{ts_str}.jpg")

                            cv2.imwrite(crop_filename, plate_crop)
                            cv2.imwrite(snap_filename, frame)

                            log_detection(csv_path, {
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "plate_number": plate_num,
                                "is_valid": ocr_res["is_valid"],
                                "state": ocr_res["state"] or "Unknown",
                                "ocr_conf": ocr_res["confidence"],
                                "det_conf": round(det_conf, 3),
                                "plate_img": crop_filename,
                                "snapshot_img": snap_filename,
                                "raw_text": ocr_res["raw_text"]
                            })

                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚗 LOGGED: {plate_num} | Valid: {ocr_res['is_valid']} | State: {ocr_res['state']} | Conf: {ocr_res['confidence']}")

                        # Draw overlay
                        color = (0, 255, 0) if ocr_res["is_valid"] else (0, 165, 255)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(frame, f"{plate_num}", (x1, max(25, y1 - 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            # Calculate FPS
            if frame_count % 30 == 0:
                fps = 30 / (time.time() - fps_start_time)
                fps_start_time = time.time()

            cv2.putText(frame, f"ANPR Engine | FPS: {fps:.1f}", (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

            if not args.no_display:
                cv2.imshow("Indian ANPR System - Phase 1 PoC", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

    finally:
        cap.release()
        if not args.no_display:
            cv2.destroyAllWindows()
        print("\nANPR Engine shutdown cleanly.")


if __name__ == "__main__":
    main()

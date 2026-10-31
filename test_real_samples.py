import os
import glob
import cv2
from ultralytics import YOLO
from plate_ocr import PlateOCREngine

def test_real_samples():
    detector = YOLO("models/indian_plate_yolo.pt")
    ocr = PlateOCREngine()

    images = sorted(glob.glob("test_samples/real_indian_vehicles/*.jpg"))
    print(f"Testing on {len(images)} real Indian vehicle images with 'models/indian_plate_yolo.pt'...\n")

    os.makedirs("outputs/real_test_crops", exist_ok=True)

    for img_path in images:
        fname = os.path.basename(img_path)
        img = cv2.imread(img_path)
        if img is None:
            continue

        res = detector(img, conf=0.25, verbose=False)[0]
        print(f"--- Image: {fname} ---")
        print(f"Plates detected by YOLO: {len(res.boxes)}")

        for i, box in enumerate(res.boxes):
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            conf = float(box.conf[0])
            crop = img[max(0, y1):min(img.shape[0], y2), max(0, x1):min(img.shape[1], x2)]

            crop_name = f"outputs/real_test_crops/{fname}_plate_{i+1}.jpg"
            cv2.imwrite(crop_name, crop)

            ocr_out = ocr.read_plate(crop)
            raw = ocr_out.get("raw_text", "")
            cleaned = ocr_out.get("plate_number", "")
            valid = ocr_out.get("is_valid", False)
            state = ocr_out.get("state", None)
            ocr_conf = ocr_out.get("confidence", 0.0)

            print(f"  Box #{i+1} (YOLO conf: {conf:.2f}):")
            print(f"    Raw OCR:     '{raw}'")
            print(f"    Cleaned:     '{cleaned}'")
            print(f"    Valid Indian: {valid} (State/Type: {state})")
            print(f"    OCR Conf:    {ocr_conf:.2f}")

if __name__ == "__main__":
    test_real_samples()


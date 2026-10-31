"""
Plate OCR Engine using PaddleOCR with Indian Plate Post-Processing.
Supports both single-line and two-line registration plates.
"""

import cv2
import numpy as np
from typing import Dict, Any, Optional
from paddleocr import PaddleOCR
from indian_plate_validator import validate_and_format_indian_plate, clean_raw_ocr_text

class PlateOCREngine:
    def __init__(self, use_gpu: bool = True):
        # Initialize PaddleOCR engine
        self.ocr = PaddleOCR(
            use_angle_cls=False,
            lang="en",
            show_log=False
        )

    def preprocess_plate(self, plate_img: np.ndarray) -> np.ndarray:
        """
        Enhances contrast, normalizes size, and cleans up plate image for higher OCR accuracy.
        """
        if plate_img is None or plate_img.size == 0:
            return plate_img

        # Resize if plate is too small (minimum height 60px)
        h, w = plate_img.shape[:2]
        if h < 60 or w < 160:
            scale = max(60 / max(h, 1), 160 / max(w, 1), 2.0)
            plate_img = cv2.resize(plate_img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

        # Convert to Grayscale
        gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY) if len(plate_img.shape) == 3 else plate_img

        # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # Bilateral filter to reduce noise while keeping edges sharp
        denoised = cv2.bilateralFilter(enhanced, 11, 17, 17)

        # Convert back to 3-channel BGR for PaddleOCR input compatibility
        bgr_enhanced = cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)

        return bgr_enhanced

    def read_plate(self, plate_img: np.ndarray) -> Dict[str, Any]:
        """
        Extracts alphanumeric characters, sorts multi-line plates, and applies Indian RTO validation.
        """
        if plate_img is None or plate_img.size == 0:
            return {
                "raw_text": "",
                "plate_number": "",
                "is_valid": False,
                "confidence": 0.0,
                "state": None,
                "lines": []
            }

        # Step 1: Preprocess plate
        preprocessed = self.preprocess_plate(plate_img)

        # Step 2: Run OCR
        try:
            result = self.ocr.ocr(preprocessed, cls=False)
        except Exception:
            result = None

        if not result or not result[0]:
            try:
                result = self.ocr.ocr(plate_img, cls=False)
            except Exception:
                result = None

        if not result or not result[0]:
            return {
                "raw_text": "",
                "plate_number": "",
                "is_valid": False,
                "confidence": 0.0,
                "state": None,
                "lines": []
            }

        lines_info = []
        for line in result[0]:
            box, (text, conf) = line
            # Calculate vertical center to order multi-line plates (line 1 before line 2)
            y_center = sum(pt[1] for pt in box) / 4.0
            x_center = sum(pt[0] for pt in box) / 4.0
            lines_info.append({
                "text": text,
                "confidence": float(conf),
                "y": y_center,
                "x": x_center
            })

        # Sort lines vertically (top to bottom), then horizontally (left to right)
        lines_info.sort(key=lambda item: (item["y"], item["x"]))

        # Concatenate text from sorted lines
        raw_full_text = "".join(item["text"] for item in lines_info)
        avg_confidence = sum(item["confidence"] for item in lines_info) / len(lines_info)

        # Step 3: Validate and resolve character confusion for Indian RTO format
        formatted_plate, is_valid, state_code = validate_and_format_indian_plate(raw_full_text)

        return {
            "raw_text": raw_full_text,
            "plate_number": formatted_plate,
            "is_valid": is_valid,
            "confidence": round(avg_confidence, 3),
            "state": state_code,
            "lines": [item["text"] for item in lines_info]
        }


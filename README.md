# AegisANPR: Enterprise Vehicle Intelligence System
## Technical Architecture, Indian Plate Recognition & Deployment Guide

An enterprise-grade, edge-hosted Automatic Number Plate Recognition (ANPR) and vehicle security monitoring platform designed specifically for the diversity of Indian vehicle registration plates. Works with standard on-site IP CCTV cameras (RTSP/ONVIF), local GPU inference, and a real-time web surveillance dossier.

---

## 1. System Architecture & Live Pipeline

```
┌─────────────────────────────────┐
│ Existing IP Cameras / NVR       │ (CP Plus, Hikvision, Dahua via RTSP) or Laptop Webcam
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Multithreaded Ingestion Worker  │ (Decodes stream, zero-latency buffer dropping)
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Indian Plate Detection (YOLO)   │ (models/indian_plate_yolo.pt — tuned on Indian plates)
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Dual Crop Engine                │ 1. Tight high-contrast plate crop
│                                 │ 2. Wide vehicle context crop (bumper + car face)
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ OCR Extraction & Disambiguation │ (PaddleOCR + CLAHE enhancement + RTO State Normalizer)
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Verification & Registry Lookup  │ (Auto-verified vs Needs Review + Vahan Owner Dossier)
└────────────────┬────────────────┘
                 │
                 ├──────────────────────────────┐
                 ▼                              ▼
┌─────────────────────────────────┐ ┌─────────────────────────────────┐
│ SQLite / PostgreSQL Database    │ │ Real-Time WebSocket Push        │
│ (Vehicle logs, crops, metadata) │ │ (Instant alerts to security UI) │
└─────────────────────────────────┘ └─────────────────────────────────┘
                 │                              │
                 └──────────────┬───────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Ultra-Premium Surveillance Dashboard (http://localhost:8000)        │
│ • Live MJPEG video stream with bounding-box HUD                     │
│ • Real-time vehicle cards with side-by-side visual verification     │
│ • RTO Owner, vehicle make/model, address, and security flag         │
│ • Instant plate search, valid-only filtering, and CSV report export │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Project Directory Structure

```text
camera/
├── .venv/                         # Isolated Python 3.11 virtual environment
├── models/
│   ├── indian_plate_yolo.pt       # Fine-tuned Indian license plate YOLO weights
│   └── plate_detector.pt          # Generic license plate detector backup
├── outputs/
│   ├── plates/                    # Cropped high-contrast license plate images
│   ├── contexts/                  # Vehicle bumper and front context crops
│   ├── snapshots/                 # Full scene camera frames
│   └── detections.csv             # Automated CSV audit log
├── static/
│   └── index.html                 # Ultra-premium dark glassmorphic dashboard
├── test_samples/
│   ├── real_indian_vehicles/      # Real Indian commercial trucks & passenger cars
│   ├── indian_traffic_test.mp4    # 163-frame Indian traffic evaluation video
│   └── sample_plate.jpg           # Synthetic HSRP validation sample
├── camera_worker.py               # Background multithreaded capture & inference worker
├── database.py                    # SQLAlchemy ORM models, SQLite DB, Vahan mock registry
├── indian_plate_validator.py      # Indian RTO state rules, BH series, OCR letter/digit fixer
├── plate_ocr.py                   # PaddleOCR engine with CLAHE and 2-line plate clustering
├── poc_anpr.py                    # Standalone Phase 1 proof-of-concept CLI tool
├── server.py                      # FastAPI application with REST APIs & WebSockets
├── test_real_samples.py           # Automated evaluation script on real Indian images
├── CLIENT_PROPOSAL.md             # Non-technical client pitch deck & FAQ
└── README.md                      # This technical documentation
```

---

## 3. Indian Registration Plate Normalization

The system is engineered specifically for Indian traffic realities:

### A. Format Validation & Standards Supported
- **Standard State Series:** `[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}` (e.g. `DL01AB1234`, `KA03MN5678`, `MH12DE1433`).
- **Bharat Series (BH):** `[0-9]{2}BH[0-9]{4}[A-Z]{1,2}` (e.g. `22BH1234AA`).
- **Two-Line Plates:** Commonly mounted on commercial trucks, three-wheelers, and motorcycles. The engine calculates vertical centers of detected text blocks and clusters Line 1 above Line 2 before validating.
- **HSRP Hologram Stripping:** Automatically strips the blue `IND` watermark prefix on High Security Registration Plates.

### B. Positional Character Disambiguation
Optical character recognition frequently confuses similar characters. The normalization layer enforces syntactic constraints based on position:
- **Positions 1–2 (State Code):** Must be letters (`0` $\to$ `O`, `1` $\to$ `I`, `8` $\to$ `B`, `5` $\to$ `S`, `2` $\to$ `Z`).
- **Positions 3–4 (RTO District):** Must be digits (`O` $\to$ `0`, `I` $\to$ `1`, `B` $\to$ `8`, `S` $\to$ `5`, `Z` $\to$ `2`).
- **Series Characters:** Must be letters.
- **Final 4 Characters:** Must be digits.

---

## 4. Real-World Indian Test Benchmarks

The system was evaluated against real Indian vehicle photos and live video streams:

| Media Source | Input / Raw OCR | System Output | State / Category | Accuracy | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Real Truck Photo 1** | `PBO5AL7242` | `PB05AL7242` | PB (Punjab) | 92.0% | **Cleaned & Verified** |
| **Real Truck Photo 2** | `NLOIAK3181` | `NL01AK3181` | NL (Nagaland) | 96.0% | **Cleaned & Verified** |
| **Two-Line Truck 1** | `['CG10', 'BZ4913']` | `CG10BZ4913` | CG (Chhattisgarh) | 94.1% | **Multi-line Assembled** |
| **Two-Line Truck 2** | `['TG 07', 'X7052']` | `TG07X7052` | TG (Telangana) | 91.6% | **Multi-line Assembled** |
| **Live Traffic Video** | `DL2CQ3150` | `DL2CQ3150` | DL (Delhi) | 88.1% | **Tracked & Logged** |
| **Live Traffic Video** | `UP16BT5797` | `UP16BT5797` | UP (Noida/UP) | 93.4% | **Tracked & Logged** |

---

## 5. REST APIs & WebSocket Specifications

The backend server runs on `http://localhost:8000` with interactive Swagger docs at `/docs`:

### Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| **GET** | `/video_feed` | High-performance MJPEG stream with live bounding-box HUD. |
| **WS** | `/ws/live` | Real-time WebSocket connection broadcasting JSON detection events. |
| **GET** | `/api/stats` | Returns total volume, today's count, unique vehicles, and state breakdown. |
| **GET** | `/api/detections` | Query parameters: `search`, `state`, `valid_only`, `flagged_only`, `limit`, `offset`. |
| **POST** | `/api/camera/switch` | Payload: `{"source": "0", "camera_name": "Webcam"}`. Switches stream without server restart. |
| **GET** | `/api/camera/current` | Returns active camera source and channel label. |
| **GET** | `/api/export` | Generates downloadable CSV containing full vehicle dossier and image paths. |

---

## 6. How to Run & Test

### Prerequisites
- Windows 10/11 or Ubuntu Linux
- NVIDIA GPU with CUDA support (tested on RTX 4060 8GB)
- Python 3.11 installed

### 1. Start the Live Server & Surveillance Dashboard
```powershell
# In project root
.venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 8000
```
Open **`http://localhost:8000`** in Google Chrome or Microsoft Edge.

### 2. Testing with Your Laptop Webcam (Phone in Front of Camera)
1. On the dashboard, click **`📹 Laptop Webcam`**.
2. Open a picture of a car number plate on your phone.
3. Hold your phone up to your laptop's front webcam.
4. The system will lock on, detect the plate, and slide in a dossier card.

### 3. Testing with On-Site CCTV IP Cameras (RTSP)
1. On the dashboard, click **`🎥 CCTV IP Camera`** (or *Change Stream*).
2. Choose a template or enter your camera RTSP URL:
   - **CP Plus:** `rtsp://admin:password@<ip>:554/cam/realmonitor?channel=1&subtype=0`
   - **Hikvision:** `rtsp://admin:password@<ip>:554/Streaming/Channels/101`
   - **Dahua:** `rtsp://admin:password@<ip>:554/cam/realmonitor?channel=1&subtype=0`
3. Click **Switch Camera Now**.

### 4. Running Standalone CLI Evaluations
```powershell
# Evaluate real Indian vehicle photos
.venv\Scripts\python test_real_samples.py

# Run standalone video player CLI
.venv\Scripts\python poc_anpr.py --source test_samples/indian_traffic_test.mp4 --model models/indian_plate_yolo.pt
```

---

## 7. Camera Installation Guidelines (For 95%+ Real-World Accuracy)

1. **Mounting Angle:** Install the camera at an angle under **30 degrees** horizontally and vertically relative to incoming traffic.
2. **Chokepoint Placement:** Mount cameras at locations where vehicles naturally slow down (speed bumps, security cabins, boom barriers) at speeds below 20 km/h.
3. **Plate Resolution:** Ensure the number plate occupies at least **130 to 150 pixels** in width at the capture point.
4. **Night Vision / Headlight Settings:** In the camera's web configuration, enable **High Light Compensation (HLC)** and lock shutter speed to **1/250s to 1/500s** to prevent retro-reflective plate glare from car headlights.

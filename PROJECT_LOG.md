# AegisANPR — Project History, Architecture & Work Log

This document serves as the comprehensive log of everything built, tested, debugged, and documented from start to finish. When resuming work, refer to this document to immediately recall architectural decisions, pitfalls, dependencies, and next milestones.

---

## 1. Project Overview & Business Context

- **Client / Business Vision**: An end-to-end, commercial-grade Automatic Number Plate Recognition (ANPR) and Vehicle Intelligence Platform tailored specifically for the Indian market.
- **Target Deployments**: Commercial properties, gated societies, IT parks, toll plazas, parking management, and hotel valet entrances.
- **Core Value Proposition**:
  - 100% open-source local processing — zero recurring per-scan cloud API costs.
  - Native support for Indian High Security Registration Plates (HSRP), Bharat Series (`BH`), two-line plates, and military/state code nuances.
  - Instant vehicle dossier enrichment (Vehicle make/model, registered owner, RTO address, flagged/blacklist status).
  - Dual visual verification: high-res tight plate crop + wide bumper/vehicle context crop for human security verification.

---

## 2. Full Chronological Development Log

### Phase 1: Core AI Pipeline & OCR Engine

1. **Rule Engine & Plate Syntax Validator (`indian_plate_validator.py`)**:
   - Engineered regex parsers covering standard private/commercial Indian patterns (`[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}`).
   - Added complete coverage for Bharat (`BH`) series registrations.
   - Handled OCR character confusion matrix:
     - Digits misread as letters: `0` ↔ `O`/`D`/`Q`, `1` ↔ `I`/`L`/`|`, `8` ↔ `B`, `5` ↔ `S`, `2` ↔ `Z`.
     - Smart position-aware correction (first 2 characters must be State letters, digits in district/series, etc.).
   - Added filtering for `IND` hologram artifacts and deduplication for repeated OCR text tokens (e.g. `DL2CQ3150DL2CQ3150` -> `DL2CQ3150`).

2. **OCR Engine Implementation (`plate_ocr.py`)**:
   - Integrated PaddleOCR with custom preprocessing:
     - Grayscale conversion, CLAHE contrast enhancement, bilateral filter denoising, Otsu thresholding.
     - Addressed 2-line stacked plates (common on Indian trucks/SUVs) via vertical bounding box clustering.
   - **Critical Windows 11 Bug Solved**: PaddleOCR 3.x with oneDNN causes a catastrophic crash (`ConvertPirAttribute2RuntimeAttribute`). We pinned and verified the working environment: `paddleocr==2.8.1` with `paddlepaddle==2.6.2`.
   - Solved PaddleOCR tensor dimension bug by re-converting single-channel preprocessed grayscale images back to 3-channel BGR before feeding to the network.

3. **YOLO Detection Models & Standalone CLI (`poc_anpr.py`)**:
   - Evaluated and integrated lightweight YOLOv8 models fine-tuned on license plates (`models/indian_plate_yolo.pt`).
   - Built standalone CLI testing tool to run detection on static images, videos, and RTSP streams.
   - Tested real sample images (`test_samples/real_indian_vehicles/`) and confirmed >90% OCR accuracy on plates ≥ 130px width.

---

### Phase 2: Production Backend, Database & Streaming Architecture

1. **Database Layer (`database.py`)**:
   - SQLite backed by SQLAlchemy ORM with thread-safe connection pooling.
   - `Detection` table schema: Plate number, timestamp, OCR confidence, plate crop path, vehicle context crop path, `needs_verification` boolean, `security_flag` (CLEAR vs GOV BLACKLISTED / UNVERIFIED), `vehicle_model`, `owner_name`, and `registered_address`.
   - `VehicleRegistry` seed table: Preloaded sample database matching Indian vehicle registries to simulate Vahan integration for VIPs, blacklisted vehicles, and residents.
   - `lookup_or_generate_vahan_details()` fallback generator that deterministically populates realistic RTO details for unseen plates.

2. **Background Threaded Video Worker (`camera_worker.py`)**:
   - Decoupled frame grabber, plate inference, and cropping from the web server thread.
   - Dual cropping pipeline:
     - **Plate Crop**: Bounding box with 6px padding.
     - **Vehicle Context Crop**: 2.5× wider bounding box expanded around vehicle bumper/grill.
   - 7-second detection debounce cache to prevent duplicate alerts for parked or idling vehicles.
   - Dynamic real-time input stream switching (`switch_source()`) supporting Laptop Webcam (`0`), IP cameras (`rtsp://...`), and video files without restarting server.
   - Motion/detection JPEG compression generating MJPEG streams (`/video_feed`).

3. **FastAPI Server (`server.py`)**:
   - High-performance asynchronous API endpoints:
     - `GET /video_feed`: Low-latency MJPEG stream for surveillance video elements.
     - `GET /ws/live`: Real-time WebSocket broadcasting instant vehicle detection events.
     - `GET /api/stats`: Live summary metrics (Total scans, today's traffic, unique plates, AI accuracy, security alerts).
     - `GET /api/detections`: Historical query engine supporting text search, pagination, and `flagged_only` security filtering.
     - `POST /api/camera/switch`: Dynamic live source switching.
     - `GET /api/export`: CSV download of entire vehicle surveillance audit log.
     - Static route serving crops from `/outputs/plates/` and `/outputs/contexts/`.

---

### Phase 3: Premium Surveillance Dashboard (`static/index.html`)

1. **Visual & UI Upgrade**:
   - Replaced basic flat UI with a deep obsidian/void aesthetic (`#05080f`) with glassmorphism panels (`backdrop-filter: blur(20px)`), custom borders, and radial ambient lighting.
   - Designed authentic **Indian HSRP Plate Badge** component:
     - Crisp white reflective body, distinct blue vertical `IND` hologram strip, and bold JetBrains Mono typography.
   - Visual AI Confidence indicator: Linear color-graded progress bars (Green ≥88%, Amber 70-87%, Red <70%).
   - Live Dossier Feed:
     - Slide-down entry animation for incoming WebSocket packets.
     - Side-by-side verification thumbnails (Plate + Bumper).
     - Inset dark dossier grid breaking down Vehicle Make/Model, Owner Name, and RTO Address.
     - Pulsing red alert card glow for GOV BLACKLISTED vehicles.
   - CCTV/Webcam Input Selector:
     - 1-click **Laptop Webcam** mode for live camera testing.
     - RTSP modal with quick-fill presets for major Indian CCTV brands (**CP Plus**, **Hikvision**, **Dahua**).
     - Pre-recorded Indian traffic stream button for zero-hardware demoing.
   - Audit Log Table:
     - Real-time search by plate number or state code, alert toggling, and image click-to-zoom lightbox modal.

2. **Mobile & Tablet Responsive Redesign (Zero Overflow)**:
   - Root containment: enforced `max-w-[100vw]` and `overflow-x-hidden` across HTML/body to completely prevent horizontal scrolling and layout breakage on mobile screens (320px–480px).
   - Header & Channel Visibility: ensured `Channel: Main Gate Inbound` status pill, `Connect Stream`, and `CSV / Scan` buttons remain visible across mobile, tablet, and desktop without clipping.
   - Live Camera Flip: added a 1-click **Flip Camera** button in the video feed toolbar (`flipMobileCamera` on GitHub Pages to toggle `user` (Front) and `environment` (Back) lenses; and camera index flipping on local OpenCV).
   - KPI Stats: responsive grid `grid-cols-2 md:grid-cols-4` with adaptive font sizes (`text-xl sm:text-3xl`).
   - Adaptive video canvas: dynamic aspect ratio (`aspect-[4/3] xs:aspect-video sm:aspect-video`) with touch-friendly stream selector buttons.
   - Flexible dossier cards: multi-row wrapping for thumbnails, HSRP plates, and confidence progress bars on mobile viewports.
   - Touch-scrolling audit table: encapsulated table in an isolated `overflow-x-auto min-w-[750px]` container to maintain full column readability on handheld devices.

---

### Phase 4: GitHub Deployment & Interactive Cloud Showcase

1. **GitHub Repository**:
   - Initialized Git with clean `.gitignore` excluding `.venv/`, `anpr.db`, and temporary snapshots.
   - Pushed main branch to `https://github.com/rishukumark19/aegis-anpr`.

2. **GitHub Pages Live Interactive Showcase (`docs/index.html`)**:
   - Deployed standalone cloud demonstration build accessible without a local GPU/Python server.
   - Live URLs:
     - **Official Company Domain**: `https://crosstech.in/anpr/` (served seamlessly via CrossTech Website GitHub Pages)
     - **Developer Showcase**: `https://rishukumar.is-a.dev/aegis-anpr/`
     - **Default GitHub URL**: `https://rishukumark19.github.io/aegis-anpr/`

---

### Phase 5: Commercial Pitch Deck & Documentation

1. **Client Proposal (`CLIENT_PROPOSAL.md`)**:
   - Formal commercial pitch deck for presenting to society RWAs, commercial facility managers, and IT park security heads.
   - Outlines hardware prerequisites, CCTV camera specifications (1/250s shutter, HLC, <30° angle, min 130px plate width), commercial savings vs cloud APIs, and security workflows.

2. **Technical Guide (`README.md`)**:
   - Complete architectural diagram, prerequisites, installation commands, API documentation, and CCTV deployment checklist.

---

## 3. Directory Structure & Key Files

```text
d:\Coading Projects\crosstech work\camera\
├── .venv/                         # Python 3.11 virtual environment (paddleocr 2.8.1, paddlepaddle 2.6.2, fastapi, uvicorn, opencv-python, ultralytics)
├── models/
│   ├── indian_plate_yolo.pt       # Fine-tuned Indian license plate YOLOv8 weights (HF: subash1652007/indian-plate-ocr)
│   └── plate_detector.pt          # Generic plate detector weights
├── outputs/
│   ├── plates/                    # Tight license plate crop snapshots
│   └── contexts/                  # Wide bumper/vehicle context snapshots
├── test_samples/
│   ├── real_indian_vehicles/      # Real Indian trucks/cars sample images
│   ├── indian_traffic_test.mp4    # Real Indian traffic MP4 demo clip
│   └── car_traffic_sample.mp4     # Intel IoT sample traffic video
├── anpr.db                        # SQLite database storing detections & vehicle registry
├── camera_worker.py               # Multithreaded background video capture, dual-cropping & YOLO/OCR inference
├── database.py                    # SQLAlchemy database models, Vahan dossier generator, SQLite init
├── indian_plate_validator.py      # Indian RTO regex validator, BH series parser, character confusion fixer
├── plate_ocr.py                   # PaddleOCR pipeline with CLAHE, bilateral filter & two-line clustering
├── poc_anpr.py                    # Standalone CLI testing script
├── server.py                      # FastAPI web server, WebSocket manager, MJPEG generator
├── static/
│   └── index.html                 # Premium dark-glass surveillance dashboard
├── CLIENT_PROPOSAL.md             # Sales & client proposal document
├── PROJECT_LOG.md                 # Complete history, architecture and status log (this file)
└── README.md                      # Developer and setup documentation
```

---

## 4. Key Lessons Learned & Gotchas

| Issue / Trap                      | Root Cause                                                                       | Solution Implemented                                                                            |
| :-------------------------------- | :------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------- |
| **PaddleOCR 3.x Windows crash**   | OneDNN / PIR runtime mismatch in PaddlePaddle 3.x                                | Locked to `paddleocr==2.8.1` and `paddlepaddle==2.6.2`.                                         |
| **Grayscale OCR dimension crash** | PaddleOCR expects 3 channels even for grayscale                                  | Convert grayscale back to BGR (`cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)`) before OCR inference.  |
| **Old SQLite Schema error**       | Added new columns to ORM without migration script                                | Re-initialized database with `init_db()` to ensure all columns exist.                           |
| **Public RTSP Stream failures**   | Most public internet RTSP feeds are blocked by ISP firewalls (Socket Error -138) | Provided local sample video loop and direct webcam mode (`0`) for testing without on-site CCTV. |
| **Plate Recognition Thresholds**  | YOLO misses small plates < 90px width                                            | Physical CCTV installation rule: zoom lens / camera distance must deliver plate width ≥ 130px.  |

---

## 5. How to Run & Resume

```powershell
# 1. Activate environment
cd "d:\Coading Projects\crosstech work\camera"
.\.venv\Scripts\Activate.ps1

# 2. Run the platform
python -m uvicorn server:app --host 0.0.0.0 --port 8000

# 3. Access in browser
# http://localhost:8000
```

---

### Phase 4: Toll Plaza & Live Traffic CCTV Ingestion

1. **Downloaded & Verified Intel OpenVINO Toll Plaza CCTV Footage**:
   - Source: `test_samples/cars_extended.mp4` (640x360 @ 60 FPS, 18,000 frames / 5 minutes continuous toll booth feed).
   - Ingested directly into `CameraWorker` with high YOLO plate detection confidence (~0.85).
2. **Added Live Public Traffic CCTV Stream**:
   - Working live stream link: `http://162.191.11.144:8000/-wvhttp-01-/GetOneShot?image_size=640x480&frame_count=1000000000`
   - Verified active HTTP Motion-JPEG highway traffic surveillance feed with OpenCV.
3. **UI & Server Integration**:
   - Set default server worker source to Toll Plaza (`test_samples/cars_extended.mp4`).
   - Added 1-click toolbar buttons: `Toll Plaza CCTV` and `Live Traffic CCTV`.
   - Added quick-fill templates in the RTSP / IP Camera modal for both feeds.

---

### Phase 5: Kaggle ANPR Ingestion & Multipart Zip Upload

1. **Direct Video & Zip Ingestion API (`server.py`)**:
   - Created `/api/upload_video` handling multipart form uploads.
   - Built automatic zip-archive unzipper to handle Kaggle dataset downloads seamlessly.
   - Dynamically reconfigures running `CameraWorker` to loop the uploaded video.
2. **Benchmark Video Dataset**:
   - Extracted and integrated 1280x720 ANPR benchmark video to `test_samples/kaggle_anpr_video.mp4`.

---

### Phase 6: Max-Confidence De-Duplication & Executive Industrial UI

1. **Active Track De-Duplication (`camera_worker.py`)**:
   - Implemented `SequenceMatcher` similarity clustering (`similarity >= 0.70`, `TRACK_TIMEOUT = 12.0s`).
   - Multiple passing frames of the same car are de-duplicated into a single DB record/card.
   - Clearer/higher-confidence frames automatically upgrade existing records in place via WebSocket action `update`.
2. **Panoramic Vehicle Context Crops**:
   - Expanded vehicle crops from tight plate bounding box to 2.2x horizontal and 4.5x vertical context (`~255x636 px`), captured at JPEG 95 quality.
3. **Warm Obsidian & Industrial Zinc UI (`static/index.html`)**:
   - GPU-accelerated Three.js atmospheric 3D particle constellation background with mouse and touch parallax.
   - Authentic Indian HSRP license plate component with ultramarine blue `IND` strip.
   - Clean typographic hierarchy using `Inter` and `JetBrains Mono`.
   - Executive tabular numerals (`.kpi-val` with OpenType `tabular-nums`) eliminating weird monospace number glyphs.
   - Zero dead space on mobile and tablet with adaptive dossier container height and horizontal-scrollable channel selector.

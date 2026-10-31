"""
Database models and connection management for Indian ANPR System.
Uses SQLite for zero-configuration, lightweight local deployment.
"""

from datetime import datetime
import hashlib
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, Index
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./anpr.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Detection(Base):
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    plate_number = Column(String(32), index=True, nullable=False)
    is_valid = Column(Boolean, default=False)
    state = Column(String(32), nullable=True, index=True)
    ocr_confidence = Column(Float, default=0.0)
    det_confidence = Column(Float, default=0.0)
    camera_id = Column(String(64), default="Main Gate Camera 01", index=True)
    plate_crop_path = Column(String(256), nullable=False)
    snapshot_path = Column(String(256), nullable=False)
    raw_ocr = Column(Text, nullable=True)

    # Security & Verification Intelligence
    needs_verification = Column(Boolean, default=False)
    security_flag = Column(String(64), default="CLEAR")  # "CLEAR", "GOV BLACKLISTED", "UNAUTHORIZED"

    # Vehicle & Registered Owner Dossier
    vehicle_model = Column(String(128), default="Unknown Vehicle")
    owner_name = Column(String(128), default="Unregistered / Visiting")
    registered_address = Column(String(256), default="RTO Record Not Found")
    watchlist_category = Column(String(64), default="Unverified")


class VehicleRegistry(Base):
    __tablename__ = "vehicle_registry"

    plate_number = Column(String(32), primary_key=True, index=True)
    owner_name = Column(String(128), nullable=False)
    vehicle_model = Column(String(128), nullable=False)
    registered_address = Column(String(256), nullable=False)
    fuel_type = Column(String(32), default="Diesel")
    security_flag = Column(String(64), default="CLEAR")  # "CLEAR", "GOV BLACKLISTED", "UNAUTHORIZED"
    notes = Column(Text, default="")


class CameraConfig(Base):
    __tablename__ = "cameras"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    rtsp_url = Column(String(256), nullable=False)
    location = Column(String(128), default="Main Entrance")
    is_active = Column(Boolean, default=True)


# Realistic Indian sample registry entries
SEED_REGISTRY = [
    {
        "plate_number": "DL01AB1234",
        "owner_name": "Rajesh Malhotra",
        "vehicle_model": "Hyundai Creta SX (White)",
        "registered_address": "Sector 14, Vasant Kunj, New Delhi - 110070",
        "fuel_type": "Petrol",
        "security_flag": "CLEAR"
    },
    {
        "plate_number": "UP16BT5797",
        "owner_name": "Amitesh Singhal",
        "vehicle_model": "Mahindra Thar 4x4 (Black)",
        "registered_address": "Tower 4, Sector 62, Noida, Gautam Buddha Nagar, UP",
        "fuel_type": "Diesel",
        "security_flag": "CLEAR"
    },
    {
        "plate_number": "DL2CQ3150",
        "owner_name": "Suresh Verma",
        "vehicle_model": "Maruti Suzuki Swift Dzire (Silver)",
        "registered_address": "Pocket D, Sector 18, Rohini, New Delhi - 110085",
        "fuel_type": "CNG",
        "security_flag": "CLEAR"
    },
    {
        "plate_number": "PB05AL7242",
        "owner_name": "Harpreet Singh & Sons",
        "vehicle_model": "Tata Signa 4825.T Heavy Hauler",
        "registered_address": "GT Road Transport Hub, Firozpur, Punjab - 152001",
        "fuel_type": "Diesel",
        "security_flag": "CLEAR"
    },
    {
        "plate_number": "NL01AK3181",
        "owner_name": "Eastern Cargo Logistics Ltd.",
        "vehicle_model": "Ashok Leyland 3118 (10 Wheeler)",
        "registered_address": "National Highway Junction, Kohima, Nagaland",
        "fuel_type": "Diesel",
        "security_flag": "CLEAR"
    },
    {
        "plate_number": "DL12A9759",
        "owner_name": "Vikram Seth (Reported Stolen)",
        "vehicle_model": "Honda Civic (Metallic Grey)",
        "registered_address": "Shahdara Industrial Area, East Delhi - 110032",
        "fuel_type": "Petrol",
        "security_flag": "GOV BLACKLISTED / POLICE FLAG"
    }
]

INDIAN_MODELS = [
    "Mahindra Scorpio-N Z8 (White)",
    "Tata Harrier Fearless+ (Dark Edition)",
    "Hyundai Creta 1.5 Diesel (Knight Edition)",
    "Toyota Fortuner 4x4 (Super White)",
    "Kia Seltos GTX+ (Gravity Grey)",
    "Maruti Suzuki Brezza ZXi (Brave Khaki)",
    "Tata Nexon EV Max (Intense Teal)",
    "Volkswagen Taigun GT (Wild Cherry Red)"
]

INDIAN_NAMES = [
    "Sunil Narang", "Pooja Deshmukh", "Arjun Reddy", "Manish Khandelwal",
    "Deepak Chopra", "Kavita Ranganathan", "Mohit Bansal", "Ramanjit Kaur"
]

def lookup_or_generate_vahan_details(plate_number: str, state: str, is_valid: bool) -> dict:
    """Retrieves registered RTO owner dossier or synthesizes an RTO record for validation demo."""
    db = SessionLocal()
    try:
        reg = db.query(VehicleRegistry).filter(VehicleRegistry.plate_number == plate_number).first()
        if reg:
            return {
                "owner_name": reg.owner_name,
                "vehicle_model": reg.vehicle_model,
                "registered_address": reg.registered_address,
                "security_flag": reg.security_flag
            }
    finally:
        db.close()

    if not is_valid:
        return {
            "owner_name": "Unverified Vehicle",
            "vehicle_model": "Vehicle Model Unregistered",
            "registered_address": "No RTO Record Found",
            "security_flag": "UNAUTHORIZED / NEEDS REVIEW"
        }

    # Deterministic RTO mock generation for any detected Indian plate
    seed_int = int(hashlib.md5(plate_number.encode()).hexdigest()[:6], 16)
    name = INDIAN_NAMES[seed_int % len(INDIAN_NAMES)]
    model = INDIAN_MODELS[seed_int % len(INDIAN_MODELS)]
    state_desc = state if state else "State RTO"
    address = f"Civil Lines, Sector {(seed_int % 80) + 1}, {state_desc} RTO District"

    # Flag 5% as flagged/unauthorized for security demonstration
    security_flag = "GOV BLACKLISTED / UNPAID CHALLANS" if (seed_int % 15 == 0) else "CLEAR"

    return {
        "owner_name": name,
        "vehicle_model": model,
        "registered_address": address,
        "security_flag": security_flag
    }


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Seed records if empty
        if db.query(VehicleRegistry).count() == 0:
            for item in SEED_REGISTRY:
                db.add(VehicleRegistry(**item))
            db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

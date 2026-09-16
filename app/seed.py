"""Two fake records so the dashboard is never empty on a fresh deploy."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Patient
from app.schemas import PatientCreate
from app.services.patient_service import create_patient

logger = logging.getLogger("seed")

SEED_PATIENTS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "04/12/1985",
        "sex": "Female", "phone_number": "5125550143", "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace", "address_line_2": "Apt 3B",
        "city": "Austin", "state": "TX", "zip_code": "78701",
        "insurance_provider": "Blue Cross Blue Shield", "insurance_member_id": "BCBS884213",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "5125550199",
    },
    {
        "first_name": "Marcus", "last_name": "Rivera", "date_of_birth": "11/30/1972",
        "sex": "Male", "phone_number": "9165550178", "email": "m.rivera@example.com",
        "address_line_1": "1180 Willow Creek Road", "city": "Sacramento", "state": "CA",
        "zip_code": "95814", "insurance_provider": "Aetna", "insurance_member_id": "AET5512908",
        "preferred_language": "Spanish",
        "emergency_contact_name": "Elena Rivera", "emergency_contact_phone": "9165550122",
    },
]


def seed_if_empty(db: Session) -> int:
    count = db.execute(select(func.count()).select_from(Patient)).scalar_one()
    if count:
        return 0
    for record in SEED_PATIENTS:
        create_patient(db, PatientCreate(**record))
    logger.info("Seeded %d demo patients", len(SEED_PATIENTS))
    return len(SEED_PATIENTS)

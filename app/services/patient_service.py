"""Business logic shared by the REST router and the Vapi tool handler."""

import logging
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_event
from app.models import CallLog, Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate
from app.validators import normalize_phone, parse_dob

logger = logging.getLogger("patients")


class NotFoundError(Exception):
    pass


class InvalidIdError(Exception):
    pass


def _validate_uuid(patient_id: str) -> str:
    try:
        uuid.UUID(str(patient_id))
    except (ValueError, AttributeError, TypeError):
        raise InvalidIdError(f"'{patient_id}' is not a valid patient id")
    return str(patient_id)


def list_patients(
    db: Session,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
) -> list[Patient]:
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        parsed: date = parse_dob(date_of_birth)
        stmt = stmt.where(Patient.date_of_birth == parsed)
    if phone_number:
        stmt = stmt.where(Patient.phone_number == normalize_phone(phone_number))
    stmt = stmt.order_by(Patient.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_patient(db: Session, patient_id: str) -> Patient:
    _validate_uuid(patient_id)
    patient = db.get(Patient, str(patient_id))
    if patient is None or patient.deleted_at is not None:
        raise NotFoundError(f"No patient with id {patient_id}")
    return patient


def find_by_phone(db: Session, phone: str) -> Patient | None:
    """Used by the duplicate check during a call. Returns the newest match."""
    digits = normalize_phone(phone)
    stmt = (
        select(Patient)
        .where(Patient.phone_number == digits, Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


def create_patient(db: Session, data: PatientCreate) -> Patient:
    patient = Patient(**data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    log_event(
        logger,
        "patient_created",
        patient_id=patient.patient_id,
        payload=data.model_dump(mode="json"),
    )
    return patient


def update_patient(db: Session, patient_id: str, data: PatientUpdate) -> Patient:
    patient = get_patient(db, patient_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(patient, field, value)
    patient.updated_at = utcnow()
    db.commit()
    db.refresh(patient)
    log_event(
        logger,
        "patient_updated",
        patient_id=patient.patient_id,
        payload=PatientUpdate(**changes).model_dump(mode="json", exclude_unset=True),
    )
    return patient


def soft_delete(db: Session, patient_id: str) -> Patient:
    patient = get_patient(db, patient_id)
    patient.deleted_at = utcnow()
    db.commit()
    db.refresh(patient)
    log_event(logger, "patient_deleted", patient_id=patient.patient_id)
    return patient


def save_call_log(
    db: Session,
    vapi_call_id: str,
    transcript: str | None,
    summary: str | None,
    patient_id: str | None = None,
    caller_phone: str | None = None,
) -> CallLog:
    """Link a transcript to a patient by id, else best-effort by caller phone."""
    linked = patient_id
    if not linked and caller_phone:
        try:
            match = find_by_phone(db, caller_phone)
            linked = match.patient_id if match else None
        except ValueError:
            linked = None
    log = CallLog(
        vapi_call_id=vapi_call_id, patient_id=linked, transcript=transcript, summary=summary
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    log_event(logger, "call_log_saved", vapi_call_id=vapi_call_id, patient_id=linked)
    return log


def call_logs_for_patient(db: Session, patient_id: str) -> list[CallLog]:
    stmt = (
        select(CallLog)
        .where(CallLog.patient_id == str(patient_id))
        .order_by(CallLog.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())

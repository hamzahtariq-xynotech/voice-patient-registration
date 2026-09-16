"""REST CRUD for patient records."""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.responses import fail, ok
from app.schemas import CallLogOut, PatientCreate, PatientOut, PatientUpdate
from app.services import patient_service as svc

router = APIRouter(tags=["patients"])
logger = logging.getLogger("api")


def _validation_response(exc: ValidationError):
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"] if p != "body") or "body",
            "message": err["msg"].removeprefix("Value error, "),
        }
        for err in exc.errors()
    ]
    return fail("VALIDATION_ERROR", "One or more fields are invalid", 422, details)


@router.get("/patients")
def list_patients(
    last_name: str | None = Query(default=None),
    date_of_birth: str | None = Query(default=None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        patients = svc.list_patients(db, last_name, date_of_birth, phone_number)
    except ValueError as exc:
        return fail("INVALID_FILTER", str(exc), 400)
    return ok([PatientOut.model_validate(p) for p in patients])


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = svc.get_patient(db, patient_id)
    except svc.InvalidIdError as exc:
        return fail("INVALID_ID", str(exc), 400)
    except svc.NotFoundError as exc:
        return fail("NOT_FOUND", str(exc), 404)
    return ok(PatientOut.model_validate(patient))


@router.get("/patients/{patient_id}/calls")
def get_patient_calls(patient_id: str, db: Session = Depends(get_db)):
    """Transcripts linked to this patient (bonus: call recording/transcript storage)."""
    try:
        svc.get_patient(db, patient_id)
    except svc.InvalidIdError as exc:
        return fail("INVALID_ID", str(exc), 400)
    except svc.NotFoundError as exc:
        return fail("NOT_FOUND", str(exc), 404)
    logs = svc.call_logs_for_patient(db, patient_id)
    return ok([CallLogOut.model_validate(log) for log in logs])


@router.post("/patients")
def create_patient(payload: dict, db: Session = Depends(get_db)):
    # Taking a dict and validating by hand keeps every error in the envelope shape.
    try:
        data = PatientCreate(**payload)
    except ValidationError as exc:
        return _validation_response(exc)
    except TypeError:
        return fail("VALIDATION_ERROR", "Request body must be a JSON object", 422)
    patient = svc.create_patient(db, data)
    return ok(PatientOut.model_validate(patient), status_code=201)


@router.put("/patients/{patient_id}")
def update_patient(patient_id: str, payload: dict, db: Session = Depends(get_db)):
    known = set(PatientUpdate.model_fields)
    filtered = {k: v for k, v in payload.items() if k in known}  # ignore unknown fields
    try:
        data = PatientUpdate(**filtered)
    except ValidationError as exc:
        return _validation_response(exc)
    try:
        patient = svc.update_patient(db, patient_id, data)
    except svc.InvalidIdError as exc:
        return fail("INVALID_ID", str(exc), 400)
    except svc.NotFoundError as exc:
        return fail("NOT_FOUND", str(exc), 404)
    return ok(PatientOut.model_validate(patient))


@router.delete("/patients/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = svc.soft_delete(db, patient_id)
    except svc.InvalidIdError as exc:
        return fail("INVALID_ID", str(exc), 400)
    except svc.NotFoundError as exc:
        return fail("NOT_FOUND", str(exc), 404)
    return ok({"patient_id": patient.patient_id, "deleted": True})

"""Vapi webhooks: custom tool calls during a call, and the end-of-call report.

Contract (verified against docs.vapi.ai):
  request  -> {"message": {"type": "tool-calls",
                           "toolCallList": [{"id", "name", "arguments"}], "call": {...}}}
  response -> {"results": [{"toolCallId": "<id>", "result": "<string>"}]}

Two rules govern this module:
  1. Never raise. An exception here means the caller hears dead air.
  2. Every result string starts with a machine-readable token (SUCCESS / NOT_FOUND /
     VALIDATION_ERROR / ERROR) that the system prompt tells the model how to react to.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.logging_config import log_event
from app.schemas import PatientCreate, PatientUpdate
from app.services import patient_service as svc
from app.validators import format_dob, normalize_phone

router = APIRouter(prefix="/vapi", tags=["vapi"])
logger = logging.getLogger("vapi")


# --------------------------------------------------------------------------- #
# payload helpers
# --------------------------------------------------------------------------- #

def _authorized(request: Request) -> bool:
    """If a secret is configured, require the x-vapi-secret header to match."""
    if not settings.vapi_webhook_secret:
        return True
    return request.headers.get("x-vapi-secret", "") == settings.vapi_webhook_secret


def _extract_tool_calls(message: dict) -> list[dict]:
    """Normalize the several shapes Vapi has used for the tool call list."""
    raw = message.get("toolCallList") or message.get("toolCalls") or []
    calls: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or fn.get("name") or ""
        args = item.get("arguments")
        if args is None:
            args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append(
            {
                "id": item.get("id") or item.get("toolCallId") or "",
                "name": name,
                "arguments": args if isinstance(args, dict) else {},
            }
        )
    return calls


def _errors_in_plain_english(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "input"
        message = err["msg"].removeprefix("Value error, ")
        parts.append(f"{field}: {message}")
    return "VALIDATION_ERROR: " + "; ".join(parts)


def _caller_number(message: dict) -> str | None:
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    customer = message.get("customer") or call.get("customer") or {}
    if isinstance(customer, dict):
        return customer.get("number")
    return None


# --------------------------------------------------------------------------- #
# tool implementations
# --------------------------------------------------------------------------- #

def _tool_check_existing_patient(db: Session, args: dict) -> str:
    phone = args.get("phone_number")
    if not phone:
        return "ERROR: phone_number is required. Ask the caller for their phone number."
    try:
        normalize_phone(phone)
    except ValueError as exc:
        return f"ERROR: {exc}. Ask the caller to repeat their phone number."
    patient = svc.find_by_phone(db, phone)
    if patient is None:
        return "NOT_FOUND"
    return (
        f"FOUND: patient_id={patient.patient_id}, first_name={patient.first_name}, "
        f"last_name={patient.last_name}, date_of_birth={format_dob(patient.date_of_birth)}"
    )


def _tool_create_patient(db: Session, args: dict) -> str:
    try:
        data = PatientCreate(**args)
    except ValidationError as exc:
        return _errors_in_plain_english(exc)
    except TypeError as exc:
        return f"VALIDATION_ERROR: {exc}"
    patient = svc.create_patient(db, data)
    return f"SUCCESS: patient_id={patient.patient_id}"


def _tool_update_patient(db: Session, args: dict) -> str:
    payload = dict(args)
    patient_id = payload.pop("patient_id", None)
    if not patient_id:
        return "ERROR: patient_id is required to update a record."
    known = set(PatientUpdate.model_fields)
    filtered = {k: v for k, v in payload.items() if k in known and v is not None}
    if not filtered:
        return "ERROR: no fields supplied to update."
    try:
        data = PatientUpdate(**filtered)
    except ValidationError as exc:
        return _errors_in_plain_english(exc)
    try:
        svc.update_patient(db, str(patient_id), data)
    except (svc.NotFoundError, svc.InvalidIdError):
        return "NOT_FOUND"
    return "SUCCESS"


TOOLS = {
    "check_existing_patient": _tool_check_existing_patient,
    "create_patient": _tool_create_patient,
    "update_patient": _tool_update_patient,
}


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #

@router.post("/tools")
async def tool_calls(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    if not _authorized(request):
        logger.warning("Rejected Vapi tool call with bad x-vapi-secret")
        return JSONResponse(status_code=401, content={"results": []})

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"results": []})

    message = body.get("message") or {}
    calls = _extract_tool_calls(message)
    log_event(
        logger,
        "vapi_tool_request",
        message_type=message.get("type"),
        tool_names=[c["name"] for c in calls],
        payload=calls,
    )

    results = []
    for call in calls:
        name = call["name"]
        handler = TOOLS.get(name)
        if handler is None:
            result = f"ERROR: unknown tool {name}."
        else:
            try:
                result = handler(db, call["arguments"])
            except Exception:
                # Never propagate: the caller would hear silence.
                db.rollback()
                logger.exception("Tool %s failed", name)
                result = (
                    "ERROR: could not save the information right now. "
                    "Apologize and tell the caller staff will follow up."
                )
        log_event(logger, "vapi_tool_result", tool=name, result=result)
        results.append({"toolCallId": call["id"], "result": result})

    return JSONResponse(status_code=200, content={"results": results})


@router.post("/events")
async def events(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """End-of-call report -> store the transcript. Always answers 200."""
    if not _authorized(request):
        return JSONResponse(status_code=401, content={"received": False})

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"received": True})

    message = body.get("message") or {}
    msg_type = message.get("type")
    if msg_type != "end-of-call-report":
        log_event(logger, "vapi_event_ignored", message_type=msg_type)
        return JSONResponse(status_code=200, content={"received": True})

    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    artifact = message.get("artifact") if isinstance(message.get("artifact"), dict) else {}
    analysis = message.get("analysis") if isinstance(message.get("analysis"), dict) else {}

    call_id = call.get("id") or message.get("callId") or "unknown"
    transcript = artifact.get("transcript") or message.get("transcript")
    summary = analysis.get("summary") or message.get("summary")

    try:
        svc.save_call_log(
            db,
            vapi_call_id=str(call_id),
            transcript=transcript,
            summary=summary,
            caller_phone=_caller_number(message),
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to store call log for %s", call_id)

    return JSONResponse(status_code=200, content={"received": True})

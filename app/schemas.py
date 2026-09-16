"""Pydantic v2 request/response schemas.

Validators delegate to app.validators so the REST API and the Vapi tool handler
reject exactly the same inputs with exactly the same wording.
"""

from datetime import date, datetime
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

from app import validators


class PatientBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class PatientCreate(PatientBase):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str = Field(min_length=1, max_length=255)
    address_line_2: Optional[str] = Field(default=None, max_length=255)
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = Field(default=None, max_length=100)
    insurance_member_id: Optional[str] = Field(default=None, max_length=50)
    preferred_language: str = "English"
    emergency_contact_name: Optional[str] = Field(default=None, max_length=100)
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name")
    @classmethod
    def _first_name(cls, v: str) -> str:
        return validators.validate_name(v, "first_name")

    @field_validator("last_name")
    @classmethod
    def _last_name(cls, v: str) -> str:
        return validators.validate_name(v, "last_name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _dob(cls, v: Any) -> date:
        return validators.parse_dob(v)

    @field_validator("sex", mode="before")
    @classmethod
    def _sex(cls, v: Any) -> str:
        return validators.normalize_sex(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _phone(cls, v: Any) -> str:
        return validators.normalize_phone(v, "phone_number")

    @field_validator("emergency_contact_phone", mode="before")
    @classmethod
    def _emergency_phone(cls, v: Any) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        return validators.normalize_phone(v, "emergency_contact_phone")

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, v: Any) -> str:
        return validators.validate_city(v)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v: Any) -> str:
        return validators.normalize_state(v)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _zip(cls, v: Any) -> str:
        return validators.validate_zip(v)

    @field_validator("preferred_language", mode="before")
    @classmethod
    def _language(cls, v: Any) -> str:
        return validators.normalize_language(v)

    @field_validator("email", mode="before")
    @classmethod
    def _email_blank_to_none(cls, v: Any) -> Any:
        # Callers who decline to give an email send "" or "none"; treat as absent.
        if v is None:
            return None
        raw = str(v).strip()
        return None if raw.lower() in ("", "none", "no", "n/a", "na") else raw

    @field_validator("address_line_2", "insurance_provider", "insurance_member_id",
                     "emergency_contact_name", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if v is None:
            return None
        raw = str(v).strip()
        return raw or None


class PatientUpdate(PatientBase):
    """Every field optional -- PUT is a partial update."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = Field(default=None, max_length=255)
    address_line_2: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = Field(default=None, max_length=100)
    insurance_member_id: Optional[str] = Field(default=None, max_length=50)
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = Field(default=None, max_length=100)
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name")
    @classmethod
    def _first_name(cls, v): return validators.validate_name(v, "first_name")

    @field_validator("last_name")
    @classmethod
    def _last_name(cls, v): return validators.validate_name(v, "last_name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _dob(cls, v): return None if v is None else validators.parse_dob(v)

    @field_validator("sex", mode="before")
    @classmethod
    def _sex(cls, v): return None if v is None else validators.normalize_sex(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _phone(cls, v):
        return None if v is None else validators.normalize_phone(v, "phone_number")

    @field_validator("emergency_contact_phone", mode="before")
    @classmethod
    def _emergency_phone(cls, v):
        if v is None or str(v).strip() == "":
            return None
        return validators.normalize_phone(v, "emergency_contact_phone")

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, v): return None if v is None else validators.validate_city(v)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v): return None if v is None else validators.normalize_state(v)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _zip(cls, v): return None if v is None else validators.validate_zip(v)

    @field_validator("preferred_language", mode="before")
    @classmethod
    def _language(cls, v): return None if v is None else validators.normalize_language(v)


class PatientOut(PatientBase):
    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("date_of_birth")
    def _fmt_dob(self, value: date) -> str:
        return validators.format_dob(value)


class CallLogOut(PatientBase):
    id: int
    vapi_call_id: str
    patient_id: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)


class Envelope(BaseModel):
    data: Any = None
    error: Optional[ErrorDetail] = None

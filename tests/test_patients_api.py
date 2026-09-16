"""API tests against an isolated in-memory SQLite database."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Patient

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # one shared in-memory DB across sessions
    future=True,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

VALID = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "date_of_birth": "12/10/1990",
    "sex": "female",
    "phone_number": "(512) 555-0147",
    "email": "ada@example.com",
    "address_line_1": "10 Analytical Way",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    # Bypass lifespan so startup does not touch the real database or seed.
    return TestClient(app)


def _create(client, **overrides):
    payload = {**VALID, **overrides}
    return client.post("/patients", json=payload)


def test_health_uses_envelope(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"data": {"status": "ok"}, "error": None}


def test_create_valid_patient(client):
    res = _create(client)
    assert res.status_code == 201
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["patient_id"]
    assert data["phone_number"] == "5125550147"   # normalized
    assert data["sex"] == "Female"                # canonicalized
    assert data["date_of_birth"] == "12/10/1990"  # formatted on output
    assert data["preferred_language"] == "English"


def test_create_with_short_phone_is_422(client):
    res = _create(client, phone_number="555")
    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any("phone_number" in d["field"] for d in error["details"])


def test_create_with_future_dob_is_422(client):
    res = _create(client, date_of_birth="01/01/2999")
    assert res.status_code == 422
    details = res.json()["error"]["details"]
    assert any("future" in d["message"] for d in details)


def test_create_with_bad_state_is_422(client):
    res = _create(client, state="ZZ")
    assert res.status_code == 422
    details = res.json()["error"]["details"]
    assert any("state" in d["field"] for d in details)


def test_get_by_id_and_missing_id(client):
    created = _create(client).json()["data"]
    res = client.get(f"/patients/{created['patient_id']}")
    assert res.status_code == 200
    assert res.json()["data"]["last_name"] == "Lovelace"

    missing = client.get("/patients/2f1c1b2a-0000-4000-8000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    malformed = client.get("/patients/not-a-uuid")
    assert malformed.status_code == 400


def test_filter_by_phone_number(client):
    _create(client)
    _create(client, first_name="Grace", last_name="Hopper", phone_number="9165550178")

    res = client.get("/patients", params={"phone_number": "916-555-0178"})
    assert res.status_code == 200
    rows = res.json()["data"]
    assert len(rows) == 1
    assert rows[0]["last_name"] == "Hopper"


def test_filter_by_last_name_is_case_insensitive(client):
    _create(client)
    res = client.get("/patients", params={"last_name": "lovelace"})
    assert len(res.json()["data"]) == 1


def test_put_partial_update_touches_only_given_field(client):
    created = _create(client).json()["data"]
    res = client.put(
        f"/patients/{created['patient_id']}",
        json={"city": "Dallas", "unknown_field": "ignored"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["city"] == "Dallas"
    assert data["first_name"] == "Ada"
    assert data["zip_code"] == "78701"


def test_put_rejects_invalid_value(client):
    created = _create(client).json()["data"]
    res = client.put(f"/patients/{created['patient_id']}", json={"zip_code": "abc"})
    assert res.status_code == 422


def test_delete_is_soft(client):
    created = _create(client).json()["data"]
    pid = created["patient_id"]

    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.delete(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []

    # The row survives with a deleted_at stamp.
    with TestingSession() as db:
        row = db.execute(select(Patient).where(Patient.patient_id == pid)).scalar_one()
        assert row.deleted_at is not None

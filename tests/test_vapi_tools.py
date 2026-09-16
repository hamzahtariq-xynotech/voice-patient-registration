"""Tests for the Vapi tool webhook.

The contract that matters: it always returns 200 with a result string, and the
string always starts with a token the system prompt knows how to react to.
"""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from tests.test_patients_api import TestingSession, engine, _override_get_db  # noqa: F401

app.dependency_overrides[get_db] = _override_get_db

NEW_PATIENT = {
    "first_name": "Grace",
    "last_name": "Hopper",
    "date_of_birth": "12/09/1906",
    "sex": "Female",
    "phone_number": "5125550188",
    "address_line_1": "1 Navy Yard",
    "city": "Arlington",
    "state": "Virginia",
    "zip_code": "22202",
}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def tool_call(client, name, arguments, call_id="call_1"):
    res = client.post(
        "/vapi/tools",
        json={
            "message": {
                "type": "tool-calls",
                "toolCallList": [{"id": call_id, "name": name, "arguments": arguments}],
                "call": {"id": "vapi-call-abc"},
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["results"][0]["toolCallId"] == call_id
    return body["results"][0]["result"]


def test_check_existing_patient_not_found(client):
    assert tool_call(client, "check_existing_patient", {"phone_number": "5125550188"}) == "NOT_FOUND"


def test_create_then_duplicate_check_finds_patient(client):
    result = tool_call(client, "create_patient", NEW_PATIENT)
    assert result.startswith("SUCCESS: patient_id=")
    patient_id = result.split("patient_id=")[1]

    found = tool_call(client, "check_existing_patient", {"phone_number": "(512) 555-0188"})
    assert found.startswith("FOUND:")
    assert f"patient_id={patient_id}" in found
    assert "first_name=Grace" in found


def test_create_with_bad_phone_returns_validation_error(client):
    result = tool_call(client, "create_patient", {**NEW_PATIENT, "phone_number": "123"})
    assert result.startswith("VALIDATION_ERROR:")
    assert "phone_number" in result


def test_create_with_future_dob_returns_validation_error(client):
    result = tool_call(client, "create_patient", {**NEW_PATIENT, "date_of_birth": "01/01/2099"})
    assert result.startswith("VALIDATION_ERROR:")
    assert "future" in result


def test_arguments_may_arrive_as_a_json_string(client):
    import json

    res = client.post(
        "/vapi/tools",
        json={
            "message": {
                "type": "tool-calls",
                "toolCallList": [
                    {
                        "id": "c2",
                        "function": {
                            "name": "check_existing_patient",
                            "arguments": json.dumps({"phone_number": "5125550188"}),
                        },
                    }
                ],
            }
        },
    )
    assert res.status_code == 200
    assert res.json()["results"][0]["result"] == "NOT_FOUND"


def test_update_patient(client):
    created = tool_call(client, "create_patient", NEW_PATIENT)
    patient_id = created.split("patient_id=")[1]

    assert tool_call(client, "update_patient", {"patient_id": patient_id, "city": "Reston"}) == "SUCCESS"
    assert client.get(f"/patients/{patient_id}").json()["data"]["city"] == "Reston"

    missing = tool_call(
        client, "update_patient",
        {"patient_id": "2f1c1b2a-0000-4000-8000-000000000000", "city": "Reston"},
    )
    assert missing == "NOT_FOUND"


def test_unknown_tool_does_not_crash(client):
    assert tool_call(client, "book_appointment", {}).startswith("ERROR:")


def test_malformed_body_still_returns_200(client):
    res = client.post("/vapi/tools", content=b"not json")
    assert res.status_code == 200
    assert res.json() == {"results": []}


def test_end_of_call_report_stores_transcript_linked_by_phone(client):
    created = tool_call(client, "create_patient", NEW_PATIENT)
    patient_id = created.split("patient_id=")[1]

    res = client.post(
        "/vapi/events",
        json={
            "message": {
                "type": "end-of-call-report",
                "call": {"id": "vapi-call-abc"},
                "customer": {"number": "+15125550188"},
                "artifact": {"transcript": "AI: Hello. User: Hi."},
                "analysis": {"summary": "Patient registered."},
            }
        },
    )
    assert res.status_code == 200

    logs = client.get(f"/patients/{patient_id}/calls").json()["data"]
    assert len(logs) == 1
    assert logs[0]["transcript"] == "AI: Hello. User: Hi."
    assert logs[0]["summary"] == "Patient registered."


def test_other_events_are_ignored(client):
    res = client.post("/vapi/events", json={"message": {"type": "status-update"}})
    assert res.status_code == 200

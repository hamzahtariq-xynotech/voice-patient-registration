"""Envelope helpers -- every response in the API is {"data": ..., "error": ...}."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"data": jsonable_encoder(data), "error": None}
    )


def fail(code: str, message: str, status_code: int, details: list | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "data": None,
            "error": {"code": code, "message": message, "details": details or []},
        },
    )

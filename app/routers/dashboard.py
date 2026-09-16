"""Serves the single-file dashboard."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])
_DASHBOARD = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD.read_text(encoding="utf-8"))

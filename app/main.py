"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.logging_config import configure_logging
from app.responses import fail
from app.routers import dashboard, patients, vapi
from app.seed import seed_if_empty

configure_logging(settings.log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seeded = seed_if_empty(db)
        logger.info("Startup complete (seeded=%d, db=%s)", seeded, settings.database_url)
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Patient registration API backing a Vapi voice agent. "
        "All responses use the envelope {\"data\": ..., \"error\": ...}."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router)
app.include_router(vapi.router)
app.include_router(dashboard.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"] if p != "body") or "body",
            "message": err["msg"].removeprefix("Value error, "),
        }
        for err in exc.errors()
    ]
    return fail("VALIDATION_ERROR", "One or more fields are invalid", 422, details)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return fail("HTTP_ERROR", str(exc.detail), exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return fail("INTERNAL_ERROR", "An unexpected error occurred", 500)


@app.get("/health", tags=["meta"])
def health():
    from app.responses import ok

    return ok({"status": "ok"})


@app.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard")

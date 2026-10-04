import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import transaction

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/health")
def health() -> JSONResponse:
    try:
        with transaction() as conn:
            conn.execute("SELECT 1")
    except Exception:
        log.exception("health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})

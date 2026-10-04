"""Domain error and unexpected error to HTTP mapping. Every error body is an ErrorBody."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas import ErrorBody
from app.core import errors as domain

log = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[domain.DomainError], int] = {
    domain.Unauthorized: 401,
    domain.IdempotencyKeyMissing: 400,
    domain.IdempotencyKeyReused: 422,
    domain.PaymentRequired: 402,
    domain.UpgradeRequired: 402,
    domain.QuotaExceeded: 429,
    domain.BillingProviderError: 502,
    domain.InvalidSignature: 400,
    domain.InvalidPayload: 400,
}


def _json(status: int, body: ErrorBody, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


async def domain_error_handler(request: Request, exc: domain.DomainError) -> JSONResponse:
    status = STATUS_BY_ERROR.get(type(exc), 500)
    headers = None
    upgrade_url = None
    if isinstance(exc, domain.QuotaExceeded):
        headers = {"Retry-After": str(exc.retry_after)}
    if isinstance(exc, domain.UpgradeRequired):
        upgrade_url = exc.upgrade_url
    return _json(
        status,
        ErrorBody(error=exc.code, message=exc.message, details=exc.details, upgrade_url=upgrade_url),
        headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only loc and msg: pydantic's ctx can hold non-serialisable exception objects.
    fields = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return _json(
        422,
        ErrorBody(
            error="validation_error",
            message="The request failed validation. See details.errors for each failing field.",
            details={"errors": fields},
        ),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the exception type and traceback only. Never log headers, bodies, or keys.
    log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return _json(500, ErrorBody(error="internal_error", message="An unexpected error occurred."))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(domain.DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

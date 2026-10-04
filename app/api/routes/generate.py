from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import CurrentTenant, IdempotencyKey
from app.api.schemas import ErrorBody, GenerateRequest, GenerateResponse
from app.services.meter import MeterService

router = APIRouter()
_meter = MeterService()


@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=201,
    responses={
        200: {"model": GenerateResponse, "description": "Replay of a stored response."},
        400: {"model": ErrorBody}, 401: {"model": ErrorBody}, 402: {"model": ErrorBody},
        422: {"model": ErrorBody}, 429: {"model": ErrorBody},
    },
)
def generate(
    body: GenerateRequest, tenant: CurrentTenant, key: IdempotencyKey
) -> JSONResponse:
    result = _meter.record(tenant.id, body, key)
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)

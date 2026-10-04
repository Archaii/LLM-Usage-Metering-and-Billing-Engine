from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.schemas import ErrorBody
from app.integrations.paymongo import SIGNATURE_HEADER
from app.services.webhook import WebhookService

router = APIRouter()
_service = WebhookService()


@router.post(
    "/webhooks/paymongo",
    responses={400: {"model": ErrorBody}},
    include_in_schema=True,
)
async def paymongo_webhook(request: Request) -> JSONResponse:
    # Raw bytes, untouched: the signature covers the exact body PayMongo sent.
    raw_body = await request.body()
    result = await run_in_threadpool(
        _service.receive, raw_body, request.headers.get(SIGNATURE_HEADER)
    )
    content = {"received": True}
    if result.duplicate:
        content["duplicate"] = True
    return JSONResponse(content=content)

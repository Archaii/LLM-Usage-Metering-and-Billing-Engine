from fastapi import APIRouter

from app.api.deps import CurrentTenant
from app.api.schemas import ErrorBody, UsageSummary
from app.services.meter import MeterService

router = APIRouter()
_meter = MeterService()


@router.get("/usage", response_model=UsageSummary, responses={401: {"model": ErrorBody}})
def get_usage(tenant: CurrentTenant) -> UsageSummary:
    return _meter.usage_summary(tenant.id)

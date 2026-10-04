from fastapi import APIRouter

from app.api.schemas import PlanInfo, PlansResponse, RatesInfo
from app.core.config import get_pricing

router = APIRouter()


@router.get("/plans", response_model=PlansResponse)
def list_plans() -> PlansResponse:
    pricing = get_pricing()
    return PlansResponse(
        currency=pricing.currency,
        micros_per_usd=pricing.micros_per_usd,
        plans=[
            PlanInfo(
                code=p.code, name=p.name, api_call_limit=p.api_call_limit,
                token_limit=p.token_limit, base_fee_micros=p.base_fee_micros,
            )
            for p in pricing.plans.values()
        ],
        rates=RatesInfo(**vars(pricing.rates)),
    )

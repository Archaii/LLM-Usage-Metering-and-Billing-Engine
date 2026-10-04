"""FastAPI app: wiring only."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routes import generate, health, plans, usage
from app.core.config import get_pricing, get_settings
from app.core.db import close_pool, init_pool
from app.services.plan_sync import verify_plans


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()  # refuses to start on a non-test Stripe key
    logging.basicConfig(level=settings.log_level)
    init_pool()
    try:
        verify_plans(get_pricing())
        yield
    finally:
        close_pool()


app = FastAPI(title="Usage Metering & Billing Engine", lifespan=lifespan)
register_error_handlers(app)
app.include_router(health.router)
app.include_router(plans.router)
app.include_router(generate.router)
app.include_router(usage.router)

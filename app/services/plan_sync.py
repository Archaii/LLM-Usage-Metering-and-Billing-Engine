"""Keeps the plans table in line with config/pricing.toml."""
from app.core.config import ConfigError, Pricing
from app.core.db import transaction
from app.core.models import Plan
from app.repositories import plans


def _expected(pricing: Pricing) -> dict[str, Plan]:
    return {
        code: Plan(
            code=code,
            name=cfg.name,
            api_call_limit=cfg.api_call_limit,
            token_limit=cfg.token_limit,
            base_fee_micros=cfg.base_fee_micros,
        )
        for code, cfg in pricing.plans.items()
    }


def sync_plans(pricing: Pricing) -> None:
    """Upsert every plan from the pricing file. Used by the seed script."""
    with transaction() as conn:
        for plan in _expected(pricing).values():
            plans.upsert(conn, plan)


def verify_plans(pricing: Pricing) -> None:
    """Startup check. An empty table is seeded; any other mismatch refuses to start."""
    expected = _expected(pricing)
    with transaction() as conn:
        actual = {p.code: p for p in plans.list_all(conn)}
        if not actual:
            for plan in expected.values():
                plans.upsert(conn, plan)
            return
    if actual != expected:
        raise ConfigError(
            "plans table does not match config/pricing.toml. "
            "Run `python -m app.seed` to sync it (and add a migration note to BUILDLOG.md)."
        )

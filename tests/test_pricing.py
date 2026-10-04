"""Cost rules: Probe 5, G3."""
import pytest

from app.api.schemas import TokenUsage
from app.core.config import get_pricing
from app.core.money import format_usd, micros_from_raw
from app.services.pricing import PricingService
from tests.helpers import body, headers

WORKED = TokenUsage(
    input_tokens=10_000, cached_input_tokens=4_000, output_tokens=2_000, reasoning_tokens=1_500
)


@pytest.fixture
def pricing() -> PricingService:
    return PricingService(get_pricing().rates)


def test_micros_from_raw_rounds_half_up():
    assert micros_from_raw(0) == 0
    assert micros_from_raw(499_999) == 0
    assert micros_from_raw(500_000) == 1
    assert micros_from_raw(1_499_999) == 1
    assert micros_from_raw(1_500_000) == 2


def test_format_usd_uses_integers_only():
    assert format_usd(0) == "0.000000"
    assert format_usd(12_850) == "0.012850"
    assert format_usd(29_000_000) == "29.000000"
    assert format_usd(29_012_850) == "29.012850"


def test_worked_example_is_10_850_micros(pricing):
    tokens = pricing.token_micros(10_000, 4_000, 2_000, 1_500)
    assert tokens == 10_850


def test_worked_example_breakdown_and_full_generate_cost(pricing):
    cost = pricing.cost(WORKED)
    assert cost.fresh_input_micros == 1_800
    assert cost.cached_input_micros == 300
    assert cost.output_micros == 8_750
    assert cost.api_call_micros == 2_000
    assert cost.total_micros == 12_850
    assert cost.total_usd == "0.012850"


@pytest.mark.parametrize("wrong", [11_750, 7_100, 5_250])
def test_the_three_wrong_answers_do_not_occur(pricing, wrong):
    # 11,750: cached billed at the full input rate. 7,100: reasoning left out.
    # 5,250: all 17,500 counted tokens at one input rate.
    assert pricing.token_micros(10_000, 4_000, 2_000, 1_500) != wrong
    assert pricing.cost(WORKED).total_micros - 2_000 != wrong


def test_reasoning_is_billed_at_the_output_rate(pricing):
    assert pricing.token_micros(0, 0, 0, 1_000) == pricing.token_micros(0, 0, 1_000, 0)
    assert pricing.token_micros(0, 0, 0, 1_000) > 0


def test_cached_tokens_are_cheaper_than_fresh(pricing):
    assert pricing.token_micros(1_000_000, 1_000_000, 0, 0) == 75_000
    assert pricing.token_micros(1_000_000, 0, 0, 0) == 300_000


def test_generate_returns_and_stores_the_real_cost(client, make_tenant):
    tenant, key = make_tenant()
    r = client.post("/generate", json=body(), headers=headers(key, "cost-1"))

    assert r.status_code == 201
    assert r.json()["cost"] == {
        "api_call_micros": 2_000,
        "fresh_input_micros": 1_800,
        "cached_input_micros": 300,
        "output_micros": 8_750,
        "total_micros": 12_850,
        "total_usd": "0.012850",
    }
    from app.core.db import transaction

    with transaction() as conn:
        row = conn.execute(
            "SELECT cost_micros FROM usage_events WHERE tenant_id = %s", (tenant.id,)
        ).fetchone()
    assert row["cost_micros"] == 12_850


def test_usage_rollup_prices_the_worked_example(client, make_tenant):
    _, key = make_tenant(plan="free")
    client.post("/generate", json=body(), headers=headers(key, "cost-1"))

    u = client.get("/usage", headers={"X-API-Key": key}).json()

    assert u["api_calls"]["cost_micros"] == 2_000
    assert u["tokens"]["cost_micros"] == 10_850
    assert u["base_fee_micros"] == 0
    assert u["total_micros"] == 12_850
    assert u["total_usd"] == "0.012850"


def test_usage_rollup_adds_the_pro_base_fee(client, make_tenant):
    _, key = make_tenant(plan="pro")
    client.post("/generate", json=body(), headers=headers(key, "cost-1"))

    u = client.get("/usage", headers={"X-API-Key": key}).json()

    assert u["base_fee_micros"] == 29_000_000
    assert u["total_micros"] == 29_012_850
    assert u["total_usd"] == "29.012850"


def test_rollup_prices_summed_counts_not_per_event_rounding(client, make_tenant, pricing):
    """100 events of 3 input + 1 output token: each event rounds to 3 micros (300 in total),
    but the summed counts (300 input, 100 output) price to 340 micros."""
    _, key = make_tenant(plan="free")
    small = body("tiny", input_tokens=3, cached=0, output=1, reasoning=0)
    for i in range(100):
        assert client.post("/generate", json=small, headers=headers(key, f"k-{i}")).status_code == 201

    u = client.get("/usage", headers={"X-API-Key": key}).json()

    assert pricing.token_micros(3, 0, 1, 0) == 3  # per-event figure
    assert u["tokens"]["cost_micros"] == pricing.token_micros(300, 0, 100, 0) == 340
    assert u["tokens"]["cost_micros"] != 100 * pricing.token_micros(3, 0, 1, 0)
    assert u["total_micros"] == 100 * 2_000 + 340

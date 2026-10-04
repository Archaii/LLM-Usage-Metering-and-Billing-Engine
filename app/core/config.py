"""Environment settings and the pinned pricing loader."""
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "pricing.toml"


class ConfigError(RuntimeError):
    """Raised at startup when configuration is unsafe or inconsistent."""


@dataclass(frozen=True)
class PlanConfig:
    code: str
    name: str
    api_call_limit: int
    token_limit: int
    base_fee_micros: int


@dataclass(frozen=True)
class Rates:
    api_call_micros: int
    input_micros_per_million: int
    cached_input_micros_per_million: int
    output_micros_per_million: int
    reasoning_micros_per_million: int


@dataclass(frozen=True)
class Pricing:
    currency: str
    micros_per_usd: int
    plans: dict[str, PlanConfig]
    rates: Rates


def load_pricing(path: Path = PRICING_PATH) -> Pricing:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    rates = Rates(**raw["rates"])
    if rates.reasoning_micros_per_million != rates.output_micros_per_million:
        raise ConfigError(
            "reasoning_micros_per_million must equal output_micros_per_million "
            "(reasoning tokens are billed as output)"
        )
    for name, value in vars(rates).items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"rates.{name} must be a non-negative integer")

    plans = {
        code: PlanConfig(code=code, **values) for code, values in raw["plans"].items()
    }
    if set(plans) != {"free", "pro"}:
        raise ConfigError("pricing.toml must define exactly the plans 'free' and 'pro'")

    return Pricing(
        currency=raw["currency"],
        micros_per_usd=raw["micros_per_usd"],
        plans=plans,
        rates=rates,
    )


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    stripe_secret_key: str = field(repr=False)
    stripe_webhook_secret: str = field(repr=False)
    stripe_pro_price_id: str
    app_base_url: str
    log_level: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable {name}")
    return value


@lru_cache
def get_settings() -> Settings:
    load_dotenv()  # real environment variables win over .env
    key = _require("STRIPE_SECRET_KEY")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise ConfigError("STRIPE_SECRET_KEY must be a Stripe test-mode key (sk_test_ or rk_test_)")
    return Settings(
        database_url=_require("DATABASE_URL"),
        stripe_secret_key=key,
        stripe_webhook_secret=_require("STRIPE_WEBHOOK_SECRET"),
        stripe_pro_price_id=_require("STRIPE_PRO_PRICE_ID"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )


@lru_cache
def get_pricing() -> Pricing:
    return load_pricing()

"""Environment settings and the pinned pricing loader."""
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "pricing.toml"
PAYMONGO_MIN_AMOUNT_CENTAVOS = 2_000  # PayMongo rejects charges below PHP 20.00


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
class CheckoutConfig:
    currency: str
    pro_amount_centavos: int
    pro_period_days: int


@dataclass(frozen=True)
class Pricing:
    currency: str
    micros_per_usd: int
    plans: dict[str, PlanConfig]
    rates: Rates
    checkout: CheckoutConfig


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

    checkout = CheckoutConfig(**raw["checkout"])
    if checkout.currency != "PHP":
        raise ConfigError("checkout.currency must be PHP (PayMongo charges Philippine pesos)")
    if checkout.pro_amount_centavos < PAYMONGO_MIN_AMOUNT_CENTAVOS:
        raise ConfigError("checkout.pro_amount_centavos is below PayMongo's PHP 20.00 minimum")
    if checkout.pro_period_days < 1:
        raise ConfigError("checkout.pro_period_days must be at least 1")

    return Pricing(
        currency=raw["currency"],
        micros_per_usd=raw["micros_per_usd"],
        plans=plans,
        rates=rates,
        checkout=checkout,
    )


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    paymongo_secret_key: str = field(repr=False)
    paymongo_webhook_secret: str = field(repr=False)
    app_base_url: str
    webhook_tolerance_seconds: int
    log_level: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable {name}")
    return value


@lru_cache
def get_settings() -> Settings:
    load_dotenv()  # real environment variables win over .env
    key = _require("PAYMONGO_SECRET_KEY")
    if not key.startswith("sk_test_"):
        raise ConfigError("PAYMONGO_SECRET_KEY must be a PayMongo test-mode key (sk_test_)")
    try:
        tolerance = int(os.environ.get("WEBHOOK_TOLERANCE_SECONDS", "300"))
    except ValueError:
        raise ConfigError("WEBHOOK_TOLERANCE_SECONDS must be an integer") from None
    return Settings(
        database_url=_require("DATABASE_URL"),
        paymongo_secret_key=key,
        paymongo_webhook_secret=_require("PAYMONGO_WEBHOOK_SECRET"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        webhook_tolerance_seconds=tolerance,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )


@lru_cache
def get_pricing() -> Pricing:
    return load_pricing()

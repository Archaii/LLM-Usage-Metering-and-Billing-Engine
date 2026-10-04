import pytest

from app.core.config import ConfigError, PRICING_PATH, get_settings, load_pricing


def test_pricing_loads_pinned_values():
    pricing = load_pricing()
    assert pricing.plans["pro"].base_fee_micros == 29_000_000
    assert pricing.rates.reasoning_micros_per_million == pricing.rates.output_micros_per_million


def test_loader_rejects_reasoning_rate_different_from_output_rate(tmp_path):
    text = PRICING_PATH.read_text(encoding="utf-8").replace(
        "reasoning_micros_per_million = 2_500_000", "reasoning_micros_per_million = 1_000_000"
    )
    bad = tmp_path / "pricing.toml"
    bad.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="reasoning"):
        load_pricing(bad)


def test_live_stripe_key_is_rejected(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_notallowed")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="test-mode"):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_settings_repr_hides_secrets():
    text = repr(get_settings())
    assert "sk_test_" not in text
    assert "whsec_" not in text
    assert "postgresql://" not in text

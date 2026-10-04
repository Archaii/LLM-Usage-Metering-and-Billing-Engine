"""Integer money helpers (spec section 5). Floats never touch money."""

MICROS_PER_USD = 1_000_000
_MILLION = 1_000_000


def micros_from_raw(raw: int) -> int:
    """Turn `tokens x micros-per-million-tokens` into micro-USD: divide once, round half up."""
    if raw < 0:
        raise ValueError("raw cost cannot be negative")
    return (raw + _MILLION // 2) // _MILLION


def format_usd(micros: int) -> str:
    """Format micro-USD as a USD string with six decimals, for example 12850 -> '0.012850'."""
    if micros < 0:
        raise ValueError("micros cannot be negative")
    whole, fraction = divmod(micros, MICROS_PER_USD)
    return f"{whole}.{fraction:06d}"

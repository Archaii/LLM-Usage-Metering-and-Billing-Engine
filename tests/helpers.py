def body(prompt: str = "Summarize this ticket", input_tokens: int = 10_000,
         cached: int = 4_000, output: int = 2_000, reasoning: int = 1_500) -> dict:
    return {
        "prompt": prompt,
        "tokens": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "reasoning_tokens": reasoning,
        },
    }


def small_body(prompt: str = "hi") -> dict:
    """1 token in, nothing else: 1 quota token."""
    return body(prompt, input_tokens=1, cached=0, output=0, reasoning=0)


def headers(api_key: str, idem_key: str | None = "key-1") -> dict:
    h = {"X-API-Key": api_key}
    if idem_key is not None:
        h["Idempotency-Key"] = idem_key
    return h

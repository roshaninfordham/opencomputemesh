"""Normalize runtime-reported statistics. Unknown values stay None; never invent numbers."""

NS_PER_MS = 1_000_000


def int_or_none(value) -> int | None:
    """Node-reported integers: anything else (str, bool, float, out of SQLite range) becomes None."""
    return value if type(value) is int and 0 <= value < 2**63 else None


def _duration_ns(value) -> int | float | None:
    """Not bool; NaN and inf fail the range check."""
    return value if type(value) in (int, float) and 0 <= value < 2**63 else None


def ns_to_ms(value) -> int | None:
    value = _duration_ns(value)
    return round(value / NS_PER_MS) if value is not None else None


def tokens_per_second(count, duration_ns) -> float | None:
    count, duration_ns = int_or_none(count), _duration_ns(duration_ns)
    if count is not None and duration_ns:
        return round(count / duration_ns * 1e9, 2)
    return None


def normalize_ollama(final: dict) -> dict:
    """Map an Ollama `done: true` chunk to job metric fields (durations in ms)."""
    inp = int_or_none(final.get("prompt_eval_count"))
    out = int_or_none(final.get("eval_count"))
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "token_count_source": "runtime_reported" if inp is not None and out is not None
        else ("partial_runtime_reported" if inp is not None or out is not None else "unavailable"),
        "runtime_total_ms": ns_to_ms(final.get("total_duration")),
        "runtime_load_ms": ns_to_ms(final.get("load_duration")),
        "prompt_eval_ms": ns_to_ms(final.get("prompt_eval_duration")),
        "generation_ms": ns_to_ms(final.get("eval_duration")),
        "tokens_per_second": tokens_per_second(out, final.get("eval_duration")),
    }

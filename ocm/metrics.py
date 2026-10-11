"""Normalize runtime-reported statistics. Unknown values stay None; never invent numbers."""

NS_PER_MS = 1_000_000


def ns_to_ms(value) -> int | None:
    return round(value / NS_PER_MS) if isinstance(value, (int, float)) and value >= 0 else None


def tokens_per_second(count, duration_ns) -> float | None:
    if isinstance(count, int) and isinstance(duration_ns, (int, float)) and duration_ns > 0:
        return round(count / duration_ns * 1e9, 2)
    return None


def normalize_ollama(final: dict) -> dict:
    """Map an Ollama `done: true` chunk to job metric fields (durations in ms)."""
    inp = final.get("prompt_eval_count")
    out = final.get("eval_count")
    inp = inp if isinstance(inp, int) else None
    out = out if isinstance(out, int) else None
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

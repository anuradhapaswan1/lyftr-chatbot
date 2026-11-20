"""LiteLLM gateway: built-in response cache + ordered key failover."""
import logging
import time
from dataclasses import dataclass

import litellm
from litellm.caching import Cache

log = logging.getLogger("llm")
litellm.suppress_debug_info = True
SIM_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    pass


@dataclass
class LLMResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    resolution_source: str  # LIVE_INFERENCE | LLM_CACHE
    key_id: str | None
    fallback_attempts: int


def setup_cache(settings) -> str:
    """Enable LiteLLM's built-in cache; disk persists across restarts, local is in-memory."""
    if settings.cache_type == "disk":
        try:
            litellm.cache = Cache(type="disk", disk_cache_dir=settings.cache_dir)
            return "disk"
        except Exception as exc:  # unwritable dir etc: degrade, don't crash
            log.warning("disk cache unavailable (%s); using in-memory cache", exc)
    litellm.cache = Cache(type="local")
    return "local"


def parse_simulation(header: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, ...}. Bad pairs are ignored."""
    out: dict[str, int] = {}
    for part in (header or "").split(","):
        name, _, status = part.partition("=")
        name, status = name.strip(), status.strip()
        if name and status.isdigit() and int(status) in SIM_STATUSES:
            out[name] = int(status)
    return out


async def _raw_call(settings, api_key: str, messages: list[dict], bypass_cache: bool):
    kwargs = dict(
        model=settings.model,
        messages=messages,
        api_key=api_key,
        timeout=settings.llm_timeout,
        num_retries=0,  # failover across keys is handled by the caller
        caching=True,
    )
    if bypass_cache:  # skip cache *read*; result is still written
        kwargs["cache"] = {"no-cache": True}
    return await litellm.acompletion(**kwargs)


async def complete(settings, messages: list[dict], simulate: dict[str, int]) -> LLMResult:
    attempts = 0
    for idx, api_key in enumerate(settings.api_keys, start=1):
        key_id = f"key_{idx}"
        sim = simulate.get(key_id) or simulate.get("*")
        if sim:
            log.warning("%s: simulated HTTP %s", key_id, sim)
            attempts += 1
            continue
        try:
            resp = await _raw_call(settings, api_key, messages, bool(simulate))
            reply = (resp.choices[0].message.content or "").strip()
            if not reply:
                raise ValueError("empty completion")
        except Exception as exc:  # 429, quota, 5xx, timeout, auth, empty -> next key
            log.warning("%s failed: %s: %s", key_id, type(exc).__name__, str(exc)[:200])
            attempts += 1
            continue
        usage = getattr(resp, "usage", None)
        hit = bool((getattr(resp, "_hidden_params", None) or {}).get("cache_hit"))
        return LLMResult(
            reply=reply,
            model=settings.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            resolution_source="LLM_CACHE" if hit else "LIVE_INFERENCE",
            key_id=None if hit else key_id,
            fallback_attempts=0 if hit else attempts,
        )
    raise AllProvidersUnavailable()

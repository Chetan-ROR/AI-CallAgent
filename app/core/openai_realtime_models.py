from __future__ import annotations

import time
from threading import Lock
from typing import Any

from openai import APIError, OpenAI

from app.core.config import OPENAI_API_KEY

# UI copy only — model IDs come from OpenAI GET /v1/models.
REALTIME_MODEL_UI_META: dict[str, dict[str, Any]] = {
    "gpt-realtime-2.1": {
        "label": "GPT Realtime 2.1",
        "description": "Newest; best reasoning, tools, and voice-agent quality",
        "recommended": True,
        "rank": 0,
    },
    "gpt-realtime-2.1-mini": {
        "label": "GPT Realtime 2.1 Mini",
        "description": "Faster, lower-cost realtime voice",
        "recommended": True,
        "rank": 1,
    },
    "gpt-realtime-2": {
        "label": "GPT Realtime 2",
        "description": "GA realtime model (common default)",
        "rank": 2,
    },
    "gpt-realtime-1.5": {
        "label": "GPT Realtime 1.5",
        "description": "Prior realtime generation",
        "rank": 3,
    },
    "gpt-realtime": {
        "label": "GPT Realtime",
        "description": "Original GA realtime",
        "rank": 4,
    },
    "gpt-realtime-mini": {
        "label": "GPT Realtime Mini",
        "description": "Mini realtime (cost-sensitive)",
        "rank": 5,
    },
    "gpt-4o-realtime-preview": {
        "label": "GPT-4o Realtime Preview",
        "description": "Legacy preview family",
        "rank": 6,
    },
    "gpt-4o-mini-realtime-preview": {
        "label": "GPT-4o Mini Realtime Preview",
        "description": "Legacy mini preview family",
        "rank": 7,
    },
}

DEFAULT_REALTIME_MODEL = "gpt-realtime-2"

# Non speech-to-speech realtime SKUs (transcription / translate).
_REALTIME_ID_BLOCKLIST = (
    "transcribe",
    "translation",
    "translate",
    "whisper",
    "live-transcribe",
)

_cache_lock = Lock()
_cache_ids: frozenset[str] = frozenset()
_cache_catalog: list[dict[str, Any]] = []
_cache_fetched_at: float = 0.0
_CACHE_MAX_AGE_SEC = 0.0  # refresh from OpenAI on every catalog read


def _speech_realtime_model_id(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    if mid == "gpt-live-1" or mid.startswith("gpt-live-1"):
        return "transcribe" not in mid
    if "realtime" not in mid:
        return False
    return not any(token in mid for token in _REALTIME_ID_BLOCKLIST)


def _meta_rank(model_id: str) -> tuple[int, str]:
    meta = REALTIME_MODEL_UI_META.get(model_id) or {}
    rank = meta.get("rank")
    if rank is None:
        rank = 100
    return (int(rank), model_id)


def _catalog_entry(model_id: str) -> dict[str, Any]:
    meta = REALTIME_MODEL_UI_META.get(model_id) or {}
    entry: dict[str, Any] = {
        "id": model_id,
        "label": meta.get("label") or model_id,
        "description": meta.get("description")
        or "Available on your OpenAI API key (from /v1/models)",
    }
    if meta.get("recommended"):
        entry["recommended"] = True
    return entry


def _fetch_from_openai(client: OpenAI) -> tuple[list[dict[str, Any]], frozenset[str]]:
    result = client.models.list()
    ids = sorted(
        {
            row.id
            for row in result.data
            if getattr(row, "id", None) and _speech_realtime_model_id(row.id)
        }
    )
    if not ids:
        raise RuntimeError("No Realtime speech models returned for this API key")
    catalog = [_catalog_entry(mid) for mid in sorted(ids, key=_meta_rank)]
    return catalog, frozenset(ids)


def refresh_realtime_models_cache(*, client: OpenAI | None = None) -> list[dict[str, Any]]:
    """Pull Realtime model IDs from OpenAI and update the in-process cache."""
    global _cache_ids, _cache_catalog, _cache_fetched_at
    api = client or OpenAI(api_key=OPENAI_API_KEY)
    try:
        catalog, ids = _fetch_from_openai(api)
    except APIError as exc:
        raise RuntimeError(str(exc) or "Could not list models from OpenAI") from exc

    with _cache_lock:
        _cache_ids = ids
        _cache_catalog = catalog
        _cache_fetched_at = time.time()
    return list(catalog)


def get_realtime_models_catalog(*, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    """
    Return UI catalog entries. Always tries OpenAI first unless a very recent cache hit
    and force_refresh is False (_CACHE_MAX_AGE_SEC is 0 → always refresh).
    """
    now = time.time()
    with _cache_lock:
        fresh = _cache_catalog and (now - _cache_fetched_at) <= _CACHE_MAX_AGE_SEC
        if fresh and not force_refresh:
            return list(_cache_catalog), "openai"

    try:
        catalog = refresh_realtime_models_cache()
        return catalog, "openai"
    except Exception:
        fallback_ids = sorted(REALTIME_MODEL_UI_META.keys(), key=_meta_rank)
        fallback = [_catalog_entry(mid) for mid in fallback_ids]
        return fallback, "fallback"


def allowed_realtime_model_ids(*, refresh_if_empty: bool = True) -> frozenset[str]:
    with _cache_lock:
        if _cache_ids and (time.time() - _cache_fetched_at) <= max(_CACHE_MAX_AGE_SEC, 30):
            return _cache_ids
    if refresh_if_empty:
        try:
            refresh_realtime_models_cache()
        except Exception:
            pass
    with _cache_lock:
        if _cache_ids:
            return _cache_ids
    return frozenset(REALTIME_MODEL_UI_META.keys())


def resolve_realtime_model_id(
    raw: str | None,
    *,
    fallback: str = DEFAULT_REALTIME_MODEL,
) -> str:
    """Pick a Realtime model id saved on the agent, validated against live OpenAI when possible."""
    choice = (raw or "").strip()
    allowed = allowed_realtime_model_ids()
    fb = (fallback or DEFAULT_REALTIME_MODEL).strip()

    if choice and choice in allowed:
        return choice
    if choice and _speech_realtime_model_id(choice) and not allowed:
        return choice
    if fb in allowed:
        return fb
    if allowed:
        return sorted(allowed, key=_meta_rank)[0]
    if fb in REALTIME_MODEL_UI_META:
        return fb
    return DEFAULT_REALTIME_MODEL

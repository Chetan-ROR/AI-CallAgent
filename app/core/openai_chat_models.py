"""OpenAI models for phone agents — Aidan V2 Live + V1 chat list."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from openai import APIError, OpenAI

from app.core.config import OPENAI_API_KEY

# Aidan “V2 (Hears and speaks directly)” — voice layer is gpt-live-1.
V2_LIVE_MODEL_UI_META: dict[str, dict[str, Any]] = {
    "gpt-live-1-terra": {
        "label": "GPT Live-1 · Terra",
        "description": "Balanced. The default.",
        "badge": "Most natural",
        "recommended": True,
        "mode": "v2",
        "group": "V2",
        "group_title": "V2 · Hears and speaks directly",
        "group_subtitle": "Reliable all-rounders. Safe default for most agents.",
        "realtime_model": "gpt-live-1",
        "smart": 4,
        "speed": 4,
        "rank": -30,
    },
    "gpt-live-1-sol": {
        "label": "GPT Live-1 · Sol",
        "description": "Deepest reasoning.",
        "badge": "Smartest",
        "mode": "v2",
        "group": "V2",
        "group_title": "V2 · Hears and speaks directly",
        "group_subtitle": "Reliable all-rounders. Safe default for most agents.",
        "realtime_model": "gpt-live-1",
        "smart": 5,
        "speed": 3,
        "rank": -20,
    },
    "gpt-live-1-luna": {
        "label": "GPT Live-1 · Luna",
        "description": "Fastest and cheapest.",
        "badge": "Fastest",
        "mode": "v2",
        "group": "V2",
        "group_title": "V2 · Hears and speaks directly",
        "group_subtitle": "Reliable all-rounders. Safe default for most agents.",
        "realtime_model": "gpt-live-1",
        "smart": 3,
        "speed": 5,
        "rank": -10,
    },
}

# Aidan “V1 (LEGACY) Transcribe, think, speak”.
CHAT_MODEL_UI_META: dict[str, dict[str, Any]] = {
    "gpt-4.1": {
        "label": "GPT-4.1",
        "description": "Highly accurate.",
        "badge": "Recommended",
        "recommended": True,
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 4,
        "speed": 3,
        "context": 2,
        "rank": 0,
    },
    "gpt-5.4": {
        "label": "GPT-5.4",
        "description": "Best quality.",
        "badge": "Best quality",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 5,
        "speed": 2,
        "context": 2,
        "rank": 1,
    },
    "gpt-5.4-mini": {
        "label": "GPT-5.4 Mini",
        "description": "Very accurate, balanced.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 4,
        "speed": 4,
        "context": 3,
        "rank": 2,
    },
    "gpt-5.4-nano": {
        "label": "GPT-5.4 Nano",
        "description": "Fast, generous context.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 2,
        "speed": 5,
        "context": 4,
        "rank": 3,
    },
    "gpt-5.2": {
        "label": "GPT-5.2",
        "description": "Accurate.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 4,
        "speed": 2,
        "context": 2,
        "rank": 4,
    },
    "gpt-5.1": {
        "label": "GPT-5.1",
        "description": "Very accurate.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 4,
        "speed": 2,
        "context": 2,
        "rank": 5,
    },
    "gpt-5-mini": {
        "label": "GPT-5 Mini",
        "description": "Moderate.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 3,
        "speed": 4,
        "context": 4,
        "rank": 6,
    },
    "gpt-5-nano": {
        "label": "GPT-5 Nano",
        "description": "Lightweight. Most context.",
        "badge": "Most context",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 2,
        "speed": 5,
        "context": 5,
        "rank": 7,
    },
    "gpt-4.1-mini": {
        "label": "GPT-4.1 Mini",
        "description": "Moderate.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 3,
        "speed": 4,
        "context": 3,
        "rank": 8,
    },
    "gpt-4.1-nano": {
        "label": "GPT-4.1 Nano",
        "description": "Lightweight.",
        "mode": "pipeline",
        "group": "V1",
        "group_title": "V1 (LEGACY) · Transcribe, think, speak",
        "group_subtitle": "OpenAI STT + chat model + TTS.",
        "smart": 2,
        "speed": 5,
        "context": 5,
        "rank": 9,
    },
}

DEFAULT_CHAT_MODEL = "gpt-4.1"
DEFAULT_V2_MODEL = "gpt-live-1-terra"
V2_LIVE_MODEL_IDS: tuple[str, ...] = tuple(V2_LIVE_MODEL_UI_META.keys())
CURATED_CHAT_MODEL_IDS: tuple[str, ...] = tuple(CHAT_MODEL_UI_META.keys())
ALL_UI_MODEL_IDS: tuple[str, ...] = V2_LIVE_MODEL_IDS + CURATED_CHAT_MODEL_IDS

_cache_lock = Lock()
_cache_ids: frozenset[str] = frozenset()
_cache_catalog: list[dict[str, Any]] = []
_cache_fetched_at: float = 0.0
_CACHE_MAX_AGE_SEC = 0.0


def is_v2_live_model_id(model_id: str | None) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if mid in V2_LIVE_MODEL_UI_META:
        return True
    # Accept raw live voice model id.
    return mid == "gpt-live-1"


def resolve_v2_realtime_model_id(model_id: str | None) -> str:
    """Map Aidan Live variant → OpenAI voice model for Realtime/Live connect."""
    mid = (model_id or "").strip().lower()
    meta = V2_LIVE_MODEL_UI_META.get(mid) or {}
    return str(meta.get("realtime_model") or "gpt-live-1")


def agent_uses_v2_live(agent: dict | None) -> bool:
    settings = (agent or {}).get("voice_settings") or {}
    if not isinstance(settings, dict):
        return False
    return is_v2_live_model_id(str(settings.get("model") or "").strip())


def _meta_for(model_id: str) -> dict[str, Any]:
    return (
        V2_LIVE_MODEL_UI_META.get(model_id)
        or CHAT_MODEL_UI_META.get(model_id)
        or {}
    )


def _meta_rank(model_id: str) -> tuple[int, str]:
    meta = _meta_for(model_id)
    rank = meta.get("rank")
    if rank is None:
        rank = 100
    return (int(rank), model_id)


def _catalog_entry(model_id: str) -> dict[str, Any]:
    meta = _meta_for(model_id)
    mode = str(meta.get("mode") or "pipeline")
    entry: dict[str, Any] = {
        "id": model_id,
        "label": meta.get("label") or model_id,
        "description": meta.get("description")
        or "Reliable model for phone agents",
        "provider": "openai",
        "mode": mode,
        "group": meta.get("group") or ("V2" if mode == "v2" else "V1"),
        "group_title": meta.get("group_title")
        or ("V2 · Hears and speaks directly" if mode == "v2" else "V1 (LEGACY) · Transcribe, think, speak"),
        "group_subtitle": meta.get("group_subtitle")
        or (
            "Reliable all-rounders. Safe default for most agents."
            if mode == "v2"
            else "OpenAI STT + chat model + TTS."
        ),
        "smart": int(meta.get("smart") or 0),
        "speed": int(meta.get("speed") or 0),
        "context": int(meta.get("context") or 0),
    }
    if meta.get("badge"):
        entry["badge"] = meta["badge"]
    if meta.get("recommended"):
        entry["recommended"] = True
    return entry


def _fallback_catalog() -> list[dict[str, Any]]:
    return [_catalog_entry(mid) for mid in ALL_UI_MODEL_IDS]


def _fetch_from_openai(client: OpenAI) -> tuple[list[dict[str, Any]], frozenset[str]]:
    result = client.models.list()
    available = {row.id for row in result.data if getattr(row, "id", None)}
    # V2 variants always offered if gpt-live-1 (or any realtime) exists on the key.
    live_ok = "gpt-live-1" in available or any("realtime" in mid for mid in available)
    ids: list[str] = []
    if live_ok:
        ids.extend(V2_LIVE_MODEL_IDS)
    for mid in CURATED_CHAT_MODEL_IDS:
        if mid in available:
            ids.append(mid)
    if not ids:
        ids = list(ALL_UI_MODEL_IDS)
    catalog = [_catalog_entry(mid) for mid in ids]
    return catalog, frozenset(ids)


def refresh_chat_models_cache(*, client: OpenAI | None = None) -> list[dict[str, Any]]:
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


def get_chat_models_catalog(*, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    now = time.time()
    with _cache_lock:
        fresh = _cache_catalog and (now - _cache_fetched_at) <= _CACHE_MAX_AGE_SEC
        if fresh and not force_refresh:
            return list(_cache_catalog), "openai"

    try:
        catalog = refresh_chat_models_cache()
        return catalog, "openai"
    except Exception:
        return _fallback_catalog(), "fallback"


def allowed_chat_model_ids(*, refresh_if_empty: bool = True) -> frozenset[str]:
    with _cache_lock:
        if _cache_ids and (time.time() - _cache_fetched_at) <= max(_CACHE_MAX_AGE_SEC, 30):
            return _cache_ids
    if refresh_if_empty:
        try:
            refresh_chat_models_cache()
        except Exception:
            pass
    with _cache_lock:
        if _cache_ids:
            return _cache_ids
    return frozenset(ALL_UI_MODEL_IDS)


def resolve_chat_model_id(
    raw: str | None,
    *,
    fallback: str = DEFAULT_CHAT_MODEL,
) -> str:
    """Resolve a V1 chat model id (not V2 Live)."""
    choice = (raw or "").strip()
    if is_v2_live_model_id(choice):
        # Caller asked for chat id but got a V2 id — fall back.
        choice = ""
    allowed = allowed_chat_model_ids()
    fb = (fallback or DEFAULT_CHAT_MODEL).strip()
    if is_v2_live_model_id(fb):
        fb = DEFAULT_CHAT_MODEL

    if choice and choice in allowed and choice in CURATED_CHAT_MODEL_IDS:
        return choice
    if choice and choice in CURATED_CHAT_MODEL_IDS:
        return choice
    if fb in CURATED_CHAT_MODEL_IDS:
        return fb
    v1_allowed = [mid for mid in allowed if mid in CURATED_CHAT_MODEL_IDS]
    if v1_allowed:
        return sorted(v1_allowed, key=_meta_rank)[0]
    return DEFAULT_CHAT_MODEL

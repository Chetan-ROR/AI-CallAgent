import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import LLC_AI_API_KEY, LLC_API_URL, LLC_CLIENT_ID


class LlcClient:
    def __init__(self):
        self.base_url = (LLC_API_URL or "").rstrip("/")
        self.headers = {
            "X-AI-API-Key": LLC_AI_API_KEY or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and LLC_AI_API_KEY)

    def _client_id(self, client_id=None):
        return client_id or LLC_CLIENT_ID

    def _sync_request(self, method: str, path: str, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        if params:
            filtered = {key: value for key, value in params.items() if value not in (None, "")}
            if filtered:
                url = f"{url}?{urlencode(filtered, doseq=True)}"

        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")

        request = Request(url, data=body, method=method)
        for key, value in self.headers.items():
            request.add_header(key, value)

        try:
            with urlopen(request, timeout=25) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except URLError as exc:
            return {"success": False, "error": f"LLC request failed: {exc.reason}"}

        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {
                "success": False,
                "error": f"LLC returned a non-JSON response ({status})",
            }

        if status >= 400 and not isinstance(data, dict):
            return {"success": False, "error": f"LLC error {status}"}

        if status >= 400 and isinstance(data, dict) and "success" not in data:
            data = {
                "success": False,
                "error": data.get("error") or f"LLC error {status}",
            }

        return data

    async def _request(self, method: str, path: str, params=None, json_body=None):
        if not self.enabled:
            return {
                "success": False,
                "error": "LLC API is not configured. Set LLC_API_URL and LLC_AI_API_KEY.",
            }

        return await asyncio.to_thread(
            self._sync_request,
            method,
            path,
            params,
            json_body,
        )

    async def lookup_member(self, *, client_id=None, member_id=None):
        return await self._request(
            "GET",
            "/ai/members/lookup",
            params={
                "client_id": self._client_id(client_id),
                "member_id": member_id,
            },
        )


    async def lookup_agent(self, *, client_id=None, agent_id=None, agent_key=None):
        params = {
            "client_id": self._client_id(client_id),
            "agent_id": agent_id,
        }
        if agent_key and not agent_id:
            params["agent_key"] = agent_key
        return await self._request(
            "GET",
            "/ai/agents/lookup",
            params=params,
        )


_crm_prefetch = {}
_agent_prefetch = {}


def _crm_cache_key(client_id, member_id):
    return f"{client_id or ''}:{member_id or ''}"


def save_crm_prefetch(client_id, member_id, result):
    packed = result or {}
    _crm_prefetch[_crm_cache_key(client_id, member_id)] = packed
    _crm_prefetch[_crm_cache_key(client_id, None)] = packed
    studio_id = (packed.get("studio") or {}).get("id")
    if studio_id and studio_id != client_id:
        _crm_prefetch[_crm_cache_key(studio_id, member_id)] = packed
        _crm_prefetch[_crm_cache_key(studio_id, None)] = packed


def get_crm_prefetch(client_id, member_id):
    if member_id:
        found = _crm_prefetch.get(_crm_cache_key(client_id, member_id))
        if found:
            return found
    return _crm_prefetch.get(_crm_cache_key(client_id, None))


def save_agent_prefetch(client_id, agent_id, result):
    agent = (result or {}).get("agent") or {}
    resolved_id = agent_id or agent.get("id")
    packed = result or {}
    _agent_prefetch[_crm_cache_key(client_id, resolved_id)] = packed
    _agent_prefetch[_crm_cache_key(client_id, None)] = packed


def get_agent_prefetch(client_id, agent_id):
    if agent_id:
        found = _agent_prefetch.get(_crm_cache_key(client_id, agent_id))
        if found:
            return found
    return _agent_prefetch.get(_crm_cache_key(client_id, None))

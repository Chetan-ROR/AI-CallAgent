"""Quick cloudflared tunnel when dev tunnels pass HTTP but fail Twilio WSS (31901)."""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import IO

_TUNNEL_URL: str | None = None
_TUNNEL_PROC: subprocess.Popen | None = None
_TUNNEL_TASK: asyncio.Task[str | None] | None = None
_URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com", re.I)
_DOWNLOAD_LOCK = asyncio.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundled_cloudflared_path() -> Path:
    return _project_root() / "bin" / "cloudflared"


def cloudflared_binary() -> str | None:
    bundled = _bundled_cloudflared_path()
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    found = shutil.which("cloudflared")
    if found:
        return found
    return None


def cloudflared_installed() -> bool:
    return cloudflared_binary() is not None


def media_stream_wss_base() -> str | None:
    return _TUNNEL_URL


def _cloudflared_download_name() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return None
    if machine in ("x86_64", "amd64"):
        return "cloudflared-linux-amd64"
    if machine in ("aarch64", "arm64"):
        return "cloudflared-linux-arm64"
    return None


def _download_cloudflared_sync() -> str | None:
    name = _cloudflared_download_name()
    if not name:
        print("⚠️ cloudflared auto-download unsupported on", platform.machine())
        return None

    dest = _bundled_cloudflared_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{name}"
    print("⬇️ Downloading cloudflared for Media Stream WSS:", url)
    try:
        from urllib.request import urlopen

        with urlopen(url, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except Exception as exc:
        print("❌ cloudflared download failed:", repr(exc))
        return None

    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("✅ cloudflared installed at", dest)
    return str(dest)


async def ensure_cloudflared_binary() -> str | None:
    existing = cloudflared_binary()
    if existing:
        return existing
    async with _DOWNLOAD_LOCK:
        existing = cloudflared_binary()
        if existing:
            return existing
        return await asyncio.to_thread(_download_cloudflared_sync)


def _read_tunnel_url_from_output(stream: IO[str], timeout_sec: float) -> str | None:
    import select
    import time

    deadline = time.monotonic() + timeout_sec
    buffer = ""
    while time.monotonic() < deadline:
        if _TUNNEL_PROC and _TUNNEL_PROC.poll() is not None:
            break
        ready, _, _ = select.select([stream], [], [], 0.5)
        if not ready:
            continue
        chunk = stream.read(4096)
        if not chunk:
            break
        buffer += chunk
        match = _URL_RE.search(buffer)
        if match:
            return match.group(0).rstrip("/")
    if buffer.strip():
        print("cloudflared output (no URL yet):", buffer[-2000:])
    return None


async def _start_cloudflared_tunnel_once(*, port: int | None = None, timeout_sec: float = 50.0) -> str | None:
    global _TUNNEL_URL, _TUNNEL_PROC

    if _TUNNEL_URL:
        return _TUNNEL_URL

    from app.core.config import APP_PORT

    port = port or APP_PORT

    binary = await ensure_cloudflared_binary()
    if not binary:
        print(
            "⚠️ Twilio WSS needs cloudflared or STREAM_PUBLIC_BASE_URL.",
            "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
        )
        return None

    if _TUNNEL_PROC is None or _TUNNEL_PROC.poll() is not None:
        print("🔌 Starting cloudflared quick tunnel for Media Stream WSS…")
        _TUNNEL_PROC = subprocess.Popen(
            [binary, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    if not _TUNNEL_PROC.stdout:
        return None

    url = await asyncio.to_thread(_read_tunnel_url_from_output, _TUNNEL_PROC.stdout, timeout_sec)
    if url:
        _TUNNEL_URL = url
        print("✅ cloudflared WSS base (Twilio Media Stream):", url)
        return url

    print("❌ cloudflared did not print a trycloudflare.com URL in time")
    return None


async def ensure_cloudflared_tunnel(*, port: int | None = None, timeout_sec: float = 50.0) -> str | None:
    """
    Start `cloudflared tunnel --url http://127.0.0.1:PORT` once and return https base URL.
    Concurrent callers share one startup task (single stdout reader).
    """
    global _TUNNEL_TASK

    if _TUNNEL_URL:
        return _TUNNEL_URL

    from app.core.config import APP_PORT

    port = port or APP_PORT

    if _TUNNEL_TASK is None or _TUNNEL_TASK.done():
        _TUNNEL_TASK = asyncio.create_task(
            _start_cloudflared_tunnel_once(port=port, timeout_sec=timeout_sec)
        )

    try:
        return await asyncio.shield(_TUNNEL_TASK)
    except asyncio.CancelledError:
        return _TUNNEL_URL


def shutdown_cloudflared_tunnel() -> None:
    global _TUNNEL_URL, _TUNNEL_PROC, _TUNNEL_TASK
    # Do not cancel _TUNNEL_TASK — uvicorn reload would hang on in-flight /make-call.
    _TUNNEL_TASK = None
    if _TUNNEL_PROC and _TUNNEL_PROC.poll() is None:
        _TUNNEL_PROC.terminate()
        try:
            _TUNNEL_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _TUNNEL_PROC.kill()
    _TUNNEL_PROC = None
    _TUNNEL_URL = None

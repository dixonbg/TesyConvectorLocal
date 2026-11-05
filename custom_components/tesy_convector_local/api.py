"""Local HTTP API of Tesy convectors (CN06AS family).

The convector answers POST http://<ip>/<command> with a JSON body.
`getStatus` returns every setting as {"<command>": {"payload": {...}}}, and the settings
are changed with commands of the same names. The local API reports no heating (relay)
state, room temperature or power.

The device's web server handles one request at a time and resets reused or overlapping
connections, so requests are serialized, never kept alive, and retried.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class TesyApiError(Exception):
    """The convector did not answer or answered something unexpected."""


class TesyLocalApi:
    """Client for one convector."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._session = session
        self.host = host
        self._lock = asyncio.Lock()

    async def request(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        """Send one command and return the decoded JSON reply."""
        async with self._lock:
            for attempt in range(3):
                try:
                    async with self._session.post(
                        f"http://{self.host}/{command}",
                        json=payload or {},
                        headers={"Connection": "close"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        data = await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                    if attempt == 2:
                        raise TesyApiError(f"{self.host} {command}: {err}") from err
                    await asyncio.sleep(1 + attempt)
                    continue
                if not isinstance(data, dict):
                    raise TesyApiError(f"{self.host} {command}: unexpected reply {data!r}")
                return data
        raise TesyApiError(f"{self.host} {command}: no reply")  # pragma: no cover

    async def get_status(self) -> dict[str, dict]:
        """Return {command: inner payload}, e.g. {"onOff": {"status": "on"}, ...}."""
        reply = await self.request("getStatus")
        payload = reply.get("payload")
        if not isinstance(payload, dict) or "onOff" not in payload:
            raise TesyApiError(f"{self.host}: not a supported Tesy convector reply: {reply}")
        return {
            key: (value.get("payload") or {})
            for key, value in payload.items()
            if isinstance(value, dict)
        }

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from meowbot.apps.api.action_guard import ApiActionGuard, COOLDOWN_MESSAGE, LOCK_MESSAGE


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_app(guard: ApiActionGuard) -> FastAPI:
    app = FastAPI()
    calls = {"update": 0, "slow": 0}

    @app.middleware("http")
    async def action_guard_middleware(request, call_next):
        return await guard.dispatch(request, call_next)

    @app.post("/settings/update")
    async def update_settings(payload: dict):
        calls["update"] += 1
        return {"ok": True, "calls": calls["update"], "value": payload.get("value")}

    @app.post("/slow-action")
    async def slow_action(payload: dict):
        await asyncio.sleep(0.05)
        calls["slow"] += 1
        return {"ok": True, "calls": calls["slow"], "value": payload.get("value")}

    @app.get("/settings/update")
    async def read_settings(user_id: str):
        calls["update"] += 1
        return {"ok": True, "calls": calls["update"], "user_id": user_id}

    app.state.calls = calls
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_identical_mutating_request_replays_cached_response() -> None:
    guard = ApiActionGuard(cooldown_seconds=5, idempotency_ttl_seconds=10)
    app = _make_app(guard)

    async with await _client(app) as client:
        first = await client.post("/settings/update", json={"user_id": "u1", "value": "on"})
        second = await client.post("/settings/update", json={"user_id": "u1", "value": "on"})

    assert first.status_code == 200
    assert first.json() == {"ok": True, "calls": 1, "value": "on"}
    assert second.status_code == 200
    assert second.headers["x-idempotent-replay"] == "true"
    assert second.json() == first.json()
    assert app.state.calls["update"] == 1


async def test_different_mutating_request_within_cooldown_is_blocked() -> None:
    guard = ApiActionGuard(cooldown_seconds=5, idempotency_ttl_seconds=10)
    app = _make_app(guard)

    async with await _client(app) as client:
        first = await client.post("/settings/update", json={"telegram_user_id": "42", "value": "on"})
        second = await client.post("/settings/update", json={"telegram_user_id": "42", "value": "off"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == COOLDOWN_MESSAGE
    assert second.json()["cooldown_left_seconds"] > 0
    assert app.state.calls["update"] == 1


async def test_concurrent_same_user_action_is_blocked_by_lock() -> None:
    guard = ApiActionGuard(cooldown_seconds=0, idempotency_ttl_seconds=1)
    app = _make_app(guard)

    async with await _client(app) as client:
        first, second = await asyncio.gather(
            client.post("/slow-action", json={"user_id": "u1", "value": 1}),
            client.post("/slow-action", json={"user_id": "u1", "value": 2}),
        )

    statuses = sorted([first.status_code, second.status_code])
    blocked = first if first.status_code == 409 else second

    assert statuses == [200, 409]
    assert blocked.json()["detail"] == LOCK_MESSAGE
    assert app.state.calls["slow"] == 1


async def test_get_requests_are_not_guarded() -> None:
    guard = ApiActionGuard(cooldown_seconds=5, idempotency_ttl_seconds=10)
    app = _make_app(guard)

    async with await _client(app) as client:
        first = await client.get("/settings/update", params={"user_id": "u1"})
        second = await client.get("/settings/update", params={"user_id": "u1"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["calls"] == 2

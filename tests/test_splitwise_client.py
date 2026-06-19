"""Tests for the async Splitwise client (§23) using httpx.MockTransport."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from tigrinho.providers.splitwise import (
    ExpenseShare,
    SplitwiseClient,
    SplitwiseError,
)


def _client(handler: Any) -> SplitwiseClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="http://sw.local", transport=transport)
    return SplitwiseClient(base_url="http://sw.local", api_key="k", client=http)


async def test_get_current_user_parses_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/get_current_user"
        return httpx.Response(
            200, json={"user": {"id": 9, "first_name": "Bot", "email": "b@x.com"}}
        )

    user = await _client(handler).get_current_user()
    assert user.id == 9
    assert user.first_name == "Bot"
    assert user.email == "b@x.com"


async def test_self_built_client_sets_bearer_auth_header() -> None:
    client = SplitwiseClient(base_url="http://sw.local", api_key="secret-key")
    try:
        assert client._client.headers["Authorization"] == "Bearer secret-key"
    finally:
        await client.aclose()


async def test_get_group_members_parses_roster() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/get_group/55"
        return httpx.Response(
            200,
            json={
                "group": {
                    "members": [
                        {"id": 1, "first_name": "João", "last_name": "Silva", "email": "j@x.com"},
                        {"id": 2, "first_name": "Maria", "last_name": None, "email": None},
                    ]
                }
            },
        )

    members = await _client(handler).get_group_members(55)
    assert [m.id for m in members] == [1, 2]
    assert members[0].display_name == "João Silva"
    assert members[1].display_name == "Maria"


async def test_create_expense_posts_shares_and_returns_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/create_expense"
        form = parse_qs(request.content.decode())
        assert form["cost"] == ["20.00"]
        assert form["currency_code"] == ["BRL"]
        assert form["group_id"] == ["55"]
        assert form["users__0__user_id"] == ["1"]
        assert form["users__0__paid_share"] == ["20.00"]
        assert form["users__0__owed_share"] == ["0.00"]
        assert form["users__1__owed_share"] == ["10.00"]
        return httpx.Response(200, json={"expenses": [{"id": 777}], "errors": {}})

    expense_id = await _client(handler).create_expense(
        group_id=55,
        cost_cents=2000,
        currency_code="BRL",
        description="🏆 Bolãozinho",
        shares=[
            ExpenseShare(user_id=1, paid_cents=2000, owed_cents=0),
            ExpenseShare(user_id=2, paid_cents=0, owed_cents=1000),
        ],
    )
    assert expense_id == 777


async def test_create_expense_raises_on_errors_in_200_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expenses": [], "errors": {"base": ["bad"]}})

    with pytest.raises(SplitwiseError):
        await _client(handler).create_expense(
            group_id=55,
            cost_cents=1000,
            currency_code="BRL",
            description="x",
            shares=[ExpenseShare(user_id=1, paid_cents=1000, owed_cents=0)],
        )


async def test_update_expense_posts_to_id_path() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"expenses": [{"id": 777}], "errors": {}})

    await _client(handler).update_expense(
        777,
        group_id=55,
        cost_cents=1000,
        currency_code="BRL",
        description="x",
        shares=[ExpenseShare(user_id=1, paid_cents=1000, owed_cents=0)],
    )
    assert seen["path"] == "/update_expense/777"


async def test_add_user_to_group_returns_created_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/add_user_to_group"
        form = parse_qs(request.content.decode())
        assert form["email"] == ["new@x.com"]
        assert form["first_name"] == ["Novato"]
        return httpx.Response(
            200, json={"success": True, "user": {"id": 123, "first_name": "Novato"}, "errors": {}}
        )

    user = await _client(handler).add_user_to_group(55, email="new@x.com", first_name="Novato")
    assert user.id == 123


async def test_raise_for_status_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).get_current_user()

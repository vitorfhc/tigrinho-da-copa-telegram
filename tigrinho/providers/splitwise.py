"""Async Splitwise REST client (Feature 8 / §23).

Grounding (per §2), verified 2026-06-19 against the official docs at https://dev.splitwise.com/ and
the community Python SDK https://github.com/namaggarwal/splitwise:
- Base ``https://secure.splitwise.com/api/v3.0``; auth via ``Authorization: Bearer <api_key>``.
- ``GET /get_current_user`` → ``{"user": {...}}``.
- ``GET /get_group/{id}`` → ``{"group": {"members": [{id, email, first_name, last_name, ...}]}}``.
- ``POST /add_user_to_group`` (form) with ``group_id`` + ``user_id`` OR ``email``/``first_name`` →
  ``{"success": bool, "user": {...}, "errors": {...}}``.
- ``POST /create_expense`` (form) with ``cost`` (decimal string), ``currency_code``, ``group_id``,
  ``description`` and per-user ``users__{i}__user_id`` / ``users__{i}__paid_share`` /
  ``users__{i}__owed_share`` → ``{"expenses": [{"id": ...}], "errors": {...}}``.
- ``POST /update_expense/{id}`` (form) — send only changed fields; same response shape.
- IMPORTANT: a 200 response can still carry a non-empty ``errors`` object — it MUST be checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from tigrinho.domain.splitwise_ledger import cents_to_amount


class SplitwiseError(RuntimeError):
    """Raised when Splitwise returns an error payload or an unexpected response."""


@dataclass(frozen=True, slots=True)
class SplitwiseUser:
    """A Splitwise user (the authenticated account, or one returned by add-to-group)."""

    id: int
    email: str | None
    first_name: str
    last_name: str | None


@dataclass(frozen=True, slots=True)
class SplitwiseMember:
    """One member of the configured Splitwise group (for the link picker)."""

    id: int
    email: str | None
    first_name: str
    last_name: str | None

    @property
    def display_name(self) -> str:
        """First + last name (last optional), for the picker button label."""
        return f"{self.first_name} {self.last_name}".strip() if self.last_name else self.first_name


@dataclass(frozen=True, slots=True)
class ExpenseShare:
    """One participant's paid/owed cents in an expense, keyed by Splitwise ``user_id``."""

    user_id: int
    paid_cents: int
    owed_cents: int


def _parse_user(raw: dict[str, Any]) -> SplitwiseUser:
    return SplitwiseUser(
        id=int(raw["id"]),
        email=raw.get("email"),
        first_name=str(raw.get("first_name") or ""),
        last_name=raw.get("last_name"),
    )


def _parse_member(raw: dict[str, Any]) -> SplitwiseMember:
    return SplitwiseMember(
        id=int(raw["id"]),
        email=raw.get("email"),
        first_name=str(raw.get("first_name") or ""),
        last_name=raw.get("last_name"),
    )


class SplitwiseClient:
    """Thin async wrapper over the Splitwise v3.0 REST API (caller owns best-effort handling)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if body.get("errors"):
            raise SplitwiseError(f"Splitwise error: {body['errors']}")
        return body

    async def get_current_user(self) -> SplitwiseUser:
        """The account that owns the API key (its id is never an expense participant)."""
        body = self._body(await self._client.get("/get_current_user"))
        return _parse_user(body["user"])

    async def get_group_members(self, group_id: int) -> list[SplitwiseMember]:
        """The configured group's roster (for the link picker)."""
        body = self._body(await self._client.get(f"/get_group/{group_id}"))
        members = body["group"].get("members") or []
        return [_parse_member(m) for m in members]

    async def add_user_to_group(
        self, group_id: int, *, email: str, first_name: str
    ) -> SplitwiseUser:
        """Invite/add a not-yet-member by email; returns the created/matched user (with its id)."""
        body = self._body(
            await self._client.post(
                "/add_user_to_group",
                data={"group_id": str(group_id), "email": email, "first_name": first_name},
            )
        )
        return _parse_user(body["user"])

    async def create_expense(
        self,
        *,
        group_id: int,
        cost_cents: int,
        currency_code: str,
        description: str,
        shares: Sequence[ExpenseShare],
    ) -> int:
        """Create one group expense from explicit per-user shares; returns the new expense id."""
        body = self._body(
            await self._client.post(
                "/create_expense",
                data=self._expense_data(
                    group_id=group_id,
                    cost_cents=cost_cents,
                    currency_code=currency_code,
                    description=description,
                    shares=shares,
                ),
            )
        )
        return int(body["expenses"][0]["id"])

    async def update_expense(
        self,
        expense_id: int,
        *,
        group_id: int,
        cost_cents: int,
        currency_code: str,
        description: str,
        shares: Sequence[ExpenseShare],
    ) -> None:
        """Update an existing expense in place to the corrected shares (§23 correction)."""
        self._body(
            await self._client.post(
                f"/update_expense/{expense_id}",
                data=self._expense_data(
                    group_id=group_id,
                    cost_cents=cost_cents,
                    currency_code=currency_code,
                    description=description,
                    shares=shares,
                ),
            )
        )

    @staticmethod
    def _expense_data(
        *,
        group_id: int,
        cost_cents: int,
        currency_code: str,
        description: str,
        shares: Sequence[ExpenseShare],
    ) -> dict[str, str]:
        data: dict[str, str] = {
            "cost": cents_to_amount(cost_cents),
            "currency_code": currency_code,
            "group_id": str(group_id),
            "description": description,
        }
        for i, share in enumerate(shares):
            data[f"users__{i}__user_id"] = str(share.user_id)
            data[f"users__{i}__paid_share"] = cents_to_amount(share.paid_cents)
            data[f"users__{i}__owed_share"] = cents_to_amount(share.owed_cents)
        return data

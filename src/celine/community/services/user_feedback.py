"""Participant-dashboard feedback client.

`celine-webapp` owns these rows. The manager BFF forwards the already verified browser
token and adapts that API to its same-origin feedback contract; it never reads another
service's database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from celine.community.api.schemas import (
    FeedbackItemResponse,
    FeedbackListResponse,
    FeedbackState,
)


@dataclass(frozen=True)
class Screenshot:
    content: bytes
    media_type: str


class UserFeedbackError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class UserFeedbackClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("detail") or response.text
        except ValueError:
            detail = response.text
        raise UserFeedbackError(response.status_code, detail or "User feedback unavailable")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers,
                    **kwargs,
                )
        except httpx.RequestError as exc:
            raise UserFeedbackError(502, "Participant feedback service unavailable") from exc
        self._raise(response)
        return response

    async def list(
        self,
        community_key: str,
        *,
        status: FeedbackState | None,
        page: int,
        page_size: int,
    ) -> FeedbackListResponse:
        params: dict[str, str | int] = {"page": page, "pageSize": page_size}
        if status:
            params["status"] = status
        response = await self._request(
            "GET",
            f"/api/feedback/manager/{community_key}",
            params=params,
        )
        return FeedbackListResponse.model_validate(response.json())

    async def screenshot(self, community_key: str, feedback_id: UUID) -> Screenshot:
        response = await self._request(
            "GET",
            f"/api/feedback/manager/{community_key}/{feedback_id}/screenshot",
        )
        return Screenshot(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream").split(
                ";", 1
            )[0],
        )

    async def update_status(
        self,
        community_key: str,
        feedback_id: UUID,
        status: str,
    ) -> FeedbackItemResponse:
        response = await self._request(
            "PATCH",
            f"/api/feedback/manager/{community_key}/{feedback_id}",
            json={"status": status},
        )
        return FeedbackItemResponse.model_validate(response.json())

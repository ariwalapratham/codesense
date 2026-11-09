"""Shared GitHub HTTP helper. Does not mint App installation tokens (stretch)."""

from typing import Any, Mapping, Optional, Union

import httpx

from core.errors import AppError

_TIMEOUT = 15.0
_USER_AGENT = "codesense"


class GitHubClient:
    """Thin httpx wrapper with a codesense User-Agent and JSON errors."""

    def __init__(self, http: Optional[httpx.AsyncClient] = None) -> None:
        self._http = http
        self._owns = http is None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._http

    async def aclose(self) -> None:
        """Close the owned HTTP client, if any."""
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def fetch_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json: Optional[Mapping[str, Any]] = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> Union[dict, list]:
        """Perform a JSON GitHub request. Non-2xx becomes AppError upstream_error."""
        request_headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if headers:
            request_headers.update(headers)
        try:
            response = await self._client().request(
                method,
                url,
                headers=request_headers,
                json=json,
                data=data,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                "GitHub request failed",
                code="upstream_error",
                status_code=502,
            ) from exc
        if response.status_code >= 400:
            raise AppError(
                "GitHub request failed",
                code="upstream_error",
                status_code=502,
                details={"status_code": response.status_code},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise AppError(
                "GitHub returned a non-JSON response",
                code="upstream_error",
                status_code=502,
            ) from exc
        if not isinstance(body, (dict, list)):
            raise AppError(
                "GitHub returned an unexpected payload",
                code="upstream_error",
                status_code=502,
            )
        return body

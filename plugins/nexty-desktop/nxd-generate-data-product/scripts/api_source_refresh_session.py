"""Self-contained refresh-aware requests session for generated API transforms.

Copy this module's source into a generated transform (or adapt the three
definitions in it) rather than importing it from an installed skill tree.  A
generated closure must remain self-contained after it is handed to the
supervisor.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import requests


def _headers_from(secrets: dict) -> dict:
    """Rebuild non-secret flat header attributes exactly once per config."""
    headers = {}
    for key, value in secrets.items():
        if not key.startswith("header_") or value in (None, ""):
            continue
        name = "-".join(part.title() for part in key[len("header_"):].split("_"))
        if name.lower() == "authorization":
            raise ValueError(
                "caller-supplied Authorization header is not allowed"
            )
        headers[name] = str(value)
    return headers


class RefreshingSession(requests.Session):
    """Replay one request safely after one refresh or one rate-limit response."""

    _TOKEN_FIELDS = (
        "token",
        "access_token",
        "bearer_token",
        "new_token",
        "accessToken",
        "bearerToken",
    )

    def __init__(
        self,
        *,
        base_url: str,
        auth_refresh_path: str,
        auth_token: str,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 30.0,
        max_retry_after_s: float = 30.0,
    ) -> None:
        super().__init__()
        self._base_url = self._validate_base_url(base_url)
        self._refresh_url = self._resolve_refresh_url(
            self._base_url, auth_refresh_path
        )
        if not isinstance(auth_token, str) or not auth_token.strip():
            raise ValueError("auth_token must be a non-empty string")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        if not math.isfinite(max_retry_after_s) or max_retry_after_s <= 0:
            raise ValueError("max_retry_after_s must be finite and positive")
        self._token = auth_token
        self._custom_headers = self._merge_headers(headers or {})
        self._timeout_s = float(timeout_s)
        self._max_retry_after_s = float(max_retry_after_s)

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ValueError("URL must have a valid HTTP(S) origin") from None
        if parsed.scheme.lower() not in {"http", "https"} or hostname is None:
            raise ValueError("URL must have a valid HTTP(S) origin")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL must not contain credentials")
        return parsed.scheme.lower(), hostname.lower(), port

    @classmethod
    def _validate_base_url(cls, base_url: str) -> str:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty URL")
        cls._origin(base_url)
        parsed = urlsplit(base_url)
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        return base_url.rstrip("/") + "/"

    @classmethod
    def _resolve_refresh_url(cls, base_url: str, auth_refresh_path: str) -> str:
        if not isinstance(auth_refresh_path, str) or not auth_refresh_path.strip():
            raise ValueError("auth_refresh_path must be a same-origin path")
        try:
            path = urlsplit(auth_refresh_path)
        except ValueError:
            raise ValueError("auth_refresh_path must be a same-origin path") from None
        if (
            path.scheme
            or path.netloc
            or auth_refresh_path.startswith("//")
            or path.query
            or path.fragment
        ):
            raise ValueError(
                "auth_refresh_path must be a same-origin path without query data"
            )
        resolved = urljoin(base_url, auth_refresh_path)
        if cls._origin(resolved) != cls._origin(base_url):
            raise ValueError("auth_refresh_path must resolve to base_url origin")
        return resolved

    @staticmethod
    def _replay_plan(request):
        body = request.body
        if body is None or isinstance(body, (bytes, bytearray, memoryview, str)):
            return lambda: None
        tell = getattr(body, "tell", None)
        seek = getattr(body, "seek", None)
        if not callable(tell) or not callable(seek):
            raise TypeError("request body must be replayable for an authenticated retry")
        try:
            position = tell()
            seek(position)
        except (OSError, TypeError, ValueError):
            raise TypeError("request body must be replayable for an authenticated retry") from None

        def rewind() -> None:
            try:
                seek(position)
            except (OSError, TypeError, ValueError):
                raise TypeError("request body could not be rewound for replay") from None

        return rewind

    @staticmethod
    def _retry_delay(retry_after: str | None, maximum: float) -> float:
        if retry_after is None:
            return min(1.0, maximum)
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(delay) or delay < 0:
            return 0.0
        if math.isinf(delay):
            return maximum if delay > 0 else 0.0
        return min(delay, maximum)

    @staticmethod
    def _merge_headers(*header_maps: Mapping[str, str]) -> dict[str, str]:
        """Merge headers while rejecting every caller-owned auth spelling."""
        merged: dict[str, str] = {}
        for header_map in header_maps:
            for name, value in header_map.items():
                if not isinstance(name, str):
                    raise TypeError("request headers must use string names")
                if name.lower() == "authorization":
                    raise ValueError(
                        "caller-supplied Authorization header is not allowed"
                    )
                for existing in list(merged):
                    if existing.lower() == name.lower():
                        del merged[existing]
                merged[name] = str(value)
        return merged

    @staticmethod
    def _set_header(headers: dict[str, str], name: str, value: str) -> None:
        for existing in list(headers):
            if existing.lower() == name.lower():
                del headers[existing]
        headers[name] = value

    def _refresh_token(self, timeout: float) -> str:
        refresh_headers = self._merge_headers(
            {"Accept": "application/json"}, self._custom_headers
        )
        self._set_header(
            refresh_headers, "Authorization", f"Bearer {self._token}"
        )
        refresh_request = requests.Request(
            "POST",
            self._refresh_url,
            headers=refresh_headers,
        ).prepare()
        try:
            response = super().send(
                refresh_request,
                timeout=timeout,
                allow_redirects=False,
            )
        except Exception:
            raise RuntimeError("token refresh request failed") from None
        try:
            if not 200 <= response.status_code < 300:
                raise RuntimeError("token refresh returned a non-2xx status")
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                raise RuntimeError("token refresh returned invalid JSON") from None
            if not isinstance(payload, Mapping):
                raise RuntimeError("token refresh JSON must be an object")
            present = [
                (field, payload[field])
                for field in self._TOKEN_FIELDS
                if field in payload
            ]
            if not present:
                return self._token
            for field, value in present:
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError(
                        f"token refresh field {field} must be a non-empty string"
                    )
            values = {value.strip() for _, value in present}
            if len(values) != 1:
                raise RuntimeError("token refresh fields contain conflicting values")
            return values.pop()
        finally:
            response.close()

    def send(self, request, **kwargs):
        if self._origin(request.url) != self._origin(self._base_url):
            raise ValueError("request URL must be same-origin with base_url")
        rewind = self._replay_plan(request)
        # Connector headers win over defaults already prepared by dlt; the
        # session-owned Authorization is added after this merge below.
        request.headers = self._merge_headers(request.headers, self._custom_headers)
        timeout = kwargs.get("timeout", self._timeout_s)
        if timeout is None or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        send_kwargs = dict(kwargs)
        send_kwargs["timeout"] = timeout
        # Returning redirects to the caller prevents requests from carrying the
        # bearer to another origin. The refresh request is also forced through
        # this setting in _refresh_token.
        send_kwargs["allow_redirects"] = False
        refreshed = False
        rate_retried = False
        while True:
            self._set_header(
                request.headers, "Authorization", f"Bearer {self._token}"
            )
            response = super().send(request, **send_kwargs)
            if response.status_code == 401:
                if refreshed:
                    response.close()
                    raise RuntimeError("request remained unauthorized after one refresh")
                response.close()
                self._token = self._refresh_token(timeout)
                refreshed = True
                rewind()
                continue
            if response.status_code == 429 and not rate_retried:
                delay = self._retry_delay(
                    response.headers.get("Retry-After"), self._max_retry_after_s
                )
                response.close()
                time.sleep(delay)
                rate_retried = True
                rewind()
                continue
            return response


def make_rest_api_config(secrets: Mapping[str, str], resources: Sequence[Mapping]):
    """Build the dlt config without moving credentials into source code."""
    headers = _headers_from(secrets)
    session = RefreshingSession(
        base_url=secrets["base_url"],
        auth_refresh_path=secrets["auth_refresh_path"],
        auth_token=secrets["auth_token"],
        headers=headers,
    )
    client = {"base_url": secrets["base_url"], "session": session}
    if headers:
        client["headers"] = headers
    return {
        "client": client,
        "resources": list(resources),
    }

"""Small, bounded HTTPS/JSON transport for explicitly trusted API hosts."""

from __future__ import annotations

import http.client
import json
import re
import ssl
from contextlib import suppress
from typing import Any, Protocol
from urllib.parse import urlsplit

_ALLOWED_HOSTS = frozenset({"api.github.com"})
_ALLOWED_METHODS = frozenset({"GET", "POST"})
_API_VERSION_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_PATH_CHARACTERS = 8_192
_MAX_REQUEST_BYTES = 512 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "WorkflowPromptGuard/0.2"


class GitHubServiceError(RuntimeError):
    """A bounded HTTPS service request failed or returned invalid data."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JsonTransport(Protocol):
    """Protocol shared by fixed-host JSON API clients."""

    def request_json(
        self,
        *,
        host: str,
        path: str,
        method: str = "GET",
        payload: object | None = None,
        api_version: str = "2022-11-28",
    ) -> Any:
        """Send one fixed-host HTTPS request and decode its JSON response."""


class HTTPSJsonTransport:
    """Perform capped JSON requests to an explicit host allowlist."""

    def __init__(self, token: str | None = None) -> None:
        if token is not None and (
            not token
            or len(token) > 4_096
            or any(character.isspace() or ord(character) < 32 for character in token)
        ):
            raise ValueError("GitHub token has an invalid format")
        self._token = token

    def request_json(
        self,
        *,
        host: str,
        path: str,
        method: str = "GET",
        payload: object | None = None,
        api_version: str = "2022-11-28",
    ) -> Any:
        """Send one HTTPS request without redirects and return decoded JSON."""

        normalized_method = method.upper()
        self._validate_destination(
            host=host,
            path=path,
            method=normalized_method,
            api_version=api_version,
        )
        body = self._encode_payload(payload)
        if normalized_method == "GET" and body is not None:
            raise GitHubServiceError("GET requests cannot include a JSON payload")

        headers = self._headers(host=host, api_version=api_version, has_body=body is not None)
        raw_response = self._perform_request(
            host=host,
            path=path,
            method=normalized_method,
            body=body,
            headers=headers,
        )
        return self._decode_response(raw_response)

    def _headers(
        self,
        *,
        host: str,
        api_version: str,
        has_body: bool,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": api_version,
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _perform_request(
        *,
        host: str,
        path: str,
        method: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> bytes:
        connection: http.client.HTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            context = ssl.create_default_context()
            connection = http.client.HTTPSConnection(
                host,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                context=context,
            )
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            HTTPSJsonTransport._validate_status(host=host, status=response.status)
            return HTTPSJsonTransport._read_response(response)
        except GitHubServiceError:
            raise
        except Exception:
            # Deliberately do not expose request headers, payloads, or exception text.
            raise GitHubServiceError(f"HTTPS request to {host} failed") from None
        finally:
            if response is not None:
                with suppress(Exception):
                    response.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

    @staticmethod
    def _decode_response(raw_response: bytes) -> Any:
        try:
            decoded: Any = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise GitHubServiceError("HTTPS service returned invalid JSON") from None
        return decoded

    @staticmethod
    def _validate_status(*, host: str, status: int) -> None:
        if 300 <= status < 400:
            raise GitHubServiceError(
                f"redirects are not permitted for HTTPS requests to {host}",
                status=status,
            )
        if status < 200 or status >= 300:
            raise GitHubServiceError(
                f"HTTPS service request to {host} failed with HTTP {status}",
                status=status,
            )

    @staticmethod
    def _read_response(response: http.client.HTTPResponse) -> bytes:
        HTTPSJsonTransport._validate_content_type(response)
        content_length = HTTPSJsonTransport._content_length(response)
        if content_length is not None and content_length > _MAX_RESPONSE_BYTES:
            raise GitHubServiceError("HTTPS service response exceeds the size limit")
        raw_response = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw_response) > _MAX_RESPONSE_BYTES:
            raise GitHubServiceError("HTTPS service response exceeds the size limit")
        return raw_response

    @staticmethod
    def _validate_content_type(response: http.client.HTTPResponse) -> None:
        value = response.getheader("Content-Type")
        if not isinstance(value, str):
            raise GitHubServiceError("HTTPS service response had an invalid Content-Type")
        media_type = value.partition(";")[0].strip().lower()
        if media_type != "application/json" and not (
            media_type.startswith("application/") and media_type.endswith("+json")
        ):
            raise GitHubServiceError("HTTPS service response had an invalid Content-Type")

    @staticmethod
    def _validate_destination(
        *,
        host: str,
        path: str,
        method: str,
        api_version: str,
    ) -> None:
        if host not in _ALLOWED_HOSTS:
            raise GitHubServiceError("HTTPS host is not permitted")
        if method not in _ALLOWED_METHODS:
            raise GitHubServiceError("HTTPS method is not permitted")
        if _API_VERSION_RE.fullmatch(api_version) is None:
            raise GitHubServiceError("GitHub API version has an invalid format")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or len(path) > _MAX_PATH_CHARACTERS
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            raise GitHubServiceError("HTTPS request path is invalid")
        try:
            parsed = urlsplit(path)
        except ValueError:
            raise GitHubServiceError("HTTPS request path is invalid") from None
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise GitHubServiceError("HTTPS request path is invalid")

    @staticmethod
    def _encode_payload(payload: object | None) -> bytes | None:
        if payload is None:
            return None
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise GitHubServiceError("request payload is not valid JSON") from None
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise GitHubServiceError("request payload exceeds the size limit")
        return encoded

    @staticmethod
    def _content_length(response: http.client.HTTPResponse) -> int | None:
        value = response.getheader("Content-Length")
        if value is None:
            return None
        try:
            length = int(value, 10)
        except ValueError:
            raise GitHubServiceError("HTTPS service returned an invalid Content-Length") from None
        if length < 0:
            raise GitHubServiceError("HTTPS service returned an invalid Content-Length")
        return length


class AnonymousLLM7Transport:
    """Call exactly one anonymous LLM7.io endpoint without accepting credentials."""

    def request_json(
        self,
        *,
        host: str,
        path: str,
        method: str = "GET",
        payload: object | None = None,
        api_version: str = "2022-11-28",
    ) -> Any:
        """POST one capped JSON document to the fixed anonymous inference endpoint."""

        if (
            host != "api.llm7.io"
            or path != "/v1/chat/completions"
            or method != "POST"
            or api_version != "2022-11-28"
        ):
            raise GitHubServiceError("anonymous AI destination is not permitted")
        body = HTTPSJsonTransport._encode_payload(payload)
        if body is None:
            raise GitHubServiceError("anonymous AI request requires a JSON payload")
        raw_response = HTTPSJsonTransport._perform_request(
            host=host,
            path=path,
            method="POST",
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        return HTTPSJsonTransport._decode_response(raw_response)

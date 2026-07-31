"""Tests for the bounded fixed-host HTTPS JSON transport."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

from workflow_prompt_guard.github_api import (
    AnonymousLLM7Transport,
    GitHubServiceError,
    HTTPSJsonTransport,
)


class FakeResponse:
    """Minimal ``HTTPResponse`` fake used by the transport tests."""

    def __init__(
        self,
        body: bytes = b'{"ok":true}',
        *,
        status: int = 200,
        content_length: str | None = None,
        content_type: str | None = "application/json",
    ) -> None:
        self.body = body
        self.status = status
        self.content_length = content_length
        self.content_type = content_type
        self.closed = False

    def getheader(self, name: str) -> str | None:
        if name == "Content-Length":
            return self.content_length
        if name == "Content-Type":
            return self.content_type
        return None

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self.body
        return self.body[:amount]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Capture requests while returning a configurable response."""

    response: ClassVar[FakeResponse] = FakeResponse()
    request_error: ClassVar[Exception | None] = None
    instances: ClassVar[list[FakeConnection]] = []

    def __init__(self, host: str, *, timeout: float, context: object) -> None:
        self.host = host
        self.timeout = timeout
        self.context = context
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False
        self.__class__.instances.append(self)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        if self.request_error is not None:
            raise self.request_error
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_https_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.response = FakeResponse()
    FakeConnection.request_error = None
    FakeConnection.instances = []
    monkeypatch.setattr(
        "workflow_prompt_guard.github_api.http.client.HTTPSConnection",
        FakeConnection,
    )


def test_anonymous_llm7_post_uses_exact_endpoint_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "github-token-that-must-never-leave-github"
    monkeypatch.setenv("GITHUB_TOKEN", canary)
    transport = AnonymousLLM7Transport()

    result = transport.request_json(
        host="api.llm7.io",
        path="/v1/chat/completions",
        method="POST",
        payload={"model": "default", "stream": False},
    )

    connection = FakeConnection.instances[0]
    method, path, body, headers = connection.requests[0]
    assert result == {"ok": True}
    assert connection.host == "api.llm7.io"
    assert connection.timeout == 15.0
    assert method == "POST"
    assert path == "/v1/chat/completions"
    assert json.loads(body or b"")["stream"] is False
    assert headers == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "WorkflowPromptGuard/0.2",
    }
    serialized_request = (body or b"").decode("utf-8") + json.dumps(headers)
    assert canary not in serialized_request
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "X-GitHub-Api-Version" not in headers
    assert connection.closed
    assert FakeConnection.response.closed


def test_tokened_github_transport_rejects_llm7_before_connecting() -> None:
    with pytest.raises(GitHubServiceError, match="host"):
        HTTPSJsonTransport("github-token").request_json(
            host="api.llm7.io",
            path="/v1/chat/completions",
            method="POST",
            payload={"model": "default"},
        )

    assert not FakeConnection.instances


@pytest.mark.parametrize(
    ("host", "path", "method"),
    [
        ("api.llm7.io", "/v1/chat/completions/", "POST"),
        ("api.llm7.io", "/v1/chat/completions?debug=true", "POST"),
        ("api.llm7.io", "/v1/models", "POST"),
        ("api.llm7.io", "/v1/chat/completions", "GET"),
        ("api.llm7.io", "/v1/chat/completions", "post"),
        ("models.github.ai", "/inference/chat/completions", "POST"),
        ("evil.example", "/v1/chat/completions", "POST"),
    ],
)
def test_anonymous_llm7_transport_rejects_every_other_destination_before_connecting(
    host: str,
    path: str,
    method: str,
) -> None:
    with pytest.raises(GitHubServiceError, match="destination"):
        AnonymousLLM7Transport().request_json(
            host=host,
            path=path,
            method=method,
            payload={"model": "default"},
        )

    assert not FakeConnection.instances


def test_anonymous_llm7_transport_rejects_wrong_api_version_before_connecting() -> None:
    with pytest.raises(GitHubServiceError, match="destination"):
        AnonymousLLM7Transport().request_json(
            host="api.llm7.io",
            path="/v1/chat/completions",
            method="POST",
            payload={"model": "default"},
            api_version="2023-01-01",
        )

    assert not FakeConnection.instances


def test_github_api_request_sets_version_without_token() -> None:
    transport = HTTPSJsonTransport()

    transport.request_json(host="api.github.com", path="/repos/octo/example")

    _, _, body, headers = FakeConnection.instances[0].requests[0]
    assert body is None
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "Authorization" not in headers


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"host": "example.com", "path": "/repos/a/b"}, "host"),
        ({"host": "api.github.com", "path": "https://evil.test/"}, "path"),
        ({"host": "api.github.com", "path": "//evil.test/"}, "path"),
        ({"host": "api.github.com", "path": "/safe#fragment"}, "path"),
        ({"host": "api.github.com", "path": "/safe\r\nX-Evil: yes"}, "path"),
        ({"host": "api.github.com", "path": "/safe", "method": "DELETE"}, "method"),
        (
            {"host": "api.github.com", "path": "/safe", "api_version": "latest"},
            "version",
        ),
    ],
)
def test_destination_validation_rejects_untrusted_values(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    transport = HTTPSJsonTransport()

    with pytest.raises(GitHubServiceError, match=message):
        transport.request_json(**kwargs)

    assert not FakeConnection.instances


def test_redirect_is_rejected_without_following_location() -> None:
    FakeConnection.response = FakeResponse(status=302)

    with pytest.raises(GitHubServiceError, match="redirects") as caught:
        HTTPSJsonTransport().request_json(host="api.github.com", path="/redirect")

    assert caught.value.status == 302
    assert len(FakeConnection.instances) == 1


def test_http_error_exposes_only_the_numeric_status() -> None:
    canary = "remote-body-canary"
    FakeConnection.response = FakeResponse(canary.encode(), status=404)

    with pytest.raises(GitHubServiceError) as caught:
        HTTPSJsonTransport().request_json(host="api.github.com", path="/missing")

    assert caught.value.status == 404
    assert canary not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(content_length=str(2 * 1024 * 1024 + 1)),
        FakeResponse(body=b"x" * (2 * 1024 * 1024 + 1)),
    ],
)
def test_response_size_is_bounded(response: FakeResponse) -> None:
    FakeConnection.response = response

    with pytest.raises(GitHubServiceError, match="size limit"):
        HTTPSJsonTransport().request_json(host="api.github.com", path="/large")


def test_invalid_json_and_content_length_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.response = FakeResponse(b"not-json")
    with pytest.raises(GitHubServiceError, match="invalid JSON"):
        HTTPSJsonTransport().request_json(host="api.github.com", path="/invalid")

    FakeConnection.response = FakeResponse(content_length="unknown")
    with pytest.raises(GitHubServiceError, match="Content-Length"):
        HTTPSJsonTransport().request_json(host="api.github.com", path="/invalid")

    def recursive_parse(_: str) -> Any:
        raise RecursionError

    monkeypatch.setattr("workflow_prompt_guard.github_api.json.loads", recursive_parse)
    FakeConnection.response = FakeResponse()
    with pytest.raises(GitHubServiceError, match="invalid JSON"):
        HTTPSJsonTransport().request_json(host="api.github.com", path="/invalid")


@pytest.mark.parametrize("content_type", [None, "text/html", "text/plain; charset=utf-8"])
def test_success_response_requires_a_json_content_type(content_type: str | None) -> None:
    FakeConnection.response = FakeResponse(content_type=content_type)

    with pytest.raises(GitHubServiceError, match="Content-Type"):
        AnonymousLLM7Transport().request_json(
            host="api.llm7.io",
            path="/v1/chat/completions",
            method="POST",
            payload={"model": "default"},
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        "application/vnd.github+json",
        "application/problem+json; charset=utf-8",
    ],
)
def test_json_and_structured_json_content_types_are_accepted(content_type: str) -> None:
    FakeConnection.response = FakeResponse(content_type=content_type)

    assert HTTPSJsonTransport().request_json(host="api.github.com", path="/repos/a/b") == {
        "ok": True
    }


def test_transport_errors_do_not_expose_token() -> None:
    canary = "token-value-that-must-not-leak"
    FakeConnection.request_error = RuntimeError(f"network failed with {canary}")

    with pytest.raises(GitHubServiceError) as caught:
        HTTPSJsonTransport(canary).request_json(host="api.github.com", path="/repos/a/b")

    assert canary not in str(caught.value)


def test_payload_rules_are_bounded_and_strict() -> None:
    github_transport = HTTPSJsonTransport()
    llm7_transport = AnonymousLLM7Transport()

    with pytest.raises(GitHubServiceError, match="GET"):
        github_transport.request_json(host="api.github.com", path="/safe", payload={})
    with pytest.raises(GitHubServiceError, match="valid JSON"):
        llm7_transport.request_json(
            host="api.llm7.io",
            path="/v1/chat/completions",
            method="POST",
            payload={"not_finite": float("nan")},
        )
    with pytest.raises(GitHubServiceError, match="size limit"):
        llm7_transport.request_json(
            host="api.llm7.io",
            path="/v1/chat/completions",
            method="POST",
            payload={"large": "x" * (512 * 1024)},
        )


@pytest.mark.parametrize("token", ["", "contains whitespace", "line\rbreak"])
def test_invalid_token_format_is_rejected(token: str) -> None:
    with pytest.raises(ValueError, match="invalid format"):
        HTTPSJsonTransport(token)

"""Tests for public repository workflow snapshot retrieval."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from workflow_prompt_guard.github_api import GitHubServiceError
from workflow_prompt_guard.remote_repository import (
    MAX_CONTENTS_ENTRIES,
    MAX_SNAPSHOT_BYTES,
    MAX_WORKFLOW_BYTES,
    MAX_WORKFLOW_FILES,
    GitHubRepositoryClient,
    RemoteSnapshot,
    RepositoryRef,
    RepositoryRequestError,
    parse_repository_request,
)

COMMIT_SHA = "a" * 40
BLOB_SHA = "b" * 40


class FakeTransport:
    """Path-keyed JSON transport fake."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, str, object | None, str]] = []

    def request_json(
        self,
        *,
        host: str,
        path: str,
        method: str = "GET",
        payload: object | None = None,
        api_version: str = "2022-11-28",
    ) -> Any:
        self.calls.append((host, path, method, payload, api_version))
        try:
            response = self.responses[path]
        except KeyError:
            raise AssertionError(f"unexpected API path: {path}") from None
        if isinstance(response, Exception):
            raise response
        return response


def _listing_entry(
    name: str,
    *,
    entry_type: str = "file",
    path: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": path or f".github/workflows/{name}",
        "type": entry_type,
        **extra,
    }


def _file_response(
    name: str,
    raw_content: bytes = b"name: CI\n",
    *,
    path: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": path or f".github/workflows/{name}",
        "type": "file",
        "size": len(raw_content),
        "encoding": "base64",
        "content": base64.b64encode(raw_content).decode("ascii"),
        "sha": BLOB_SHA,
        **extra,
    }


def _base_responses(
    listing: list[dict[str, Any]],
    *,
    branch: str = "main",
    repository_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repository = {
        "private": False,
        "visibility": "public",
        "default_branch": branch,
        **(repository_overrides or {}),
    }
    encoded_branch = branch.replace("/", "%2F")
    return {
        "/repos/octo/example": repository,
        f"/repos/octo/example/branches/{encoded_branch}": {"commit": {"sha": COMMIT_SHA}},
        f"/repos/octo/example/contents/.github/workflows?ref={COMMIT_SHA}": listing,
    }


def _client(
    listing: list[dict[str, Any]],
    *,
    branch: str = "main",
    repository_overrides: dict[str, Any] | None = None,
) -> tuple[GitHubRepositoryClient, FakeTransport, dict[str, Any]]:
    responses = _base_responses(
        listing,
        branch=branch,
        repository_overrides=repository_overrides,
    )
    transport = FakeTransport(responses)
    return GitHubRepositoryClient(transport), transport, responses


def test_parse_repository_request_requires_one_exact_standalone_url() -> None:
    ref = parse_repository_request(
        "Please scan this repository:\n\n  https://github.com/Octo/example_repo \nThanks."
    )

    assert ref == RepositoryRef(
        full_name="Octo/example_repo",
        canonical_url="https://github.com/Octo/example_repo",
    )


@pytest.mark.parametrize(
    "text",
    [
        "scan https://github.com/octo/example please",
        "https://github.com/octo/example/",
        "https://github.com/octo/example?tab=readme",
        "http://github.com/octo/example",
        "https://evil.test/octo/example",
        "https://github.com/-invalid/example",
        "https://github.com/octo/..",
        pytest.param("x" * 65_537, id="oversized-body"),
    ],
)
def test_parse_repository_request_rejects_noncanonical_input(text: str) -> None:
    with pytest.raises(RepositoryRequestError):
        parse_repository_request(text)


def test_parse_repository_request_rejects_multiple_urls() -> None:
    text = "https://github.com/a/one\nhttps://github.com/b/two"

    with pytest.raises(RepositoryRequestError, match="more than one"):
        parse_repository_request(text)


def test_fetches_only_direct_supported_files_at_pinned_commit() -> None:
    listing = [
        _listing_entry("z-review.md"),
        _listing_entry("notes.txt"),
        _listing_entry("nested", entry_type="dir"),
        _listing_entry("ci.yml"),
    ]
    client, transport, responses = _client(listing, branch="feature/default")
    responses[f"/repos/octo/example/contents/.github/workflows/ci.yml?ref={COMMIT_SHA}"] = (
        _file_response("ci.yml", b"name: CI\n")
    )
    responses[f"/repos/octo/example/contents/.github/workflows/z-review.md?ref={COMMIT_SHA}"] = (
        _file_response("z-review.md", b"---\non: issues\n---\n")
    )

    snapshot = client.fetch_public_workflows(
        RepositoryRef("octo/example", "https://github.com/octo/example")
    )

    assert isinstance(snapshot, RemoteSnapshot)
    assert snapshot.repository == RepositoryRef(
        "octo/example",
        "https://github.com/octo/example",
    )
    assert snapshot.default_branch == "feature/default"
    assert snapshot.commit_sha == COMMIT_SHA
    assert [workflow.path for workflow in snapshot.workflows] == [
        ".github/workflows/ci.yml",
        ".github/workflows/z-review.md",
    ]
    assert snapshot.workflows[0].content == b"name: CI\n"
    assert snapshot.workflows[0].sha == BLOB_SHA
    assert all(call[0] == "api.github.com" for call in transport.calls)
    assert transport.calls[1][1].endswith("/branches/feature%2Fdefault")
    assert all(f"ref={COMMIT_SHA}" in call[1] for call in transport.calls[2:])


@pytest.mark.parametrize(
    "repository_overrides",
    [
        {"private": True},
        {"private": False, "visibility": "internal"},
        {"private": None},
    ],
)
def test_nonpublic_or_ambiguous_visibility_is_rejected(
    repository_overrides: dict[str, Any],
) -> None:
    client, _, _ = _client([], repository_overrides=repository_overrides)

    with pytest.raises(GitHubServiceError, match="public"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )


@pytest.mark.parametrize(
    "ref",
    [
        RepositoryRef("../example", "https://github.com/../example"),
        RepositoryRef("octo/example/extra", "https://github.com/octo/example/extra"),
        RepositoryRef("octo/example", "https://github.com/evil/example"),
    ],
)
def test_constructed_repository_reference_is_revalidated(ref: RepositoryRef) -> None:
    client = GitHubRepositoryClient(FakeTransport({}))

    with pytest.raises(RepositoryRequestError, match="invalid"):
        client.fetch_public_workflows(ref)


def test_invalid_commit_sha_is_rejected_before_contents_request() -> None:
    client, transport, responses = _client([])
    responses["/repos/octo/example/branches/main"] = {"commit": {"sha": "main"}}

    with pytest.raises(GitHubServiceError, match="SHA"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )

    assert len(transport.calls) == 2


def test_missing_workflow_directory_returns_empty_pinned_snapshot() -> None:
    client, transport, responses = _client([])
    listing_path = f"/repos/octo/example/contents/.github/workflows?ref={COMMIT_SHA}"
    responses[listing_path] = GitHubServiceError("not found", status=404)

    snapshot = client.fetch_public_workflows(
        RepositoryRef("octo/example", "https://github.com/octo/example")
    )

    assert snapshot.commit_sha == COMMIT_SHA
    assert snapshot.workflows == ()
    assert len(transport.calls) == 3


def test_file_count_is_capped_before_blob_requests() -> None:
    listing = [_listing_entry(f"workflow-{index}.yml") for index in range(MAX_WORKFLOW_FILES + 1)]
    client, transport, _ = _client(listing)

    with pytest.raises(GitHubServiceError, match="too many"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )

    assert len(transport.calls) == 3


def test_contents_api_limit_fails_closed_before_hidden_workflows_can_be_missed() -> None:
    listing = [_listing_entry(f"junk-{index}.txt") for index in range(MAX_CONTENTS_ENTRIES)]
    client, transport, _ = _client(listing)

    with pytest.raises(GitHubServiceError, match="may be truncated"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )

    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "entry",
    [
        _listing_entry("link.yml", entry_type="symlink", target="../outside"),
        _listing_entry(
            "module.yml",
            entry_type="file",
            submodule_git_url="https://github.com/evil/module",
        ),
        _listing_entry(
            "nested.yml",
            path=".github/workflows/nested/escape.yml",
        ),
        _listing_entry("bad.yml", entry_type="fifo"),
    ],
)
def test_unsafe_directory_entries_are_rejected(entry: dict[str, Any]) -> None:
    client, _, _ = _client([entry])

    with pytest.raises(GitHubServiceError):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"encoding": "utf-8"},
        {"content": "not base64!"},
        {"size": True},
        {"size": MAX_WORKFLOW_BYTES + 1},
        {"sha": "not-a-sha"},
        {"type": "symlink", "target": "../outside"},
        {"path": ".github/workflows/other.yml"},
    ],
)
def test_unsafe_or_malformed_file_response_is_rejected(overrides: dict[str, Any]) -> None:
    client, _, responses = _client([_listing_entry("ci.yml")])
    responses[f"/repos/octo/example/contents/.github/workflows/ci.yml?ref={COMMIT_SHA}"] = (
        _file_response("ci.yml", **overrides)
    )

    with pytest.raises(GitHubServiceError):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )


def test_declared_and_decoded_file_sizes_must_match() -> None:
    client, _, responses = _client([_listing_entry("ci.yml")])
    responses[f"/repos/octo/example/contents/.github/workflows/ci.yml?ref={COMMIT_SHA}"] = (
        _file_response("ci.yml", b"content", size=1)
    )

    with pytest.raises(GitHubServiceError, match="does not match"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )


def test_total_snapshot_size_is_capped() -> None:
    file_count = MAX_SNAPSHOT_BYTES // MAX_WORKFLOW_BYTES + 1
    listing = [_listing_entry(f"workflow-{index}.yml") for index in range(file_count)]
    client, transport, responses = _client(listing)
    content = b"x" * MAX_WORKFLOW_BYTES
    for entry in listing:
        name = entry["name"]
        path = entry["path"]
        responses[f"/repos/octo/example/contents/{path}?ref={COMMIT_SHA}"] = _file_response(
            name, content
        )

    with pytest.raises(GitHubServiceError, match="total size"):
        client.fetch_public_workflows(
            RepositoryRef("octo/example", "https://github.com/octo/example")
        )

    assert len(transport.calls) == 3 + file_count

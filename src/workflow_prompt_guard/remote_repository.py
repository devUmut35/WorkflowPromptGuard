"""Secure retrieval of public GitHub workflow files at an immutable commit."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from workflow_prompt_guard.github_api import GitHubServiceError, JsonTransport

_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_REPOSITORY_URL_RE = re.compile(
    r"[ \t]*https://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/"
    r"(?P<repository>[A-Za-z0-9._-]{1,100})[ \t]*"
)
_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_ALLOWED_SUFFIXES = (".yml", ".yaml", ".md")
_WORKFLOW_DIRECTORY = ".github/workflows"
_MAX_REQUEST_TEXT_CHARACTERS = 65_536
MAX_WORKFLOW_FILES = 64
MAX_WORKFLOW_BYTES = 256 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_CONTENTS_ENTRIES = 1_000
_MAX_ENCODED_FILE_CHARACTERS = MAX_WORKFLOW_BYTES * 2


class RepositoryRequestError(ValueError):
    """An issue body did not contain one unambiguous supported repository URL."""


@dataclass(frozen=True)
class RepositoryRef:
    """Canonical identity for one GitHub repository."""

    full_name: str
    canonical_url: str


@dataclass(frozen=True)
class RemoteWorkflow:
    """One workflow blob fetched from GitHub's Contents API."""

    path: str
    content: bytes
    sha: str


@dataclass(frozen=True)
class RemoteSnapshot:
    """A repository workflow snapshot pinned to one immutable commit."""

    repository: RepositoryRef
    default_branch: str
    commit_sha: str
    workflows: tuple[RemoteWorkflow, ...]


def parse_repository_request(text: str) -> RepositoryRef:
    """Extract exactly one canonical GitHub repository URL on its own line."""

    if len(text) > _MAX_REQUEST_TEXT_CHARACTERS:
        raise RepositoryRequestError("issue body is too large")

    matches: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _REPOSITORY_URL_RE.fullmatch(line)
        if match is not None:
            matches.append((match.group("owner"), match.group("repository")))

    if not matches:
        raise RepositoryRequestError(
            "add exactly one https://github.com/OWNER/REPOSITORY URL on its own line"
        )
    if len(matches) != 1:
        raise RepositoryRequestError("issue body contains more than one repository URL")

    owner, repository = matches[0]
    if repository in {".", ".."}:
        raise RepositoryRequestError("repository name is invalid")
    full_name = f"{owner}/{repository}"
    return RepositoryRef(
        full_name=full_name,
        canonical_url=f"https://github.com/{full_name}",
    )


class GitHubRepositoryClient:
    """Fetch bounded public workflow content through GitHub's Contents API."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def fetch_public_workflows(self, ref: RepositoryRef) -> RemoteSnapshot:
        """Return direct workflow files from the public repository's default branch."""

        owner, repository = _validate_repository_ref(ref)
        encoded_full_name = f"{quote(owner, safe='')}/{quote(repository, safe='')}"

        repository_data = self._request(f"/repos/{encoded_full_name}")
        repository_object = _require_object(repository_data, "repository metadata")
        self._require_public(repository_object)
        default_branch = _require_branch_name(repository_object.get("default_branch"))

        encoded_branch = quote(default_branch, safe="")
        branch_data = self._request(f"/repos/{encoded_full_name}/branches/{encoded_branch}")
        branch_object = _require_object(branch_data, "default branch metadata")
        commit_object = _require_object(branch_object.get("commit"), "default branch commit")
        commit_sha = _require_sha(commit_object.get("sha"), "default branch commit")

        query = urlencode({"ref": commit_sha})
        try:
            listing_data = self._request(
                f"/repos/{encoded_full_name}/contents/{_WORKFLOW_DIRECTORY}?{query}"
            )
        except GitHubServiceError as exc:
            if exc.status != 404:
                raise
            listing_data = []
        listing = _require_list(listing_data, "workflow directory")
        if len(listing) >= MAX_CONTENTS_ENTRIES:
            raise GitHubServiceError("workflow directory listing may be truncated")
        candidates = self._workflow_candidates(listing)

        workflows: list[RemoteWorkflow] = []
        total_bytes = 0
        for path in candidates:
            encoded_path = quote(path, safe="/")
            file_data = self._request(f"/repos/{encoded_full_name}/contents/{encoded_path}?{query}")
            workflow = self._decode_workflow(file_data, expected_path=path)
            total_bytes += len(workflow.content)
            if total_bytes > MAX_SNAPSHOT_BYTES:
                raise GitHubServiceError("workflow snapshot exceeds the total size limit")
            workflows.append(workflow)

        return RemoteSnapshot(
            repository=ref,
            default_branch=default_branch,
            commit_sha=commit_sha,
            workflows=tuple(workflows),
        )

    def _request(self, path: str) -> Any:
        return self._transport.request_json(host="api.github.com", path=path)

    @staticmethod
    def _require_public(repository: dict[str, Any]) -> None:
        private = repository.get("private")
        if private is True:
            raise GitHubServiceError("only public GitHub repositories can be scanned")
        if private is not False:
            raise GitHubServiceError("repository metadata does not declare public visibility")
        visibility = repository.get("visibility")
        if visibility is not None and visibility != "public":
            raise GitHubServiceError("only public GitHub repositories can be scanned")

    @staticmethod
    def _workflow_candidates(listing: list[Any]) -> tuple[str, ...]:
        candidates: list[str] = []
        seen_paths: set[str] = set()
        for value in listing:
            entry = _require_object(value, "workflow directory entry")
            path, name = _require_direct_path(entry)
            if path in seen_paths:
                raise GitHubServiceError("workflow directory contains duplicate paths")
            seen_paths.add(path)

            entry_type = entry.get("type")
            if _is_link_or_submodule(entry, entry_type):
                raise GitHubServiceError("workflow links and submodules are not permitted")
            if entry_type == "dir":
                continue
            if entry_type != "file":
                raise GitHubServiceError("workflow directory contains an unsupported entry type")
            if name.endswith(_ALLOWED_SUFFIXES):
                candidates.append(path)

        if len(candidates) > MAX_WORKFLOW_FILES:
            raise GitHubServiceError("repository contains too many workflow files")
        return tuple(sorted(candidates))

    @staticmethod
    def _decode_workflow(value: Any, *, expected_path: str) -> RemoteWorkflow:
        entry = _require_object(value, "workflow file")
        path, name = _require_direct_path(entry)
        if path != expected_path or not name.endswith(_ALLOWED_SUFFIXES):
            raise GitHubServiceError("workflow file path does not match the requested path")

        entry_type = entry.get("type")
        if _is_link_or_submodule(entry, entry_type) or entry_type != "file":
            raise GitHubServiceError("workflow response is not a regular file")

        size = entry.get("size")
        if type(size) is not int or size < 0:
            raise GitHubServiceError("workflow file has an invalid size")
        if size > MAX_WORKFLOW_BYTES:
            raise GitHubServiceError("workflow file exceeds the size limit")
        if entry.get("encoding") != "base64":
            raise GitHubServiceError("workflow file is not base64 encoded")

        encoded_content = entry.get("content")
        if not isinstance(encoded_content, str):
            raise GitHubServiceError("workflow file content is missing")
        if len(encoded_content) > _MAX_ENCODED_FILE_CHARACTERS:
            raise GitHubServiceError("workflow file encoding exceeds the size limit")
        content = _decode_base64(encoded_content)
        if len(content) != size:
            raise GitHubServiceError("workflow file size does not match its content")
        if len(content) > MAX_WORKFLOW_BYTES:
            raise GitHubServiceError("workflow file exceeds the size limit")

        sha = _require_sha(entry.get("sha"), "workflow blob")
        return RemoteWorkflow(path=path, content=content, sha=sha)


def _validate_repository_ref(ref: RepositoryRef) -> tuple[str, str]:
    parts = ref.full_name.split("/")
    if len(parts) != 2:
        raise RepositoryRequestError("repository reference is invalid")
    owner, repository = parts
    if (
        _OWNER_RE.fullmatch(owner) is None
        or _REPOSITORY_RE.fullmatch(repository) is None
        or repository in {".", ".."}
        or ref.canonical_url != f"https://github.com/{owner}/{repository}"
    ):
        raise RepositoryRequestError("repository reference is invalid")
    return owner, repository


def _require_branch_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise GitHubServiceError("repository has an invalid default branch")
    return value


def _require_sha(value: Any, subject: str) -> str:
    if not isinstance(value, str) or _COMMIT_SHA_RE.fullmatch(value) is None:
        raise GitHubServiceError(f"{subject} has an invalid SHA")
    return value.lower()


def _require_object(value: Any, subject: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise GitHubServiceError(f"{subject} is malformed")
    return value


def _require_list(value: Any, subject: str) -> list[Any]:
    if not isinstance(value, list):
        raise GitHubServiceError(f"{subject} is malformed")
    return value


def _require_direct_path(entry: dict[str, Any]) -> tuple[str, str]:
    path = entry.get("path")
    name = entry.get("name")
    if not isinstance(path, str) or not isinstance(name, str):
        raise GitHubServiceError("workflow entry path is missing")
    expected_prefix = f"{_WORKFLOW_DIRECTORY}/"
    if (
        not path.startswith(expected_prefix)
        or path != f"{expected_prefix}{name}"
        or not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise GitHubServiceError("workflow entry path is invalid")
    return path, name


def _is_link_or_submodule(entry: dict[str, Any], entry_type: Any) -> bool:
    return (
        entry_type in {"symlink", "submodule"}
        or entry.get("target") is not None
        or entry.get("submodule_git_url") is not None
    )


def _decode_base64(value: str) -> bytes:
    compact = value.replace("\n", "").replace("\r", "")
    try:
        encoded = compact.encode("ascii")
        return base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise GitHubServiceError("workflow file contains invalid base64") from None

"""GitHub REST adapter + the sticky-comment upsert.

``GitHubClient`` is a Protocol so tests can drop in an in-memory fake; the upsert
logic is written against the protocol and is fully unit-testable.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Comment:
    id: int
    body: str


@dataclass
class Release:
    tag_name: str


@dataclass
class PullRequest:
    number: int
    head: str
    title: str
    body: str


class GitHubClient(Protocol):
    def list_comments(self, issue: int) -> list[Comment]: ...
    def create_comment(self, issue: int, body: str) -> Comment: ...
    def update_comment(self, comment_id: int, body: str) -> None: ...
    def ensure_label(self, name: str, color: str) -> None: ...
    def add_labels(self, issue: int, labels: list[str]) -> None: ...
    # release-watch: query an upstream repo, find/open a bump PR on this repo.
    def latest_release(self, repo: str) -> Release | None: ...
    def list_tags(self, repo: str) -> list[str]: ...
    def find_open_pr(self, head: str) -> PullRequest | None: ...
    def create_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequest: ...
    def update_pull_request(self, number: int, *, title: str, body: str) -> None: ...


class RestGitHubClient:
    """Minimal REST client over urllib (no third-party deps)."""

    def __init__(self, token: str, repo: str, api: str = "https://api.github.com") -> None:
        self._token = token
        self._repo = repo
        self._api = api.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None, repo: str | None = None):
        url = f"{self._api}/repos/{repo or self._repo}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def list_comments(self, issue: int) -> list[Comment]:
        comments: list[Comment] = []
        page = 1
        while True:
            batch = self._request("GET", f"/issues/{issue}/comments?per_page=100&page={page}")
            if not batch:
                break
            comments.extend(Comment(c["id"], c.get("body") or "") for c in batch)
            if len(batch) < 100:
                break
            page += 1
        return comments

    def create_comment(self, issue: int, body: str) -> Comment:
        res = self._request("POST", f"/issues/{issue}/comments", {"body": body})
        return Comment(res["id"], res.get("body") or body)

    def update_comment(self, comment_id: int, body: str) -> None:
        self._request("PATCH", f"/issues/comments/{comment_id}", {"body": body})

    def ensure_label(self, name: str, color: str) -> None:
        try:
            self._request("POST", "/labels", {"name": name, "color": color})
        except urllib.error.HTTPError as exc:
            if exc.code == 422:  # already exists — refresh its colour
                self._request("PATCH", f"/labels/{name}", {"color": color})
            else:
                raise

    def add_labels(self, issue: int, labels: list[str]) -> None:
        self._request("POST", f"/issues/{issue}/labels", {"labels": labels})

    def latest_release(self, repo: str) -> Release | None:
        """Latest published GitHub Release of ``repo`` (owner/name), or None."""
        try:
            res = self._request("GET", "/releases/latest", repo=repo)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # no releases published yet
                return None
            raise
        return Release(res["tag_name"]) if res else None

    def list_tags(self, repo: str) -> list[str]:
        """All tag names of ``repo`` (paginated), newest-API-order preserved."""
        tags: list[str] = []
        page = 1
        while True:
            batch = self._request("GET", f"/tags?per_page=100&page={page}", repo=repo)
            if not batch:
                break
            tags.extend(t["name"] for t in batch)
            if len(batch) < 100:
                break
            page += 1
        return tags

    def find_open_pr(self, head: str) -> PullRequest | None:
        """The open PR whose head branch is ``head`` on this repo, or None."""
        owner = self._repo.split("/")[0]
        res = self._request("GET", f"/pulls?state=open&head={owner}:{head}&per_page=1")
        if not res:
            return None
        pr = res[0]
        return PullRequest(pr["number"], head, pr.get("title") or "", pr.get("body") or "")

    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        res = self._request(
            "POST", "/pulls", {"title": title, "head": head, "base": base, "body": body}
        )
        return PullRequest(res["number"], head, title, body)

    def update_pull_request(self, number: int, *, title: str, body: str) -> None:
        self._request("PATCH", f"/pulls/{number}", {"title": title, "body": body})


def upsert_sticky_comment(client: GitHubClient, issue: int, marker: str, body: str) -> int:
    """Update the single comment containing ``marker``, or create it. Returns id."""
    for comment in client.list_comments(issue):
        if marker in comment.body:
            client.update_comment(comment.id, body)
            return comment.id
    return client.create_comment(issue, body).id

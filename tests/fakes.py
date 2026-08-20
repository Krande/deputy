"""In-memory fakes standing in for GitHub + the Actions event payload."""

from __future__ import annotations

from deputy.github import Comment, PullRequest, Release


class FakeGitHubClient:
    """Records comment/label/PR calls in memory; satisfies the GitHubClient protocol."""

    def __init__(self) -> None:
        self.comments: list[Comment] = []
        self.ensured_labels: list[tuple[str, str]] = []
        self.added_labels: list[str] = []
        self._next_id = 0
        # release-watch fixtures: upstream repo -> latest release tag / tag list,
        # and the PRs this client has opened on the consumer repo.
        self.releases: dict[str, str] = {}
        self.tags: dict[str, list[str]] = {}
        self.pulls: list[PullRequest] = []
        self._next_pr = 100

    def list_comments(self, issue: int) -> list[Comment]:
        return list(self.comments)

    def create_comment(self, issue: int, body: str) -> Comment:
        self._next_id += 1
        comment = Comment(self._next_id, body)
        self.comments.append(comment)
        return comment

    def update_comment(self, comment_id: int, body: str) -> None:
        for i, comment in enumerate(self.comments):
            if comment.id == comment_id:
                self.comments[i] = Comment(comment_id, body)
                return
        raise KeyError(comment_id)

    def ensure_label(self, name: str, color: str) -> None:
        self.ensured_labels.append((name, color))

    def add_labels(self, issue: int, labels: list[str]) -> None:
        self.added_labels.extend(labels)

    # ── release-watch surface ────────────────────────────────────────────────
    def latest_release(self, repo: str) -> Release | None:
        tag = self.releases.get(repo)
        return Release(tag) if tag else None

    def list_tags(self, repo: str) -> list[str]:
        return list(self.tags.get(repo, []))

    def find_open_pr(self, head: str) -> PullRequest | None:
        for pr in self.pulls:
            if pr.head == head:
                return pr
        return None

    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        self._next_pr += 1
        pr = PullRequest(self._next_pr, head, title, body)
        self.pulls.append(pr)
        return pr

    def update_pull_request(self, number: int, *, title: str, body: str) -> None:
        for i, pr in enumerate(self.pulls):
            if pr.number == number:
                self.pulls[i] = PullRequest(number, pr.head, title, body)
                return
        raise KeyError(number)


def pr_event(
    *,
    title: str = "feat: something",
    labels: list[str] | None = None,
    merged: bool = False,
    number: int = 7,
    base_ref: str = "main",
) -> dict:
    """Build a minimal `pull_request` event payload."""
    return {
        "pull_request": {
            "number": number,
            "title": title,
            "merged": merged,
            "labels": [{"name": name} for name in (labels or [])],
            "base": {"ref": base_ref},
        }
    }


class OutputRecorder:
    """Stand-in for actions_io.set_output that records into a dict."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def __call__(self, name: str, value: str) -> None:
        self.values[name] = value

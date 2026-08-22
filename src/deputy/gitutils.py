"""Small git helpers used by the flows (injectable runner for tests)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

Runner = Callable[[Sequence[str]], object]


def seed_title_commit(title: str, runner: Runner = subprocess.run) -> None:
    """Create an empty commit carrying the PR title.

    The PR-review version calc runs on the base ref, where the PR's own commits
    aren't present yet. Seeding the title as a conventional commit lets
    semantic-release preview what the merge would release.
    """
    runner(["git", "config", "user.email", "pr-review-bot@users.noreply.github.com"])
    runner(["git", "config", "user.name", "pr-review-bot"])
    runner(["git", "commit", "--allow-empty", "-m", title])


def commit_and_push(
    cwd: str,
    paths: Sequence[str],
    message: str,
    *,
    push: bool = True,
    runner: Runner = subprocess.run,
) -> None:
    """Stage ``paths``, commit with ``message``, and (optionally) push, all in
    ``cwd``. Used by the gitops-update flow against a checked-out gitops repo;
    the deploy-key auth is set up by the workflow's checkout step, exactly like
    the release tagger relies on SOURCE_KEY.
    """
    runner(["git", "-C", cwd, "config", "user.email", "deputy-bot@users.noreply.github.com"])
    runner(["git", "-C", cwd, "config", "user.name", "deputy"])
    runner(["git", "-C", cwd, "add", *paths])
    runner(["git", "-C", cwd, "commit", "-m", message])
    if push:
        runner(["git", "-C", cwd, "push"])


def commit_to_branch(
    cwd: str,
    branch: str,
    paths: Sequence[str],
    message: str,
    *,
    push: bool = True,
    runner: Runner = subprocess.run,
) -> None:
    """Create/reset ``branch``, stage ``paths``, commit, and (optionally) push it.

    Used by the release-watch flow to land a dependency bump on a dedicated,
    per-target branch (one open PR per target). The branch is recreated from the
    current checkout each run (``checkout -B``) and pushed with
    ``--force-with-lease`` so a re-run that finds a still-newer upstream release
    updates the same throwaway bump branch in place rather than piling commits or
    failing on a non-fast-forward. Push auth comes from the workflow checkout,
    the same way the other flows rely on it.
    """
    runner(["git", "-C", cwd, "config", "user.email", "deputy-bot@users.noreply.github.com"])
    runner(["git", "-C", cwd, "config", "user.name", "deputy"])
    runner(["git", "-C", cwd, "checkout", "-B", branch])
    runner(["git", "-C", cwd, "add", *paths])
    runner(["git", "-C", cwd, "commit", "-m", message])
    if push:
        runner(["git", "-C", cwd, "push", "--force-with-lease", "-u", "origin", branch])


def stage_paths(
    paths: Sequence[str],
    *,
    cwd: str | None = None,
    runner: Runner = subprocess.run,
) -> None:
    """``git add`` ``paths`` without committing.

    Used by the release flow for files deputy bumps itself (see
    ``[release].version_json``): semantic-release commits whatever is in the
    index, so staging beforehand lands the change in the version commit instead
    of leaving it dirty in the tree.
    """
    if not paths:
        return
    prefix = ["git", "-C", cwd] if cwd else ["git"]
    runner([*prefix, "add", "--", *paths])

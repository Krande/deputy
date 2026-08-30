"""Small git helpers used by the flows (injectable runner for tests)."""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable, Sequence

Runner = Callable[[Sequence[str]], object]


def run_checked(args: Sequence[str]) -> object:
    """``subprocess.run`` that RAISES on a non-zero exit.

    The default runner, and it did not used to be. `Runner` returns ``object``
    and every call site discards it, so a failing git command was invisible:
    the process printed git's error to the log and deputy carried on as if the
    command had worked.

    That is not theoretical. A `git push` rejected with "stale info" left
    release-watch believing it had pushed, so it asked GitHub to open a PR from
    a branch that was never pushed and died on an unhandled `HTTP 422` several
    frames away from the actual failure. The traceback named `urllib`; the fault
    was two commands earlier.
    """
    return subprocess.run(args, check=True)


def seed_title_commit(title: str, runner: Runner = subprocess.run) -> None:
    """Create an empty commit carrying the PR title.

    The PR-review version calc runs on the base ref, where the PR's own commits
    aren't present yet. Seeding the title as a conventional commit lets
    semantic-release preview what the merge would release.
    """
    runner(["git", "config", "user.email", "pr-review-bot@users.noreply.github.com"])
    runner(["git", "config", "user.name", "pr-review-bot"])
    runner(["git", "commit", "--allow-empty", "-m", title])


def _nothing_staged(cwd: str, runner: Runner) -> bool:
    """Is the index empty relative to HEAD?

    `git diff --cached --quiet` exits 0 when there is NO difference, so success
    here means there is nothing to commit -- the inversion is git's, not ours.

    This exists because making the runner strict would otherwise turn a benign
    case into a failure. `gitops-update` writes the patched file and commits
    unconditionally, so setting an image to the value it already has stages
    nothing and `git commit` exits non-zero. That used to be swallowed along
    with everything else. Swallowing it was wrong, but so is failing on it: the
    honest answer is that there was nothing to do.
    """
    try:
        runner(["git", "-C", cwd, "diff", "--cached", "--quiet"])
    except subprocess.CalledProcessError:
        return False
    return True


def commit_and_push(
    cwd: str,
    paths: Sequence[str],
    message: str,
    *,
    push: bool = True,
    runner: Runner = run_checked,
) -> None:
    """Stage ``paths``, commit with ``message``, and (optionally) push, all in
    ``cwd``. Used by the gitops-update flow against a checked-out gitops repo;
    the deploy-key auth is set up by the workflow's checkout step, exactly like
    the release tagger relies on SOURCE_KEY.
    """
    runner(["git", "-C", cwd, "config", "user.email", "deputy-bot@users.noreply.github.com"])
    runner(["git", "-C", cwd, "config", "user.name", "deputy"])
    runner(["git", "-C", cwd, "add", *paths])
    if _nothing_staged(cwd, runner):
        print("nothing to commit: the file already holds this value")
        return
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
    runner: Runner = run_checked,
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
    # Teach the lease what it is leasing. `--force-with-lease` compares against
    # `refs/remotes/origin/<branch>`, and a CI checkout fetches only the branch
    # it checked out -- so on the second run, when the bump branch DOES exist on
    # the remote, git has nothing to compare against and rejects the push with
    # "stale info". The push then fails for a branch deputy owns and recreates
    # every run, which is exactly the case the lease is meant to allow.
    #
    # Tolerated rather than checked: a first run has no such branch, and "the
    # remote does not have it" is the normal answer, not a failure.
    with contextlib.suppress(subprocess.CalledProcessError):
        runner(
            [
                "git",
                "-C",
                cwd,
                "fetch",
                "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ]
        )
    runner(["git", "-C", cwd, "checkout", "-B", branch])
    runner(["git", "-C", cwd, "add", *paths])
    if _nothing_staged(cwd, runner):
        print(f"nothing to commit on {branch}: the file already holds this value")
        return
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

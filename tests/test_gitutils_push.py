"""The release-watch push path — the one that failed silently in CI.

Observed: deputy pushed a bump branch, git rejected it with "stale info", and
deputy went on to ask GitHub for a PR from a branch that was never pushed. The
visible failure was an unhandled `HTTP 422` several frames later, in urllib.
These tests pin each link of that chain.
"""

from __future__ import annotations

import subprocess

import pytest

from deputy.gitutils import commit_to_branch, run_checked


class RecordingRunner:
    """Records commands; optionally fails one of them, the way git would.

    `staged=True` makes `git diff --cached --quiet` RAISE, which is git's way of
    saying there IS something staged -- the inversion is git's. The default is
    staged, because every flow that reaches a commit has just written a change.
    """

    def __init__(self, fail_on: str | None = None, staged: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._fail_on = fail_on
        self._staged = staged

    def __call__(self, args):
        self.calls.append(list(args))
        if self._staged and "--cached" in args:
            raise subprocess.CalledProcessError(1, list(args))
        if self._fail_on is not None and self._fail_on in args:
            raise subprocess.CalledProcessError(1, list(args))
        return None

    def ran(self, verb: str) -> bool:
        return any(verb in c for c in self.calls)


def test_a_rejected_push_stops_the_flow_instead_of_reporting_success():
    """The whole bug in one assertion.

    A push that git rejects must reach the caller. It used to be swallowed --
    `Runner` returns `object` and every call site discards it -- so the next
    step opened a PR for a branch that was not there.
    """
    runner = RecordingRunner(fail_on="push")
    with pytest.raises(subprocess.CalledProcessError):
        commit_to_branch("/repo", "deputy/release-watch/x", ["f.yaml"], "msg", runner=runner)


def test_the_lease_is_given_something_to_compare_against():
    """`--force-with-lease` needs `refs/remotes/origin/<branch>` to exist.

    A CI checkout fetches only the branch it checked out, so on the SECOND run
    -- when the bump branch does exist on the remote -- the lease had nothing to
    compare and git refused with "stale info". Fetching the branch first is what
    makes the lease decidable.
    """
    runner = RecordingRunner()
    commit_to_branch("/repo", "deputy/release-watch/x", ["f.yaml"], "msg", runner=runner)
    fetch = next(c for c in runner.calls if "fetch" in c)
    assert fetch[-1] == "+refs/heads/deputy/release-watch/x:refs/remotes/origin/deputy/release-watch/x"
    # And it happens BEFORE the push, or it has answered nothing.
    assert runner.calls.index(fetch) < runner.calls.index(next(c for c in runner.calls if "push" in c))


def test_a_first_run_survives_having_no_branch_to_fetch():
    """The remote not having the branch yet is the normal first-run answer.

    Tolerated specifically, and only here: every other command still raises.
    """
    runner = RecordingRunner(fail_on="fetch")
    commit_to_branch("/repo", "deputy/release-watch/x", ["f.yaml"], "msg", runner=runner)
    assert runner.ran("push"), "a failed fetch must not stop the push it was preparing"


def test_the_default_runner_actually_checks():
    """The default is what CI uses, so the default is what has to raise."""
    with pytest.raises(subprocess.CalledProcessError):
        run_checked(["python3", "-c", "raise SystemExit(3)"])


def test_an_unchanged_file_is_nothing_to_do_rather_than_a_failure():
    """The one case a strict runner must NOT turn into an error.

    `gitops-update` writes the patched file and commits unconditionally, so
    setting an image to the value it already holds stages nothing and
    `git commit` exits non-zero. Failing there would break re-running a bump
    that has already landed.
    """
    runner = RecordingRunner(staged=False)
    commit_to_branch("/repo", "deputy/release-watch/x", ["f.yaml"], "msg", runner=runner)
    assert not runner.ran("commit"), "there was nothing staged to commit"
    assert not runner.ran("push"), "and therefore nothing to push"

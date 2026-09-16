"""Tests for the release_watch flow, wired to the in-memory fakes."""

from __future__ import annotations

from deputy.flows import latest_upstream_version, release_watch
from fakes import FakeGitHubClient

REQUIREMENTS = "some-lib==1.2.3\n"
PATTERN = r"some-lib==([0-9]+\.[0-9]+\.[0-9]+)"


def _target(**over):
    base = {
        "name": "some-lib",
        "repo": "owner/some-lib",
        "file": "requirements.txt",
        "pattern": PATTERN,
    }
    base.update(over)
    return base


def _run(client, targets, files, *, dry_run=False):
    commits: list[dict] = []
    rc = release_watch(
        targets,
        client,
        repo_dir="repo",
        base="main",
        dry_run=dry_run,
        reader=lambda p: files[p],
        writer=lambda p, text: files.__setitem__(p, text),
        commit_fn=lambda cwd, branch, paths, message, base, push: commits.append(
            {
                "cwd": cwd,
                "branch": branch,
                "paths": list(paths),
                "message": message,
                "base": base,
                "push": push,
            }
        ),
    )
    return rc, commits


def test_upstream_client_looks_up_releases_and_client_opens_the_pr():
    # PRs on one forge (e.g. Forgejo), releases on another (GitHub).
    consumer = FakeGitHubClient()
    upstream = FakeGitHubClient()
    upstream.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc = release_watch(
        [_target()],
        consumer,
        repo_dir="repo",
        reader=lambda p: files[p],
        writer=lambda p, text: files.__setitem__(p, text),
        commit_fn=lambda *a, **k: None,
        upstream_client=upstream,
    )

    assert rc == 0
    assert files["repo/requirements.txt"] == "some-lib==1.3.0\n"
    # the PR lands on the consumer, nothing on the upstream
    assert [pr.head for pr in consumer.pulls] == ["deputy/release-watch/some-lib"]
    assert upstream.pulls == []


def test_newer_release_opens_pr_and_bumps_file():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc, commits = _run(client, [_target()], files)

    assert rc == 0
    assert files["repo/requirements.txt"] == "some-lib==1.3.0\n"
    # committed to a per-target branch and pushed
    assert commits == [
        {
            "cwd": "repo",
            "branch": "deputy/release-watch/some-lib",
            "paths": ["requirements.txt"],
            "message": "chore(some-lib): bump 1.2.3 -> 1.3.0",
            "base": "main",
            "push": True,
        }
    ]
    # one PR opened, with the default title and a dependencies label
    assert len(client.pulls) == 1
    pr = client.pulls[0]
    assert pr.head == "deputy/release-watch/some-lib"
    assert pr.title == "chore: bump some-lib to 1.3.0"
    assert "1.2.3" in pr.body and "1.3.0" in pr.body
    assert client.added_labels == ["dependencies"]


def test_every_target_in_a_run_is_committed_against_base():
    """Two bumps in one run must stay two independent PRs.

    Each target's branch has to start from `base`. When it started from HEAD
    instead, the second target of a `--all` run branched off the first one's
    commit and shipped it inside its own PR.
    """
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    client.releases["owner/other-lib"] = "v2.1.0"
    files = {
        "repo/requirements.txt": REQUIREMENTS,
        "repo/other.txt": "other-lib==2.0.0\n",
    }
    other = _target(
        name="other-lib",
        repo="owner/other-lib",
        file="other.txt",
        pattern=r"other-lib==([0-9]+\.[0-9]+\.[0-9]+)",
    )

    rc, commits = _run(client, [_target(), other], files)

    assert rc == 0
    assert [c["branch"] for c in commits] == [
        "deputy/release-watch/some-lib",
        "deputy/release-watch/other-lib",
    ]
    # Neither one is allowed to build on the other's branch.
    assert [c["base"] for c in commits] == ["main", "main"]
    # And each branch carries only its own file.
    assert [c["paths"] for c in commits] == [["requirements.txt"], ["other.txt"]]


def test_up_to_date_is_a_noop():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.2.3"  # same as pinned
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc, commits = _run(client, [_target()], files)

    assert rc == 0
    assert commits == []
    assert client.pulls == []
    assert files["repo/requirements.txt"] == REQUIREMENTS  # unchanged


def test_existing_open_pr_is_updated_not_duplicated():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.4.0"
    files = {"repo/requirements.txt": REQUIREMENTS}
    # a stale open PR already exists on the target's head branch
    existing = client.create_pull_request(
        head="deputy/release-watch/some-lib",
        base="main",
        title="chore: bump some-lib to 1.3.0",
        body="old body",
    )

    rc, commits = _run(client, [_target()], files)

    assert rc == 0
    assert len(client.pulls) == 1  # not duplicated
    assert client.pulls[0].number == existing.number
    assert client.pulls[0].title == "chore: bump some-lib to 1.4.0"  # updated
    assert len(commits) == 1


def test_dry_run_computes_but_does_not_write_commit_or_open_pr():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v2.0.0"
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc, commits = _run(client, [_target()], files, dry_run=True)

    assert rc == 0
    assert commits == []
    assert client.pulls == []
    assert files["repo/requirements.txt"] == REQUIREMENTS  # not written


def test_falls_back_to_latest_semver_tag_when_no_release():
    client = FakeGitHubClient()
    # no release published, only tags
    client.tags["owner/some-lib"] = ["v1.2.3", "v1.5.0", "nightly", "v1.4.0"]
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc, _commits = _run(client, [_target()], files)

    assert rc == 0
    assert files["repo/requirements.txt"] == "some-lib==1.5.0\n"
    assert len(client.pulls) == 1


def test_no_upstream_release_or_tag_skips_cleanly():
    client = FakeGitHubClient()  # no releases, no tags
    files = {"repo/requirements.txt": REQUIREMENTS}

    rc, _commits = _run(client, [_target()], files)

    assert rc == 0
    assert _commits == []
    assert client.pulls == []


def test_pattern_mismatch_returns_nonzero_and_skips():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/requirements.txt": "unrelated content\n"}

    rc, commits = _run(client, [_target()], files)

    assert rc == 1  # loud failure so the workflow step goes red
    assert commits == []
    assert client.pulls == []


def test_custom_title_branch_prefix_and_labels():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/requirements.txt": REQUIREMENTS}
    target = _target(
        pr_title="deps: {name} -> {version}",
        branch_prefix="bot/bumps",
        labels=["deps", "automated"],
    )

    rc, commits = _run(client, [target], files)

    assert rc == 0
    assert commits[0]["branch"] == "bot/bumps/some-lib"
    assert client.pulls[0].head == "bot/bumps/some-lib"
    assert client.pulls[0].title == "deps: some-lib -> 1.3.0"
    assert client.added_labels == ["deps", "automated"]


def test_multiple_targets_mixed_outcomes():
    client = FakeGitHubClient()
    client.releases["owner/lib-a"] = "v2.0.0"  # newer
    client.releases["owner/lib-b"] = "v1.0.0"  # up to date
    files = {
        "repo/a.txt": "lib-a==1.0.0\n",
        "repo/b.txt": "lib-b==1.0.0\n",
    }
    targets = [
        _target(name="lib-a", repo="owner/lib-a", file="a.txt", pattern=r"lib-a==([0-9.]+)"),
        _target(name="lib-b", repo="owner/lib-b", file="b.txt", pattern=r"lib-b==([0-9.]+)"),
    ]

    rc, _commits = _run(client, targets, files)

    assert rc == 0
    assert files["repo/a.txt"] == "lib-a==2.0.0\n"
    assert files["repo/b.txt"] == "lib-b==1.0.0\n"  # untouched
    assert len(client.pulls) == 1
    assert client.pulls[0].head == "deputy/release-watch/lib-a"


def test_files_list_bumps_every_file_in_one_commit_and_one_pr():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/a.txt": REQUIREMENTS, "repo/b.txt": REQUIREMENTS}
    target = _target(files=["a.txt", "b.txt"])
    del target["file"]

    rc, commits = _run(client, [target], files)

    assert rc == 0
    assert files["repo/a.txt"] == "some-lib==1.3.0\n"
    assert files["repo/b.txt"] == "some-lib==1.3.0\n"
    # one branch, one commit carrying both paths, one PR
    assert len(commits) == 1
    assert commits[0]["paths"] == ["a.txt", "b.txt"]
    assert commits[0]["message"] == "chore(some-lib): bump 1.2.3 -> 1.3.0"
    assert len(client.pulls) == 1


def test_files_list_compares_against_the_oldest_pin_when_they_drift():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    # b.txt was already bumped by hand; a.txt lags behind
    files = {"repo/a.txt": "some-lib==1.2.3\n", "repo/b.txt": "some-lib==1.3.0\n"}
    target = _target(files=["a.txt", "b.txt"])
    del target["file"]

    rc, commits = _run(client, [target], files)

    assert rc == 0  # the lagging file still gets caught up
    assert files["repo/a.txt"] == "some-lib==1.3.0\n"
    assert files["repo/b.txt"] == "some-lib==1.3.0\n"
    assert commits[0]["message"] == "chore(some-lib): bump 1.2.3 -> 1.3.0"


def test_files_list_writes_nothing_when_one_file_misses_the_pattern():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/a.txt": REQUIREMENTS, "repo/b.txt": "unrelated content\n"}
    target = _target(files=["a.txt", "b.txt"])
    del target["file"]

    rc, commits = _run(client, [target], files)

    assert rc == 1
    assert files["repo/a.txt"] == REQUIREMENTS  # no half-done bump
    assert commits == []
    assert client.pulls == []


def test_separate_targets_on_one_upstream_still_get_separate_prs():
    """Two pins of the same upstream that are meant to move independently."""
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.3.0"
    files = {"repo/a.txt": REQUIREMENTS, "repo/b.txt": REQUIREMENTS}
    targets = [
        _target(name="lib-a", file="a.txt"),
        _target(name="lib-b", file="b.txt"),
    ]

    rc, commits = _run(client, targets, files)

    assert rc == 0
    assert [c["branch"] for c in commits] == [
        "deputy/release-watch/lib-a",
        "deputy/release-watch/lib-b",
    ]
    assert [p.head for p in client.pulls] == [
        "deputy/release-watch/lib-a",
        "deputy/release-watch/lib-b",
    ]


def test_latest_upstream_version_prefers_release_over_tags():
    client = FakeGitHubClient()
    client.releases["owner/some-lib"] = "v1.2.0"
    client.tags["owner/some-lib"] = ["v9.9.9"]
    assert latest_upstream_version(client, "owner/some-lib") == "v1.2.0"

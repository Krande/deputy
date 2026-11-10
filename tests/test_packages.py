"""[[release.packages]]: several packages with their own version/tag in one repo."""

import datetime as dt
import subprocess

import pytest

from deputy.actions_io import parse_pull_request
from deputy.config import render_release_config
from deputy.flows import pr_review, tag_on_merge
from deputy.labels import decide_bump
from deputy.packages import (
    add_changelog_entry,
    changed_files,
    flag_from_title,
    package_flag,
    release_packages,
    touched_packages,
)
from deputy.version import package_version_lines, run_package_release
from fakes import FakeGitHubClient, OutputRecorder, pr_event

CFG = {
    "release": {
        "commit_parser_options": {"minor_tags": ["feat"], "patch_tags": ["fix", "perf"]},
        "version_toml": ["pyproject.toml:project.version"],
        "packages": [
            {"name": "app", "path": "app", "version_toml": ["app/pyproject.toml:project.version"]},
            {
                "name": "plugin",
                "path": "plugins/plugin/",
                "version_toml": ["plugins/plugin/pyproject.toml:project.version"],
                "tag_format": "plugin/{version}",
                "changelog_file": "docs/plugin-changes.md",
            },
        ],
    }
}


# ── config ─────────────────────────────────────────────────────────────────────


def test_packages_get_shared_keys_and_their_own():
    app, plugin = release_packages(CFG)
    assert app.name == "app" and app.path == "app"
    assert app.overrides["commit_parser_options"]["minor_tags"] == ["feat"]
    assert app.overrides["version_toml"] == ["app/pyproject.toml:project.version"]
    assert app.tag_format == "app-v{version}"
    assert app.tag("1.2.0") == "app-v1.2.0"
    assert app.overrides["commit_message"].startswith("app {version}")
    assert app.changelog_file == "app/CHANGELOG.md"
    assert plugin.path == "plugins/plugin"  # trailing slash normalised
    assert plugin.tag("2.0.0") == "plugin/2.0.0"
    assert plugin.changelog_file == "docs/plugin-changes.md"
    assert "packages" not in app.overrides and "name" not in app.overrides


def test_a_package_does_not_inherit_the_root_version_file():
    pkgs = release_packages(
        {
            "release": {
                "version_toml": ["pyproject.toml:project.version"],
                "packages": [{"name": "a", "path": "a"}],
            }
        }
    )
    assert pkgs[0].overrides["version_toml"] == []


def test_no_packages_declared_is_the_single_package_repo():
    assert release_packages({}) == []
    assert release_packages({"release": {"version_toml": ["x"]}}) == []


@pytest.mark.parametrize(
    "entries",
    [[{"name": "a"}], [{"path": "a"}], [{"name": "a", "path": "a"}, {"name": "a", "path": "b"}]],
)
def test_bad_package_entries_are_rejected(entries):
    with pytest.raises(ValueError):
        release_packages({"release": {"packages": entries}})


def test_packages_key_never_reaches_the_semantic_release_config():
    assert "packages" not in render_release_config(CFG["release"])


# ── which packages a change touches ──────────────────────────────────────────


def test_touched_packages_match_whole_path_segments():
    app, plugin = release_packages(CFG)
    files = ["app/src/x.py", "application/readme.md", "README.md"]
    assert touched_packages([app, plugin], files) == [app]
    assert touched_packages([app, plugin], ["plugins/plugin/a.py", "app/b.py"]) == [app, plugin]
    assert touched_packages([app, plugin], ["docs/index.md"]) == []
    assert touched_packages([app, plugin], ["app"]) == [app]


# ── the bump level ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "flag"),
    [
        ("feat: add x", "--minor"),
        ("feat(app): add x", "--minor"),
        ("fix(plugin): y", "--patch"),
        ("perf: faster", "--patch"),
        ("feat!: drop python 3.11", "--major"),
        ("fix(app)!: rename", "--major"),
        ("feat: x\n\nBREAKING CHANGE: y", "--major"),
        ("chore(ci): bump", None),
        ("docs: typo", None),
        ("Update readme", None),
        ("", None),
    ],
)
def test_flag_from_title(title, flag):
    assert flag_from_title(title) == flag


def test_flag_from_title_honours_configured_tags():
    assert flag_from_title("chore: x", {"patch_tags": ["fix", "chore"]}) == "--patch"
    assert flag_from_title("feat: x", {"minor_tags": []}) is None


def test_package_flag_explicit_label_wins_auto_reads_title_skip_releases_nothing():
    app = release_packages(CFG)[0]
    assert package_flag("--patch", "feat: x", app) == "--patch"
    assert package_flag("", "feat: x", app) == "--minor"
    assert package_flag("", "chore: x", app) is None
    assert package_flag(None, "feat: x", app) is None


# ── git and changelog helpers ─────────────────────────────────────────────────


def test_changed_files_diffs_the_merge_commit_against_its_first_parent():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="app/a.py\n\nplugins/plugin/b.py\n", stderr=""
        )

    assert changed_files("abc123", runner) == ["app/a.py", "plugins/plugin/b.py"]
    assert calls == [["git", "diff", "--name-only", "abc123^1", "abc123"]]


def test_changed_files_raises_rather_than_reporting_nothing():
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="bad revision")

    with pytest.raises(RuntimeError):
        changed_files("abc123", runner)


def test_changelog_entry_goes_above_older_entries(tmp_path):
    path = tmp_path / "pkg" / "CHANGELOG.md"
    add_changelog_entry(path, "## app-v1.0.0 (2026-01-01)\n\n* feat: a (#1)\n")
    add_changelog_entry(path, "## app-v1.1.0 (2026-02-01)\n\n* feat: b (#2)\n")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# CHANGELOG\n\n## app-v1.1.0")
    assert text.index("app-v1.1.0") < text.index("app-v1.0.0")


# ── releasing one package ──────────────────────────────────────────────────────


def _release(tmp_path, monkeypatch, planned="1.3.0", rc=0):
    monkeypatch.chdir(tmp_path)
    app = release_packages(CFG)[0]
    calls = {"staged": [], "cmd": None, "releases": []}

    def runner(cmd):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, rc)

    result = run_package_release(
        app,
        "--minor",
        title="feat(app): add x",
        number=12,
        url="https://github.com/o/r/pull/12",
        create_release_fn=lambda tag, name, body: calls["releases"].append((tag, name, body)),
        write_config_fn=lambda overrides: "cfg.toml",
        planned_fn=lambda config, flag: planned,
        stage_fn=lambda paths: calls["staged"].extend(paths),
        runner=runner,
        today=dt.date(2026, 11, 6),
    )
    return result, calls, tmp_path


def test_run_package_release_tags_the_package_and_records_the_pr(tmp_path, monkeypatch):
    rc, calls, root = _release(tmp_path, monkeypatch)
    assert rc == 0
    assert calls["cmd"] == [
        "semantic-release",
        "-c",
        "cfg.toml",
        "version",
        "--no-changelog",
        "--no-vcs-release",
        "--minor",
    ]
    assert calls["staged"] == ["app/CHANGELOG.md"]
    text = (root / "app" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## app-v1.3.0 (2026-11-06)" in text
    assert "* feat(app): add x ([#12](https://github.com/o/r/pull/12))" in text
    assert calls["releases"] == [
        ("app-v1.3.0", "app-v1.3.0", "* feat(app): add x ([#12](https://github.com/o/r/pull/12))")
    ]


def test_run_package_release_with_nothing_to_release_changes_nothing(tmp_path, monkeypatch):
    rc, calls, root = _release(tmp_path, monkeypatch, planned=None)
    assert rc == 0 and calls["cmd"] is None and calls["releases"] == []
    assert not (root / "app" / "CHANGELOG.md").exists()


def test_run_package_release_creates_no_release_when_semantic_release_fails(tmp_path, monkeypatch):
    rc, calls, _ = _release(tmp_path, monkeypatch, rc=1)
    assert rc == 1 and calls["releases"] == []


# ── the flows ──────────────────────────────────────────────────────────────────


def _tag(event, files, results=None):
    released = []

    def release(package, flag, pr):
        released.append((package.name, flag))
        return (results or {}).get(package.name, 0)

    rc = tag_on_merge(
        event,
        packages=release_packages(CFG),
        changed_files_fn=lambda sha: files,
        package_release_fn=release,
    )
    return rc, released


def test_tag_on_merge_releases_only_the_touched_package_at_the_title_level():
    event = pr_event(
        title="feat(app): x", labels=["release-auto"], merged=True, merge_commit_sha="abc"
    )
    rc, released = _tag(event, ["app/src/x.py", "README.md"])
    assert rc == 0 and released == [("app", "--minor")]


def test_tag_on_merge_explicit_label_applies_to_every_touched_package():
    event = pr_event(title="chore: shared", labels=["release-patch"], merged=True)
    rc, released = _tag(event, ["app/a", "plugins/plugin/b"])
    assert rc == 0
    assert released == [("app", "--patch"), ("plugin", "--patch")]


def test_tag_on_merge_auto_with_a_chore_title_releases_nothing():
    event = pr_event(title="chore(app): tidy", labels=["release-auto"], merged=True)
    assert _tag(event, ["app/a"]) == (0, [])


def test_tag_on_merge_with_no_package_touched_releases_nothing():
    event = pr_event(title="feat: docs site", labels=["release-auto"], merged=True)
    assert _tag(event, ["docs/index.md"]) == (0, [])


def test_tag_on_merge_release_skip_still_wins():
    event = pr_event(title="feat(app): x", labels=["release-skip"], merged=True)
    assert _tag(event, ["app/a"]) == (0, [])


def test_tag_on_merge_stops_at_the_first_failing_package():
    event = pr_event(title="fix: both", labels=["release-auto"], merged=True)
    rc, released = _tag(event, ["app/a", "plugins/plugin/b"], results={"app": 1})
    assert rc == 1 and released == [("app", "--patch")]


def test_tag_on_merge_with_packages_needs_a_client_for_the_releases():
    event = pr_event(title="fix: x", labels=["release-auto"], merged=True)
    with pytest.raises(ValueError):
        tag_on_merge(event, packages=release_packages(CFG), changed_files_fn=lambda sha: ["app/a"])


def test_pr_review_lists_the_next_version_per_touched_package():
    client = FakeGitHubClient()
    event = pr_event(title="feat(app): x", labels=["release-auto"])
    client.labels[7] = ["release-auto"]
    client.pr_files[7] = ["app/a.py", "plugins/plugin/b.py"]
    seen = {}

    def lines(decision, title, touched):
        seen["touched"] = [p.name for p in touched]
        return " * (per-package lines)"

    rc = pr_review(
        event,
        client,
        has_source_key=True,
        seed_fn=lambda t: None,
        set_output_fn=OutputRecorder(),
        packages=release_packages(CFG),
        package_lines_fn=lines,
    )
    assert rc == 0
    assert seen["touched"] == ["app", "plugin"]
    assert "(per-package lines)" in client.comments[0].body


def test_package_version_lines_report_each_package():
    app, plugin = release_packages(CFG)
    decision = decide_bump(["release-auto"])

    def runner(cmd):
        return subprocess.CompletedProcess(cmd, 0, stdout="1.3.0\n", stderr="")

    text = package_version_lines(
        decision, "feat: x", [app, plugin], write_config_fn=lambda o: "c.toml", runner=runner
    )
    assert ' * ✅ app: next version "1.3.0" (app-v1.3.0)' in text
    assert ' * ✅ plugin: next version "1.3.0" (plugin/1.3.0)' in text
    assert "no package changed" in package_version_lines(decision, "feat: x", [])
    assert "nothing releasable" in package_version_lines(
        decision, "chore: x", [app], write_config_fn=lambda o: "c", runner=runner
    )
    assert "release-skip" in package_version_lines(decide_bump(["release-skip"]), "feat: x", [app])


def test_parse_pull_request_reads_merge_commit_and_link():
    pr = parse_pull_request(pr_event(number=12, merged=True, merge_commit_sha="abc"))
    assert pr.merge_commit_sha == "abc"
    assert pr.html_url.endswith("/pull/12")

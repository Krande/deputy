import subprocess

import pytest

from deputy.jsonversion import JsonVersionError
from deputy.labels import decide_bump
from deputy.version import (
    _isolated_env,
    next_version_noop,
    planned_version,
    prepare_json_versions,
    run_release,
    version_line_for,
)


def cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess(
        ["semantic-release"], returncode=rc, stdout=stdout, stderr=stderr
    )


# What `semantic-release version --print` really emits when nothing is due: the
# version on stdout (rprint sends everything else to stderr), so only stderr
# distinguishes it from a real release. Verified against python-semantic-release
# 8.5.1.
NO_RELEASE = {
    "stdout": "0.38.0\n",
    "stderr": "No release will be made, 0.38.0 has already been released!\n",
}


def test_next_version_parses_last_line():
    assert next_version_noop("cfg", "--minor", lambda c: cp("chatter\n0.2.0\n")) == "0.2.0"


def test_next_version_none_when_empty():
    assert next_version_noop("cfg", None, lambda c: cp("")) is None


def test_next_version_none_when_not_semver():
    assert next_version_noop("cfg", None, lambda c: cp("no release will be made")) is None


def test_next_version_none_on_runner_error():
    def boom(cmd):
        raise FileNotFoundError("semantic-release not installed")

    assert next_version_noop("cfg", None, boom) is None


def test_version_line_skip_when_no_release():
    line = version_line_for(decide_bump(["release-skip"]), "cfg", lambda c: cp())
    assert "Skipping release" in line


def test_version_line_reports_calculated_version():
    line = version_line_for(decide_bump(["release-minor"]), "cfg", lambda c: cp("0.2.0"))
    assert "0.2.0" in line and "Calculated next version" in line


def test_version_line_no_release_when_version_missing():
    line = version_line_for(decide_bump(["release-auto"]), "cfg", lambda c: cp(""))
    assert "No release will be issued" in line


def test_isolated_env_strips_the_actions_output_handshake(monkeypatch):
    # This is the fix for the empty-PR-comment bug: semantic-release must not be
    # able to write into (and corrupt) our step's GITHUB_OUTPUT.
    monkeypatch.setenv("GITHUB_OUTPUT", "/tmp/out")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("KEEP_ME", "yes")
    env = _isolated_env()
    assert "GITHUB_OUTPUT" not in env
    assert "GITHUB_ACTIONS" not in env
    assert env["KEEP_ME"] == "yes"


def test_run_release_builds_the_command_and_returns_rc():
    seen = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)
        return cp(rc=0)

    rc = run_release("action_config.toml", "--minor", runner)
    assert rc == 0
    assert seen["cmd"] == [
        "semantic-release",
        "-c",
        "action_config.toml",
        "version",
        "--changelog",
        "--vcs-release",
        "--minor",
    ]


def test_run_release_omits_flag_when_none():
    seen = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)
        return cp()

    run_release("cfg", None, runner)
    assert seen["cmd"][-1] == "--vcs-release"
    assert "--minor" not in seen["cmd"]


# ── [release].version_json orchestration ──────────────────────────────────────


class FakeUpdate:
    def __init__(self, old, new, fields=("version",)):
        self.old_version, self.new_version, self.fields = old, new, fields

    @property
    def changed(self):
        return self.old_version != self.new_version


def test_prepare_bumps_every_declared_file_to_the_version_psr_will_cut():
    bumped, staged = [], []

    def bump(path, version):
        bumped.append((path, version))
        return FakeUpdate("0.35.1", version)

    version = prepare_json_versions(
        ["a/package.json", "a/package-lock.json"],
        "cfg",
        "--minor",
        noop_runner=lambda c: cp("0.38.0"),
        bump_fn=bump,
        stage_fn=staged.extend,
    )

    assert version == "0.38.0"
    assert bumped == [("a/package.json", "0.38.0"), ("a/package-lock.json", "0.38.0")]
    # Staged, so semantic-release's own `git commit` folds them into the version
    # commit instead of leaving them dirty in the tree.
    assert staged == ["a/package.json", "a/package-lock.json"]


def test_prepare_uses_the_same_flag_the_release_will_use():
    seen = {}

    def noop_runner(cmd):
        seen["cmd"] = list(cmd)
        return cp("0.38.0")

    prepare_json_versions(
        ["a.json"],
        "cfg",
        "--major",
        noop_runner=noop_runner,
        bump_fn=lambda p, v: FakeUpdate("0.1.0", v),
        stage_fn=lambda p: None,
    )
    assert seen["cmd"] == [
        "semantic-release",
        "-c",
        "cfg",
        "--noop",
        "version",
        "--print",
        "--major",
    ]


def test_prepare_leaves_files_alone_when_no_release_is_due():
    calls = []
    version = prepare_json_versions(
        ["a.json"],
        "cfg",
        None,
        noop_runner=lambda c: cp(**NO_RELEASE),
        bump_fn=lambda p, v: calls.append((p, v)),
        stage_fn=lambda p: calls.append(("staged", p)),
    )
    # A bump with no commit to carry it would just leave the tree dirty.
    assert version is None
    assert calls == []


def test_prepare_does_not_stage_files_that_did_not_change():
    staged = []
    prepare_json_versions(
        ["unchanged.json", "stale.json"],
        "cfg",
        None,
        noop_runner=lambda c: cp("0.38.0"),
        bump_fn=lambda p, v: FakeUpdate("0.38.0" if p == "unchanged.json" else "0.35.1", v),
        stage_fn=staged.extend,
    )
    assert staged == ["stale.json"]


def test_prepare_propagates_a_declaration_failure():
    def boom(path, version):
        raise JsonVersionError("a.json: no such file")

    with pytest.raises(JsonVersionError):
        prepare_json_versions(
            ["a.json"],
            "cfg",
            None,
            noop_runner=lambda c: cp("0.38.0"),
            bump_fn=boom,
            stage_fn=lambda p: None,
        )


def test_run_release_prepares_json_files_before_shelling_out():
    order = []

    def prepare(paths, config_file, flag):
        order.append(("prepare", list(paths), config_file, flag))
        return "0.38.0"

    def runner(cmd):
        order.append(("release", list(cmd)))
        return cp()

    run_release("cfg", "--minor", runner, version_json=["a.json"], prepare_fn=prepare)

    assert order[0] == ("prepare", ["a.json"], "cfg", "--minor")
    assert order[1][0] == "release"


def test_run_release_without_version_json_is_unchanged():
    # Back-compat: every repo pinned to an older deputy tag declares no
    # version_json, and must behave exactly as it does today.
    called = []
    run_release("cfg", None, lambda c: cp(), prepare_fn=lambda *a: called.append(a))
    assert called == []


# ── planned_version: never guesses ────────────────────────────────────────────


def test_planned_version_returns_the_version_to_be_cut():
    assert planned_version("cfg", "--minor", lambda c: cp("0.2.0\n")) == "0.2.0"


def test_planned_version_is_none_when_semantic_release_says_no_release():
    assert planned_version("cfg", None, lambda c: cp(**NO_RELEASE)) is None


def test_planned_version_needs_stderr_to_tell_the_two_cases_apart():
    # stdout is a bare version in BOTH cases; only stderr distinguishes them.
    assert planned_version("cfg", None, lambda c: cp(NO_RELEASE["stdout"])) == "0.38.0"


def test_planned_version_raises_when_semantic_release_is_missing():
    def boom(cmd):
        raise FileNotFoundError("semantic-release not installed")

    with pytest.raises(RuntimeError) as exc:
        planned_version("cfg", None, boom)
    assert "semantic-release" in str(exc.value)


def test_planned_version_raises_on_a_non_zero_exit():
    with pytest.raises(RuntimeError) as exc:
        planned_version("cfg", None, lambda c: cp("", rc=2))
    assert "exited 2" in str(exc.value)


def test_planned_version_raises_when_nothing_version_shaped_was_printed():
    with pytest.raises(RuntimeError) as exc:
        planned_version("cfg", None, lambda c: cp("some unexpected chatter"))
    assert "printed no version" in str(exc.value)


def test_next_version_noop_stays_best_effort_for_the_pr_comment():
    # The PR-comment path must degrade to "no version", not blow up the review.
    def boom(cmd):
        raise FileNotFoundError("semantic-release not installed")

    assert next_version_noop("cfg", None, boom) is None
    assert next_version_noop("cfg", None, lambda c: cp("chatter")) is None

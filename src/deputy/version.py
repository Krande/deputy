"""semantic-release wrappers.

Two entry points: a best-effort ``--noop --print`` used by the PR-review comment,
and the real ``version`` invocation used on merge. Both take an injectable runner
so tests never shell out.

``run_release`` also owns the one bump semantic-release cannot do itself: the
JSON version declarations from ``[release].version_json`` (see
:mod:`deputy.jsonversion`). They are applied *before* the ``semantic-release
version`` call and staged, so they land in the version commit rather than
dangling as uncommitted changes.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence

from .gitutils import stage_paths
from .jsonversion import JsonVersionUpdate, bump_file
from .labels import BumpDecision

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")

# semantic-release's way of saying "the version I just printed is the one you
# already have". It only ever appears on stderr, and only when no release is due.
# deputy pins python-semantic-release==8.5.1, so the wording is fixed; if it ever
# drifted the fallback is benign — deputy would write the already-released
# version into the JSON files, which is a no-op whenever they are in sync.
_NO_RELEASE_MARKER = "has already been released"


def _isolated_env() -> dict[str, str]:
    """Env with the Actions output handshake removed.

    On a release branch ``semantic-release version`` writes released/version/tag
    into GITHUB_OUTPUT, and its final ``tag=`` line has no trailing newline —
    which corrupts any output we append afterwards. Hiding GITHUB_OUTPUT (and the
    GITHUB_ACTIONS flag) from the child keeps it out of our step's outputs.
    """
    return {k: v for k, v in os.environ.items() if k not in ("GITHUB_OUTPUT", "GITHUB_ACTIONS")}


def _default_noop_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=_isolated_env())


def _print_argv(config_file: str, flag: str | None) -> list[str]:
    cmd = ["semantic-release", "-c", config_file, "--noop", "version", "--print"]
    if flag:
        cmd.append(flag)
    return cmd


def _parse_printed_version(stdout: str) -> str | None:
    """The version on ``version --print``'s stdout, or None when there isn't one."""
    out = (stdout or "").strip()
    if not out:
        return None
    last = out.splitlines()[-1].strip()
    return last if _VERSION_RE.match(last) else None


def next_version_noop(
    config_file: str, flag: str | None, runner: Runner = _default_noop_runner
) -> str | None:
    """What version would semantic-release cut right now? None if none/unknown.

    Best-effort on purpose: this feeds an informational line in the PR comment,
    so a missing or unhappy semantic-release degrades to "no version" rather than
    failing the review. Use :func:`planned_version` where the answer must be
    trusted.
    """
    try:
        res = runner(_print_argv(config_file, flag))
    except Exception:
        return None
    return _parse_printed_version(getattr(res, "stdout", ""))


def planned_version(
    config_file: str, flag: str | None, runner: Runner = _default_noop_runner
) -> str | None:
    """Same question as :func:`next_version_noop`, but never guesses.

    Returns the version semantic-release is about to cut, or None when it reports
    it will not release. Raises ``RuntimeError`` when semantic-release could not
    be run, exited non-zero, or printed nothing version-shaped — because the
    caller acts on the answer. Treating "the tool is missing" as "no release" is
    exactly how a version bump silently stops happening.

    Note ``version --print`` puts *only* the computed version on stdout; every
    other message goes to stderr (semantic-release's ``rprint`` writes there so
    that capturing the command's output isn't cluttered). stdout therefore looks
    identical whether or not a release is actually due, and the one thing that
    distinguishes the two is :data:`_NO_RELEASE_MARKER` on stderr.
    """
    cmd = _print_argv(config_file, flag)
    try:
        res = runner(cmd)
    except Exception as exc:
        raise RuntimeError(
            f"could not ask semantic-release for the next version ({' '.join(cmd)}): {exc}"
        ) from exc

    rc = getattr(res, "returncode", 0)
    stderr = getattr(res, "stderr", "") or ""
    if rc != 0:
        raise RuntimeError(
            f"semantic-release exited {rc} while computing the next version "
            f"({' '.join(cmd)}): {stderr.strip()}"
        )

    stdout = getattr(res, "stdout", "") or ""
    version = _parse_printed_version(stdout)
    if version is None:
        raise RuntimeError(
            "semantic-release printed no version while computing the next version "
            f"({' '.join(cmd)}); stdout was {stdout.strip()!r}, stderr was {stderr.strip()!r}"
        )
    if _NO_RELEASE_MARKER in stderr:
        return None
    return version


def version_line_for(
    decision: BumpDecision, config_file: str, runner: Runner = _default_noop_runner
) -> str:
    """The informational '* … next version …' bullet for the PR comment."""
    if decision.multiple or not decision.release:
        return " * ✅ Skipping release (release-skip)"
    ver = next_version_noop(config_file, decision.flag, runner)
    if ver:
        return f' * ✅ Calculated next version: "{ver}"'
    return " * ℹ️ No release will be issued for these commits"


def _default_release_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    # Inherit the full env on purpose: the real release needs GH_TOKEN and the
    # SSH-authenticated `origin` remote to push the tag. Stream output.
    return subprocess.run(cmd)


BumpFn = Callable[[str, str], JsonVersionUpdate]
StageFn = Callable[[Sequence[str]], None]


def prepare_json_versions(
    paths: Sequence[str],
    config_file: str,
    flag: str | None,
    *,
    noop_runner: Runner = _default_noop_runner,
    bump_fn: BumpFn = bump_file,
    stage_fn: StageFn = stage_paths,
) -> str | None:
    """Write the version about to be cut into the ``version_json`` files and stage them.

    Ordering matters. semantic-release knows nothing about ``version_json``, so
    deputy applies it — and it has to land *inside* the version commit, not after
    it. semantic-release stages its own version files and then runs a plain
    ``git commit`` (no pathspec), which commits whatever the index holds; staging
    here first therefore folds these files into the same commit it tags.

    The version comes from ``semantic-release version --print``, which reports
    exactly what the real run a moment later will compute (same commits, same
    config, same flag). When it reports that nothing will be released, the files
    are deliberately left alone: bumping them would leave the tree dirty with no
    commit to carry the change. That question is asked through
    :func:`planned_version`, which raises rather than guessing — a release that
    quietly skipped the bump is the failure this feature exists to prevent.

    Returns the version written, or None when no release is due. Any failure to
    apply a declaration raises ``JsonVersionError`` and aborts the release.
    """
    version = planned_version(config_file, flag, noop_runner)
    if version is None:
        print("No release will be issued; leaving the version_json files untouched.")
        return None

    staged: list[str] = []
    for path in paths:
        update = bump_fn(path, version)
        if update.changed:
            fields = ", ".join(update.fields)
            print(f"Bumped {path}: {update.old_version} -> {version} ({fields})")
            staged.append(path)
        else:
            print(f"{path} is already at {version}; nothing to change")
    stage_fn(staged)
    return version


def run_release(
    config_file: str,
    flag: str | None,
    runner: Runner = _default_release_runner,
    *,
    version_json: Sequence[str] = (),
    prepare_fn: Callable[[Sequence[str], str, str | None], str | None] = prepare_json_versions,
) -> int:
    """Actually bump the version, tag, push, and cut the GitHub Release.

    ``version_json`` lists JSON files whose own version deputy writes itself
    (semantic-release's regex mechanism cannot do it safely — see
    :mod:`deputy.jsonversion`). Left empty, which is the default and what every
    repo without the declaration gets, this behaves exactly as it always has.

    Returns the subprocess return code.
    """
    if version_json:
        prepare_fn(list(version_json), config_file, flag)
    cmd = ["semantic-release", "-c", config_file, "version", "--changelog", "--vcs-release"]
    if flag:
        cmd.append(flag)
    res = runner(cmd)
    return getattr(res, "returncode", 0)

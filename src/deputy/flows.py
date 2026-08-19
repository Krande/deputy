"""End-to-end flows, with every side effect injected so they run under pytest.

``pr_review`` and ``tag_on_merge`` take the GitHub client, output writer, version
calculators, and git seeder as parameters (defaulting to the real adapters). The
CLI wires the real ones; tests wire fakes.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from .actions_io import parse_pull_request
from .actions_io import set_output as _set_output
from .comment import MARKER, render_body
from .github import GitHubClient, upsert_sticky_comment
from .gitops import set_image
from .gitutils import commit_and_push, seed_title_commit
from .labels import DEFAULT_LABEL, LABEL_PALETTE, SILENCE_LABEL, BumpDecision, decide_bump
from .pr_checks import PrChecks, title_ok
from .version import run_release, version_line_for


def pr_review(
    event: dict,
    client: GitHubClient,
    *,
    has_source_key: bool,
    marker: str = MARKER,
    config_file: str = "pyproject.toml",
    ensure_labels: bool = True,
    version_line_fn: Callable[[BumpDecision], str] | None = None,
    seed_fn: Callable[[str], None] | None = None,
    set_output_fn: Callable[[str, str], None] | None = None,
) -> int:
    """Run the PR-review checks, post/refresh the sticky comment, set review_ok.

    Returns 0 when the checks pass, 1 when they fail (so the workflow step goes red).
    """
    version_line_fn = version_line_fn or (lambda d: version_line_for(d, config_file))
    seed_fn = seed_fn or seed_title_commit
    set_output_fn = set_output_fn or _set_output

    pr = parse_pull_request(event)
    labels = list(pr.labels)

    if ensure_labels:
        for name, color in LABEL_PALETTE.items():
            client.ensure_label(name, color)

    # Default a missing release-* label to release-skip (and reflect it locally).
    if not any(label.startswith("release-") for label in labels):
        client.add_labels(pr.number, [DEFAULT_LABEL])
        labels.append(DEFAULT_LABEL)

    decision = decide_bump(labels)
    checks = PrChecks(
        title_ok=title_ok(pr.title),
        label_ok=not decision.multiple,
        has_source_key=has_source_key,
    )

    seed_fn(pr.title)
    version_line = version_line_fn(decision)
    body = marker + "\n" + render_body(checks, version_line)

    if SILENCE_LABEL not in labels:
        upsert_sticky_comment(client, pr.number, marker, body)

    set_output_fn("review_ok", "true" if checks.ok else "false")
    return 0 if checks.ok else 1


def tag_on_merge(
    event: dict,
    *,
    config_file: str = "pyproject.toml",
    release_fn: Callable[[str | None], int] | None = None,
) -> int:
    """On a merged PR, run semantic-release to tag/release per the release label.

    Returns the release subprocess's return code, or 0 when nothing is released.
    """
    release_fn = release_fn or (lambda flag: run_release(config_file, flag))

    pr = parse_pull_request(event)
    if not pr.merged:
        print("PR is not merged; nothing to do.")
        return 0

    decision = decide_bump(pr.labels)
    if not decision.release:
        print(f"No release ({decision.reason}).")
        return 0

    print(f"Releasing (label={decision.label}, flag={decision.flag or 'auto'}) …")
    return release_fn(decision.flag)


def gitops_update(
    *,
    repo_dir: str,
    file: str,
    kind: str,
    image_path: str,
    image: str,
    push: bool = True,
    message: str | None = None,
    reader: Callable[[str], str] | None = None,
    writer: Callable[[str, str], None] | None = None,
    commit_fn: Callable[..., None] | None = None,
    set_image_fn: Callable[..., tuple[str, int]] = set_image,
) -> int:
    """Bump a container image reference in a checked-out gitops repo and push.

    Reads ``<repo_dir>/<file>``, sets the image at ``image_path`` for every doc
    of ``kind`` to ``image``, writes it back, then commits (and optionally
    pushes). All I/O is injected so the flow unit-tests without a real repo. The
    gitops repo is expected to be already checked out with push auth in place
    (the workflow's checkout step handles the deploy key).

    Returns 0 on success; raises if the kind/path don't match (fail loud rather
    than push an unchanged file).
    """
    reader = reader or (lambda p: pathlib.Path(p).read_text(encoding="utf-8"))
    writer = writer or (lambda p, text: pathlib.Path(p).write_text(text, encoding="utf-8"))
    commit_fn = commit_fn or commit_and_push

    full = str(pathlib.PurePosixPath(repo_dir) / file)
    patched, matched = set_image_fn(reader(full), kind=kind, image_path=image_path, image=image)
    writer(full, patched)
    print(f"Patched {matched} {kind} doc(s) in {file} -> {image}")

    commit_fn(
        repo_dir,
        [file],
        message or f"chore(gitops): set {kind} image to {image}",
        push=push,
    )
    return 0

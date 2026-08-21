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
from .config import fill_template
from .github import GitHubClient, upsert_sticky_comment
from .gitops import set_image
from .gitutils import commit_and_push, commit_to_branch, seed_title_commit
from .labels import DEFAULT_LABEL, LABEL_PALETTE, SILENCE_LABEL, BumpDecision, decide_bump
from .pr_checks import PrChecks, title_ok
from .release_watch import (
    DEFAULT_BRANCH_PREFIX,
    DEFAULT_LABELS,
    DEFAULT_PR_TITLE,
    MARKER_TEMPLATE,
    find_pinned,
    is_newer,
    normalize_version,
    pick_latest_tag,
    render_pr_body,
    replace_pinned,
)
from .sshkey import key_basename, keygen_argv, resolve_email, unique_path
from .version import run_release, version_line_for


def pr_review(
    event: dict,
    client: GitHubClient,
    *,
    has_source_key: bool,
    marker: str = MARKER,
    config_file: str = "pyproject.toml",
    default_label: str = DEFAULT_LABEL,
    ensure_labels: bool = True,
    version_line_fn: Callable[[BumpDecision], str] | None = None,
    seed_fn: Callable[[str], None] | None = None,
    set_output_fn: Callable[[str, str], None] | None = None,
) -> int:
    """Run the PR-review checks, post/refresh the sticky comment, set review_ok.

    ``default_label`` is the release-* label applied when the PR carries none —
    the repo's configured default (``[pr_review].default_label``), falling back
    to ``release-skip``.

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

    # Default a missing release-* label to the configured default (and reflect
    # it locally).
    if not any(label.startswith("release-") for label in labels):
        client.add_labels(pr.number, [default_label])
        labels.append(default_label)

    decision = decide_bump(labels, default_label)
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
    default_label: str = DEFAULT_LABEL,
    release_fn: Callable[[str | None], int] | None = None,
) -> int:
    """On a merged PR, run semantic-release to tag/release per the release label.

    A PR with no release-* label (pr-review never ran, or the label was removed)
    falls back to the same configured ``default_label`` the review flow applies.

    Returns the release subprocess's return code, or 0 when nothing is released.
    """
    release_fn = release_fn or (lambda flag: run_release(config_file, flag))

    pr = parse_pull_request(event)
    if not pr.merged:
        print("PR is not merged; nothing to do.")
        return 0

    decision = decide_bump(pr.labels, default_label)
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


def latest_upstream_version(client: GitHubClient, repo: str) -> str | None:
    """Latest upstream version: the newest GitHub Release, else newest semver tag.

    Returns the raw tag string (e.g. ``"v1.2.3"``) or None when the upstream repo
    has neither a release nor a semver-shaped tag.
    """
    release = client.latest_release(repo)
    if release is not None:
        return release.tag_name
    return pick_latest_tag(client.list_tags(repo))


def release_watch(
    targets: list[dict],
    client: GitHubClient,
    *,
    repo_dir: str = ".",
    base: str = "main",
    dry_run: bool = False,
    reader: Callable[[str], str] | None = None,
    writer: Callable[[str, str], None] | None = None,
    commit_fn: Callable[..., None] | None = None,
) -> int:
    """For each watched target, open/update a PR bumping a pinned dependency.

    Per target: look up the upstream repo's latest release/tag, read the pinned
    version out of the consumer file, and if upstream is strictly newer, rewrite
    the pin, commit it to a per-target branch, and open a PR (or update the
    existing open one — idempotent by head branch). Up-to-date targets are no-ops.
    With ``dry_run`` the change is computed and printed but nothing is written,
    committed, or opened. All I/O is injected so this unit-tests without a repo or
    network. Returns 0 when every target succeeds, 1 if any pattern failed to
    match (so the workflow step goes red on a stale/misconfigured pattern).
    """
    reader = reader or (lambda p: pathlib.Path(p).read_text(encoding="utf-8"))
    writer = writer or (lambda p, text: pathlib.Path(p).write_text(text, encoding="utf-8"))
    commit_fn = commit_fn or commit_to_branch

    rc = 0
    for target in targets:
        rc |= _watch_one(
            target,
            client,
            repo_dir=repo_dir,
            base=base,
            dry_run=dry_run,
            reader=reader,
            writer=writer,
            commit_fn=commit_fn,
        )
    return rc


def _watch_one(
    target: dict,
    client: GitHubClient,
    *,
    repo_dir: str,
    base: str,
    dry_run: bool,
    reader: Callable[[str], str],
    writer: Callable[[str, str], None],
    commit_fn: Callable[..., None],
) -> int:
    name = target["name"]
    upstream = target["repo"]
    file = target["file"]
    pattern = target["pattern"]

    latest = latest_upstream_version(client, upstream)
    if latest is None:
        print(f"[{name}] no upstream release or semver tag on {upstream}; skipping")
        return 0

    full = str(pathlib.PurePosixPath(repo_dir) / file)
    text = reader(full)
    current = find_pinned(text, pattern)
    if current is None:
        print(f"[{name}] pattern did not match anything in {file}; skipping (check the pattern)")
        return 1

    new_version = normalize_version(latest)
    if not is_newer(new_version, current):
        print(f"[{name}] up to date (pinned {current}, latest {new_version}); nothing to do")
        return 0

    branch = f"{target.get('branch_prefix', DEFAULT_BRANCH_PREFIX)}/{name}"
    title = fill_template(target.get("pr_title", DEFAULT_PR_TITLE), name=name, version=new_version)
    marker = MARKER_TEMPLATE.format(name=name)
    body = render_pr_body(name, current, new_version, upstream, marker)
    labels = list(target.get("labels", DEFAULT_LABELS))

    if dry_run:
        print(f"[{name}] would bump {current} -> {new_version} on {branch} (dry-run)")
        return 0

    new_text, count = replace_pinned(text, pattern, new_version)
    writer(full, new_text)
    commit_fn(
        repo_dir,
        branch,
        [file],
        f"chore({name}): bump {current} -> {new_version}",
        push=True,
    )
    print(f"[{name}] patched {count} pin(s) in {file}: {current} -> {new_version}")

    existing = client.find_open_pr(branch)
    if existing is not None:
        client.update_pull_request(existing.number, title=title, body=body)
        number = existing.number
        print(f"[{name}] updated existing PR #{number}")
    else:
        pr = client.create_pull_request(head=branch, base=base, title=title, body=body)
        number = pr.number
        print(f"[{name}] opened PR #{number}")

    if labels:
        client.add_labels(number, labels)
    return 0


def create_sshkey(
    *,
    cli_email: str | None,
    key_dir: pathlib.Path,
    state_path: pathlib.Path,
    print_private: bool,
    load_email: Callable[[pathlib.Path], str | None],
    save_email: Callable[[pathlib.Path, str], None],
    exists: Callable[[pathlib.Path], bool],
    make_dir: Callable[[pathlib.Path], None],
    runner: Callable[[list[str]], object],
    read_text: Callable[[pathlib.Path], str],
) -> dict:
    """Generate a passphrase-less ed25519 key, remembering the email for next time.

    Resolves the email (``cli_email`` wins, else the remembered one), picks a
    unique filename under ``key_dir``, runs ssh-keygen, and persists the email.
    Returns the email (and whether it came from the flag or the store), the
    private/public key paths, and the public key text; the private key text is
    included only when ``print_private`` is set. Every side effect is injected.
    """
    stored = load_email(state_path)
    email = resolve_email(cli_email, stored)
    priv = unique_path(key_dir, key_basename(email), exists)
    pub = pathlib.Path(str(priv) + ".pub")

    make_dir(priv.parent)
    runner(keygen_argv(priv, email))
    save_email(state_path, email)

    result = {
        "email": email,
        "email_source": "flag" if cli_email else "remembered",
        "private_key_path": str(priv),
        "public_key_path": str(pub),
        "public_key": read_text(pub).strip(),
    }
    if print_private:
        result["private_key"] = read_text(priv)
    return result

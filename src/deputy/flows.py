"""End-to-end flows, with every side effect injected so they run under pytest.

``pr_review`` and ``tag_on_merge`` take the GitHub client, output writer, version
calculators, and git seeder as parameters (defaulting to the real adapters). The
CLI wires the real ones; tests wire fakes.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence

from .actions_io import parse_pull_request
from .actions_io import set_output as _set_output
from .comment import MARKER, render_body
from .config import fill_template
from .github import GitHubClient, upsert_sticky_comment
from .gitops import container_images, set_container_images, set_image
from .gitutils import commit_and_push, commit_to_branch, seed_title_commit
from .labels import DEFAULT_LABEL, LABEL_PALETTE, SILENCE_LABEL, BumpDecision, decide_bump
from .pr_checks import PrChecks, title_ok
from .release_watch import (
    DEFAULT_BRANCH_PREFIX,
    DEFAULT_LABELS,
    DEFAULT_ON_OTHER_IMAGE,
    DEFAULT_PR_TITLE,
    DEFAULT_TAG_TEMPLATE,
    MARKER_TEMPLATE,
    find_pinned,
    image_target_containers,
    is_newer,
    normalize_version,
    oldest_version,
    pick_latest_tag,
    release_pin,
    render_image_pr_body,
    render_pr_body,
    replace_pinned,
    target_files,
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
    to ``release-skip``. Because it is only a stand-in, deputy also *removes* it
    again once an explicit release-* label appears alongside it: two release-*
    labels mean no release is cut at all, so leaving its own default behind is
    how deputy would quietly suppress the release it exists to arrange. Two
    explicit labels are left alone and still fail the check.

    Returns 0 when the checks pass, 1 when they fail (so the workflow step goes red).
    """
    version_line_fn = version_line_fn or (lambda d: version_line_for(d, config_file))
    seed_fn = seed_fn or seed_title_commit
    set_output_fn = set_output_fn or _set_output

    pr = parse_pull_request(event)

    # Labels come from the API, not from the event payload.
    #
    # The payload's label list is a snapshot frozen when the event fired, so a
    # label applied in the seconds afterwards can never appear in it, however
    # long this job takes to start. That is not a rare race — opening a PR and
    # then labelling it is the ordinary shape of `gh pr create` followed by
    # `gh pr edit --add-label`, and of every UI flow where the label is chosen
    # after the PR exists. Deputy would read "no release-* label", add its
    # default, and leave the PR carrying two of them: which releases nothing at
    # all, the exact outcome the default exists to prevent.
    #
    # Falling back to the payload keeps a transient API failure from turning a
    # review red. It is never worse than the old behaviour, which used the stale
    # list unconditionally.
    try:
        labels = list(client.list_labels(pr.number))
    except Exception:  # any read failure degrades to the payload
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

    # Take that default back off again once an explicit label supersedes it.
    # The default is a fallback: the moment someone states a choice, deputy's
    # stand-in has to go, or the PR carries two release-* labels and releases
    # nothing at all. Removing it here is what makes "just add the label I
    # actually want" work, including after a remove-and-re-add of the real label
    # (which re-triggers the review with no labels at all, so the default comes
    # straight back). Only ever the repo's own default, never a second explicit
    # label.
    superseded = decision.superseded_default
    if superseded is not None and superseded in labels:
        client.remove_label(pr.number, superseded)
        labels.remove(superseded)

    checks = PrChecks(
        title_ok=title_ok(pr.title),
        label_ok=not decision.multiple,
        has_source_key=has_source_key,
    )

    seed_fn(pr.title)
    version_line = version_line_fn(decision)
    note = (
        f"Removed the default `{superseded}` label — `{decision.label}` supersedes it."
        if superseded is not None
        else None
    )
    body = marker + "\n" + render_body(checks, version_line, note=note)

    if SILENCE_LABEL not in labels:
        upsert_sticky_comment(client, pr.number, marker, body)

    set_output_fn("review_ok", "true" if checks.ok else "false")
    return 0 if checks.ok else 1


def tag_on_merge(
    event: dict,
    *,
    config_file: str = "pyproject.toml",
    default_label: str = DEFAULT_LABEL,
    version_json: Sequence[str] = (),
    release_fn: Callable[[str | None], int] | None = None,
) -> int:
    """On a merged PR, run semantic-release to tag/release per the release label.

    A PR with no release-* label (pr-review never ran, or the label was removed)
    falls back to the same configured ``default_label`` the review flow applies.
    A PR that merged still carrying the default *next to* an explicit label
    releases at the explicit label's level: the same supersession rule the review
    flow applies, repeated here so a PR that merged before the review could tidy
    it up still releases instead of silently doing nothing.

    ``version_json`` are ``[release].version_json`` paths — JSON files (npm
    ``package.json`` / ``package-lock.json``) whose own version deputy writes
    itself, since semantic-release's regex mechanism would rewrite every
    dependency in a lockfile. Empty by default, so a repo that declares none
    behaves exactly as before.

    Returns the release subprocess's return code, or 0 when nothing is released.
    """
    release_fn = release_fn or (
        lambda flag: run_release(config_file, flag, version_json=version_json)
    )

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
    upstream_client: GitHubClient | None = None,
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

    ``client`` opens the PRs on the consumer repo; ``upstream_client`` (default:
    the same client) looks the upstream releases up. They differ when the two live
    on different forges -- PRs on a Forgejo gitops repo, releases on GitHub.
    """
    reader = reader or (lambda p: pathlib.Path(p).read_text(encoding="utf-8"))
    writer = writer or (lambda p, text: pathlib.Path(p).write_text(text, encoding="utf-8"))
    commit_fn = commit_fn or commit_to_branch
    upstream_client = upstream_client or client

    rc = 0
    for target in targets:
        rc |= _watch_one(
            target,
            client,
            upstream_client,
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
    upstream_client: GitHubClient,
    *,
    repo_dir: str,
    base: str,
    dry_run: bool,
    reader: Callable[[str], str],
    writer: Callable[[str, str], None],
    commit_fn: Callable[..., None],
) -> int:
    if target.get("image") is not None:
        return _watch_image(
            target,
            client,
            upstream_client,
            repo_dir=repo_dir,
            base=base,
            dry_run=dry_run,
            reader=reader,
            writer=writer,
            commit_fn=commit_fn,
        )

    name = target["name"]
    upstream = target["repo"]
    files = target_files(target)
    pattern = target["pattern"]

    latest = latest_upstream_version(upstream_client, upstream)
    if latest is None:
        print(f"[{name}] no upstream release or semver tag on {upstream}; skipping")
        return 0

    # Read every file up front: a pattern that misses one of them is a config
    # error, and bailing before the first write keeps a multi-file target from
    # landing a half-done bump.
    texts: dict[str, str] = {}
    pinned: list[str] = []
    for file in files:
        full = str(pathlib.PurePosixPath(repo_dir) / file)
        text = reader(full)
        found = find_pinned(text, pattern)
        if found is None:
            print(
                f"[{name}] pattern did not match anything in {file}; skipping (check the pattern)"
            )
            return 1
        texts[file] = text
        pinned.append(found)

    # With several files, compare against the oldest pin so a drifted file is
    # still caught up rather than treated as up to date.
    current = oldest_version(pinned)
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

    for file, was in zip(files, pinned, strict=True):
        new_text, count = replace_pinned(texts[file], pattern, new_version)
        writer(str(pathlib.PurePosixPath(repo_dir) / file), new_text)
        print(f"[{name}] patched {count} pin(s) in {file}: {was} -> {new_version}")
    commit_fn(
        repo_dir,
        branch,
        list(files),
        f"chore({name}): bump {current} -> {new_version}",
        push=True,
    )
    _open_or_update_pr(
        client, name=name, branch=branch, base=base, title=title, body=body, labels=labels
    )
    return 0


def _watch_image(
    target: dict,
    client: GitHubClient,
    upstream_client: GitHubClient,
    *,
    repo_dir: str,
    base: str,
    dry_run: bool,
    reader: Callable[[str], str],
    writer: Callable[[str, str], None],
    commit_fn: Callable[..., None],
) -> int:
    """release-watch for an ``image`` target: containers by name, full image refs.

    Versions are compared only while every selected container runs a release of
    ``image``. A container on anything else -- a development build from another
    registry, a ``latest`` tag -- has no version to compare, so by default the PR
    puts it back on the latest release; ``on_other_image = "skip"`` leaves it.
    """
    name = target["name"]
    upstream = target["repo"]
    files = target_files(target)
    containers = image_target_containers(target)
    release_image = target["image"]

    latest = latest_upstream_version(upstream_client, upstream)
    if latest is None:
        print(f"[{name}] no upstream release or semver tag on {upstream}; skipping")
        return 0

    # Read every file up front, as the pattern path does: a container that cannot
    # be found is a config error, and failing before the first write keeps a
    # multi-file bump whole.
    texts: dict[str, str] = {}
    running: list[tuple[str, str, str]] = []  # (file, container, image)
    for file in files:
        text = reader(str(pathlib.PurePosixPath(repo_dir) / file))
        found = container_images(text, containers)
        if not found:
            print(
                f"[{name}] no container named any of {containers} in {file}; "
                "skipping (check containers)"
            )
            return 1
        texts[file] = text
        running.extend((file, container, image) for container, image in found)
    missing = sorted(set(containers) - {container for _, container, _ in running})
    if missing:
        print(
            f"[{name}] container(s) {', '.join(missing)} found in no file; "
            "skipping (check containers)"
        )
        return 1

    new_version = normalize_version(latest)
    tag = fill_template(target.get("tag_template", DEFAULT_TAG_TEMPLATE), version=new_version)
    new_ref = f"{release_image}:{tag}"
    pins = [release_pin(image, release_image) for _, _, image in running]
    released = [pin for pin in pins if pin is not None]
    if len(released) < len(pins):
        others = sorted(
            {image for (_, _, image), pin in zip(running, pins, strict=True) if not pin}
        )
        if target.get("on_other_image", DEFAULT_ON_OTHER_IMAGE) == "skip":
            print(
                f"[{name}] running {', '.join(others)}, not a release of {release_image}; "
                "skipping (on_other_image = skip)"
            )
            return 0
        current = ", ".join(others)
    else:
        current = oldest_version(released)
        if not is_newer(new_version, current):
            print(f"[{name}] up to date (running {current}, latest {new_version}); nothing to do")
            return 0

    branch = f"{target.get('branch_prefix', DEFAULT_BRANCH_PREFIX)}/{name}"
    title = fill_template(target.get("pr_title", DEFAULT_PR_TITLE), name=name, version=new_version)
    marker = MARKER_TEMPLATE.format(name=name)
    was = [f"`{file}` {container}: `{image}`" for file, container, image in running]
    body = render_image_pr_body(name, was, new_ref, upstream, marker)
    labels = list(target.get("labels", DEFAULT_LABELS))

    if dry_run:
        print(f"[{name}] would set {new_ref} (running {current}) on {branch} (dry-run)")
        return 0

    for file in files:
        new_text, count = set_container_images(texts[file], containers, new_ref)
        writer(str(pathlib.PurePosixPath(repo_dir) / file), new_text)
        print(f"[{name}] set {count} container image(s) in {file} to {new_ref}")
    commit_fn(repo_dir, branch, list(files), f"chore({name}): set image to {new_ref}", push=True)
    _open_or_update_pr(
        client, name=name, branch=branch, base=base, title=title, body=body, labels=labels
    )
    return 0


def _open_or_update_pr(
    client: GitHubClient,
    *,
    name: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    labels: list[str],
) -> None:
    """Open the target's PR, or update the open one on the same head branch."""
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

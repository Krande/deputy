"""Command-line entrypoints wiring the real adapters to the flows.

Config comes from three layers, most-specific first:

    CLI flag / env var   ->   deputy.toml   ->   built-in default

Usage (in a workflow, after installing deputy):

    deputy pr-review
    deputy tag-on-merge
    deputy gitops-update --target web-app-beta --tag sha-abc-42   # from deputy.toml
    deputy gitops-update --all --tag sha-abc-42                       # every target
    deputy gitops-update --image ghcr.io/o/a:1.2.3 \\                  # ad-hoc, no toml
        --file deploy.yaml --kind Deployment \\
        --image-path spec.template.spec.containers.0.image

Env used:
    GITHUB_TOKEN        REST auth for comments/labels (pr-review)
    GITHUB_REPOSITORY   "owner/repo" (provided by Actions)
    GITHUB_EVENT_PATH   event payload JSON (provided by Actions)
    HAS_SOURCE_KEY      "true"/"false" — informational SOURCE_KEY presence (pr-review)
    DEPUTY_TOML         path to deputy.toml (default: ./deputy.toml)
    DEPUTY_MARKER       override the sticky-comment marker
    DEPUTY_CONFIG       use an existing semantic-release config file as-is
                        (back-compat; otherwise deputy renders one from [release])
"""

from __future__ import annotations

import argparse
import os
import sys

from .actions_io import read_event
from .comment import MARKER
from .config import (
    compose_image,
    fill_template,
    find_target,
    gitops_targets,
    load_config,
    resolve_image_ref,
    write_release_config,
)
from .flows import gitops_update, pr_review, tag_on_merge
from .github import RestGitHubClient


def _release_config_path(cfg: dict) -> str:
    """Path to the semantic-release config for the version calc / tagger.

    ``DEPUTY_CONFIG`` (back-compat) points at an existing config file used as-is;
    otherwise deputy renders one from its defaults plus deputy.toml's [release].
    """
    explicit = os.environ.get("DEPUTY_CONFIG")
    return explicit if explicit else write_release_config(cfg.get("release"))


def _marker(cfg: dict) -> str:
    return os.environ.get("DEPUTY_MARKER") or cfg.get("pr_review", {}).get("marker") or MARKER


def cmd_pr_review(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    client = RestGitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    return pr_review(
        read_event(),
        client,
        has_source_key=os.environ.get("HAS_SOURCE_KEY") == "true",
        marker=_marker(cfg),
        config_file=_release_config_path(cfg),
    )


def cmd_tag_on_merge(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    return tag_on_merge(read_event(), config_file=_release_config_path(cfg))


def cmd_gitops_update(args: argparse.Namespace) -> int:
    if args.all and args.target:
        _fail("use either --target or --all, not both")

    cfg = load_config(args.config)

    if args.all or args.target:
        if args.all:
            targets = gitops_targets(cfg)
            if not targets:
                _fail("--all given but deputy.toml declares no [[gitops]] targets")
        else:
            targets = [find_target(cfg, name) for name in args.target]
        if not (args.tag or args.image):
            _fail("--target/--all needs --tag (or an explicit --image)")
        rc = 0
        for t in targets:
            rc |= _bump_one(args, cfg, t)
        return rc

    # Ad-hoc mode: everything on the command line, no deputy.toml needed.
    missing = [
        name
        for name, val in (
            ("--image", args.image),
            ("--file", args.file),
            ("--kind", args.kind),
            ("--image-path", args.image_path),
        )
        if not val
    ]
    if missing:
        _fail(f"without --target/--all these are required: {', '.join(missing)}")
    return _bump_one(args, cfg, target=None)


def _bump_one(args: argparse.Namespace, cfg: dict, target: dict | None) -> int:
    """Resolve one target's fields (CLI flag > deputy.toml > default) and bump it."""
    t = target or {}
    base_ref = args.image or resolve_image_ref(cfg, t["image"])
    image = compose_image(base_ref, args.tag)
    name = t.get("name", "")
    template = args.message or t.get("message") or "chore(gitops): set {kind} image to {image}"
    kind = args.kind or t.get("kind")
    message = fill_template(template, name=name, tag=args.tag or "", image=image, kind=kind or "")
    return gitops_update(
        repo_dir=args.repo_dir or t.get("repo_dir", "."),
        file=args.file or t["file"],
        kind=kind,
        image_path=args.image_path or t["image_path"],
        image=image,
        push=not args.no_push,
        message=message,
    )


def _fail(msg: str) -> int:
    print(f"deputy: error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _add_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=None, help="path to deputy.toml (default: ./deputy.toml)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deputy")
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("pr-review", help="check a PR and post the sticky review comment")
    _add_config_arg(pr)

    tg = sub.add_parser("tag-on-merge", help="tag/release a merged PR per its release label")
    _add_config_arg(tg)

    g = sub.add_parser(
        "gitops-update", help="bump a container image in a gitops YAML file and push"
    )
    _add_config_arg(g)
    g.add_argument(
        "--target", action="append", default=[], help="deputy.toml [[gitops]] name (repeatable)"
    )
    g.add_argument("--all", action="store_true", help="bump every deputy.toml [[gitops]] target")
    g.add_argument("--tag", default=None, help="tag to apply to the target's image ref")
    g.add_argument(
        "--image", default=None, help="full image ref (ad-hoc) or ref override for a target"
    )
    g.add_argument("--repo-dir", default=None, help="path to the checked-out gitops repo")
    g.add_argument("--file", default=None, help="YAML file to patch, relative to --repo-dir")
    g.add_argument("--kind", default=None, help="k8s kind to match (e.g. Deployment)")
    g.add_argument("--image-path", default=None, help="dotted path to the image field")
    g.add_argument(
        "--message", default=None, help="commit message; {name}/{tag}/{image}/{kind} templated"
    )
    g.add_argument("--no-push", action="store_true", help="commit but do not push")

    args = parser.parse_args(argv)

    if args.command == "pr-review":
        return cmd_pr_review(args)
    if args.command == "tag-on-merge":
        return cmd_tag_on_merge(args)
    if args.command == "gitops-update":
        return cmd_gitops_update(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Command-line entrypoints wiring the real adapters to the flows.

Usage (in a workflow, after installing deputy):

    deputy pr-review
    deputy tag-on-merge
    deputy gitops-update --repo-dir gitops --file path/to/deploy.yaml \\
        --kind Deployment \\
        --image-path spec.template.spec.containers.0.image \\
        --image ghcr.io/owner/app:1.2.3

Env used:
    GITHUB_TOKEN        REST auth for comments/labels (pr-review)
    GITHUB_REPOSITORY   "owner/repo" (provided by Actions)
    GITHUB_EVENT_PATH   event payload JSON (provided by Actions)
    HAS_SOURCE_KEY      "true"/"false" — informational SOURCE_KEY presence (pr-review)
    DEPUTY_MARKER       override the sticky-comment marker (default: <!-- DEPUTY_PR_BOT -->)
    DEPUTY_CONFIG       override the semantic-release config file (default: pyproject.toml)
"""

from __future__ import annotations

import argparse
import os
import sys

from .actions_io import read_event
from .comment import MARKER
from .flows import gitops_update, pr_review, tag_on_merge
from .github import RestGitHubClient


def _config_file() -> str:
    return os.environ.get("DEPUTY_CONFIG", "pyproject.toml")


def cmd_pr_review() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    client = RestGitHubClient(token, repo)
    return pr_review(
        read_event(),
        client,
        has_source_key=os.environ.get("HAS_SOURCE_KEY") == "true",
        marker=os.environ.get("DEPUTY_MARKER", MARKER),
        config_file=_config_file(),
    )


def cmd_tag_on_merge() -> int:
    return tag_on_merge(read_event(), config_file=_config_file())


def cmd_gitops_update(args: argparse.Namespace) -> int:
    return gitops_update(
        repo_dir=args.repo_dir,
        file=args.file,
        kind=args.kind,
        image_path=args.image_path,
        image=args.image,
        push=not args.no_push,
        message=args.message,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deputy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pr-review", help="check a PR and post the sticky review comment")
    sub.add_parser("tag-on-merge", help="tag/release a merged PR per its release label")

    g = sub.add_parser(
        "gitops-update", help="bump a container image in a gitops YAML file and push"
    )
    g.add_argument("--repo-dir", default=".", help="path to the checked-out gitops repo")
    g.add_argument("--file", required=True, help="YAML file to patch, relative to --repo-dir")
    g.add_argument("--kind", required=True, help="k8s kind to match (e.g. Deployment)")
    g.add_argument(
        "--image-path",
        required=True,
        help="dotted path to the image field (e.g. spec.template.spec.containers.0.image)",
    )
    g.add_argument("--image", required=True, help="full image reference to set")
    g.add_argument("--message", default=None, help="commit message (default: auto)")
    g.add_argument("--no-push", action="store_true", help="commit but do not push")

    args = parser.parse_args(argv)

    if args.command == "pr-review":
        return cmd_pr_review()
    if args.command == "tag-on-merge":
        return cmd_tag_on_merge()
    if args.command == "gitops-update":
        return cmd_gitops_update(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())

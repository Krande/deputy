"""deputy — your repo's CI deputy.

The GitHub CI logic that usually lives as inline ``shell: python`` blocks inside
Actions workflows, extracted into a small, unit-testable package. Workflows
``pip install`` deputy and call ``deputy <command>``; the logic runs and is
tested locally instead of by pushing commits and reading Actions logs.

Commands: ``pr-review`` (conventional-title + release-label checks, next-version
preview, one sticky comment), ``tag-on-merge`` (semantic-release version bump +
tag + GitHub Release), ``gitops-update`` (bump a container image reference in a
gitops YAML file and push), and ``release-watch`` (open/update PRs bumping pinned
dependencies to watched upstream repos' latest releases).

Design: pure decision logic (:mod:`deputy.labels`, :mod:`deputy.pr_checks`,
:mod:`deputy.comment`, :mod:`deputy.gitops`) with no I/O, plus thin, injectable
adapters for the GitHub API (:mod:`deputy.github`), the Actions runtime
(:mod:`deputy.actions_io`), git, and semantic-release. :mod:`deputy.flows` wires
them together and takes every side-effecting dependency as an argument so the
whole thing runs under pytest with fakes — no GitHub, no live Actions runner.
"""

__version__ = "0.3.1"

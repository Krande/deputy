# deputy

Your repo's CI deputy — PR checks, release tagging, and gitops image bumps as
testable Python, not logic buried in Actions YAML. Workflows `pip install`
deputy and call `deputy <command>`; the logic runs and is tested **locally**
instead of by pushing commits and reading Actions logs.

```sh
pip install "deputy @ git+https://github.com/Krande/deputy.git@v0.1.0"
```

## Commands

| Command | What it does |
|---|---|
| `deputy pr-review` | Check a PR (conventional title + exactly one `release-*` label), compute the next version for information, post/update one sticky comment, set the `review_ok` output, exit non-zero if a check fails. |
| `deputy tag-on-merge` | On a merged PR, run [python-semantic-release](https://python-semantic-release.readthedocs.io/) per the release label to bump the version, tag `vX.Y.Z`, push it, and cut a GitHub Release. |
| `deputy gitops-update` | Bump a container image reference in a gitops YAML file (comment-preserving, matching a k8s `kind`) and commit + push. |

### gitops-update

```sh
deputy gitops-update \
  --repo-dir gitops \
  --file cluster/app-deployment.yaml \
  --kind Deployment \
  --image-path spec.template.spec.containers.0.image \
  --image ghcr.io/owner/app:1.2.3
```

Patches only the image line (ruamel round-trip preserves comments, key order,
and indentation), for every document whose top-level `kind` matches. Fails loud
if the kind/path don't match rather than pushing an unchanged file. The gitops
repo is expected to be already checked out with push auth in place (the
workflow's checkout step handles the deploy key), the same way `tag-on-merge`
relies on the checkout's `SOURCE_KEY`.

## Why

Logic in YAML can't be run, tested, or debugged locally. The bug deputy was born
from — an empty PR comment caused by semantic-release's newline-less `tag=` line
corrupting the next step's `GITHUB_OUTPUT` — needed a live PR and log forensics
to find. Here it's a one-line unit test
(`test_version.py::test_isolated_env_strips_the_actions_output_handshake`).

## Design

Pure decision logic with no I/O, plus thin injectable adapters:

```
labels.py      release-* label -> bump decision              (pure)
pr_checks.py   conventional-title + one-label checks         (pure)
comment.py     render the sticky markdown body               (pure)
gitops.py      patch a container image in YAML text          (pure)
version.py     semantic-release wrappers (env-isolated, injectable runner)
actions_io.py  read the event payload; write GITHUB_OUTPUT   (heredoc-safe)
github.py      GitHubClient protocol + REST impl + sticky upsert
gitutils.py    git helpers (injectable runner)
flows.py       pr_review()/tag_on_merge()/gitops_update() — every side effect is a parameter
cli.py         wire the real adapters from env/args; argparse entrypoints
```

`flows.py` takes the GitHub client, file reader/writer, version calculator, and
git helpers as arguments, so every flow runs under pytest with in-memory fakes
(`tests/fakes.py`) — no GitHub, no git, no semantic-release, no Actions runner.

## Configuration

Per-repo overrides via environment variables (so the defaults stay generic):

| Env | Default | Purpose |
|---|---|---|
| `DEPUTY_MARKER` | `<!-- DEPUTY_PR_BOT -->` | Hidden marker identifying the single sticky comment. Set to a previous bot's marker to keep an existing thread. |
| `DEPUTY_CONFIG` | `pyproject.toml` | semantic-release config file for `pr-review`/`tag-on-merge`. |

## Develop & test

```sh
python -m venv .venv && . .venv/Scripts/activate   # or bin/activate on POSIX
pip install -e . pytest ruff
pytest -q
ruff format --check src tests && ruff check src tests
```

Tests need nothing installed beyond the dev deps — `tests/conftest.py` puts
`src/` on the path.

## Extending

Add a command: a pure function or two for the decision, an adapter method if it
needs a new side effect, a `flows.py` function that takes those as parameters, a
`cli.py` subcommand that wires the real ones, and tests using the fakes. Keep
side effects out of the pure modules.

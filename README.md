# deputy

Your repo's CI deputy — PR checks, release tagging, and gitops image bumps as
testable Python, not logic buried in Actions YAML. Workflows `pip install`
deputy and call `deputy <command>`; the logic runs and is tested **locally**
instead of by pushing commits and reading Actions logs.

```sh
pip install "deputy @ git+https://github.com/Krande/deputy.git@v0.3.0"
```

## Commands

| Command | What it does |
|---|---|
| `deputy pr-review` | Check a PR (conventional title + exactly one `release-*` label), compute the next version for information, post/update one sticky comment, set the `review_ok` output, exit non-zero if a check fails. |
| `deputy tag-on-merge` | On a merged PR, run [python-semantic-release](https://python-semantic-release.readthedocs.io/) per the release label to bump the version, tag `vX.Y.Z`, push it, and cut a GitHub Release. |
| `deputy gitops-update` | Bump a container image reference in a gitops YAML file (comment-preserving, matching a k8s `kind`) and commit + push. |
| `deputy release-watch` | On a schedule, check watched upstream repos for a newer release/tag than the version pinned in this repo, and open (or update) a PR bumping the pin. |
| `deputy sshkey` | Generate a passphrase-less ed25519 SSH key (e.g. a CI deploy key), remembering the email so it auto-fills next time, into a unique filename. `--print` also dumps the private key for pasting into a secret. |

### sshkey

Create a deploy key without memorising `ssh-keygen` flags. The email is
remembered (in `~/.config/deputy/sshkey.json`) and reused when you omit `--email`,
and the filename is made unique so repeat runs never clobber an earlier key.

```sh
deputy sshkey --email dev@example.com          # writes ~/.ssh/deputy_ed25519_dev_at_example_com[.pub]
deputy sshkey                                   # reuses the remembered email
deputy sshkey --out ./keys --print              # custom dir; also prints the private key to paste into a CI secret
```

The **public** key is printed to register as a repo Deploy key; add `--print` to
also print the **private** key (the value for the consumer's Actions secret).

### gitops-update

Declarative (targets from `deputy.toml`, only the tag changes per run):

```sh
deputy gitops-update --target web-app-beta --tag sha-abc-42   # one target
deputy gitops-update --all --tag sha-abc-42                       # every target
```

Ad-hoc (everything on the command line, no `deputy.toml`):

```sh
deputy gitops-update \
  --repo-dir gitops --file cluster/app-deployment.yaml \
  --kind Deployment --image-path spec.template.spec.containers.0.image \
  --image ghcr.io/owner/app:1.2.3
```

Patches only the image line (ruamel round-trip preserves comments, key order,
and indentation), for every document whose top-level `kind` matches. Fails loud
if the kind/path don't match rather than pushing an unchanged file. The gitops
repo is expected to be already checked out with push auth in place (the
workflow's checkout step handles the deploy key), the same way `tag-on-merge`
relies on the checkout's `SOURCE_KEY`.

### release-watch

Keep a dependency pinned to an upstream repo's latest release, automatically. On
a schedule, deputy checks each watched target's upstream for a newer
release/tag than the version currently pinned in your repo, and opens a PR that
bumps the pin. Targets live in `deputy.toml` (see below).

```sh
deputy release-watch --all                      # check every [[release_watch]] target
deputy release-watch --target some-lib          # just one (repeatable)
deputy release-watch --all --dry-run            # compute + print, open nothing
deputy release-watch --all --base develop       # PRs target a non-default base branch
```

Per target deputy:

1. Looks up the upstream repo's **latest GitHub Release**, falling back to its
   highest **semver tag** when no release is published.
2. Reads the currently-pinned version out of your file via a **regex with one
   capture group** (group 1 is the version).
3. If upstream is strictly newer (semver compare, leading `v` and pre-releases
   handled), rewrites **only** the captured span, commits it to a per-target
   branch (`<branch_prefix>/<name>`), pushes, and opens a PR.

It is **idempotent**: the PR is keyed to the per-target head branch, so a later
run that finds a still-newer release updates the same PR instead of opening a
duplicate. Up-to-date targets are no-ops. A pattern that matches nothing fails
loud (non-zero exit) rather than silently doing nothing.

Auth is the `GH_TOKEN` env var; `GITHUB_REPOSITORY` (`owner/repo`, provided by
Actions) names the repo the PRs are opened on. `--dry-run` needs neither a repo
nor push access.

#### Scheduled workflow (consumer side)

A repo wires it up with a cron workflow that checks out, installs deputy, and
calls `release-watch`. Generic example:

```yaml
# .github/workflows/release-watch.yml
name: release-watch
on:
  schedule:
    - cron: "0 6 * * 1"      # every Monday 06:00 UTC
  workflow_dispatch: {}        # allow manual runs

permissions:
  contents: write             # push the bump branch
  pull-requests: write        # open/update the PR

jobs:
  watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "deputy @ git+https://github.com/Krande/deputy.git@v0.3.0"
      - run: deputy release-watch --all
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
```

## deputy.toml

Keep the static flags in a `deputy.toml` so workflows pass only what changes per
run. Precedence is **CLI flag / env var → `deputy.toml` → built-in default**, so
flags always win. Found at `./deputy.toml` (override with `--config` /
`$DEPUTY_TOML`); everything works with no file at all, on defaults.

```toml
[images]                                    # optional: name -> ref, keeps targets DRY
app = "registry.example.com/web-app"

[[gitops]]                                  # repeatable rollout targets
name       = "web-app-beta"
image      = "app"  # an [images] key, or a full ref inline
repo_dir   = "gitops"
file       = "clusters/prod/web-app.yaml"
kind       = "Deployment"
image_path = "spec.template.spec.containers.0.image"
message    = "chore({name}): deploy {tag}"  # {name}/{tag}/{image}/{kind} templated

[[release_watch]]                           # repeatable dependency-watch targets
name          = "some-lib"                  # label used in branch/commit/PR text
repo          = "owner/some-lib"            # upstream repo to query for the latest release
file          = "requirements.txt"          # file in THIS repo holding the pin
pattern       = 'some-lib==([0-9]+\.[0-9]+\.[0-9]+)'  # regex; group 1 = the version to bump
# optional, with sensible defaults:
pr_title      = "chore: bump {name} to {version}"     # {name}/{version} templated
branch_prefix = "deputy/release-watch"      # head branch is "<branch_prefix>/<name>"
labels        = ["dependencies"]            # labels applied to the PR

[pr_review]
marker = "<!-- MY_PR_BOT -->"               # keep an existing sticky-comment thread

[release]                                   # semantic-release overrides (see below)
version_toml = ["pyproject.toml:project.version"]
```

### `[[release_watch]]` schema

| Field | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | — | Identifier for the target; used in the branch, commit, PR title, and marker. |
| `repo` | yes | — | Upstream repo (`owner/name`) queried for the latest release/tag. |
| `file` | yes | — | Path in **this** repo holding the pinned version. |
| `pattern` | yes | — | Regex locating the pin; **capture group 1** is the version substring rewritten in place (no group → the whole match is replaced). |
| `pr_title` | no | `chore: bump {name} to {version}` | PR title; `{name}` / `{version}` templated. |
| `branch_prefix` | no | `deputy/release-watch` | Head branch is `<branch_prefix>/<name>`. |
| `labels` | no | `["dependencies"]` | Labels applied to the opened/updated PR. |

The upstream tag is normalised (a leading `v` is stripped) before it is spliced
into the captured span, so a `v1.2.3` release lands as `1.2.3`. Put any literal
`v` or quotes **outside** the capture group in your `pattern`.

## Release config is self-contained

deputy ships the semantic-release defaults itself (`tag_format = "v{version}"`,
angular parser, main branch, push over `origin`, upload to the VCS release), so a
repo needs **no** `action_config.toml` / `[tool.semantic_release]` block. At
release time deputy renders a config from *its defaults ⊕ your `[release]`
overrides* and hands that to semantic-release. Override any key under `[release]`
(e.g. `version_toml`, `tag_format`, `commit_parser_options`).

## Releasing

deputy dogfoods its own CI: `pr-review.yaml` and `tag-on-pr-merge.yaml` install
this package and run it against deputy's own PRs. To cut a release, merge a PR
carrying exactly one `release-*` label (`release-patch` / `release-minor` /
`release-major`); `tag-on-merge` then bumps the version (both `pyproject.toml`
and `src/deputy/__init__.py`), tags `vX.Y.Z`, and publishes a GitHub Release.
A `release-skip` PR merges without cutting a tag.

## Why

Logic in YAML can't be run, tested, or debugged locally. The bug deputy was born
from — an empty PR comment caused by semantic-release's newline-less `tag=` line
corrupting the next step's `GITHUB_OUTPUT` — needed a live PR and log forensics
to find. Here it's a one-line unit test
(`test_version.py::test_isolated_env_strips_the_actions_output_handshake`).

## Design

Pure decision logic with no I/O, plus thin injectable adapters:

```
labels.py        release-* label -> bump decision              (pure)
pr_checks.py     conventional-title + one-label checks         (pure)
comment.py       render the sticky markdown body               (pure)
gitops.py        patch a container image in YAML text          (pure)
release_watch.py semver compare + pinned-version rewrite       (pure)
config.py        deputy.toml loader, precedence, release defaults (pure)
version.py       semantic-release wrappers (env-isolated, injectable runner)
actions_io.py    read the event payload; write GITHUB_OUTPUT   (heredoc-safe)
github.py        GitHubClient protocol + REST impl (comments, labels, releases, PRs)
gitutils.py      git helpers (injectable runner)
flows.py         pr_review()/tag_on_merge()/gitops_update()/release_watch() — every side effect is a parameter
cli.py           wire the real adapters from env/args/deputy.toml; argparse entrypoints
```

`flows.py` takes the GitHub client, file reader/writer, version calculator, and
git helpers as arguments, so every flow runs under pytest with in-memory fakes
(`tests/fakes.py`) — no GitHub, no git, no semantic-release, no Actions runner.

## Environment overrides

Prefer `deputy.toml`; these env vars exist for workflows and back-compat and take
precedence over it:

| Env | Purpose |
|---|---|
| `DEPUTY_TOML` | Path to `deputy.toml` (default `./deputy.toml`). |
| `DEPUTY_MARKER` | Sticky-comment marker; overrides `[pr_review].marker`. Set to a previous bot's marker to keep an existing thread. |
| `DEPUTY_CONFIG` | Use an existing semantic-release config file **as-is** instead of rendering one from `[release]` (back-compat). |

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

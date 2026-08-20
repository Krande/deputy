# CHANGELOG



## v0.3.1 (2026-08-20)

### Fix

* fix: accept scoped conventional-commit titles in the PR-review check

The title check was a bare startswith over (feat:/fix:/chore: + ! variants), so
a standard conventional-commit SCOPE failed it — `chore(libs):`, `feat(api):`,
`fix(ui)!:` were all rejected even though the type is recognised. Replace the
prefix tuple with a regex that accepts an optional `(scope)` and `!` breaking
marker after the type. Unknown types (`ci:`, `docs:`), a missing space after
the colon, an empty `()` scope, and capitalised types are still rejected.

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`6b989bd`](https://github.com/Krande/deputy/commit/6b989bdf5439784bba78b57b16a026eb6459b45d))

### Unknown

* Merge pull request #6 from Krande/fix/title-check-accept-scopes

fix: accept scoped conventional-commit titles in the PR-review check ([`f9ff725`](https://github.com/Krande/deputy/commit/f9ff725975232066fc46c33c64a652629b1c33f8))

* Merge pull request #5 from Krande/ci/tag-on-merge-source-key

chore: release over the SOURCE_KEY deploy key (fix protected-main push) ([`e082195`](https://github.com/Krande/deputy/commit/e0821958d5a5d103ad0d3bd749fb0d8e584fb163))

* ci: push deputy&#39;s release over the SOURCE_KEY deploy key (fix protected-main)

deputy&#39;s tag-on-merge failed on v0.3.0: semantic-release pushes a version-bump
commit to main, and the default GITHUB_TOKEN cannot push to a protected branch
(GH006, &#39;Changes must be made through a pull request&#39;). Switch the release
checkout to ssh-key: SOURCE_KEY so the push uses a deploy key that can bypass
branch protection — same pattern as every consumer repo. Also surface
HAS_SOURCE_KEY on pr-review so its sticky comment reflects whether the deploy key
is configured (it correctly warned it wasn&#39;t).

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`6632f1b`](https://github.com/Krande/deputy/commit/6632f1b80a4fd196495fd0cbad786d2dacd843d3))


## v0.3.0 (2026-08-20)

### Feature

* feat: add `deputy sshkey` — generate an ed25519 key, remembering the email

A small helper for making CI deploy keys without memorising ssh-keygen flags:
resolves the comment email (flag &gt; last-remembered, stored in
~/.config/deputy/sshkey.json), picks a unique filename so repeats never clobber
an earlier key, runs `ssh-keygen -t ed25519 -N &#34;&#34;`, and prints the public key
(add `--print` to also dump the private key for pasting into a secret).

Follows deputy&#39;s architecture: pure helpers (slug, unique path, argv, email
resolution) in sshkey.py, a fully-injected flow in flows.py, a cli subcommand,
and tests with in-memory fakes. All examples generic.

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`29e2516`](https://github.com/Krande/deputy/commit/29e25166db65c0a7cfe684377f05111c3b2831c5))

* feat: release-watch — scheduled dependency-bump PRs (v0.3.0)

Add a `deputy release-watch` command that, on a schedule, checks watched
upstream repos for a newer GitHub Release/tag than the version pinned in
the consumer repo and opens (or updates) a PR bumping the pin.

- release_watch.py: pure semver parse/compare + capture-group pin rewrite
- github.py: extend the client protocol/REST impl with latest_release,
  list_tags, and PR find/create/update
- gitutils.py: commit_to_branch helper (injectable runner)
- flows.release_watch(): orchestration with every side effect injected;
  idempotent by per-target head branch, dry-run supported
- config.py: [[release_watch]] target loaders
- cli.py: release-watch subcommand (--all/--target/--dry-run/--base)
- tests: pure logic, flow (newer→PR, up-to-date→noop, existing-PR→update,
  fallback-to-tag, pattern-mismatch), and CLI wiring, all via in-memory fakes
- README + docstrings: command, [[release_watch]] schema, scheduled workflow

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`2c94ad2`](https://github.com/Krande/deputy/commit/2c94ad22f19473ad7ca94a4a011cc9f2cd90a4eb))

### Unknown

* Merge pull request #4 from Krande/docs/document-release-process

chore: document deputy&#39;s self-hosted release process ([`059d70f`](https://github.com/Krande/deputy/commit/059d70fa5212a8ab44cd1d4e8b8679d6231bf747))

* docs: lead global-install with pixi-build single-line + cache-dir tip

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`856d9d8`](https://github.com/Krande/deputy/commit/856d9d8549641e43588f999b3247d8cf36f1b539))

* build: add pixi-build config for single-line pixi global install (WIP)

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt; ([`a22f472`](https://github.com/Krande/deputy/commit/a22f47204f8adf1984e80594462944d12e2ce3a2))

* docs: add a global-install (uv via pixi) tip to the README

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`f08d15d`](https://github.com/Krande/deputy/commit/f08d15d88ccf33d9943ea44276776cb63c7364fb))

* docs: document deputy&#39;s self-hosted release process

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`9c862a4`](https://github.com/Krande/deputy/commit/9c862a4796092b32669b2742bdc19e2caba985c8))

* Merge pull request #3 from Krande/ci/dogfood-deputy

ci: dogfood deputy + add `deputy sshkey` helper ([`ac41da5`](https://github.com/Krande/deputy/commit/ac41da5516f726dc9fb03d8037f8f439c975950a))

* ci: dogfood deputy — self-hosted pr-review + tag-on-merge

deputy now uses ITSELF for its own PR review and release tagging (installed from
the checkout with `pip install .`), instead of relying on manual tags. This
means a regression in deputy&#39;s CI logic surfaces on deputy&#39;s own PRs first, and
releases get a vX.Y.Z tag automatically on a merged, release-labelled PR.

deputy.toml keeps the two version sources (pyproject [project].version and
src/deputy/__init__.py:__version__) in lockstep via semantic-release. The tag is
pushed over GITHUB_TOKEN (contents: write) — deputy has no tag-triggered
workflow, so no deploy key is needed.

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`f188351`](https://github.com/Krande/deputy/commit/f1883516396e2a8c5a6cdd25f5bd7768c0f68714))

* Merge pull request #2 from Krande/feat/release-watch

feat: release-watch — scheduled dependency-bump PRs (v0.3.0) ([`6735e0b`](https://github.com/Krande/deputy/commit/6735e0be66223675ed8d1c07a1971455a6d02a9e))

* Merge pull request #1 from Krande/chore/scrub-company-refs

docs: scrub company-specific examples from deputy ([`4d1b193`](https://github.com/Krande/deputy/commit/4d1b19328f83d1ce64fb4bf606b2fbbbafa6e704))

* docs: scrub company-specific examples from README/CLI/tests

deputy is a generic, public package, so its examples shouldn&#39;t carry
company-internal names. Replace the asa-viewer / ablacr.azurecr.io /
cluster_test paths in the README, the cli.py docstring, and the config/cli
tests with neutral placeholders (registry.example.com/web-app, web-app-beta,
clusters/prod/web-app.yaml, images key &#34;app&#34;).

Behaviour unchanged — only string literals in docs + test fixtures/assertions.
63 tests pass, ruff clean. (No homelab refs were present.)

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`ac94d73`](https://github.com/Krande/deputy/commit/ac94d73da2272478c03af6ada98399e967a1f646))


## v0.2.0 (2026-08-19)

### Feature

* feat: deputy.toml declarative config + self-contained release defaults (v0.2.0)

Config now comes from three layers, most-specific first:
    CLI flag / env var  -&gt;  deputy.toml  -&gt;  built-in default

- config.py: load deputy.toml (tomllib), deep-merge precedence, [images] map,
  [[gitops]] targets, {name}/{tag}/{image}/{kind} message templating.
- gitops-update: --target NAME (repeatable) / --all bump declared targets,
  composing image refs from [images] + --tag; ad-hoc --image/--file/--kind flags
  still work with no toml.
- Release config is self-contained: deputy ships the semantic-release defaults
  (RELEASE_DEFAULTS) and renders a config from defaults ⊕ [release] overrides at
  runtime — no external action_config.toml needed. DEPUTY_CONFIG still honored
  for back-compat.
- pr-review marker reads [pr_review].marker (DEPUTY_MARKER overrides).

63 tests (18 new: test_config, test_cli), ruff clean. Verified end-to-end: a
deputy.toml --target bump patches + commits, and PSR accepts the rendered config.

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`f8923ec`](https://github.com/Krande/deputy/commit/f8923eca099e6825ad444ca5c6d4b592dff0b97e))


## v0.1.0 (2026-08-19)

### Feature

* feat: deputy — CI logic as a testable package

Extracted from conda-server&#39;s in-repo ci_tools into a standalone, reusable
package. Same PR-review + release-tagging logic (pure decision code + injectable
adapters + in-memory fakes), renamed ci_tools -&gt; deputy, plus a new
gitops-update command that bumps a container image reference in a gitops YAML
file (comment-preserving ruamel round-trip) and commits + pushes.

- pr-review:     conventional-title + one release-* label checks, next-version
                 preview, single sticky comment.
- tag-on-merge:  semantic-release version bump + tag + GitHub Release.
- gitops-update: patch image at a dotted path for a matching k8s kind, push.

Per-repo config via DEPUTY_MARKER / DEPUTY_CONFIG env (defaults are generic, not
conda-server-specific). 45 unit tests, ruff clean, own CI workflow. MIT.

Co-Authored-By: Claude Opus 4.8 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01J3zfaYytWJnEeZrNGo3aup ([`fd872c2`](https://github.com/Krande/deputy/commit/fd872c228bc78a52ffdf36910a89a61f271a8763))

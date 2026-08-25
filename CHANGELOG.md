# CHANGELOG



## v0.5.2 (2026-08-25)

### Fix

* fix: pin gitpython below 3.1.60 (#10)

GitPython 3.1.60, released 2026-08-25, removed `git.Actor.name_email_regex`.
python-semantic-release reads that attribute UNCONDITIONALLY at config load --
8.5.1 at cli/config.py:294, and its current default branch still does at :745 --
so every semantic-release subcommand raises

    AttributeError: type object &#39;Actor&#39; has no attribute &#39;name_email_regex&#39;

the moment an install resolves 3.1.60. deputy pins the version of PSR but not of
the library PSR breaks on, so a fresh `pip install deputy` was enough to break
tagging everywhere it is used.

The failure is quiet, which is the reason to pin rather than hope: pr-review
catches the exception and degrades its sticky comment to &#34;No release will be
issued for these commits&#34; while the job still reports success. A merge then
yields a green tick and no release, which reads exactly like ordinary
base-ref behaviour.

Pinned in BOTH dependency blocks. The pip path and the conda path resolve
independently, so constraining only [project].dependencies would leave the
run-dependencies free to take 3.1.60.

Verified by installing this source into a clean target:

    gitpython resolved      : 3.1.59
    name_email_regex present: True
    PSR config module loaded: OK

deputy&#39;s own release workflows use `pip install .`, so they pick this up from
the merge commit and need no separate unblocking.

Drop the pin once PSR guards the attribute, or once its pinned version no
longer reads it. ([`06e6a93`](https://github.com/Krande/deputy/commit/06e6a93d312ffabad22a6f6db4a0a924b3c6b320))


## v0.5.1 (2026-08-22)

### Fix

* fix(tests): make the zip strictness explicit in the indent helper

ruff B905. Chose strict=False rather than True on purpose: if a regression DID
change the line count, this helper should still report which lines differ so
the failure is readable. strict=True would raise a ValueError instead and hide
it behind a traceback. The line count has its own assertion in
test_line_count_is_unchanged_in_both_styles.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01Mdyz12Wh4LQgzdAN1DYneo ([`e6158f0`](https://github.com/Krande/deputy/commit/e6158f0d3face851cdc9b3a2dd47d6dc0dbff2dd))

* fix(gitops): match the file&#39;s list indentation instead of imposing one

set_image hardcoded ruamel&#39;s expanded block-sequence style. Kubernetes
manifests are written both ways:

    containers:            containers:
    - name: x                - name: x

and ruamel cannot infer which a file uses -- it reformats every block sequence
to whatever it was configured with. So bumping an image in a compact-style file
rewrote the indentation of the entire file.

That is not cosmetic. A gitops write-back is supposed to be a reviewable
one-line change. It arrived as &#34;38 insertions, 38 deletions&#34; across a 76-line
manifest, and a version REGRESSION inside it -- an image going from 1.1.6 to
0.0.1 -- read as whitespace and went unnoticed until the deployment was
inspected by hand.

detect_sequence_indent learns the style from the first block sequence in the
file and set_image configures ruamel with it, so both styles now round-trip to
a single changed line. Files with no block sequence fall back to ruamel&#39;s
defaults, where there is nothing to preserve and so nothing to get wrong.

Deliberately no regex: the detector walks lines, which keeps it readable and
avoids a pattern that has to be right about comments, blank lines and quoting
all at once. A file mixing both styles is already inconsistent and cannot be
reproduced exactly either way; the first sequence wins.

8 new tests, including that the line COUNT is unchanged -- a reflow shows up
there when a folded scalar wraps differently, which is the variant easiest to
miss in review -- and that a nested second sequence keeps its style too.
Suite 188 -&gt; 196.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01Mdyz12Wh4LQgzdAN1DYneo ([`3151d6b`](https://github.com/Krande/deputy/commit/3151d6b71507acacec9830ca2db83111d58385d5))

### Unknown

* Merge pull request #9 from Krande/fix/gitops-match-existing-indent

fix(gitops): match the file&#39;s list indentation instead of imposing one ([`726d8ec`](https://github.com/Krande/deputy/commit/726d8ecd0db32b6e2dfb0437a6425629bc3e1204))

* style: ruff format the indent tests

I ran `ruff check` but not `ruff format` on the previous fix; the repo&#39;s test
task gates on both. No behaviour change -- two set_image calls wrapped.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01Mdyz12Wh4LQgzdAN1DYneo ([`6fca8eb`](https://github.com/Krande/deputy/commit/6fca8ebe7a5a9966b601a6af6a7649e43c2e0317))


## v0.5.0 (2026-08-22)

### Feature

* feat: add version_json — a JSON-aware bump for npm lockfiles

semantic-release&#39;s version_variables is a regex: it builds
`&lt;variable&gt;\s*[:=]\s*&#34;&lt;semver&gt;&#34;` and substitutes every match in the file.
That is fine for package.json, whose own &#34;version&#34; is the only match, but
catastrophic for package-lock.json — every dependency carries a &#34;version&#34;
key. On adapy&#39;s lock the pattern matches 471 times across 230 distinct
versions, so declaring it would rewrite the whole dependency tree to the
project version. As a result lockfiles just don&#39;t get bumped: adapy&#39;s has
drifted three minor versions behind.

Add a third declaration alongside version_toml / version_variables:

    [release]
    version_json = [&#34;src/frontend/package.json&#34;,
                    &#34;src/frontend/package-lock.json&#34;]

Paths only — the fields are implied by the format, and a lockfile carries
its version in two places at once, which no single pointer could express.
deputy parses the JSON and writes only the package&#39;s own version: the root
&#34;version&#34; (package.json, and lockfileVersion 1/2/3) plus packages[&#34;&#34;]
[&#34;version&#34;] (npm&#39;s root-package key, v2/v3 only). Nothing under
dependencies, and no non-empty packages key, is touched.

Formatting is preserved byte for byte — indent, LF/CRLF, trailing newline
and BOM are detected and reproduced, so a bump is a two-line diff instead
of a whole-file reformat that npm install would undo. deputy proves this
per file by re-rendering the unmodified document and comparing it with the
original, and refuses to write when it does not come back identical.

Failures are loud: a missing file, invalid JSON, a missing or non-semver
root version, or an unreproducible file aborts the release with an error
naming the file.

semantic-release cannot run this itself, so deputy does, immediately
before invoking it: bump, `git add`, then release. semantic-release stages
its own version files and runs a bare `git commit`, which picks up the
whole index — so the JSON files land in the commit that gets tagged rather
than dangling dirty in the tree. version_json is stripped from the
generated semantic-release config, which knows nothing about the key.

The version to write comes from `version --print` via a new strict
planned_version(), which raises instead of guessing when semantic-release
cannot be run or fails. Note that command puts only the version on stdout
and everything else on stderr, so stdout is identical whether or not a
release is due; the &#34;already been released&#34; line on stderr is what tells
them apart. next_version_noop stays best-effort for the PR comment.

Fully backward compatible: a repo that declares no version_json takes
exactly the path it does today.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01Mdyz12Wh4LQgzdAN1DYneo ([`566a8b6`](https://github.com/Krande/deputy/commit/566a8b64d9624001dffc7ee8207e9ec97a63c75d))

### Unknown

* Merge pull request #8 from Krande/feat/json-version-declarations

feat: add version_json — a JSON-aware version bump for npm lockfiles ([`375db7e`](https://github.com/Krande/deputy/commit/375db7efe770e1ed66f36b9b0bfc7139e7103164))


## v0.4.0 (2026-08-21)

### Feature

* feat: make the default release label configurable

pr-review applied a hardcoded release-skip to PRs carrying no release-*
label. Repos that want the opposite policy (release by default, skip as
the opt-out) can now set it in deputy.toml:

    [pr_review]
    default_label = &#34;release-auto&#34;

Resolved as $DEPUTY_DEFAULT_LABEL -&gt; [pr_review].default_label -&gt;
release-skip, and validated: an unrecognised value is a hard error, not a
silent fallback to release-skip. The key deliberately lives under
[pr_review], not [release] — [release] is deep-merged into the
semantic-release config deputy generates, so a deputy-only key there
would be handed to semantic-release.

Both flows honour it: pr-review applies it, and tag-on-merge falls back
to the same value when a merged PR carries no release-* label (pr-review
never ran, or the label was removed).

Also fixes a latent bug this would otherwise have activated:
decide_bump computed `release = label != DEFAULT_LABEL`, which is only
accidentally correct while the default is release-skip. Under a
release-auto default, a PR explicitly labelled release-skip evaluated
&#34;release-skip&#34; != &#34;release-auto&#34; -&gt; release=True with flag=None, i.e.
deputy would try to release a PR the user told it to skip. Release-ness
now follows the label&#39;s flag (`flag is not None`), which is independent
of whatever the default happens to be.

deputy&#39;s own deputy.toml keeps release-skip; releasing here stays opt-in.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_0168SX1LcVozsHmJeUWYLRdw ([`0a3e29e`](https://github.com/Krande/deputy/commit/0a3e29e66f738e1ddfafc99a6714999ef5101fde))

### Unknown

* Merge pull request #7 from Krande/feat/configurable-default-label

feat: make the default release label configurable ([`14c509e`](https://github.com/Krande/deputy/commit/14c509e3fbb2a66081f48563c02a17cce4442ef7))


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

from deputy.comment import MARKER
from deputy.flows import pr_review, tag_on_merge
from fakes import FakeGitHubClient, OutputRecorder, pr_event


def run_review(event, *, has_source_key=True, version_line=" * (version line)", **kwargs):
    client = FakeGitHubClient()
    out = OutputRecorder()
    rc = pr_review(
        event,
        client,
        has_source_key=has_source_key,
        version_line_fn=lambda decision: version_line,
        seed_fn=lambda title: None,
        set_output_fn=out,
        **kwargs,
    )
    return rc, client, out


def test_pr_review_happy_path_posts_sticky_and_passes():
    rc, client, out = run_review(pr_event(title="feat: x", labels=["release-minor"]))
    assert rc == 0
    assert out.values["review_ok"] == "true"
    assert len(client.comments) == 1
    assert client.comments[0].body.startswith(MARKER)


def test_pr_review_defaults_missing_label_to_skip():
    rc, client, out = run_review(pr_event(title="feat: x", labels=[]))
    assert "release-skip" in client.added_labels
    assert out.values["review_ok"] == "true"
    assert rc == 0


def test_pr_review_bad_title_fails_but_still_comments():
    rc, client, out = run_review(pr_event(title="nope, not conventional", labels=["release-skip"]))
    assert rc == 1
    assert out.values["review_ok"] == "false"
    assert "things to address" in client.comments[0].body


def test_pr_review_multiple_release_labels_fails():
    rc, _, out = run_review(pr_event(title="feat: x", labels=["release-minor", "release-major"]))
    assert rc == 1
    assert out.values["review_ok"] == "false"


def test_pr_review_silence_bot_suppresses_comment_but_still_sets_output():
    _rc, client, out = run_review(
        pr_event(title="feat: x", labels=["release-minor", "silence-bot"])
    )
    assert client.comments == []
    assert "review_ok" in out.values


def test_pr_review_updates_existing_sticky_rather_than_duplicating():
    client = FakeGitHubClient()
    client.create_comment(7, MARKER + "\nstale body")
    out = OutputRecorder()
    pr_review(
        pr_event(title="feat: x", labels=["release-minor"]),
        client,
        has_source_key=True,
        version_line_fn=lambda d: "v",
        seed_fn=lambda t: None,
        set_output_fn=out,
    )
    assert len(client.comments) == 1


def test_tag_on_merge_skips_when_not_merged():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=False, labels=["release-minor"]),
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == []


def test_tag_on_merge_skips_on_release_skip():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-skip"]),
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == []


def test_tag_on_merge_releases_with_the_forced_flag():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-minor"]),
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == ["--minor"]


# ── configured default release label ──────────────────────────────────────────


def test_pr_review_applies_the_configured_default_label():
    rc, client, out = run_review(pr_event(title="feat: x", labels=[]), default_label="release-auto")
    assert client.added_labels == ["release-auto"]
    assert rc == 0
    assert out.values["review_ok"] == "true"


def test_pr_review_default_label_reaches_the_bump_decision():
    seen = []
    client = FakeGitHubClient()
    pr_review(
        pr_event(title="feat: x", labels=[]),
        client,
        has_source_key=True,
        default_label="release-auto",
        version_line_fn=lambda d: seen.append(d) or " * v",
        seed_fn=lambda t: None,
        set_output_fn=OutputRecorder(),
    )
    (decision,) = seen
    assert decision.label == "release-auto"
    assert decision.release is True
    assert decision.flag == ""


def test_pr_review_explicit_skip_under_an_auto_default_does_not_release():
    seen = []
    client = FakeGitHubClient()
    pr_review(
        pr_event(title="feat: x", labels=["release-skip"]),
        client,
        has_source_key=True,
        default_label="release-auto",
        version_line_fn=lambda d: seen.append(d) or " * v",
        seed_fn=lambda t: None,
        set_output_fn=OutputRecorder(),
    )
    (decision,) = seen
    assert client.added_labels == []  # the PR already carries a release-* label
    assert decision.label == "release-skip"
    assert decision.release is False


def test_tag_on_merge_uses_the_configured_default_when_no_label_is_present():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=[]),
        default_label="release-auto",
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == [""]  # released, letting semantic-release derive the bump


def test_tag_on_merge_unconfigured_still_defaults_to_skip():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=[]),
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == []


def test_tag_on_merge_explicit_skip_wins_over_an_auto_default():
    # The bug: with `release = label != default_label`, a PR the user explicitly
    # labelled release-skip would release (flag None) under a release-auto default.
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-skip"]),
        default_label="release-auto",
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == []


# ── [release].version_json passthrough ────────────────────────────────────────


def test_tag_on_merge_hands_version_json_to_the_release(monkeypatch):
    seen = {}

    def fake_run_release(config_file, flag, *args, **kwargs):
        seen.update(config_file=config_file, flag=flag, version_json=kwargs.get("version_json"))
        return 0

    monkeypatch.setattr("deputy.flows.run_release", fake_run_release)
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-minor"]),
        config_file="cfg.toml",
        version_json=["src/frontend/package-lock.json"],
    )
    assert rc == 0
    assert seen == {
        "config_file": "cfg.toml",
        "flag": "--minor",
        "version_json": ["src/frontend/package-lock.json"],
    }


def test_tag_on_merge_declares_no_version_json_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "deputy.flows.run_release",
        lambda config_file, flag, *a, **kw: (seen.update(kw), 0)[1],
    )
    tag_on_merge(pr_event(merged=True, labels=["release-patch"]), config_file="cfg.toml")
    assert seen["version_json"] == ()

from deputy.comment import MARKER
from deputy.flows import pr_review, tag_on_merge
from fakes import FakeGitHubClient, OutputRecorder, pr_event


def run_review(
    event, *, has_source_key=True, version_line=" * (version line)", client=None, **kwargs
):
    # By default the live labels agree with the payload, which is the ordinary
    # case: the PR was labelled before the event fired. A test that needs the two
    # to DISAGREE — a label applied afterwards — passes its own client with
    # `client.labels` already set, and this leaves it alone.
    if client is None:
        client = FakeGitHubClient()
        pr = event["pull_request"]
        client.labels[pr["number"]] = [label["name"] for label in pr["labels"]]
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
    client.labels[7] = ["release-skip"]  # live state agrees with the payload
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


def test_pr_review_sees_a_label_added_after_the_event_fired():
    """A label applied seconds after `opened` still counts.

    The real sequence, from asa-weld-gen#72: PR opened at 06:09:22 with no
    labels, `release-patch` applied at 06:09:48, deputy wrote `release-auto` at
    06:10:05 off the payload it had been handed at open time. The PR ended up
    with two release-* labels, which releases nothing — the precise outcome the
    default label exists to avoid.

    So the payload here says no labels, exactly as it did then, and the live
    state says `release-patch`.
    """
    seen = []
    client = FakeGitHubClient()
    client.labels[7] = ["release-patch"]
    pr_review(
        pr_event(title="fix: x", labels=[]),
        client,
        has_source_key=True,
        default_label="release-auto",
        version_line_fn=lambda d: seen.append(d) or " * v",
        seed_fn=lambda t: None,
        set_output_fn=OutputRecorder(),
    )
    (decision,) = seen
    assert client.added_labels == []  # no default: the PR is already labelled
    assert client.labels[7] == ["release-patch"]  # and nothing was piled on top
    assert decision.label == "release-patch"
    assert decision.multiple is False


def test_pr_review_falls_back_to_the_payload_when_the_label_read_fails():
    """A failed live read degrades to the old behaviour rather than going red."""

    class NoListing(FakeGitHubClient):
        def list_labels(self, issue: int) -> list[str]:
            raise RuntimeError("502 from the labels endpoint")

    seen = []
    client = NoListing()
    rc = pr_review(
        pr_event(title="feat: x", labels=["release-minor"]),
        client,
        has_source_key=True,
        version_line_fn=lambda d: seen.append(d) or " * v",
        seed_fn=lambda t: None,
        set_output_fn=OutputRecorder(),
    )
    (decision,) = seen
    assert rc == 0
    assert client.added_labels == []  # the payload's label was enough
    assert decision.label == "release-minor"


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


# ── event sequences: the default label yields to an explicit one ──────────────
#
# These replay the *order* things happen in, because that is where the bug lived.
# A PR that carries both deputy's default and an explicit release-* label
# releases nothing at all — the merge is green and no version is ever cut — and
# the ordinary ways of getting there are "deputy defaulted the label, then I
# added the one I wanted" and "I removed and re-added a label to re-trigger the
# check". A snapshot test of a single review call cannot see either.


class PrTimeline:
    """A PR whose label state persists across reviews, as GitHub's does.

    ``human_adds``/``human_removes`` are label edits made by a person; ``review``
    is one ``deputy pr-review`` run over whatever the labels are *now*, with its
    own label edits landing back on the same PR. So a test can write a sequence
    out in the order it happened.
    """

    number = 7

    def __init__(self, *, title="feat: x", default_label="release-auto"):
        self.title = title
        self.default_label = default_label
        self.client = FakeGitHubClient()
        self.out = OutputRecorder()
        self.client.labels[self.number] = []

    @property
    def labels(self) -> list[str]:
        return list(self.client.labels[self.number])

    @property
    def review_ok(self) -> str:
        return self.out.values["review_ok"]

    @property
    def comment(self) -> str:
        return self.client.comments[-1].body

    def human_adds(self, *labels: str) -> None:
        self.client.add_labels(self.number, list(labels))
        self.client.added_labels.clear()  # only deputy's own adds are asserted on

    def human_removes(self, *labels: str) -> None:
        for label in labels:
            self.client.remove_label(self.number, label)
        self.client.removed_labels.clear()

    def review(self) -> int:
        return pr_review(
            pr_event(title=self.title, labels=self.labels, number=self.number),
            self.client,
            has_source_key=True,
            default_label=self.default_label,
            version_line_fn=lambda decision: " * (version line)",
            seed_fn=lambda title: None,
            set_output_fn=self.out,
        )

    def merge(self):
        """What ``tag-on-merge`` would do with the labels the PR ended up with."""
        calls = []
        rc = tag_on_merge(
            pr_event(title=self.title, labels=self.labels, merged=True, number=self.number),
            default_label=self.default_label,
            release_fn=lambda flag: (calls.append(flag), 0)[1],
        )
        assert rc == 0
        return calls


def test_sequence_a_labelless_pr_gets_the_default_and_only_the_default():
    pr = PrTimeline()
    assert pr.review() == 0
    assert pr.labels == ["release-auto"]
    assert pr.client.removed_labels == []
    assert pr.merge() == [""]


def test_sequence_default_then_explicit_leaves_exactly_one_label_and_releases():
    # Case 1. The PR arrives unlabelled, deputy defaults it, the author then adds
    # the label they actually wanted. Before the fix both labels stayed and the
    # merge released nothing.
    pr = PrTimeline()
    pr.review()
    pr.human_adds("release-patch")
    assert pr.review() == 0

    assert pr.labels == ["release-patch"]
    assert pr.client.removed_labels == ["release-auto"]
    assert pr.review_ok == "true"
    assert pr.merge() == ["--patch"]


def test_sequence_the_removal_is_reported_in_the_review_comment():
    pr = PrTimeline()
    pr.review()
    pr.human_adds("release-minor")
    pr.review()
    assert "Removed the default `release-auto` label" in pr.comment
    assert "release-minor" in pr.comment


def test_sequence_an_explicit_label_from_the_start_is_left_completely_alone():
    pr = PrTimeline()
    pr.human_adds("release-minor")
    assert pr.review() == 0
    assert pr.client.added_labels == []  # nothing to default
    assert pr.client.removed_labels == []  # nothing to supersede
    assert pr.labels == ["release-minor"]
    assert pr.merge() == ["--minor"]


def test_sequence_remove_and_re_add_to_retrigger_still_ends_with_one_label():
    # Case 2. Toggling a label to force the check to re-run is an ordinary
    # gesture: the removal re-triggers the review, which finds no release-* label
    # and applies the default; re-adding the real label then used to leave two.
    pr = PrTimeline()
    pr.human_adds("release-minor")
    pr.review()

    pr.human_removes("release-minor")  # `unlabeled` re-triggers the review
    pr.review()
    assert pr.labels == ["release-auto"]  # deputy stood in for the missing label

    pr.human_adds("release-minor")  # and the real label comes back
    assert pr.review() == 0

    assert pr.labels == ["release-minor"]
    assert pr.review_ok == "true"
    assert pr.merge() == ["--minor"]


def test_sequence_reviewing_again_changes_nothing_once_settled():
    # deputy's own edits re-trigger nothing in practice, but the flow must
    # converge rather than oscillate if a run does happen again.
    pr = PrTimeline()
    pr.review()
    pr.human_adds("release-patch")
    pr.review()
    pr.client.removed_labels.clear()
    pr.client.added_labels.clear()

    assert pr.review() == 0
    assert pr.review() == 0
    assert pr.labels == ["release-patch"]
    assert (pr.client.added_labels, pr.client.removed_labels) == ([], [])


def test_sequence_two_explicit_labels_still_fail_loudly_and_nothing_is_removed():
    # deputy must not guess between two explicit choices — a wrong guess ships
    # the wrong version. This stays a red check.
    pr = PrTimeline()
    pr.human_adds("release-patch", "release-minor")
    assert pr.review() == 1
    assert pr.review_ok == "false"
    assert pr.client.removed_labels == []
    assert sorted(pr.labels) == ["release-minor", "release-patch"]
    assert "exactly one release-* label" in pr.comment
    assert pr.merge() == []  # still no release, and the red check says so


def test_sequence_two_explicit_labels_beside_the_default_also_fail_loudly():
    pr = PrTimeline()
    pr.review()  # defaults to release-auto
    pr.human_adds("release-patch", "release-minor")
    assert pr.review() == 1
    assert pr.client.removed_labels == []
    assert len(pr.labels) == 3


def test_sequence_deputy_only_ever_removes_its_own_default_label():
    # The invariant that keeps this safe: whatever the sequence, the only label
    # deputy takes off a PR is the default it applies itself.
    for default in ("release-skip", "release-auto"):
        for explicit in ("release-skip", "release-auto", "release-patch", "release-major"):
            pr = PrTimeline(default_label=default)
            pr.review()
            pr.human_adds(explicit)
            pr.review()
            assert set(pr.client.removed_labels) <= {default}
            assert explicit in pr.labels


def test_sequence_an_explicit_skip_beside_a_releasing_default_does_not_release():
    # Supersession resolves toward the explicit label, not toward releasing.
    pr = PrTimeline(default_label="release-auto")
    pr.review()
    pr.human_adds("release-skip")
    assert pr.review() == 0
    assert pr.labels == ["release-skip"]
    assert pr.merge() == []


def test_merging_with_a_stale_default_still_releases():
    # The retroactive half of the fix: a PR merged before the review could tidy
    # the labels up (or in a repo whose workflow does not re-run on `labeled`)
    # must still release at the explicit label's level rather than silently doing
    # nothing.
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-auto", "release-major"]),
        default_label="release-auto",
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == ["--major"]


def test_merging_with_two_explicit_labels_still_releases_nothing():
    calls = []
    rc = tag_on_merge(
        pr_event(merged=True, labels=["release-patch", "release-minor"]),
        default_label="release-auto",
        release_fn=lambda flag: (calls.append(flag), 0)[1],
    )
    assert rc == 0
    assert calls == []

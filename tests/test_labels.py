from deputy.labels import decide_bump


def test_no_labels_defaults_to_skip():
    d = decide_bump([])
    assert d.release is False
    assert d.label == "release-skip"
    assert d.multiple is False


def test_unknown_labels_are_ignored():
    d = decide_bump(["bug", "documentation"])
    assert d.label == "release-skip"
    assert d.release is False


def test_auto_releases_with_no_forced_flag():
    d = decide_bump(["release-auto"])
    assert d.release is True
    assert d.flag == ""  # let semantic-release decide the bump


def test_patch_minor_major_force_flags():
    assert decide_bump(["release-patch"]).flag == "--patch"
    assert decide_bump(["release-minor"]).flag == "--minor"
    assert decide_bump(["release-major"]).flag == "--major"
    assert decide_bump(["release-minor"]).release is True


def test_multiple_release_labels_is_invalid_and_not_a_release():
    d = decide_bump(["release-minor", "release-major"])
    assert d.multiple is True
    assert d.release is False
    assert d.reason == "multiple release-* labels"


# ── configurable default label ────────────────────────────────────────────────


def test_default_label_is_configurable_and_releases():
    d = decide_bump([], default_label="release-auto")
    assert d.label == "release-auto"
    assert d.release is True
    assert d.flag == ""


def test_explicit_skip_never_releases_under_an_auto_default():
    # Regression: `release = label != default_label` made an explicitly
    # release-skip-labelled PR "release" (with flag None) once the default moved
    # off release-skip. Release-ness follows the flag, not the default.
    d = decide_bump(["release-skip"], default_label="release-auto")
    assert d.label == "release-skip"
    assert d.flag is None
    assert d.release is False


def test_explicit_labels_are_unaffected_by_the_default():
    for default in ("release-skip", "release-auto", "release-major"):
        d = decide_bump(["release-patch"], default_label=default)
        assert (d.label, d.flag, d.release) == ("release-patch", "--patch", True)


def test_multiple_release_labels_still_invalid_under_a_non_skip_default():
    d = decide_bump(["release-minor", "release-major"], default_label="release-auto")
    assert d.multiple is True
    assert d.release is False
    assert d.flag is None


def test_unknown_labels_fall_back_to_the_configured_default():
    d = decide_bump(["bug"], default_label="release-patch")
    assert d.label == "release-patch"
    assert d.flag == "--patch"
    assert d.release is True


# ── the default yields to an explicit label ───────────────────────────────────


def test_default_alongside_an_explicit_label_resolves_to_the_explicit_one():
    # The silent-suppression bug: deputy applies release-auto to a label-less PR,
    # the author then adds the label they wanted, and the two together used to
    # mean "invalid" -> no release at all.
    d = decide_bump(["release-auto", "release-patch"], default_label="release-auto")
    assert d.multiple is False
    assert d.label == "release-patch"
    assert d.flag == "--patch"
    assert d.release is True
    assert d.superseded_default == "release-auto"


def test_supersession_does_not_care_which_order_the_labels_are_in():
    d = decide_bump(["release-patch", "release-auto"], default_label="release-auto")
    assert (d.label, d.superseded_default, d.release) == ("release-patch", "release-auto", True)


def test_the_builtin_skip_default_is_superseded_too():
    d = decide_bump(["release-skip", "release-minor"])
    assert d.label == "release-minor"
    assert d.superseded_default == "release-skip"
    assert d.release is True


def test_an_explicit_skip_supersedes_a_releasing_default():
    # Supersession is not "always release": under a release-auto default, an
    # explicit release-skip is the choice and the default is the stand-in.
    d = decide_bump(["release-auto", "release-skip"], default_label="release-auto")
    assert d.label == "release-skip"
    assert d.superseded_default == "release-auto"
    assert d.release is False


def test_two_explicit_labels_still_fail_even_beside_the_default():
    # Three labels: the default cannot rescue a genuine patch-vs-minor conflict.
    d = decide_bump(
        ["release-auto", "release-patch", "release-minor"], default_label="release-auto"
    )
    assert d.multiple is True
    assert d.release is False
    assert d.superseded_default is None


def test_a_single_label_supersedes_nothing():
    for labels in ([], ["release-patch"], ["release-auto"]):
        assert decide_bump(labels, default_label="release-auto").superseded_default is None


def test_present_still_reports_the_labels_actually_on_the_pr():
    # `present` is the raw finding, not the resolution — the review comment and
    # any future diagnostics need to see what the PR really carried.
    d = decide_bump(["release-auto", "release-major"], default_label="release-auto")
    assert d.present == ("release-auto", "release-major")

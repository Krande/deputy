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

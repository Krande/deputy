from deputy.pr_checks import PrChecks, title_ok


def test_valid_title_prefixes():
    assert title_ok("feat: add thing")
    assert title_ok("fix: bug")
    assert title_ok("fix!: breaking bugfix")
    assert title_ok("feat!: breaking feature")
    assert title_ok("chore: bump deps")


def test_valid_titles_with_scope():
    assert title_ok("feat(api): add endpoint")
    assert title_ok("fix(ui): stop overflow")
    assert title_ok("chore(ci): dogfood deputy")
    assert title_ok("chore(libs): compress archives")
    assert title_ok("feat(scope)!: breaking change with scope")
    assert title_ok("fix(a-b_c): dashes and underscores in scope")


def test_invalid_title_prefixes():
    assert not title_ok("update stuff")
    assert not title_ok("Feat: capitalised")
    assert not title_ok("feat:no-space")
    assert not title_ok("docs: not in the allowed set")
    assert not title_ok("ci: not a recognised type")
    assert not title_ok("chore(): empty scope")
    assert not title_ok("feature: type must be exact, not a prefix")
    assert not title_ok("feat(scope) no colon")


def test_ok_requires_title_and_label_but_not_source_key():
    assert PrChecks(title_ok=True, label_ok=True, has_source_key=False).ok is True
    assert PrChecks(title_ok=False, label_ok=True, has_source_key=True).ok is False
    assert PrChecks(title_ok=True, label_ok=False, has_source_key=True).ok is False

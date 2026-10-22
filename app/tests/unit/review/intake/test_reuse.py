"""Step 3: the key that decides whether a stored result may be served again."""

from review.intake.reuse import file_digest, reuse_key, short

VERSIONS = {"harness": "2.0.0", "je_type": "sha-a", "rules": "sha-b", "model": "gpt-5.5"}


def _key(**overrides: object) -> str:
    args: dict[str, object] = {
        "file_sha256": file_digest(b"workbook"),
        "submitted_je_type": "vacation-accrual-france",
        "versions": VERSIONS,
    }
    return reuse_key(**{**args, **overrides})  # type: ignore[arg-type]


def test_file_digest_is_sha256_hex() -> None:
    digest = file_digest(b"workbook")
    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_the_same_inputs_give_the_same_key() -> None:
    assert _key() == _key()


def test_dict_order_cannot_change_the_key() -> None:
    """Versions are sorted before hashing, so a caller cannot invalidate a cache by accident."""
    assert _key(versions=dict(reversed(list(VERSIONS.items())))) == _key()


def test_a_different_file_is_a_different_key() -> None:
    assert _key(file_sha256=file_digest(b"other")) != _key()


def test_a_rule_change_invalidates_the_key() -> None:
    """The point of the key: a stored result is never served across a rules change."""
    assert _key(versions={**VERSIONS, "rules": "sha-c"}) != _key()


def test_a_prompt_change_invalidates_the_key() -> None:
    assert _key(versions={**VERSIONS, "prompt_ai3": "v8"}) != _key()


def test_a_model_change_invalidates_the_key() -> None:
    assert _key(versions={**VERSIONS, "model": "gpt-4o"}) != _key()


def test_a_different_submitted_type_is_a_different_key() -> None:
    assert _key(submitted_je_type="international-payroll") != _key()


def test_no_submitted_type_is_stable() -> None:
    assert _key(submitted_je_type=None) == _key(submitted_je_type=None)
    assert _key(submitted_je_type=None) != _key()


def test_separator_cannot_be_forged_across_fields() -> None:
    """Two different splits of the same text must not collide into one key."""
    first = _key(submitted_je_type="a", versions={"x": "b"})
    second = _key(submitted_je_type="a|x=b", versions={})
    assert first != second


def test_short_is_a_prefix_long_enough_to_tell_templates_apart() -> None:
    digest = file_digest(b"workbook")
    assert digest.startswith(short(digest))
    assert len(short(digest)) == 12

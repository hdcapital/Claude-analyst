from analyst.diff_lane import compute_diff, normalise


def test_identical_is_trivial() -> None:
    text = "Net asset value per share\n1.234\nas at month end\n" * 20
    outcome = compute_diff(text, text, "synth:SYNTH-PREV")
    assert outcome.trivial
    assert outcome.changed_lines == 0


def test_small_change_is_trivial() -> None:
    prev = "\n".join(f"line {i} stable content here" for i in range(100))
    cur = prev.replace("line 5 stable", "line 5 slightly-different")
    outcome = compute_diff(cur, prev, "synth:SYNTH-PREV")
    assert outcome.trivial  # 2 changed lines < MIN_CHANGED_LINES


def test_large_change_forwarded() -> None:
    prev = "\n".join(f"line {i} stable content here" for i in range(50))
    cur = "\n".join(f"line {i} rewritten materially" for i in range(50))
    outcome = compute_diff(cur, prev, "synth:SYNTH-PREV")
    assert not outcome.trivial
    assert outcome.changed_lines > 50
    assert "+line 0 rewritten materially" in outcome.delta_text


def test_normalise_collapses_whitespace() -> None:
    assert normalise("a   b\t c \n\n  d ") == ["a b c", "d"]

import pytest

from analyst.triage.stage1 import Stage1Invalid, validate_verdict

TAXONOMY = {
    "event_track": ["activist_stake", "capital_return"],
    "compounder_track": ["operational_inflection"],
}


def test_valid_verdict() -> None:
    raw = (
        '{"event_types": ["activist_stake"], "interest_score": 7, '
        '"permanent_loss_risk": 3, "track": "event", "why": "Synthetic reason."}'
    )
    v = validate_verdict("synth:SYNTH-001", raw, TAXONOMY, "claude-haiku-4-5")
    assert v.interest_score == 7
    assert v.track == "event"


def test_fenced_json_accepted() -> None:
    raw = (
        '```json\n{"event_types": [], "interest_score": 1, "permanent_loss_risk": 0, '
        '"track": "none", "why": "Routine."}\n```'
    )
    v = validate_verdict("synth:SYNTH-001", raw, TAXONOMY, "m")
    assert v.track == "none"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"event_types": ["made_up_type"], "interest_score": 5, "permanent_loss_risk": 1, "track": "event", "why": "x"}',
        '{"event_types": [], "interest_score": 15, "permanent_loss_risk": 1, "track": "none", "why": "x"}',
        '{"event_types": [], "interest_score": 5, "permanent_loss_risk": 1, "track": "maybe", "why": "x"}',
        '{"event_types": [], "interest_score": 5, "permanent_loss_risk": 1, "track": "none", "why": ""}',
        '[1,2,3]',
    ],
)
def test_invalid_rejected(raw: str) -> None:
    with pytest.raises(Stage1Invalid):
        validate_verdict("synth:SYNTH-001", raw, TAXONOMY, "m")

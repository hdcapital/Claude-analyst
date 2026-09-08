from analyst.db import Store
from analyst.models import Fact, Lane, Provenance, RouteDecision
from tests.conftest import make_announcement


def test_upsert_idempotent(store: Store) -> None:
    ann = make_announcement()
    decision = RouteDecision(lane=Lane.AI, rule="default.unmatched")
    assert store.upsert_announcement(ann, decision) is True
    assert store.upsert_announcement(ann, decision) is False  # same doc, no duplicate


def test_processed_flag(store: Store) -> None:
    ann = make_announcement()
    store.upsert_announcement(ann, RouteDecision(lane=Lane.AI, rule="r"))
    assert not store.is_processed(ann.doc_id)
    store.mark_processed(ann.doc_id)
    assert store.is_processed(ann.doc_id)


def test_fact_idempotent(store: Store) -> None:
    ann = make_announcement()
    store.upsert_announcement(ann, RouteDecision(lane=Lane.DETERMINISTIC, rule="r", parser="p"))
    fact = Fact(
        fact_type="synthetic_test",
        issuer_key=ann.issuer_key,
        data={"value": 1},
        provenance=Provenance(ann.doc_id, ann.published_date, "chars 0-10"),
        parser="p",
        confidence="parsed",
    )
    assert store.add_fact(fact) is True
    assert store.add_fact(fact) is False


def test_spend_accumulates(store: Store) -> None:
    for _ in range(3):
        store.record_spend(
            model="claude-haiku-4-5", purpose="test", input_tokens=10, output_tokens=5,
            cache_write_tokens=0, cache_read_tokens=0, batch=False, cost_usd=0.01,
        )
    assert abs(store.total_spend_usd() - 0.03) < 1e-9
    assert store.spend_breakdown()[0]["calls"] == 3


def test_previous_same_group(store: Store) -> None:
    first = make_announcement(
        doc_id="synth:SYNTH-A", native_id="SYNTH-A", published_date="2026-01-05"
    )
    second = make_announcement(
        doc_id="synth:SYNTH-B", native_id="SYNTH-B", published_date="2026-01-12"
    )
    d = RouteDecision(lane=Lane.DIFF, rule="r", diff_group="nta")
    store.upsert_announcement(first, d)
    store.upsert_announcement(second, d)
    prev = store.previous_same_group(second.issuer_key, "nta", "2026-01-12", second.doc_id)
    assert prev is not None and prev.doc_id == "synth:SYNTH-A"
    none_prev = store.previous_same_group(first.issuer_key, "nta", "2026-01-05", first.doc_id)
    assert none_prev is None

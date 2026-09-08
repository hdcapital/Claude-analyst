"""Evaluation scaffold: precision/recall of triage against ground-truth labels.

The ``labels`` table is populated later from a real price feed supplied by
the user (see PROGRESS.md) — this module never fetches or invents outcome
data; with no labels it reports exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from .db import Store
from .db.schema import LabelRow, TriageResultRow


@dataclass(frozen=True)
class EvalReport:
    labelled: int
    predicted_positive: int
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float | None:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else None

    def render(self) -> str:
        if not self.labelled:
            return (
                "No labels present. Load ground truth into the `labels` table "
                "(doc_id, label in {interesting, not_interesting}, source) — "
                "outcome labelling from a real price feed is a planned follow-up."
            )
        p = f"{self.precision:.2%}" if self.precision is not None else "n/a"
        r = f"{self.recall:.2%}" if self.recall is not None else "n/a"
        return (
            f"labels={self.labelled} predicted_positive={self.predicted_positive} "
            f"tp={self.true_positive} fp={self.false_positive} fn={self.false_negative} "
            f"precision={p} recall={r}"
        )


def evaluate(store: Store, threshold: int) -> EvalReport:
    """Positive prediction = Stage-1 interest_score >= threshold (or any Stage-2 record)."""
    with store.session() as s:
        labels = {
            row.doc_id: row.label
            for row in s.execute(select(LabelRow)).scalars().all()
        }
        predictions: dict[str, bool] = {}
        for row in s.execute(select(TriageResultRow)).scalars().all():
            positive = (
                row.stage == 2
                or (row.interest_score is not None and row.interest_score >= threshold)
            )
            predictions[row.doc_id] = predictions.get(row.doc_id, False) or positive

    tp = fp = fn = 0
    for doc_id, label in labels.items():
        pred = predictions.get(doc_id, False)
        truth = label == "interesting"
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif truth and not pred:
            fn += 1
    return EvalReport(
        labelled=len(labels),
        predicted_positive=sum(1 for v in predictions.values() if v),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
    )

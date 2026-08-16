"""Scoring for Experiment 0001.

The primary question is binary and it is the product's headline output: for a
requirement that demands documentation, is there a gap or not? So gold labels
collapse to HAS_COVERAGE (COVERED or PARTIAL) versus NO_COVERAGE, and the
positive class for precision/recall is **the gap** — because a false gap is the
expensive failure (Discovery 0002), not a missed one.

Baselines cannot express PARTIAL, so the binary framing is also what makes the
comparison fair to them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scores:
    system: str
    tp: int   # correctly called a gap
    fp: int   # called a gap that is not one  <- the expensive error
    fn: int   # missed a real gap
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def accuracy(self) -> float:
        n = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / n if n else 0.0

    def row(self) -> str:
        return (
            f"{self.system:<26} acc {self.accuracy:5.0%}   "
            f"gap-P {self.precision:5.0%}  gap-R {self.recall:5.0%}  "
            f"F1 {self.f1:5.2f}   false gaps: {self.fp}"
        )


def load_gold(path: Path = Path("eval/gold.json")) -> dict[str, str]:
    g = json.loads(path.read_text())
    return {l["ep"]: l["label"] for l in g["labels"]}


def is_gap(label: str) -> bool:
    return label == "NOT_COVERED"


def score(system: str, gold: dict[str, str], pred: dict[str, str]) -> Scores:
    tp = fp = fn = tn = 0
    for ep, truth in gold.items():
        g_gap, p_gap = is_gap(truth), is_gap(pred.get(ep, "COVERED"))
        if g_gap and p_gap:
            tp += 1
        elif p_gap and not g_gap:
            fp += 1
        elif g_gap and not p_gap:
            fn += 1
        else:
            tn += 1
    return Scores(system, tp, fp, fn, tn)


def confusion(gold: dict[str, str], pred: dict[str, str]) -> list[str]:
    """The specific EPs a system got wrong — more useful than an aggregate."""
    out = []
    for ep, truth in gold.items():
        p = pred.get(ep, "COVERED")
        if is_gap(truth) != is_gap(p):
            kind = "FALSE GAP " if is_gap(p) else "MISSED GAP"
            out.append(f"    {kind}  {ep:20} gold={truth:12} pred={p}")
    return out

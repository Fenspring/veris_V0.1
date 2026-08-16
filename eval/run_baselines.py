"""Baselines B0 and B3, scored against the frozen gold set.

Run before any semantic machinery exists, so the bar semantics has to clear is
established rather than assumed. Both baselines are given every reasonable
advantage: B0's threshold is swept and the best result is reported, and B3
matches at CFR section level rather than requiring exact subsection agreement.
Beating a strawman would prove nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.score import load_gold, score, confusion  # noqa: E402
from veris.claims import load_claims  # noqa: E402
from veris.ingest import load_canonical  # noqa: E402
from veris.retrieve import BM25, citation_join, policy_cfr_index  # noqa: E402

data = Path("data")
gold = load_gold()
claims = load_claims(data)
reqs = {c.locator: c for c in claims if c.role == "REQUIRES" and c.expects_document}
pols = [c for c in claims if c.role == "COMMITS"]
canon = {
    d["doc_id"]: load_canonical(data, d["doc_id"])
    for d in json.loads((data / "manifest.json").read_text())
}
bm = BM25(pols)
cfr_idx = policy_cfr_index(pols, canon)

print(f"gold set: {len(gold)} EPs  "
      f"({sum(1 for v in gold.values() if v == 'NOT_COVERED')} gaps)\n")

# --- B0: BM25 top-1 over a threshold sweep, best F1 reported -----------------
best = None
for thr in range(0, 205, 5):
    pred = {}
    for ep in gold:
        hits = bm.search(reqs[ep].quote, 1)
        pred[ep] = "COVERED" if hits and hits[0][1] >= thr else "NOT_COVERED"
    s = score(f"B0 BM25 (thr={thr})", gold, pred)
    if best is None or s.f1 > best[0].f1:
        best = (s, thr, pred)
b0, b0_thr, b0_pred = best
print(b0.row())
for line in confusion(gold, b0_pred):
    print(line)

# --- B3: CFR citation join ---------------------------------------------------
b3_pred = {}
for ep in gold:
    hits = citation_join(reqs[ep], pols, cfr_idx)
    b3_pred[ep] = "COVERED" if hits else "NOT_COVERED"
b3 = score("B3 CFR citation join", gold, b3_pred)
print("\n" + b3.row())
for line in confusion(gold, b3_pred):
    print(line)

# --- Trivial reference points ------------------------------------------------
print()
for name, label in [("always COVERED", "COVERED"), ("always NOT_COVERED", "NOT_COVERED")]:
    print(score(f"reference: {name}", gold, {ep: label for ep in gold}).row())

Path("eval/results_baselines.json").write_text(
    json.dumps(
        {
            "b0_bm25": {"threshold": b0_thr, "accuracy": b0.accuracy,
                        "gap_precision": b0.precision, "gap_recall": b0.recall,
                        "false_gaps": b0.fp, "predictions": b0_pred},
            "b3_citation_join": {"accuracy": b3.accuracy, "gap_precision": b3.precision,
                                 "gap_recall": b3.recall, "false_gaps": b3.fp,
                                 "predictions": b3_pred},
        },
        indent=2,
    ),
    encoding="utf-8",
)
print("\nwrote eval/results_baselines.json")

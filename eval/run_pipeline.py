"""Run the coverage pipeline over the documentation-required EPs.

Usage:
    python eval/run_pipeline.py prompts   # write pending prompts for a recorded model
    python eval/run_pipeline.py run       # adjudicate (needs recordings or a live model)
    python eval/run_pipeline.py score     # score against the frozen gold set

Model selection is entirely by environment (Decision 0004):
    VERIS_MODEL_PROVIDER=ollama VERIS_MODEL=llama3.1:8b
    VERIS_MODEL_PROVIDER=anthropic VERIS_MODEL=claude-sonnet-5 ANTHROPIC_API_KEY=...
    VERIS_MODEL_PROVIDER=recorded   (default; replays data/recordings)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.score import load_gold, score, confusion  # noqa: E402
from veris.adjudicate import (  # noqa: E402
    SYSTEM, adjudicate, build_prompt, save,
)
from veris.claims import POLICY_ROLES, load_claims  # noqa: E402
from veris.model import ModelError, call_id, from_env  # noqa: E402
from veris.retrieve import BM25  # noqa: E402

DATA = Path("data")


def setup():
    claims = load_claims(DATA)
    reqs = [c for c in claims if c.role == "REQUIRES" and c.expects_document]
    pols = [c for c in claims if c.role in POLICY_ROLES]
    return reqs, pols, BM25(pols)


def cmd_prompts(only_gold: bool) -> None:
    reqs, pols, bm = setup()
    gold = load_gold()
    if only_gold:
        reqs = [r for r in reqs if r.locator in gold]
    rec = Path("data/recordings")
    rec.mkdir(parents=True, exist_ok=True)

    pending = 0
    for r in reqs:
        candidates = [c for c, _ in bm.search(r.quote, 6)]
        prompt = build_prompt(r, candidates)
        cid = call_id(SYSTEM, prompt)
        if (rec / f"{cid}.response.txt").exists():
            continue
        (rec / f"{cid}.request.json").write_text(
            json.dumps({"ep": r.locator, "system": SYSTEM, "prompt": prompt}, indent=2),
            encoding="utf-8",
        )
        pending += 1
        print(f"=== {cid} :: {r.locator}")
        print(prompt)
        print()
    print(f"[{pending} prompts pending of {len(reqs)} requirements]", file=sys.stderr)


def cmd_run(only_gold: bool) -> None:
    reqs, pols, bm = setup()
    gold = load_gold()
    if only_gold:
        reqs = [r for r in reqs if r.locator in gold]
    model = from_env()
    scope = f"{len({p.doc_id for p in pols})} policy documents supplied by the organization"

    findings, missing = [], 0
    for r in reqs:
        try:
            findings.append(adjudicate(r, pols, bm, model, corpus_description=scope))
        except ModelError:
            missing += 1
    save(findings, Path("data/findings.json"))
    print(f"adjudicated {len(findings)} requirements "
          f"({missing} skipped for missing recordings) -> data/findings.json")
    if findings:
        from collections import Counter
        print(" ", dict(Counter(f.verdict for f in findings)))


def cmd_score() -> None:
    gold = load_gold()
    findings = json.loads(Path("data/findings.json").read_text())
    pred = {f["ep"]: f["verdict"] for f in findings if f["ep"] in gold}
    if not pred:
        print("no gold-set findings to score")
        return
    s = score(f"veris pipeline (n={len(pred)})", gold, pred)
    print(s.row())
    for line in confusion(gold, pred):
        print(line)

    exact = sum(1 for ep, g in gold.items() if pred.get(ep) == g)
    print(f"\n  three-way exact agreement: {exact}/{len(pred)}")
    unp = sum(1 for f in findings if f["verdict"] == "UNPARSEABLE")
    print(f"  unparseable model outputs: {unp}")
    ev_ok = sum(1 for f in findings if f["verdict"] in ("COVERED", "PARTIAL") and f["evidence"])
    ev_tot = sum(1 for f in findings if f["verdict"] in ("COVERED", "PARTIAL"))
    print(f"  coverage findings carrying resolved evidence: {ev_ok}/{ev_tot}")

    print(
        "\n  NOTE: if the model provider is `recorded` and the recordings were written\n"
        "  by the same agent that authored eval/gold.json, this score is circular and\n"
        "  is NOT evidence. It measures self-agreement. See docs/decisions/0002."
    )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prompts"
    only_gold = "--all" not in sys.argv
    {"prompts": lambda: cmd_prompts(only_gold),
     "run": lambda: cmd_run(only_gold),
     "score": cmd_score}[cmd]()

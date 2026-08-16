"""Regression tests for retrieval relevance.

The bug these guard against was not the missing floor. It was that "what" was
treated as a content word, so a short pain-assessment provision reading "What
makes the pain better? What makes it worse?" matched every question a clinician
could ask, amplified by BM25's length normalisation favouring short documents.

Run: .venv/bin/python -m pytest tests/ -q     (or execute this file directly)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from veris.brief import apply_floor  # noqa: E402
from veris.claims import load_claims  # noqa: E402
from veris.retrieve import BM25, tokenize  # noqa: E402

DATA = Path("data")


def _pool(role: str):
    return [c for c in load_claims(DATA) if c.role == role]


def _kept(question: str, role: str, k: int = 4):
    hits = BM25(_pool(role)).search(question, k)
    kept, dropped = apply_floor(hits)
    return kept, dropped


def test_question_words_are_not_content():
    for word in ("what", "how", "why", "our", "does"):
        assert word not in tokenize(f"{word} is our policy on restraints"), word


def test_unrelated_question_returns_nothing_from_policies():
    """A topic the corpus has no coverage of must not fill sections with the
    least-bad match. Answering a parking question with a DNR provision is worse
    than answering nothing."""
    for role in ("COMMITS", "OPERATIONALIZES"):
        kept, _ = _kept("What is our policy on parking permits?", role)
        assert kept == [], [c.locator for c in kept]


def test_off_topic_standards_do_not_surface_for_a_clinical_question():
    """The reported bug: hazard-vulnerability-analysis standards appearing for a
    question about blood products."""
    kept, _ = _kept("What is our policy on giving blood products?", "REQUIRES")
    locators = [c.locator for c in kept]
    assert locators, "the relevant blood standards must still survive the floor"
    assert not any(loc.startswith("EM.") for loc in locators), locators


def test_floor_does_not_empty_a_well_covered_topic():
    """The floor must not manufacture a false absence — the failure mode that
    matters most here (Discovery 0002)."""
    kept, _ = _kept("What is our policy on giving blood products?", "COMMITS")
    assert len(kept) >= 3
    assert all("Blood" in c.locator for c in kept), [c.locator for c in kept]


def test_strong_match_survives_and_weak_neighbours_are_cut():
    kept, dropped = _kept("What is our hand hygiene policy?", "COMMITS")
    assert [c.locator for c in kept][:1] == [
        c.locator for c in kept if "Hand Hygiene" in c.locator
    ][:1]
    assert dropped >= 1


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")
    print(f"\n{'all passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)

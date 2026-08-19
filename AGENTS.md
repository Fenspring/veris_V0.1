# Agents

An agent is a named, versioned piece of reasoning that produces findings with
provenance. Agents are modular: adding one touches no other agent, no dashboard
code, and no API route.

## The four-way distinction

Every agent output separates:

```
SOURCE FACT      the document says this
OBSERVATION      Veris computed this from what it was given
INFERENCE        a model judged this
RECOMMENDATION   what a human might do about it
```

An agent may never present an inference as a regulatory requirement. The
evaluation fails if any finding is labelled `SOURCE_FACT`.

## Shipping agents

### Policy-to-Training Agent
Compares a policy's effective date against the last revision of the course that
teaches it. Requires **both** a policy system and an LMS — the comparison exists
in neither system alone, because each holds one half. This is the product thesis
expressed as code.

Output: `REQUIRES_HUMAN_REVIEW`, provenance `VERIS_INTERPRETATION` (it is
arithmetic on two dates, not an opinion).

### Gap Analysis Agent
Policies with no recorded owner, review dates that have passed, required courses
connected to no policy or requirement. Runs on metadata alone, so it works from
first connection — before any document text has been read.

### Survey Readiness Agent
Summarises requirements, coverage, ownership and open findings, and names the
dimensions it cannot assess.

**It deliberately produces no score.** A single readiness percentage would be
the most requested number in the product and the least defensible: it would
average over knowledge Veris has read, knowledge it has only metadata for, and
knowledge nobody has connected, then present the result as a measurement of the
organization.

### Regulatory Change and Policy Alignment
Implemented in `veris/changes.py` and `veris/analyze.py`: version diffing,
impact traversal, conflict and gap detection with evidence. See
`docs/decisions/0006`.

## Agents state what they cannot do

An agent that needs a system nobody has connected reports that instead of
guessing:

> "No learning system is connected, so Veris cannot compare policies against the
> training that teaches them."

`GET /api/v1/agents` reports `runnable` and `blocked_by` for each, and the UI
disables the run button with the reason.

## Writing one

```python
info = AgentInfo(id="…", name="…", description="…",
                 produces=("POTENTIAL_GAP",), requires=("LMS",))

def run(store) -> AgentResult:
    ...  # every finding needs scope, confidence, and a provenance class

register(info, run)
```

Rules: name the scope on every absence claim; choose the provenance class
honestly; prefer reporting an inability over producing a confident guess; make
the finding title stable so re-running does not duplicate it.

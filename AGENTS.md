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
teaches it. Requires **both** `policy_metadata` and `course_catalog` — the
comparison exists in neither system alone, because each holds one half. This is
the product thesis expressed as code.

Output: `REQUIRES_HUMAN_REVIEW`, provenance `VERIS_INTERPRETATION` (it is
arithmetic on two dates, not an opinion).

### Gap Analysis Agent
Policies with no recorded owner, review dates that have passed, required courses
connected to no policy or requirement. Runs on metadata alone, so it works from
first connection — before any document text has been read.

### Survey Readiness Agent
Summarises requirements, coverage, ownership and open findings, and names the
dimensions it cannot assess.

It also reports every capability nothing supplies, each with what its absence
costs. That is the honest half of a readiness view, and the reason there is no
score.

**It deliberately produces no score.** A single readiness percentage would be
the most requested number in the product and the least defensible: it would
average over knowledge Veris has read, knowledge it has only metadata for, and
knowledge nobody has connected, then present the result as a measurement of the
organization.

### Regulatory Change and Policy Alignment
Implemented in `veris/changes.py` and `veris/analyze.py`: version diffing,
impact traversal, conflict and gap detection with evidence. See
`docs/decisions/0006`.

## Agents depend on capabilities, not vendors

An agent declares the *knowledge* it needs, from the shared capability
vocabulary — never a connector id and never a category:

```python
requires=("policy_metadata", "course_catalog")
```

Connect a different vendor that supplies the same thing and the agent runs, with
no edit. Connect one that supplies less and the agent does not run.

What counts is what a connection has actually delivered, not what its vendor
declared: an agent that ran on a promise would produce findings about nothing.
Documents uploaded directly count too — dragging in a policy PDF supplies policy
text as surely as a policy system would.

## Agents state what they cannot do

An agent that lacks a capability reports what is missing and what its absence
costs, in the customer's terms, because their next action is to connect
something and they need to know which and why:

> "Veris cannot tell you whether required training was actually done."

Never "requirements not met". `GET /api/v1/agents` reports `runnable`,
`blocked_by` (the full capability declarations) and `blocked_reason`; the UI
disables the run button and shows the sentence. `GET /api/v1/capabilities`
gives the same picture for the whole system, and pairs every absence with the
connectors that could fill it.

## Writing one

```python
info = AgentInfo(id="…", name="…", description="…",
                 produces=("POTENTIAL_GAP",),
                 requires=("course_catalog",))   # validated against CAPABILITIES

def run(store) -> AgentResult:
    ...  # every finding needs scope, confidence, and a provenance class

register(info, run)
```

Rules: declare the capabilities you depend on and check them before running;
name the scope on every absence claim; choose the provenance class honestly;
prefer reporting an inability over producing a confident guess; make the finding
title stable so re-running does not duplicate it.

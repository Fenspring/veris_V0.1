# Decision 0004 — Model-Agnostic Inference, Assuming the Weakest Plausible Model

**Date:** 2026-08-15
**Status:** Accepted — implemented in `veris/model.py`
**Driver:** Founder requirement — the hospital chooses its model, cloud or local.

---

## The requirement

Veris must run on whatever model the hospital selects: a frontier cloud API, or
a 7–8B model served by Ollama on a workstation inside the hospital network.

This is not a preference to be accommodated later. For a large share of health
systems the deciding question is whether policy text and PHI-adjacent content
leave the building, and the answer determines whether the product can be bought
at all. It is therefore a constraint on the architecture, not a configuration
option bolted on at the end.

## What "model-agnostic" actually costs

The naive reading — "put an interface in front of the model call" — is the easy
half and is worth almost nothing on its own. The real constraint is that the
pipeline must produce trustworthy output when the model is **weak**:

| Assumption a cloud-only design makes | Available on a local 8B model? |
|---|---|
| 200k context — "read the whole corpus" | No. Often 8k. |
| Reliable JSON / structured output mode | No. |
| Tool use / function calling | Unreliable. |
| Strong multi-step instruction following | No. |
| Will not fabricate a quotation | Definitely not. |

Every one of these is load-bearing in a conventional LLM pipeline. So the design
principle is: **assume the weakest plausible model and compensate structurally
rather than by prompting harder.**

## Consequences, in order of importance

**1. Mechanical verification replaces trust.** Every extracted claim must quote
its source verbatim, and the quote is checked byte-for-byte against the
canonical text before the claim is allowed to exist (`ingest.verify_span`). A
weak model that fabricates a quotation produces a claim that fails the check and
is discarded. This is what makes a small local model *safe* here — not that it
hallucinates less, but that its hallucinations cannot survive the pipeline. The
grounded-claim primitive (Decision 0001) was chosen for trust reasons and turns
out to be the thing that makes model agnosticism achievable. Those two decisions
reinforce each other.

**2. Small tasks, never whole-corpus reasoning.** This independently kills the
long-context architecture (Decision 0003, option D) as anything but a baseline:
it cannot run on the deployment target at all. Extraction operates on one
document section; adjudication on one candidate group.

**3. Line-oriented output, not JSON.** Small models fail at strict JSON far more
often than at emitting delimited lines. Model-facing formats will be
line-oriented with a tolerant parser, and parse failure will be a recorded
metric rather than a crash.

**4. Candidate generation must not require an embedding model.** Requiring
embeddings would mean requiring a *second* model deployment, doubling the
hospital's setup burden. Discovery 0003 found that this corpus supplies a free
deterministic candidate generator — the CFR citation crosswalk — and lexical
methods cover the rest. Embeddings become an optional precision upgrade, not a
prerequisite.

**5. No vendor SDKs.** `veris/model.py` uses only the standard library. No
dependency can pin Veris to one vendor, and nothing breaks in an air-gapped
install.

## Implementation

Three providers behind one method, `complete(system, prompt) -> str`:

- **`OpenAICompatModel`** — `/v1/chat/completions`, the lingua franca of local
  inference (Ollama, vLLM, LM Studio, llama.cpp server) and of most hosted
  providers. Supporting this one protocol is what makes "the hospital chooses"
  real. It is the default, pointed at `http://localhost:11434/v1`, because the
  on-premises case should work without ceremony.
- **`AnthropicModel`** — the Messages API.
- **`RecordedModel`** — replays responses from disk, keyed by a hash of the call.

`RecordedModel` earns its place by doing three jobs at once. It lets an agent
act as the model today with no API key, by writing the response file directly —
which is how Experiment 0001 will run right now. It makes evaluation
**reproducible**: scoring runs over recorded artifacts, so a result can be
re-derived later without paying for inference or depending on a model version
that no longer exists. And it is a cache, so re-running after a downstream code
change costs nothing. It also composes — wrapped around a live provider, it
records while it runs.

## What this buys beyond the requirement

Because every model call is recorded as an artifact, **comparing two models
becomes a configuration change plus a re-run over a fixed gold set.** That turns
"which model should the hospital use?" from a sales conversation into a measured
one, and it means Veris can tell a customer what accuracy their chosen local
model actually delivers on their own corpus. That is a stronger position than
promising accuracy from a model the customer cannot run.

## Tradeoffs accepted

- Small-task decomposition costs more total tokens than one long-context call,
  and is slower per document. Accepted: it is the price of running anywhere.
- Some findings may genuinely require reasoning over more context than a local
  model can hold. If so, capability will differ by deployment, and the honest
  response is to **measure and disclose** the difference rather than hide it.
- Two extra providers is surface area to maintain. Small — roughly 40 lines each,
  no SDK churn.

## What would cause reconsideration

- Local models prove unable to reach usable extraction quality even with
  verification. Then the product tiers explicitly by deployment rather than
  pretending parity.
- A structured-output standard becomes genuinely universal across local runtimes,
  at which point the line-oriented format is unnecessary complexity.

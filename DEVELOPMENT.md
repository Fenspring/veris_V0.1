# Development

```bash
make install    # virtualenv + dependencies
make seed       # ingest corpus, connect demo systems, analyse, run agents
make test       # retrieval regression + connector contract tests
make eval       # 49 checks across ten intelligence capabilities
make serve      # http://127.0.0.1:8000
```

No API key is needed: the default model provider replays recorded responses, so
analysis is real and reproducible without inference.

## Layout

```
veris/
  connectors/   base.py (framework) · mock.py · catalog.py
  store.py      domain model — all SQL lives here
  pipeline.py   ingestion · extract.py structural extraction
  sync.py       synchronization engine
  changes.py    version diffing · analyze.py relationships and findings
  agents.py     modular reasoning · ask.py natural language
  model.py      provider-agnostic inference · credentials.py OS keychain
  api.py        HTTP boundary · cli.py commands
web/            static workspace, no build step
eval/           northstar_cases.json (planted) · gold.json (real corpus)
tests/          connector contract tests · retrieval regression
docs/           decisions/ and discoveries/
```

## Adding things

- **A connector** — see `CONNECTORS.md`. Must pass the contract tests.
- **An agent** — see `AGENTS.md`. Must state what it cannot do.
- **A model provider** — one class in `model.py` with `complete()`. No SDK.
- **A document format** — one function in `pipeline.read_text`.

## Conventions

Comments explain *why*, especially where the obvious approach is wrong — several
of the sharpest bugs in this codebase looked correct in review and were caught by
tests. When you fix one of those, write down what made it invisible.

Any change to retrieval, extraction, or agents must keep `make eval` at 49/49 and
the contract tests green. If a change makes a test fail for a good reason, change
the test deliberately and say why in the commit.

## Recorded model responses

`data/recordings/` holds request/response pairs keyed by a hash of the call. A
missing recording raises with the request written to disk, so it can be filled in
and the run resumed. This makes evaluation reproducible without paying for
inference and lets the demo run with no model configured.

To use a live model: set `VERIS_MODEL_PROVIDER` (see `.env.example`).

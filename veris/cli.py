"""Veris command line.

    python -m veris.cli seed [--corpus DIR]   ingest, detect changes, analyse
    python -m veris.cli ingest PATH           add a file or directory
    python -m veris.cli changes FROM TO       diff two document versions
    python -m veris.cli analyze DOCUMENT_ID   build relationships and findings
    python -m veris.cli serve [--port 8000]   run the API and workspace
    python -m veris.cli stats                 what the graph currently holds
    python -m veris.cli reset                 empty the graph (keeps files)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyze import analyze_source_version
from .changes import find_version_pairs, record_changes
from .model import ModelError, from_env
from .pipeline import IngestError, ingest_directory, ingest_file
from .seed import seed
from .store import Store


def _store(args) -> Store:
    return Store(args.db)


def cmd_seed(args) -> int:
    store = _store(args)
    try:
        model = from_env()
    except ModelError as e:
        print(f"Model unavailable: {e}", file=sys.stderr)
        return 2
    report = seed(store, Path(args.corpus), Path(args.data), model, reset=not args.keep)
    print(json.dumps(report, indent=2, default=str))
    if report["analysis"].get("skipped"):
        print(f"\n{report['analysis']['skipped']} requirements were not analysed because "
              f"the model had no recorded response for them.\n"
              f"Configure a live model (see .env.example) or supply recordings.",
              file=sys.stderr)
    return 0


def cmd_ingest(args) -> int:
    store, path = _store(args), Path(args.path)
    try:
        if path.is_dir():
            results = ingest_directory(store, path, Path(args.data))
        else:
            results = [ingest_file(store, path, Path(args.data))]
    except IngestError as e:
        print(f"Rejected: {e}", file=sys.stderr)
        return 1
    for r in results:
        state = "already present" if r.reused else f"{r.entities} entities"
        print(f"  {r.document_type:11} {r.title[:52]:54} {state}")
    return 0


def cmd_changes(args) -> int:
    store = _store(args)
    if args.from_id and args.to_id:
        pairs = [({"id": args.from_id}, {"id": args.to_id})]
    else:
        pairs = find_version_pairs(store)
        if not pairs:
            print("No version pairs found. Ingest two versions of a source first.")
            return 1
    total = 0
    for older, newer in pairs:
        total += len(record_changes(store, older["id"], newer["id"]))
    print(f"{total} changes recorded")
    return 0


def cmd_analyze(args) -> int:
    store = _store(args)
    try:
        model = from_env()
    except ModelError as e:
        print(f"Model unavailable: {e}", file=sys.stderr)
        return 2
    print(json.dumps(analyze_source_version(store, args.document_id, model), indent=2))
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    os.environ.setdefault("VERIS_DB", str(args.db))
    print(f"Veris on http://{args.host}:{args.port}  (workspace at /, API at /api/v1)")
    uvicorn.run("veris.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_stats(args) -> int:
    print(json.dumps(_store(args).stats(), indent=2))
    return 0


def cmd_reset(args) -> int:
    store = _store(args)
    if not args.yes:
        print("This empties the knowledge graph. Re-run with --yes to confirm.")
        return 1
    store.reset()
    print("Graph emptied. Source files and canonical text are untouched.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="veris", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.environ.get("VERIS_DB", "data/veris.db"))
    p.add_argument("--data", default=os.environ.get("VERIS_DATA_DIR", "data"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="ingest, detect changes, analyse")
    s.add_argument("--corpus", default="corpus/northstar")
    s.add_argument("--keep", action="store_true", help="do not empty the graph first")
    s.set_defaults(fn=cmd_seed)

    s = sub.add_parser("ingest", help="add a file or directory")
    s.add_argument("path")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("changes", help="diff two document versions")
    s.add_argument("from_id", nargs="?")
    s.add_argument("to_id", nargs="?")
    s.set_defaults(fn=cmd_changes)

    s = sub.add_parser("analyze", help="build relationships and findings")
    s.add_argument("document_id")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("serve", help="run the API and workspace")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    sub.add_parser("stats", help="what the graph holds").set_defaults(fn=cmd_stats)

    s = sub.add_parser("reset", help="empty the graph")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

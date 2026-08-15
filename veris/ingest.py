"""Document ingestion: source files -> frozen canonical text.

Every claim Veris makes cites a character span in a canonical text file. That
only means something if the canonical text is stable, so extraction happens
exactly once per document and the result is hashed. If a source file changes,
its hash changes, and every claim citing it is stale by construction.

Canonical text is the unit of grounding, not the original file. A PDF has no
character offsets; its extracted text does.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "docs", "data", "veris", "__pycache__"}


@dataclass(frozen=True)
class Document:
    doc_id: str
    source_path: str
    genre: str          # policy | standard — the hospital's own filing label
    title: str
    text_sha256: str
    char_count: int
    metadata: dict      # front-matter for standards, parsed header for policies


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def extract_pdf(path: Path) -> str:
    import pypdf

    pages = [p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages]
    return "\n".join(pages)


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Split YAML-ish front matter from a markdown file.

    Deliberately not a YAML parser: the front matter here is flat key/value and
    a real parser would pull in a dependency for no benefit. Unparseable input
    yields no metadata rather than an error, because metadata is an enrichment
    and never the grounding.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip('"')
    return meta, text[end + 4 :].lstrip("\n")


def load_document(path: Path, root: Path) -> Document | None:
    rel = path.relative_to(root)

    if path.suffix.lower() == ".pdf":
        text = extract_pdf(path)
        genre = "policy"
        meta: dict = {}
        title = re.sub(r"^\d+_", "", path.stem).replace("_", " ")
    elif path.suffix.lower() == ".md":
        raw = path.read_text(encoding="utf-8")
        meta, text = parse_front_matter(raw)
        genre = "standard"
        title = meta.get("standard") or path.stem
    else:
        return None

    if not text.strip():
        # Empty sources are kept, not dropped. A standard with no retrievable
        # content is invisible to extraction but must stay visible to coverage
        # analysis: silently dropping it manufactures a false gap downstream,
        # which is the exact failure mode Discovery 0002 is about. The flag
        # travels with the document so confidence can be capped later.
        text = ""
        meta = {**meta, "content_status": "empty"}

    return Document(
        doc_id=_slug(str(rel.with_suffix(""))),
        source_path=str(rel),
        genre=genre,
        title=title,
        text_sha256=_sha(text),
        char_count=len(text),
        metadata=meta,
    ), text


def ingest(root: Path, out: Path) -> list[Document]:
    canonical = out / "canonical"
    canonical.mkdir(parents=True, exist_ok=True)

    docs: list[Document] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        # Chapter index files and the repo's own governing markdown are
        # navigation and project documentation, not hospital knowledge.
        if path.suffix.lower() == ".md" and (
            path.name.startswith("_") or path.parent == root
        ):
            continue

        loaded = load_document(path, root)
        if loaded is None:
            continue
        doc, text = loaded
        (canonical / f"{doc.doc_id}.txt").write_text(text, encoding="utf-8")
        docs.append(doc)

    (out / "manifest.json").write_text(
        json.dumps([asdict(d) for d in docs], indent=2), encoding="utf-8"
    )
    return docs


def load_canonical(out: Path, doc_id: str) -> str:
    return (out / "canonical" / f"{doc_id}.txt").read_text(encoding="utf-8")


def verify_span(out: Path, doc_id: str, start: int, end: int, quote: str) -> bool:
    """The trust primitive. A claim whose quote is not literally present in the
    canonical text at the cited offsets is fabricated, and is dropped."""
    return load_canonical(out, doc_id)[start:end] == quote


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "data").resolve()
    docs = ingest(root, out)
    by_genre: dict[str, int] = {}
    empty = 0
    for d in docs:
        by_genre[d.genre] = by_genre.get(d.genre, 0) + 1
        empty += d.char_count == 0
    print(f"ingested {len(docs)} documents into {out}")
    for g, n in sorted(by_genre.items()):
        print(f"  {g}: {n}")
    print(f"  empty (zero-length canonical text): {empty}")

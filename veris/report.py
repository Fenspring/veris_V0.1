"""Coverage report.

Progressive disclosure, per DESIGN_PRINCIPLES §7: the finding first, then what
it rests on, then the evidence, then the source. Nothing is asserted without a
citation the reader can open, and every absence names the scope it was
established over.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path

from .claims import load_claims

STATUS = {
    "NOT_COVERED": ("gap", "No coverage found"),
    "PARTIAL": ("partial", "Partially covered"),
    "COVERED": ("ok", "Covered"),
    "UNPARSEABLE": ("unk", "Could not assess"),
}

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a19;--mut:#6b6b66;--line:#e3e3df;--card:#fff;
--gap:#b3261e;--partial:#8a6100;--ok:#1f6b3a;--accent:#2b5cab}
:root:not([data-theme=light]){}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#161614;--fg:#eceae5;--mut:#a3a099;--line:#302e2a;--card:#1e1d1a;
--gap:#f2938c;--partial:#e5b45e;--ok:#7fca9b;--accent:#8fb4ee}}
:root[data-theme=dark]{--bg:#161614;--fg:#eceae5;--mut:#a3a099;--line:#302e2a;
--card:#1e1d1a;--gap:#f2938c;--partial:#e5b45e;--ok:#7fca9b;--accent:#8fb4ee}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;padding:2rem 1.25rem 5rem;
font:16px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:60rem;margin:0 auto}
h1{font-size:1.7rem;margin:0 0 .3rem;letter-spacing:-.01em}
.sub{color:var(--mut);margin:0 0 2rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.75rem;margin-bottom:2rem}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem}
.tile b{display:block;font-size:1.6rem;line-height:1.2;font-variant-numeric:tabular-nums}
.tile span{color:var(--mut);font-size:.8rem}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:.9rem 1.1rem;margin:0 0 2rem;font-size:.92rem}
h2{font-size:1.05rem;margin:2.2rem 0 .8rem;padding-bottom:.4rem;border-bottom:1px solid var(--line)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;
margin-bottom:.6rem;overflow:hidden}
summary{cursor:pointer;padding:.85rem 1rem;display:flex;gap:.7rem;align-items:baseline;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";color:var(--mut);font-size:.8rem}
details[open] summary::before{content:"▾"}
.loc{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.lab{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;padding:.13rem .5rem;
border-radius:99px;border:1px solid currentColor;white-space:nowrap}
.gap{color:var(--gap)}.partial{color:var(--partial)}.ok{color:var(--ok)}.unk{color:var(--mut)}
.tl{color:var(--mut);font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.body{padding:0 1rem 1.1rem;border-top:1px solid var(--line);margin-top:-1px}
.body h4{margin:1.1rem 0 .3rem;font-size:.76rem;text-transform:uppercase;
letter-spacing:.06em;color:var(--mut)}
blockquote{margin:.4rem 0;padding:.5rem .9rem;border-left:2px solid var(--line);
color:var(--fg);font-size:.93rem}
blockquote cite{display:block;font-style:normal;color:var(--mut);font-size:.8rem;margin-bottom:.3rem}
.scope{color:var(--mut);font-size:.83rem;margin-top:.9rem;font-style:italic}
"""


def render(data: Path, out: Path) -> Path:
    findings = json.loads((data / "findings.json").read_text())
    claims = {c.claim_id: c for c in load_claims(data)}
    manifest = json.loads((data / "manifest.json").read_text())
    n_pol = sum(1 for d in manifest if d["genre"] == "policy")
    n_std = sum(1 for d in manifest if d["genre"] == "standard")
    counts = Counter(f["verdict"] for f in findings)

    order = {"NOT_COVERED": 0, "PARTIAL": 1, "UNPARSEABLE": 2, "COVERED": 3}
    findings.sort(key=lambda f: (order.get(f["verdict"], 9), f["ep"]))

    rows = []
    for f in findings:
        cls, label = STATUS.get(f["verdict"], ("unk", f["verdict"]))
        ev = ""
        for cid in f["evidence"]:
            c = claims.get(cid)
            if not c:
                continue
            quote = html.escape(c.quote[:600]).replace("\n", " ")
            ev += (f"<blockquote><cite>{html.escape(c.locator)}</cite>{quote}</blockquote>")
        missing = (f"<h4>Not addressed by any policy found</h4>"
                   f"<p>{html.escape(f['missing'])}</p>") if f["missing"] else ""
        evidence_block = f"<h4>Evidence</h4>{ev}" if ev else ""
        disc = ""
        if f["verdict"] == "NOT_COVERED":
            disc = ("<p class='scope'>This absence was re-tested with independently "
                    "generated search terms before being reported.</p>"
                    if f["disconfirmed"] else "")
        rows.append(
            f"<details><summary><span class='lab {cls}'>{label}</span>"
            f"<span class='loc'>{html.escape(f['ep'])}</span>"
            f"<span class='tl'>{html.escape(f['reason'][:110])}</span></summary>"
            f"<div class='body'><h4>Assessment</h4><p>{html.escape(f['reason'])}</p>"
            f"{missing}{evidence_block}"
            f"<p class='scope'>Scope: {html.escape(f['scope'])}.</p>{disc}</div></details>"
        )

    body = f"""<title>Veris Coverage Probe</title>
<style>{CSS}</style>
<div class="wrap">
<h1>Accreditation coverage</h1>
<p class="sub">Joint Commission Elements of Performance that require documentation,
assessed against the organization's own policy library.</p>

<div class="tiles">
<div class="tile"><b class="gap">{counts.get('NOT_COVERED',0)}</b><span>no coverage found</span></div>
<div class="tile"><b class="partial">{counts.get('PARTIAL',0)}</b><span>partially covered</span></div>
<div class="tile"><b class="ok">{counts.get('COVERED',0)}</b><span>covered</span></div>
<div class="tile"><b>{n_pol}</b><span>policy documents</span></div>
<div class="tile"><b>{n_std}</b><span>standards</span></div>
</div>

<p class="note"><b>How to read this.</b> Every finding names the policy text it
rests on, and every statement of absence names the scope it was established
over. &ldquo;No coverage found&rdquo; means nothing addressing the requirement
was found in the {n_pol} policy documents supplied &mdash; not that the
organization lacks it. Evidence may exist in systems that were not connected.</p>

<h2>Findings ({len(findings)})</h2>
{''.join(rows)}
</div>"""
    out.write_text(body, encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    p = render(Path("data"), Path(sys.argv[1] if len(sys.argv) > 1 else "data/report.html"))
    print(f"wrote {p}")


def render_brief(data: Path, out: Path) -> Path:
    """The clinician brief. Same progressive disclosure, but organised by what
    each source does — and showing the sections that are empty."""
    b = json.loads((data / "brief.json").read_text())

    def cite(c):
        q = html.escape(c["quote"][:700]).replace("\n", " ")
        return f"<blockquote><cite>{html.escape(c['locator'])}</cite>{q}</blockquote>"

    secs = ""
    for s in b["sections"]:
        if s["claims"]:
            inner = "".join(cite(c) for c in s["claims"])
            head = (f"<summary><span class='lab ok'>{len(s['claims'])} connected</span>"
                    f"<span class='loc'>{html.escape(s['label'])}</span></summary>")
            secs += f"<details open>{head}<div class='body'>{inner}</div></details>"
        else:
            head = ("<summary><span class='lab unk'>nothing connected</span>"
                    f"<span class='loc'>{html.escape(s['label'])}</span>"
                    f"<span class='tl'>{html.escape(s['absence_note'])}</span></summary>")
            secs += (f"<details>{head}<div class='body'><p class='scope'>"
                     f"{html.escape(s['absence_note'])} This is a statement about what "
                     f"has been connected to Veris, not about what the organization "
                     f"possesses.</p></div></details>")

    reg = ""
    for r in b["regulatory"]:
        cls, label = STATUS.get(r["verdict"], ("unk", r["verdict"]))
        miss = (f"<h4>Not addressed by any connected policy</h4><p>{html.escape(r['missing'])}</p>"
                if r["missing"] else "")
        reg += (f"<details><summary><span class='lab {cls}'>{label}</span>"
                f"<span class='loc'>{html.escape(r['ep'])}</span></summary>"
                f"<div class='body'><p>{html.escape(r['reason'])}</p>{miss}</div></details>")
    reg_block = (f"<h2>Regulatory intelligence</h2><p class='note'>These findings appear "
                 f"in no single document. They exist only in the relation between the "
                 f"accreditation standards and the organization's own policies.</p>{reg}"
                 if reg else "")

    summary = html.escape(b["summary"])
    summary = re.sub(r"\[(S\d+)\]", r"<sup class='src'>\1</sup>", summary)

    body = f"""<title>Veris Brief</title>
<style>{CSS}
.q{{font-size:1.35rem;font-weight:600;letter-spacing:-.01em;margin:0 0 1.2rem}}
.sum{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:1.1rem 1.25rem;margin-bottom:1.6rem;font-size:1.02rem}}
.src{{color:var(--accent);font-size:.68em;font-weight:600;padding-left:.1em}}
</style>
<div class="wrap">
<p class="sub">Veris &middot; clinician brief</p>
<p class="q">{html.escape(b['question'])}</p>
<div class="sum">{summary}</div>
<h2>Connected knowledge</h2>
{secs}
{reg_block}
<p class="scope">Answered across {html.escape(b['scope'])}. Every statement above is
drawn from a cited extract; sections marked &ldquo;nothing connected&rdquo; describe
the limits of what Veris has been given.</p>
</div>"""
    out.write_text(body, encoding="utf-8")
    return out

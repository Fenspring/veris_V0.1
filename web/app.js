/* Veris workspace client.
   Every view reads the same API the domain model exposes; nothing is computed
   here. The UI's job is to make relationships legible, keep evidence one click
   away, and never state a finding more strongly than its provenance allows. */

/* In the desktop shell the core listens on a loopback port chosen at startup,
   injected as window.__VERIS_API__. Served from the API itself, this is a
   same-origin relative path. One bundle, both deployments. */
const API = (typeof window !== 'undefined' && window.__VERIS_API__)
  ? window.__VERIS_API__ + '/api/v1'
  : '/api/v1';
const $ = (s, r = document) => r.querySelector(s);
const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c; if (h !== undefined) n.innerHTML = h; return n; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/* Source text is stored verbatim so its span stays verifiable. Structural
   markers are stripped for display only — never in the store. */
const clean = s => String(s ?? '').replace(/\*\*(Attributes|Crosswalk):\*\*[^\n]*/g, '').replace(/\s+/g, ' ').trim();
const clip = (s, n) => { s = String(s ?? ''); if (s.length <= n) return s;
  const cut = s.slice(0, n); const sp = cut.lastIndexOf(' ');
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut) + '…'; };

const state = { view: 'landing', entity: null, docs: [], entities: [] };

async function api(path, opts) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

const sevClass = s => ({ HIGH: 'b-high', MEDIUM: 'b-med', LOW: 'b-low' }[s] || 'b-neutral');
const sevLabel = s => ({ HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low' }[s] || s);
const typeLabel = t => ({
  POTENTIAL_CONFLICT: 'Potential conflict', POTENTIAL_GAP: 'Potential gap',
  LIKELY_ALIGNED: 'Likely aligned', INSUFFICIENT_EVIDENCE: 'Insufficient evidence',
  REQUIRES_HUMAN_REVIEW: 'Requires review',
}[t] || t);
const relLabel = t => (t || '').toLowerCase().replace(/_/g, ' ');
const provLabel = p => ({
  SOURCE_FACT: 'Source fact', VERIS_INTERPRETATION: 'Veris interpretation',
  MODEL_INFERENCE: 'Model inference', HUMAN_REVIEW: 'Human review',
}[p] || p);

/* ---------- navigation ---------- */

function go(view) {
  state.view = view;
  if (view === 'landing') {
    $('#landing').classList.remove('hidden');
    $('#shell').classList.add('hidden');
    return;
  }
  $('#landing').classList.add('hidden');
  $('#shell').classList.remove('hidden');
  document.querySelectorAll('.nav').forEach(n =>
    n.classList.toggle('active', n.dataset.goto === view));
  ({ investigation, explorer, ask: askView, findings: findingsView,
     documents: documentsView, connections: connectionsView, agents: agentsView }[view] || investigation)();
}

document.addEventListener('click', e => {
  const g = e.target.closest('[data-goto]');
  if (g) { go(g.dataset.goto); return; }
  if (e.target.closest('[data-close-drawer]')) closeDrawer();
});

/* ---------- drawer ---------- */

function openDrawer(html) {
  $('#drawer-panel').innerHTML = html;
  $('#drawer').classList.remove('hidden');
}
function closeDrawer() { $('#drawer').classList.add('hidden'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

/* ---------- investigation workspace ---------- */

async function investigation() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Investigation</h2>
    <p class="page-sub">A change in an authoritative source, and what it may affect
    across the organization's own knowledge.</p><div class="spinner">Loading…</div>`;

  const [changes, overview] = await Promise.all([api('/changes'), api('/overview')]);
  const active = changes.filter(c => c.change_type !== 'UNCHANGED');
  main.innerHTML = `<h2 class="page">Investigation</h2>
    <p class="page-sub">A change in an authoritative source, and what it may affect
    across the organization's own knowledge.</p>`;

  if (!active.length) { main.append(el('div', 'empty', 'No source changes detected yet.')); return; }

  const head = active[0];
  main.append(el('div', 'tiles', `
    <div class="tile"><b>${active.length}</b><span>requirements changed</span></div>
    <div class="tile"><b class="">${overview.counts.relationships}</b><span>relationships</span></div>
    <div class="tile"><b>${overview.findings_by_severity.find(s => s.severity === 'HIGH')?.n || 0}</b><span>high severity</span></div>
    <div class="tile"><b>${overview.open_findings}</b><span>awaiting review</span></div>`));

  main.append(el('div', 'card', `<div class="card-body" style="border:0;padding:1.1rem">
    <div class="prov">Source</div>
    <div class="title" style="font-size:1.02rem">${esc(head.document_title || 'Source')}</div>
    <div class="meta">${esc(head.publisher || '')} · version ${esc(head.from_version || '?')} → <b>${esc(head.to_version || '?')}</b>${head.effective_date ? ' · effective ' + esc(head.effective_date) : ''}</div>
  </div>`));

  for (const c of active) main.append(await changeCard(c));
}

async function changeCard(c) {
  const impact = await api(`/changes/${c.id}/impact`);
  const detail = JSON.parse(c.detail || '{}');
  const card = el('div', 'card');

  const byType = {};
  for (const r of impact.related) byType[r.document_type] = (byType[r.document_type] || 0) + 1;
  const worst = impact.findings.find(f => f.severity === 'HIGH') ? 'HIGH'
    : impact.findings.length ? 'MEDIUM' : 'LOW';

  card.append(el('div', 'card-head', `
    <span class="badge ${sevClass(worst)}">${esc(c.change_type)}</span>
    <div style="flex:1">
      <div class="title">${esc(c.locator)} — ${esc(clip(c.summary, 190))}</div>
      <div class="meta">${impact.findings.length} finding${impact.findings.length === 1 ? '' : 's'} ·
        ${impact.related.length} connected knowledge item${impact.related.length === 1 ? '' : 's'}</div>
    </div>`));

  const body = el('div', 'card-body');

  if (impact.requirement) {
    body.append(el('h4', 'sec', 'What the source now requires'));
    body.append(el('blockquote', '', `<cite>${esc(impact.requirement.locator)} · ${esc(impact.requirement.publisher || '')} · ${esc(impact.requirement.authority || '')}</cite>${esc(clip(clean(impact.requirement.statement), 900))}`));
  }
  if (detail.removed?.length || detail.added?.length) {
    body.append(el('h4', 'sec', 'What changed'));
    for (const r of (detail.removed || [])) body.append(el('div', 'diffline diff-del', esc(r)));
    for (const a of (detail.added || [])) body.append(el('div', 'diffline diff-add', esc(a)));
    body.append(el('div', 'prov', 'Veris interpretation — computed by comparing the two versions'));
  }

  body.append(el('h4', 'sec', 'Potential impact'));
  const flow = el('div', 'flow');
  const order = ['POLICY', 'PROCEDURE', 'EDUCATION', 'COMPETENCY', 'STANDARD'];
  const present = order.filter(t => byType[t]);
  if (!present.length) flow.append(el('div', 'empty', 'No connected organizational knowledge was found for this requirement.'));
  present.forEach((t, i) => {
    flow.append(el('div', 'flow-node', `<div class="n">${byType[t]}</div><div class="l">${t.toLowerCase()}</div>`));
    if (i < present.length - 1) flow.append(el('div', 'flow-arrow', '→'));
  });
  body.append(flow);

  if (impact.findings.length) {
    body.append(el('h4', 'sec', 'Findings'));
    for (const f of impact.findings) {
      const row = el('div', 'card', '');
      row.style.margin = '.4rem 0';
      row.append(el('div', 'card-head', `
        <span class="badge ${sevClass(f.severity)}">${sevLabel(f.severity)}</span>
        <span class="badge b-neutral">${typeLabel(f.finding_type)}</span>
        <div style="flex:1">
          <div class="title" style="font-size:.94rem">${esc(f.title)}</div>
          <div class="stmt">${esc(clip(f.statement, 260))}</div>
          <div class="meta">${esc(f.status)} · review: ${esc(f.recommended_reviewer || '—')}</div>
        </div>`));
      row.querySelector('.card-head').onclick = () => openFinding(f.id);
      body.append(row);
    }
  }
  card.append(body);
  card.querySelector('.card-head').onclick = e => {
    if (e.target.closest('.card-body')) return;
    body.classList.toggle('hidden');
  };
  return card;
}

/* ---------- finding drawer with review ---------- */

async function openFinding(id) {
  openDrawer('<div class="spinner">Loading…</div>');
  const f = await api(`/findings/${id}`);
  const ev = f.evidence.map(e => `<blockquote><cite>${esc(e.document_title)} · ${esc(e.location_label || '')} · ${esc(e.evidence_role || '')}</cite>${esc(clip(clean(e.quote), 1200))}</blockquote>`).join('');
  const ents = f.entities.map(e => `<div class="list-item" onclick="showEntity('${e.id}')"><b>${esc(e.locator)}</b><div class="d">${esc(e.document_title)} · ${esc(e.document_type)}${e.department ? ' · ' + esc(e.department) : ''}</div></div>`).join('');
  const log = f.reviews.length ? f.reviews.map(r => `<div class="ev"><b>${esc(r.action)}</b> by ${esc(r.reviewer)}
      ${r.assigned_to ? '→ ' + esc(r.assigned_to) : ''} ${r.due_date ? '(due ' + esc(r.due_date) + ')' : ''}
      <div class="when">${esc(r.created_at)}</div>${r.comment ? '<div>' + esc(r.comment) + '</div>' : ''}</div>`).join('')
    : '<div class="empty">No review recorded yet.</div>';

  openDrawer(`
    <button class="btn sm" data-close-drawer style="float:right">Close</button>
    <span class="badge ${sevClass(f.severity)}">${sevLabel(f.severity)}</span>
    <span class="badge b-neutral">${typeLabel(f.finding_type)}</span>
    <span class="badge b-accent">${esc(f.status)}</span>
    <h3>${esc(f.title)}</h3>
    <div class="meta">${provLabel(f.provenance_class)}${f.confidence ? ' · confidence ' + f.confidence : ''} · recommended reviewer: ${esc(f.recommended_reviewer || '—')}</div>
    <p>${esc(f.statement)}</p>
    ${f.missing ? `<h4 class="sec">Not addressed by any connected knowledge</h4><p>${esc(f.missing)}</p>` : ''}
    <h4 class="sec">Evidence</h4>${ev || '<div class="empty">No evidence attached.</div>'}
    <div class="scope">Scope: ${esc(f.scope)}. This describes what has been connected to Veris, not what the organization possesses.</div>
    <h4 class="sec">Connected knowledge</h4>${ents || '<div class="empty">None.</div>'}
    <h4 class="sec">Review</h4>
    <div class="field"><label>Reviewer</label><input id="rv-who" value="kyle" maxlength="120"></div>
    <div class="field"><label>Comment</label><textarea id="rv-note" rows="2" maxlength="4000" placeholder="Optional"></textarea></div>
    <div class="grid two">
      <div class="field"><label>Assign to</label><input id="rv-assign" placeholder="e.g. Nurse Executive" maxlength="120"></div>
      <div class="field"><label>Due date</label><input id="rv-due" type="date"></div>
    </div>
    <div class="rowbtns">
      <button class="btn sm primary" onclick="review('${f.id}','ACCEPT')">Accept</button>
      <button class="btn sm" onclick="review('${f.id}','REJECT')">Reject</button>
      <button class="btn sm" onclick="review('${f.id}','NEEDS_REVIEW')">Needs review</button>
      <button class="btn sm" onclick="review('${f.id}','ASSIGN')">Assign</button>
      <button class="btn sm" onclick="review('${f.id}','COMMENT')">Comment</button>
      <button class="btn sm" onclick="review('${f.id}','RESOLVE')">Resolve</button>
    </div>
    <h4 class="sec">Review history</h4><div class="timeline">${log}</div>`);
}

window.review = async (id, action) => {
  const body = {
    action, reviewer: ($('#rv-who')?.value || 'unknown').trim() || 'unknown',
    comment: $('#rv-note')?.value || null,
    assigned_to: $('#rv-assign')?.value || null,
    due_date: $('#rv-due')?.value || null,
  };
  try {
    await api(`/findings/${id}/reviews`, { method: 'POST', body: JSON.stringify(body) });
    await openFinding(id);
    if (state.view === 'findings') findingsView();
  } catch (e) { alert('Review failed: ' + e.message); }
};

/* ---------- knowledge explorer ---------- */

async function explorer() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Knowledge Explorer</h2>
    <p class="page-sub">Select any piece of knowledge to see what it connects to,
    why, and what is unresolved around it.</p>
    <div class="explorer"><div class="list" id="ent-list"></div><div id="ent-detail"></div></div>`;
  state.entities = await api('/knowledge?limit=200');
  const list = $('#ent-list');
  for (const e of state.entities) {
    const conns = e.relationship_count
      ? `${e.relationship_count} connection${e.relationship_count === 1 ? '' : 's'}` : 'no connections';
    const flag = e.finding_count ? ` · <span style="color:var(--high)">${e.finding_count} finding${e.finding_count === 1 ? '' : 's'}</span>` : '';
    const item = el('div', 'list-item', `<b>${esc(clip(e.locator, 62))}</b>
      <div class="d">${esc(e.document_type)} · ${conns}${flag}</div>`);
    item.onclick = () => { document.querySelectorAll('.list-item').forEach(i => i.classList.remove('active')); item.classList.add('active'); showEntity(e.id); };
    list.append(item);
  }
  if (state.entities.length) { list.firstChild.classList.add('active'); showEntity(state.entities[0].id); }
}

window.showEntity = async (id) => {
  const target = $('#ent-detail');
  if (!target) { const d = await api(`/knowledge/${id}/relationships`); return openDrawer(entityHtml(d)); }
  target.innerHTML = '<div class="spinner">Loading…</div>';
  target.innerHTML = entityHtml(await api(`/knowledge/${id}/relationships`));
};

function entityHtml(d) {
  const e = d.entity;
  const groups = {};
  for (const r of d.related) (groups[r.relationship_type] ||= []).push(r);
  const rels = Object.entries(groups).map(([type, items]) => `
    <h4 class="sec">${relLabel(type)} (${items.length})</h4>
    ${items.map(r => `<div class="list-item" onclick="showEntity('${r.entity_id}')">
        <b>${esc(r.locator).slice(0, 74)}</b>
        <div class="d">${esc(r.document_type)}${r.department ? ' · ' + esc(r.department) : ''}
        · ${provLabel(r.provenance_class)} · ${esc(r.status)}</div>
        ${r.rationale ? `<div class="d" style="margin-top:.2rem">${esc(clip(r.rationale, 190))}</div>` : ''}
      </div>`).join('')}`).join('');

  const finds = d.findings.length ? d.findings.map(f => `
    <div class="list-item" onclick="openFinding('${f.id}')">
      <span class="badge ${sevClass(f.severity)}">${sevLabel(f.severity)}</span>
      <b style="margin-left:.4rem">${typeLabel(f.finding_type)}</b>
      <div class="d">${esc(clip(f.title, 130))}</div></div>`).join('')
    : '<div class="empty">No open findings on this item.</div>';

  return `<div class="card"><div class="card-body" style="border:0;padding:1.2rem">
      <span class="badge b-req">${esc(e.role)}</span>
      <span class="badge b-neutral">${esc(e.document_type)}</span>
      <h3 style="margin:.5rem 0 .1rem">${esc(e.locator)}</h3>
      <div class="meta">${esc(e.document_title)}${e.document_version ? ' v' + esc(e.document_version) : ''}
        ${e.publisher ? ' · ' + esc(e.publisher) : ''}${e.authority ? ' · ' + esc(e.authority) : ''}
        ${e.department ? ' · ' + esc(e.department) : ''}${e.owner ? ' · owner ' + esc(e.owner) : ''}
        ${e.effective_date ? ' · effective ' + esc(e.effective_date) : ''}</div>
      <blockquote style="margin-top:.8rem"><cite>${provLabel(e.provenance_class)} · verified span in source</cite>${esc(clip(clean(e.statement), 1400))}</blockquote>
      <h4 class="sec">Findings</h4>${finds}
      ${rels || '<h4 class="sec">Connections</h4><div class="empty">Nothing connected to this item yet.</div>'}
    </div></div>`;
}

/* ---------- ask ---------- */

function askView() {
  $('#main').innerHTML = `<h2 class="page">Ask Veris</h2>
    <p class="page-sub">Questions resolve against the same knowledge graph the
    workspace uses. Answers cite their sources, and carry the findings recorded
    against the knowledge they touch.</p>
    <div class="searchbar">
      <input id="q" placeholder="What is our policy on wasting controlled substances?" maxlength="500">
      <button class="btn primary" onclick="runAsk()">Ask</button>
    </div>
    <div class="rowbtns" style="margin-bottom:1.4rem">
      ${['What is our policy on wasting controlled substances?',
         'What education is connected to controlled substance handling?',
         'Where do we have conflicting guidance?']
        .map(q => `<button class="btn sm" onclick="document.getElementById('q').value=${JSON.stringify(q).replace(/"/g, '&quot;')};runAsk()">${esc(q)}</button>`).join('')}
    </div>
    <div id="ask-out"></div>`;
}

window.runAsk = async () => {
  const q = $('#q').value.trim();
  if (q.length < 3) return;
  const out = $('#ask-out');
  out.innerHTML = '<div class="spinner">Consulting the knowledge graph…</div>';
  let d;
  try { d = await api('/intelligence/query', { method: 'POST', body: JSON.stringify({ question: q }) }); }
  catch (e) { out.innerHTML = `<div class="empty">Query failed: ${esc(e.message)}</div>`; return; }

  out.innerHTML = '';
  if (d.summary) out.append(el('div', 'card', `<div class="card-body" style="border:0;padding:1.15rem">
    ${esc(d.summary).replace(/\[(E\d+)\]/g, '<sup style="color:var(--accent);font-weight:600">$1</sup>')}</div>`));

  if (d.findings.length) {
    out.append(el('h4', 'sec', 'What Veris knows that no single document says'));
    for (const f of d.findings) {
      const c = el('div', 'card');
      c.append(el('div', 'card-head', `<span class="badge ${sevClass(f.severity)}">${sevLabel(f.severity)}</span>
        <span class="badge b-neutral">${typeLabel(f.finding_type)}</span>
        <div style="flex:1"><div class="title" style="font-size:.94rem">${esc(f.title)}</div>
        <div class="stmt">${esc(clip(f.statement, 240))}</div></div>`));
      c.querySelector('.card-head').onclick = () => openFinding(f.id);
      out.append(c);
    }
  }

  for (const s of d.sections) {
    out.append(el('h4', 'sec', s.label));
    if (!s.items.length) { out.append(el('div', 'empty', s.absence_note)); continue; }
    for (const it of s.items) {
      const c = el('div', 'card');
      c.append(el('div', 'card-head', `<div style="flex:1">
        <div class="title" style="font-size:.93rem">${esc(it.locator)}</div>
        <div class="meta">${esc(it.document_type)}${it.department ? ' · ' + esc(it.department) : ''}${it.publisher ? ' · ' + esc(it.publisher) : ''}</div>
        <div class="stmt">${esc(clip(clean(it.statement), 300))}</div></div>`));
      c.querySelector('.card-head').onclick = () => showEntity(it.entity_id);
      out.append(c);
    }
  }
  out.append(el('div', 'scope', `Answered across ${esc(d.scope)}. Sections marked empty describe the limits of what Veris has been given.`));
};

/* ---------- findings ---------- */

async function findingsView() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Findings</h2>
    <p class="page-sub">Everything awaiting a human decision, most severe first.</p>
    <div class="spinner">Loading…</div>`;
  const list = await api('/findings');
  main.innerHTML = `<h2 class="page">Findings</h2>
    <p class="page-sub">Everything awaiting a human decision, most severe first.</p>`;
  if (!list.length) { main.append(el('div', 'empty', 'No findings recorded.')); return; }
  for (const f of list) {
    const c = el('div', 'card');
    c.append(el('div', 'card-head', `
      <span class="badge ${sevClass(f.severity)}">${sevLabel(f.severity)}</span>
      <span class="badge b-neutral">${typeLabel(f.finding_type)}</span>
      <span class="badge ${f.status === 'PROPOSED' ? 'b-accent' : 'b-low'}">${esc(f.status)}</span>
      <div style="flex:1"><div class="title">${esc(f.title)}</div>
        <div class="stmt">${esc(clip(f.statement, 250))}</div>
        <div class="meta">${provLabel(f.provenance_class)} · review: ${esc(f.recommended_reviewer || '—')}</div></div>`));
    c.querySelector('.card-head').onclick = () => openFinding(f.id);
    main.append(c);
  }
}

/* ---------- documents ---------- */

async function documentsView() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Your knowledge</h2>
    <p class="page-sub">Veris holds no knowledge of its own. Everything below already
    belonged to the organization — Veris connects it and reports what the connections
    mean.</p><div class="spinner">Loading…</div>`;

  const cov = await api('/coverage');
  const owners = cov.owned_by.map(o => o.publisher).filter(Boolean);
  main.innerHTML = `<h2 class="page">Your knowledge</h2>
    <p class="page-sub">Veris holds no knowledge of its own. Everything below already
    belonged to the organization — Veris connects it and reports what the connections
    mean.</p>`;

  main.append(el('div', 'tiles', `
    <div class="tile"><b>${cov.documents}</b><span>documents supplied</span></div>
    <div class="tile"><b>${cov.relationships}</b><span>connections Veris made</span></div>
    <div class="tile"><b>${cov.roles_present}/${cov.roles_total}</b><span>knowledge roles filled</span></div>`));

  if (owners.length) main.append(el('div', 'prov', `Sourced from ${owners.map(esc).join(' · ')}`));

  // The lifecycle strip. An unfilled role is not a defect in Veris — it is a
  // statement about what Veris has been given, and about what it therefore
  // cannot yet say.
  main.append(el('h4', 'sec', 'Across the knowledge lifecycle'));
  for (const r of cov.lifecycle) {
    const c = el('div', 'card');
    if (r.present) {
      c.append(el('div', 'card-head', `
        <span class="badge b-low">${r.entities} connected</span>
        <div style="flex:1">
          <div class="title">${esc(r.label)}</div>
          <div class="meta">${esc(r.examples)}</div>
          <div class="meta">${r.documents} document${r.documents === 1 ? '' : 's'} ·
            ${r.connected_entities} of ${r.entities} items linked to something else</div>
        </div>`));
    } else {
      c.append(el('div', 'card-head', `
        <span class="badge b-neutral">not connected</span>
        <div style="flex:1">
          <div class="title" style="color:var(--mut)">${esc(r.label)}</div>
          <div class="meta">${esc(r.examples)}</div>
          <div class="stmt" style="color:var(--med)">${esc(r.absence_note)}</div>
        </div>`));
    }
    main.append(c);
  }

  main.append(el('h4', 'sec', 'Add knowledge you already have'));
  main.append(el('div', 'card', `<div class="card-body" style="border:0;padding:1.1rem">
      <div class="field"><label>PDF, DOCX, Markdown or TXT — max 25 MB</label>
      <input type="file" id="upl" accept=".pdf,.docx,.md,.markdown,.txt"></div>
      <button class="btn sm primary" onclick="upload()">Connect</button>
      <span id="upl-msg" class="meta"></span>
      <div class="scope" style="margin-top:.7rem">Veris stores the original, freezes the
      extracted text and hashes it. Nothing is rewritten, and every citation points back
      into your document.</div></div>`));

  main.append(el('h4', 'sec', `Documents connected (${cov.documents})`));
  main.append(el('div', '', '<div id="doclist"><div class="spinner">Loading…</div></div>'));
  await renderDocs();
}

async function renderDocs() {
  const docs = await api('/documents');
  const box = $('#doclist');
  box.innerHTML = '';
  for (const d of docs) {
    const c = el('div', 'card');
    c.append(el('div', 'card-head', `
      <span class="badge ${d.source_type === 'ACCREDITATION_STANDARD' ? 'b-accent' : 'b-neutral'}">${esc(d.document_type)}</span>
      <div style="flex:1"><div class="title">${esc(d.title)}${d.version ? ' <span class="meta">v' + esc(d.version) + '</span>' : ''}</div>
        <div class="meta">${esc(d.publisher || '—')}${d.authority ? ' · ' + esc(d.authority) : ''}
          ${d.department ? ' · ' + esc(d.department) : ''}${d.owner ? ' · owner ' + esc(d.owner) : ''}
          ${d.effective_date ? ' · effective ' + esc(d.effective_date) : ''}
          · ${d.entity_count} knowledge item${d.entity_count === 1 ? '' : 's'}</div></div>`));
    c.querySelector('.card-head').onclick = async () => {
      const full = await api(`/documents/${d.id}`);
      openDrawer(`<button class="btn sm" data-close-drawer style="float:right">Close</button>
        <h3>${esc(full.title)}</h3>
        <div class="meta">${esc(full.publisher || '')} · ${esc(full.source_type)} · v${esc(full.version || '—')}
          ${full.jurisdiction ? ' · ' + esc(full.jurisdiction) : ''}
          ${full.retrieval_date ? ' · retrieved ' + esc(full.retrieval_date) : ''}</div>
        <div class="meta" style="margin-top:.3rem">content hash ${esc(full.text_sha256).slice(0, 16)}… · ${full.char_count} characters</div>
        <h4 class="sec">Knowledge items (${full.entities.length})</h4>
        ${full.entities.map(e => `<div class="list-item" onclick="showEntity('${e.id}')">
          <b>${esc(e.locator).slice(0, 76)}</b><div class="d">${esc(e.role)}</div></div>`).join('')}`);
    };
    box.append(c);
  }
}

window.upload = async () => {
  const input = $('#upl'), msg = $('#upl-msg');
  if (!input.files?.length) { msg.textContent = 'Choose a file first.'; return; }
  msg.textContent = 'Ingesting…';
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const res = await fetch(API + '/documents', { method: 'POST', body: fd });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || res.statusText);
    msg.textContent = d.already_present
      ? `Already connected: ${d.title}`
      : `Connected ${d.title} — ${d.entities} knowledge items extracted.`;
    await documentsView();
  } catch (e) { msg.textContent = 'Failed: ' + e.message; }
};


/* ---------- connection center ---------- */
/* Rendered entirely from connector registry metadata. Adding an integration
   adds no code here — which is the difference between a plugin architecture
   and a switch statement. */

const catLabel = { LMS: 'Learning', POLICY: 'Policies', REGULATORY: 'Standards',
                   IDENTITY: 'Identity', DOCUMENT: 'Files and exports',
                   EVIDENCE: 'Evidence' };
const stateClass = s => ({
  SYNCED: 'b-low', CONNECTED: 'b-low', SYNCING: 'b-accent',
  WARNING: 'b-med', ERROR: 'b-high', AUTHENTICATION_REQUIRED: 'b-med',
  DISCONNECTED: 'b-neutral' }[s] || 'b-neutral');

async function connectionsView() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Connections</h2>
    <p class="page-sub">Connect the systems you already use. Veris reads from them —
    it never writes back.</p><div class="spinner">Loading…</div>`;

  const [cat, conns] = await Promise.all([api('/connectors'), api('/connections')]);
  const byId = Object.fromEntries(conns.map(c => [c.connector_id, c]));
  main.innerHTML = `<h2 class="page">Connections</h2>
    <p class="page-sub">Connect the systems you already use. Veris reads from them —
    it never writes back.</p>`;

  const cs = cat.credential_store || {};
  main.append(el('div', 'card', `<div class="card-body" style="border:0;padding:.9rem 1.1rem">
    <span class="badge ${cs.writable ? 'b-low' : 'b-med'}">${esc(cs.name || 'unknown')}</span>
    <span class="meta" style="margin-left:.5rem">${esc(cs.detail || '')}</span></div>`));

  for (const [category, items] of Object.entries(cat.categories)) {
    main.append(el('h4', 'sec', catLabel[category] || category));
    for (const info of items) {
      const conn = byId[info.id];
      const card = el('div', 'card');
      const planned = info.availability !== 'available';
      const badge = conn
        ? `<span class="badge ${stateClass(conn.status)}">${esc(conn.status.replace(/_/g, ' '))}</span>`
        : planned ? '<span class="badge b-neutral">not yet available</span>'
                  : '<span class="badge b-accent">available</span>';
      card.append(el('div', 'card-head', `
        ${badge}
        <div style="flex:1">
          <div class="title">${esc(info.name)}
            ${info.is_mock ? '<span class="badge b-neutral" style="margin-left:.4rem">demo data</span>' : ''}</div>
          <div class="meta">${esc(info.vendor || '')}${conn && conn.last_sync_at
            ? ' · last sync ' + esc(conn.last_sync_at.replace('T', ' ').slice(0, 16)) : ''}</div>
          ${planned && info.setup_note
            ? `<div class="stmt" style="color:var(--med)">${esc(info.setup_note)}</div>` : ''}
        </div>`));
      card.querySelector('.card-head').onclick = () =>
        conn ? openConnection(conn.id) : openWizard(info);
      main.append(card);
    }
  }

  main.append(el('h4', 'sec', "Don't see your system?"));
  main.append(el('div', 'card', `<div class="card-body" style="border:0;padding:1.1rem">
    <div class="meta">Most systems can produce an export even when their API is
    closed. Veris maps the columns for you and asks about anything it is unsure of.</div>
    <div class="rowbtns" style="margin-top:.6rem">
      <button class="btn sm" onclick="openImport()">Upload a CSV export</button>
      <button class="btn sm" data-goto="documents">Upload documents</button>
    </div></div>`));
}

function openWizard(info) {
  if (info.availability !== 'available') {
    openDrawer(`<button class="btn sm" data-close-drawer style="float:right">Close</button>
      <h3>${esc(info.name)}</h3>
      <div class="meta">${esc(info.vendor || '')}</div>
      <p>${esc(info.setup_note)}</p>
      <h4 class="sec">When available, Veris will read</h4>
      ${(info.reads || []).map(r => `<div class="list-item">${esc(r)}</div>`).join('')}
      <p class="scope">Veris will never modify this system.</p>
      <h4 class="sec">Available today</h4>
      <div class="meta">You can import an export from this system as a CSV, or upload
      its documents directly, and connect the live system later.</div>`);
    return;
  }
  openDrawer(`<button class="btn sm" data-close-drawer style="float:right">Close</button>
    <h3>Connect ${esc(info.name)}</h3>
    <div class="meta">${esc(info.vendor || '')}</div>
    <h4 class="sec">Veris will read</h4>
    ${(info.reads || []).map(r => `<div class="list-item">✓ ${esc(r)}</div>`).join('')}
    <p class="scope">Veris will not modify ${esc(info.name)}. Connectors have no
    write capability.</p>
    ${info.auth_methods.includes('none') ? ''
      : `<div class="field"><label>API key</label>
         <input id="wz-key" type="password" autocomplete="off"
                placeholder="Stored in your operating system's keychain"></div>`}
    <div class="rowbtns">
      <button class="btn sm primary" onclick="runConnect('${info.id}')">Connect ${esc(info.name)}</button>
    </div>
    <div id="wz-out" style="margin-top:1rem"></div>`);
}

window.runConnect = async (connectorId) => {
  const out = $('#wz-out');
  out.innerHTML = '<div class="spinner">Checking your connection…</div>';
  const key = $('#wz-key')?.value;
  try {
    const res = await api('/connections', { method: 'POST', body: JSON.stringify({
      connector_id: connectorId, credentials: key ? { api_key: key } : {} }) });
    if (res.state !== 'CONNECTED') {
      out.innerHTML = `<div class="empty">${esc(res.message || res.state)}</div>`;
      return;
    }
    const d = res.discovery || { notes: [] };
    out.innerHTML = `<h4 class="sec">Connection successful</h4>
      <div class="card"><div class="card-body" style="border:0;padding:1rem">
        ${(d.notes || []).map(n => `<div class="title">${esc(n)}</div>`).join('')}
      </div></div>
      ${(res.warnings || []).map(w => `<div class="stmt" style="color:var(--med)">${esc(w)}</div>`).join('')}
      <div class="rowbtns"><button class="btn sm primary"
        onclick="startSync('${res.connection_id}')">Start sync</button></div>
      <div id="wz-sync"></div>`;
  } catch (e) { out.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
};

window.startSync = async (connectionId) => {
  const out = $('#wz-sync');
  out.innerHTML = '<div class="spinner">Syncing…</div>';
  try {
    const rep = await api(`/connections/${connectionId}/sync?kind=FULL`,
                          { method: 'POST' });
    out.innerHTML = `<h4 class="sec">You're connected</h4>
      <div class="meta">${rep.synced} records synchronized${rep.failed ? `, ${rep.failed} skipped` : ''}.
      Veris will keep monitoring this connection.</div>
      <div class="rowbtns"><button class="btn sm" data-close-drawer
        onclick="setTimeout(()=>go('connections'),50)">View connections</button></div>`;
  } catch (e) { out.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
};

window.openImport = () => {
  openDrawer(`<button class="btn sm" data-close-drawer style="float:right">Close</button>
    <h3>Import an export</h3>
    <p class="meta">Paste or upload a CSV from any system. Veris maps the columns
    onto its own schema and tells you about anything it could not place.</p>
    <div class="field"><label>What does this export contain?</label>
      <select id="im-type">
        <option value="course">Courses or training</option>
        <option value="person">Staff roster</option>
        <option value="completion">Completions or assignments</option>
        <option value="policy_record">Policies</option>
      </select></div>
    <div class="field"><label>CSV content</label>
      <textarea id="im-csv" rows="7" placeholder="id,title,department&#10;..."></textarea></div>
    <div class="rowbtns"><button class="btn sm primary" onclick="runImport()">Import</button></div>
    <div id="im-out" style="margin-top:1rem"></div>`);
};

window.runImport = async () => {
  const out = $('#im-out');
  const content = $('#im-csv').value.trim();
  if (!content) { out.innerHTML = '<div class="empty">Paste some CSV first.</div>'; return; }
  out.innerHTML = '<div class="spinner">Reading your export…</div>';
  try {
    const res = await api('/connections', { method: 'POST', body: JSON.stringify({
      connector_id: 'file_import', name: 'Imported export',
      config: { content, record_type: $('#im-type').value } }) });
    const d = res.discovery || { notes: [] };
    out.innerHTML = `${(d.notes || []).map(n => `<div class="title">${esc(n)}</div>`).join('')}
      <div class="rowbtns"><button class="btn sm primary"
        onclick="startSync('${res.connection_id}')">Import these records</button></div>
      <div id="wz-sync"></div>`;
  } catch (e) { out.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
};

async function openConnection(connectionId) {
  openDrawer('<div class="spinner">Loading…</div>');
  const c = await api(`/connections/${connectionId}`);
  const runs = c.runs.map(r => `<div class="ev">
      <b>${esc(r.kind)}</b> · ${esc(r.status)} · ${r.synced} synced${r.failed ? `, ${r.failed} failed` : ''}
      <div class="when">${esc((r.started_at || '').replace('T', ' ').slice(0, 16))}</div>
      ${r.error ? `<div style="color:var(--high)">${esc(r.error)}</div>` : ''}</div>`).join('');
  openDrawer(`<button class="btn sm" data-close-drawer style="float:right">Close</button>
    <span class="badge ${stateClass(c.status)}">${esc(c.status.replace(/_/g, ' '))}</span>
    ${c.is_mock ? '<span class="badge b-neutral">demo data</span>' : ''}
    <h3>${esc(c.name)}</h3>
    <div class="meta">${esc(c.category)} · last sync
      ${esc((c.last_sync_at || '—').replace('T', ' ').slice(0, 16))} · next
      ${esc((c.next_sync_at || '—').replace('T', ' ').slice(0, 16))}</div>
    ${c.last_error ? `<div class="stmt" style="color:var(--high)">${esc(c.last_error)}</div>` : ''}
    <h4 class="sec">Records synchronized</h4>
    <div class="tiles">
      <div class="tile"><b>${c.records.courses}</b><span>courses</span></div>
      <div class="tile"><b>${c.records.people}</b><span>people</span></div>
      <div class="tile"><b>${c.records.completions}</b><span>completions</span></div>
    </div>
    <h4 class="sec">Veris reads</h4>
    ${(c.reads || []).map(r => `<div class="list-item">✓ ${esc(r)}</div>`).join('')}
    <p class="scope">Veris does not write to this system.</p>
    <div class="rowbtns">
      <button class="btn sm primary" onclick="startSync('${c.id}')">Sync now</button>
      <button class="btn sm" onclick="dropConnection('${c.id}')">Disconnect</button>
    </div>
    <div id="wz-sync"></div>
    <h4 class="sec">Sync history</h4><div class="timeline">${runs || '<div class="empty">No runs yet.</div>'}</div>`);
}

window.dropConnection = async (id) => {
  try { await api(`/connections/${id}`, { method: 'DELETE' }); closeDrawer(); go('connections'); }
  catch (e) { alert(e.message); }
};

/* ---------- agents ---------- */

async function agentsView() {
  const main = $('#main');
  main.innerHTML = `<h2 class="page">Agents</h2>
    <p class="page-sub">Modular reasoning over everything connected. An agent that
    needs a system you have not connected says so rather than guessing.</p>
    <div class="spinner">Loading…</div>`;
  const agents = await api('/agents');
  main.innerHTML = `<h2 class="page">Agents</h2>
    <p class="page-sub">Modular reasoning over everything connected. An agent that
    needs a system you have not connected says so rather than guessing.</p>`;
  for (const a of agents) {
    const card = el('div', 'card');
    card.append(el('div', 'card-body', `<div style="padding:.2rem 0">
      <span class="badge ${a.runnable ? 'b-low' : 'b-neutral'}">${a.runnable ? 'ready' : 'needs a connection'}</span>
      <div class="title" style="margin-top:.4rem">${esc(a.name)}</div>
      <div class="stmt">${esc(a.description)}</div>
      ${a.blocked_by.length ? `<div class="meta" style="color:var(--med)">Needs: ${a.blocked_by.map(esc).join(', ')}</div>` : ''}
      <div class="rowbtns"><button class="btn sm" ${a.runnable ? '' : 'disabled'}
        onclick="runAgent('${a.id}')">Run</button></div>
      <div id="ag-${a.id}"></div></div>`));
    card.querySelector('.card-body').style.borderTop = '0';
    main.append(card);
  }
}

window.runAgent = async (id) => {
  const out = document.getElementById('ag-' + id);
  out.innerHTML = '<div class="spinner">Running…</div>';
  try {
    const r = await api(`/agents/${id}/run`, { method: 'POST' });
    out.innerHTML = `<div class="meta">Examined ${r.examined} · created
      ${r.findings_created} finding${r.findings_created === 1 ? '' : 's'}
      ${r.skipped_reason ? '· ' + esc(r.skipped_reason) : ''}</div>
      ${(r.notes || []).map(n => `<div class="stmt">${esc(n)}</div>`).join('')}`;
  } catch (e) { out.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
};

/* ---------- boot ---------- */

(async function boot() {
  try {
    const h = await api('/health');
    $('#health').innerHTML = `<b>●</b> ${esc(h.status)}<br>model: ${esc(h.model)}<br>auth: ${esc(h.auth)}`;
    const c = h.counts;
    // Labelled to make the division of labour explicit: the organization
    // supplied the first two, Veris produced the last two.
    $('#landing-stats').innerHTML = `
      <div><b>${c.documents}</b><span>documents you supplied</span></div>
      <div><b>${c.entities}</b><span>knowledge items in them</span></div>
      <div><b>${c.relationships}</b><span>connections Veris made</span></div>
      <div><b>${c.findings}</b><span>findings in the connections</span></div>`;
  } catch (e) {
    $('#health').textContent = 'API unavailable';
  }
})();

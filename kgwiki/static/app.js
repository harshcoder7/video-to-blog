/* Video Knowledge Graph -- frontend */

const TYPE_COLOR = { video: '#2dd4bf', section: '#8b9cff', entity: '#f0b429' };

const state = {
  nodesById: new Map(),
  highlightNodes: new Set(),
  highlightLinks: new Set(),
  hoverNode: null,
  Graph: null,
  searchMatchNodes: new Set(),
  searchActive: false,
};

function isNodeDimmed(node) {
  if (state.highlightNodes.size > 0 && !state.highlightNodes.has(node)) return true;
  if (state.searchActive && !state.searchMatchNodes.has(node)) return true;
  return false;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

/* ---------------- View switching ---------------- */

document.querySelectorAll('.nav-icon[data-view]').forEach((btn) => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

function switchView(name) {
  document.querySelectorAll('.nav-icon[data-view]').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === name);
  });
  document.querySelectorAll('.view').forEach((v) => {
    v.classList.toggle('active', v.id === `view-${name}`);
  });
  if (name === 'graph' && state.Graph) {
    requestAnimationFrame(() => resizeGraph());
  }
}

/* ---------------- Data loading ---------------- */

async function loadGraph() {
  const res = await fetch('/api/graph');
  const data = await res.json();

  state.nodesById.clear();
  data.nodes.forEach((n) => {
    n.neighbors = [];
    n.links = [];
    state.nodesById.set(n.id, n);
  });
  data.edges.forEach((e) => {
    const a = state.nodesById.get(e.source);
    const b = state.nodesById.get(e.target);
    if (!a || !b) return;
    a.neighbors.push(b);
    b.neighbors.push(a);
    a.links.push(e);
    b.links.push(e);
  });

  renderGraph(data);
  renderLibrary(data);
}

document.getElementById('refresh-btn').addEventListener('click', async () => {
  const btn = document.getElementById('refresh-btn');
  btn.style.transform = 'rotate(360deg)';
  btn.style.transition = 'transform 0.5s ease';
  await fetch('/api/refresh', { method: 'POST' });
  await loadGraph();
  setTimeout(() => { btn.style.transition = 'none'; btn.style.transform = 'none'; }, 550);
});

/* ---------------- Graph view ---------------- */

function nodeRadius(node) {
  if (node.type === 'video') return 11;
  if (node.type === 'entity') return 4 + Math.min(node.mentions || 1, 10) * 1.1;
  return 6;
}

function resizeGraph() {
  const el = document.getElementById('graph-container');
  if (state.Graph && el) {
    state.Graph.width(el.clientWidth).height(el.clientHeight);
  }
}

function refreshHighlight() {
  const g = state.Graph;
  if (!g) return;
  if (typeof g.refresh === 'function') g.refresh();
}

function renderGraph(data) {
  const el = document.getElementById('graph-container');

  if (!state.Graph) {
    state.Graph = ForceGraph()(el)
      .backgroundColor('#16161a')
      .nodeId('id')
      .nodeVal(nodeRadius)
      .linkDirectionalParticleWidth(2)
      .linkDirectionalParticleColor(() => '#2dd4bf')
      .linkDirectionalParticleSpeed(0.006)
      .linkColor((link) => (state.highlightLinks.has(link) ? 'rgba(45,212,191,0.85)' : 'rgba(255,255,255,0.08)'))
      .linkWidth((link) => (state.highlightLinks.has(link) ? 2.2 : 0.6))
      .linkDirectionalParticles((link) => (state.highlightLinks.has(link) ? 3 : 0))
      .nodeCanvasObject((node, ctx, globalScale) => {
        const r = nodeRadius(node);
        const dim = isNodeDimmed(node);
        ctx.globalAlpha = dim ? 0.15 : 1;

        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
        ctx.fillStyle = TYPE_COLOR[node.type] || '#999';
        ctx.fill();

        if (node === state.hoverNode) {
          ctx.lineWidth = 2 / globalScale;
          ctx.strokeStyle = '#ffffff';
          ctx.stroke();
        }

        const shouldLabel = node.type === 'video' || globalScale > 1.4 || node === state.hoverNode;
        if (shouldLabel && !dim) {
          const label = node.label.length > 30 ? node.label.slice(0, 28) + '…' : node.label;
          const fontSize = node.type === 'video' ? 5.6 : 4.4;
          ctx.font = `${node.type === 'video' ? 700 : 500} ${fontSize}px -apple-system, sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillStyle = 'rgba(220,220,228,0.95)';
          ctx.fillText(label, node.x, node.y + r + 1.5);
        }
        ctx.globalAlpha = 1;
      })
      .nodePointerAreaPaint((node, color, ctx) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, nodeRadius(node) + 3, 0, 2 * Math.PI, false);
        ctx.fill();
      })
      .onNodeHover((node) => {
        state.highlightNodes.clear();
        state.highlightLinks.clear();
        if (node) {
          state.highlightNodes.add(node);
          node.neighbors.forEach((n) => state.highlightNodes.add(n));
          node.links.forEach((l) => state.highlightLinks.add(l));
        }
        state.hoverNode = node || null;
        el.style.cursor = node ? 'pointer' : 'grab';
        refreshHighlight();
      })
      .onNodeClick((node) => {
        openDetail(node);
        state.Graph.centerAt(node.x, node.y, 500);
      })
      .onBackgroundClick(() => closeDetail())
      .cooldownTicks(300)
      .d3AlphaDecay(0.02)
      .d3VelocityDecay(0.28)
      .onEngineStop(() => state.Graph.zoomToFit(500, 70));

    state.Graph.d3Force('charge').strength(-110);
    state.Graph.d3Force('link').distance((l) => (l.type === 'mentions' ? 55 : 42));

    window.addEventListener('resize', resizeGraph);
    resizeGraph();
  }

  state.Graph.graphData({ nodes: data.nodes, links: data.edges });
  resizeGraph();
}

const graphSearchInput = document.getElementById('graph-search');
graphSearchInput.addEventListener('input', () => {
  const q = graphSearchInput.value.trim().toLowerCase();
  state.searchMatchNodes.clear();
  if (!q) {
    state.searchActive = false;
  } else {
    state.searchActive = true;
    state.nodesById.forEach((node) => {
      if (node.label.toLowerCase().includes(q)) state.searchMatchNodes.add(node);
    });
  }
  refreshHighlight();
});

/* ---------------- Detail panel ---------------- */

function typeLabel(t) {
  return { video: 'Video', section: 'Section', entity: 'Topic' }[t] || t;
}

function locateButton(node) {
  return `<button class="detail-link locate-btn" data-id="${node.id}" style="background:none;border:1px solid var(--border);border-radius:7px;padding:5px 10px;cursor:pointer;">
    Locate in graph
  </button>`;
}

function renderDetailHTML(node) {
  if (node.type === 'video') {
    const sections = node.neighbors.filter((n) => n.type === 'section').sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
    return `
      <span class="detail-type-badge video">Video</span>
      ${node.thumbnail ? `<img class="detail-img" src="${node.thumbnail}" />` : ''}
      <h2 class="detail-title">${escapeHtml(node.label)}</h2>
      <div class="detail-meta-row">
        <span>${escapeHtml(node.channel || '')}</span>
        <span>·</span>
        <span>${node.section_count} sections</span>
        ${node.url ? `<a class="detail-link" href="${node.url}" target="_blank" rel="noopener">Watch on YouTube ↗</a>` : ''}
      </div>
      ${node.subtitle ? `<p class="detail-text">${escapeHtml(node.subtitle)}</p>` : ''}
      <div class="detail-section-title">Sections</div>
      <div class="mini-link-list">
        ${sections.map((s) => `<div class="mini-link" data-id="${s.id}">${escapeHtml(s.label)} <span style="color:var(--text-dim);float:right;">${s.start}–${s.end}</span></div>`).join('')}
      </div>
    `;
  }

  if (node.type === 'section') {
    const entities = node.neighbors.filter((n) => n.type === 'entity');
    return `
      <span class="detail-type-badge section">Section</span>
      ${node.screenshot ? `<img class="detail-img" src="${node.screenshot}" />` : ''}
      <h2 class="detail-title">${escapeHtml(node.label)}</h2>
      <div class="detail-meta-row">
        <span>${escapeHtml(node.video_title || '')}</span>
        <span class="badge">${node.start} – ${node.end}</span>
        ${node.timestamp_url ? `<a class="detail-link" href="${node.timestamp_url}" target="_blank" rel="noopener">Watch this moment ↗</a>` : ''}
      </div>
      <div class="detail-text">${escapeHtml(node.text || '')}</div>
      ${entities.length ? `
        <div class="detail-section-title">Topics mentioned</div>
        <div class="mini-link-list">
          ${entities.map((e) => `<div class="mini-link" data-id="${e.id}">${escapeHtml(e.label)}</div>`).join('')}
        </div>` : ''}
    `;
  }

  // entity
  const sections = node.neighbors.filter((n) => n.type === 'section');
  return `
    <span class="detail-type-badge entity">Topic</span>
    <h2 class="detail-title">${escapeHtml(node.label)}</h2>
    <div class="detail-meta-row"><span>${node.mentions} mention${node.mentions === 1 ? '' : 's'} across ${sections.length} section${sections.length === 1 ? '' : 's'}</span></div>
    <div class="detail-section-title">Appears in</div>
    <div class="mini-link-list">
      ${sections.map((s) => `<div class="mini-link" data-id="${s.id}"><b>${escapeHtml(s.video_title || '')}</b><br/>${escapeHtml(s.label)} <span style="color:var(--text-dim);">(${s.start}–${s.end})</span></div>`).join('')}
    </div>
  `;
}

function openDetail(node) {
  const panel = document.getElementById('detail-panel');
  const body = document.getElementById('detail-body');
  body.innerHTML = renderDetailHTML(node);
  panel.classList.add('open');
  document.querySelector('.main').classList.add('panel-open');
  if (state.Graph) setTimeout(resizeGraph, 300);

  body.querySelectorAll('.mini-link, .section-row-link').forEach((el) => {
    el.addEventListener('click', () => {
      const target = state.nodesById.get(el.dataset.id);
      if (target) openDetail(target);
    });
  });
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
  document.querySelector('.main').classList.remove('panel-open');
  if (state.Graph) setTimeout(resizeGraph, 300);
}
document.getElementById('detail-close').addEventListener('click', closeDetail);

function locateInGraph(node) {
  switchView('graph');
  requestAnimationFrame(() => {
    resizeGraph();
    if (state.Graph && node.x !== undefined) {
      state.Graph.centerAt(node.x, node.y, 600);
      state.Graph.zoom(2.2, 600);
    }
  });
  openDetail(node);
}

/* ---------------- Query view: multi-turn chat ---------------- */

const queryForm = document.getElementById('query-form');
const queryInput = document.getElementById('query-input');
const chatThread = document.getElementById('chat-thread');
const clearChatBtn = document.getElementById('clear-chat-btn');

state.chatHistory = []; // [{role, content}, ...] sent to the API for conversational memory

const SOURCE_LABELS = {
  ollama: '🧠 Local Qwen model',
  anthropic: '☁️ Claude API',
  extractive: '📄 Quoted from transcript',
  chitchat: null,
  none: null,
};

function fetchWithTimeout(url, opts, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return fetch(url, { ...opts, signal: controller.signal }).finally(() => clearTimeout(timer));
}

queryForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = queryInput.value.trim();
  if (!q) return;
  queryInput.value = '';

  const turn = document.createElement('div');
  turn.className = 'chat-turn';
  turn.innerHTML = `
    <div class="chat-user-msg">${escapeHtml(q)}</div>
    <div class="chat-loading"><span class="ingest-spinner"></span> Thinking...</div>
  `;
  chatThread.appendChild(turn);
  clearChatBtn.hidden = false;
  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });

  let data;
  try {
    const res = await fetchWithTimeout(
      '/api/query',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q, history: state.chatHistory }),
      },
      75000,
    );
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    data = await res.json();
  } catch (err) {
    const isAbort = err && err.name === 'AbortError';
    turn.querySelector('.chat-loading').outerHTML = `<div class="empty-state">${
      isAbort
        ? 'That took too long and timed out. The local model might be under heavy load -- try again.'
        : "Couldn't reach the server for an answer. Is kgwiki still running?"
    }</div>`;
    return;
  }

  const loadingEl = turn.querySelector('.chat-loading');

  if (data.answer) {
    const sourceLabel = SOURCE_LABELS[data.answer_source];
    const answerEl = document.createElement('div');
    answerEl.className = 'query-answer';
    answerEl.innerHTML = `
      <span class="answer-label">Answer${sourceLabel ? ` <span class="answer-source">· ${sourceLabel}</span>` : ''}</span>
      <div class="answer-text">${escapeHtml(data.answer)}</div>
      <button class="copy-answer-btn" title="Copy answer">Copy</button>
    `;
    answerEl.querySelector('.copy-answer-btn').addEventListener('click', (ev) => {
      navigator.clipboard.writeText(data.answer).then(() => {
        ev.target.textContent = 'Copied!';
        setTimeout(() => { ev.target.textContent = 'Copy'; }, 1500);
      });
    });
    loadingEl.replaceWith(answerEl);

    // Only real (non-chitchat, non-empty) exchanges build conversational memory.
    if (data.answer_source && data.answer_source !== 'none') {
      state.chatHistory.push({ role: 'user', content: q });
      state.chatHistory.push({ role: 'assistant', content: data.answer });
    }
  } else {
    loadingEl.remove();
  }

  if (data.matches && data.matches.length) {
    const resultsEl = document.createElement('div');
    resultsEl.className = 'query-results';
    resultsEl.innerHTML = data.matches.map(renderResultCard).join('');
    resultsEl.querySelectorAll('.result-card').forEach((card) => {
      card.addEventListener('click', () => {
        const node = state.nodesById.get(card.dataset.id);
        if (node) locateInGraph(node);
      });
    });
    turn.appendChild(resultsEl);
  }

  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });
});

clearChatBtn.addEventListener('click', () => {
  chatThread.innerHTML = '';
  state.chatHistory = [];
  clearChatBtn.hidden = true;
});

// Ctrl/Cmd+K focuses the Ask input from anywhere in the app.
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    switchView('query');
    queryInput.focus();
  }
  if (e.key === 'Escape') closeDetail();
});

async function loadLlmStatus() {
  const el = document.getElementById('llm-status');
  try {
    const res = await fetch('/api/llm_status');
    const s = await res.json();
    if (s.ollama_available && s.embeddings_ready) {
      el.className = 'llm-status ok';
      el.innerHTML = `<span class="status-dot"></span> Local ${escapeHtml(s.chat_model)} is running -- real generative answers, fully offline`;
    } else if (s.ollama_available) {
      el.className = 'llm-status warn';
      el.innerHTML = '<span class="status-dot"></span> Ollama is running but embeddings aren\'t ready yet -- hit refresh once model pulls finish';
    } else if (s.anthropic_key_set) {
      el.className = 'llm-status ok';
      el.innerHTML = '<span class="status-dot"></span> Using Claude API for answers';
    } else {
      el.className = 'llm-status warn';
      el.innerHTML = '<span class="status-dot"></span> No local model or API key detected -- answers will be quoted directly from the transcript';
    }
  } catch {
    el.hidden = true;
  }
}
loadLlmStatus();

function renderResultCard(m) {
  const thumb = m.screenshot
    ? `<img class="result-thumb" src="${m.screenshot}" />`
    : '<div class="result-thumb"></div>';
  return `
    <div class="result-card" data-id="${m.id}">
      ${thumb}
      <div class="result-body">
        <div class="result-meta">
          <span class="result-video">${escapeHtml(m.video_title || '')}</span>
          <span class="badge">${m.start} – ${m.end}</span>
        </div>
        <div class="result-heading">${escapeHtml(m.heading)}</div>
        <div class="result-excerpt">${escapeHtml(m.excerpt || '')}</div>
      </div>
    </div>
  `;
}

/* ---------------- Library view ---------------- */

function renderLibrary(data) {
  const videos = data.nodes.filter((n) => n.type === 'video');
  const container = document.getElementById('library-list');

  if (!videos.length) {
    container.innerHTML = '<div class="empty-state">No videos ingested yet. Run vidblog on a YouTube URL, then hit refresh.</div>';
    return;
  }

  container.innerHTML = videos.map((v) => {
    const videoId = v.id.split(':').slice(1).join(':');
    const sections = data.nodes
      .filter((n) => n.type === 'section' && n.video_id === videoId)
      .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
    return `
      <div class="video-card" data-id="${v.id}">
        <div class="video-card-head">
          ${v.thumbnail ? `<img class="video-thumb" src="${v.thumbnail}"/>` : '<div class="video-thumb"></div>'}
          <div>
            <div class="video-title">${escapeHtml(v.label)}</div>
            <div class="video-meta">${escapeHtml(v.channel || '')} · ${v.section_count} sections</div>
          </div>
        </div>
        <div class="section-list">
          ${sections.map((s) => `<div class="section-row" data-id="${s.id}"><span>${escapeHtml(s.label)}</span><span class="ts">${s.start}–${s.end}</span></div>`).join('')}
        </div>
      </div>
    `;
  }).join('');

  container.querySelectorAll('.video-card').forEach((card) => {
    card.querySelector('.video-card-head').addEventListener('click', () => card.classList.toggle('expanded'));
    card.querySelectorAll('.section-row').forEach((row) => {
      row.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const node = state.nodesById.get(row.dataset.id);
        if (node) openDetail(node);
      });
    });
  });
}

/* ---------------- Ingest a new video ---------------- */

const ingestForm = document.getElementById('ingest-form');
const ingestInput = document.getElementById('ingest-input');
const ingestButton = ingestForm.querySelector('button');
const ingestProgress = document.getElementById('ingest-progress');
const ingestProgressTitle = document.getElementById('ingest-progress-title');
const ingestLog = document.getElementById('ingest-log');
const ingestStagesEl = document.getElementById('ingest-stages');
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const uploadProgressWrap = document.getElementById('upload-progress-wrap');
const uploadProgressBar = document.getElementById('upload-progress-bar');
const uploadProgressPct = document.getElementById('upload-progress-pct');
let ingestPollTimer = null;

const STAGE_ICONS = [
  { label: 'Load', svg: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h9A2.5 2.5 0 0 1 18 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 4 17.5v-11Z"/><path d="m18 9.5 4-2.3v9.6l-4-2.3"/>' },
  { label: 'Transcribe', svg: '<path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"/><path d="M6 11a6 6 0 0 0 12 0"/><path d="M12 17v3"/>' },
  { label: 'Segment', svg: '<path d="M4 6h16"/><path d="M4 12h10"/><path d="M4 18h16"/>' },
  { label: 'Screenshots', svg: '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1-2h7l1 2h2A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-9Z"/><circle cx="12" cy="13" r="3.2"/>' },
  { label: 'Write', svg: '<path d="M4 19.5 4.8 16l10-10 3.2 3.2-10 10L4 19.5Z"/><path d="m13.5 6.9 3.2 3.2"/>' },
  { label: 'Render', svg: '<path d="m12 3 1.6 4.8L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.2L12 3Z"/><path d="M18.5 15.5 19.3 18l2.2.8-2.2.7-.8 2.2-.8-2.2-2.2-.7 2.2-.8.8-2.5Z"/>' },
];

function renderStagesShell() {
  ingestStagesEl.innerHTML = STAGE_ICONS.map((s, i) => `
    <div class="ingest-stage" data-stage="${i + 1}">
      <div class="ingest-stage-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${s.svg}</svg>
      </div>
      <span>${s.label}</span>
    </div>
  `).join('');
}
renderStagesShell();

function renderStages(stage) {
  const current = stage ? stage.current : 0;
  ingestStagesEl.querySelectorAll('.ingest-stage').forEach((el) => {
    const n = parseInt(el.dataset.stage, 10);
    el.classList.toggle('done', n < current);
    el.classList.toggle('active', n === current);
  });
}

function renderIngestLog(lines) {
  ingestLog.textContent = (lines || []).join('\n');
  ingestLog.scrollTop = ingestLog.scrollHeight;
}

function setUploadProgress(pct) {
  uploadProgressWrap.hidden = false;
  uploadProgressBar.style.width = `${pct}%`;
  uploadProgressPct.textContent = `${Math.round(pct)}%`;
}

async function pollIngestStatus() {
  let data;
  try {
    const res = await fetch('/api/ingest/status');
    data = await res.json();
  } catch {
    return; // transient network hiccup -- try again next tick
  }

  ingestProgress.hidden = false;
  renderIngestLog(data.log);
  renderStages(data.stage);

  if (data.status === 'uploading') {
    ingestProgress.className = 'ingest-progress';
    ingestProgressTitle.textContent = `Uploading ${data.url || 'video'}...`;
    ingestButton.disabled = true;
  } else if (data.status === 'running') {
    uploadProgressWrap.hidden = true;
    ingestProgress.className = 'ingest-progress';
    ingestProgressTitle.textContent = `Processing ${data.url || 'video'}...`;
    ingestButton.disabled = true;
  } else if (data.status === 'done') {
    uploadProgressWrap.hidden = true;
    renderStages({ current: 7 }); // mark all six done
    ingestProgress.className = 'ingest-progress done';
    ingestProgressTitle.textContent = 'Done! Added to your knowledge base.';
    ingestButton.disabled = false;
    stopIngestPolling();
    await loadGraph();
    await loadLlmStatus();
  } else if (data.status === 'error') {
    uploadProgressWrap.hidden = true;
    ingestProgress.className = 'ingest-progress error';
    ingestProgressTitle.textContent = `Failed: ${data.error || 'unknown error'}`;
    ingestButton.disabled = false;
    stopIngestPolling();
  }
}

function startIngestPolling() {
  if (ingestPollTimer) return;
  pollIngestStatus();
  ingestPollTimer = setInterval(pollIngestStatus, 2000);
}

function stopIngestPolling() {
  if (ingestPollTimer) {
    clearInterval(ingestPollTimer);
    ingestPollTimer = null;
  }
}

ingestForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = ingestInput.value.trim();
  if (!url) return;

  ingestButton.disabled = true;
  try {
    const res = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (res.status === 409) {
      const data = await res.json();
      alert(data.detail || 'A video is already being processed.');
      ingestButton.disabled = false;
      startIngestPolling(); // catch up on the job already in flight
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || 'Could not start ingestion -- check the URL and try again.');
      ingestButton.disabled = false;
      return;
    }
    ingestInput.value = '';
    startIngestPolling();
  } catch {
    alert('Could not reach the server. Is kgwiki still running?');
    ingestButton.disabled = false;
  }
});

/* ---- Upload dropzone ---- */

function uploadFile(file) {
  ingestProgress.hidden = false;
  ingestProgress.className = 'ingest-progress';
  ingestProgressTitle.textContent = `Uploading ${file.name}...`;
  renderStages(null);
  renderIngestLog([`Uploading ${file.name}...`]);
  setUploadProgress(0);

  const formData = new FormData();
  formData.append('file', file);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/ingest/upload');

  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) setUploadProgress((e.loaded / e.total) * 100);
  });

  xhr.addEventListener('load', () => {
    if (xhr.status === 200) {
      setUploadProgress(100);
      startIngestPolling();
    } else if (xhr.status === 409) {
      alert('A video is already being processed -- check back once it finishes.');
      startIngestPolling();
    } else {
      let detail = 'Upload failed.';
      try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* ignore */ }
      ingestProgress.className = 'ingest-progress error';
      ingestProgressTitle.textContent = `Failed: ${detail}`;
    }
  });

  xhr.addEventListener('error', () => {
    ingestProgress.className = 'ingest-progress error';
    ingestProgressTitle.textContent = 'Upload failed -- could not reach the server.';
  });

  xhr.send(formData);
}

dropzone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
  fileInput.value = '';
});

['dragenter', 'dragover'].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
  });
});
dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

// If a job was already running when this page loaded (e.g. the page was
// refreshed mid-ingest), resume showing its progress instead of losing it.
(async function resumeIngestIfRunning() {
  try {
    const res = await fetch('/api/ingest/status');
    const data = await res.json();
    if (data.status === 'running' || data.status === 'uploading') startIngestPolling();
  } catch {
    /* server not reachable yet at load time -- ignore */
  }
})();

/* ---------------- Folders view ---------------- */

const foldersHome = document.getElementById('folders-home');
const folderDetailEl = document.getElementById('folder-detail');
const foldersGrid = document.getElementById('folders-grid');
const newFolderForm = document.getElementById('new-folder-form');
const newFolderInput = document.getElementById('new-folder-input');
const folderBackBtn = document.getElementById('folder-back-btn');
const folderDetailTitle = document.getElementById('folder-detail-title');
const folderSourcesList = document.getElementById('folder-sources-list');
const folderVideoDropzone = document.getElementById('folder-video-dropzone');
const folderVideoInput = document.getElementById('folder-video-input');
const folderDocDropzone = document.getElementById('folder-doc-dropzone');
const folderDocInput = document.getElementById('folder-doc-input');
const folderUploadProgress = document.getElementById('folder-upload-progress');
const folderUploadTitle = document.getElementById('folder-upload-title');
const folderBriefBtn = document.getElementById('folder-brief-btn');
const folderBriefEl = document.getElementById('folder-brief-content');
const folderChatForm = document.getElementById('folder-chat-form');
const folderChatInput = document.getElementById('folder-chat-input');
const folderChatThread = document.getElementById('folder-chat-thread');

state.currentFolderId = null;
state.folderChatHistory = [];
let folderIngestPollTimer = null;

async function loadFoldersHome() {
  foldersHome.hidden = false;
  folderDetailEl.hidden = true;
  state.currentFolderId = null;
  let list = [];
  try {
    const res = await fetch('/api/folders');
    list = await res.json();
  } catch {
    foldersGrid.innerHTML = '<div class="empty-state">Could not reach the server.</div>';
    return;
  }
  if (!list.length) {
    foldersGrid.innerHTML = '<div class="empty-state">No folders yet -- create one above to start an audit project.</div>';
    return;
  }
  foldersGrid.innerHTML = list.map((f) => `
    <div class="folder-card" data-id="${f.id}">
      <div class="folder-card-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h4l1.8 2.2H18.5A1.5 1.5 0 0 1 20 8.7v8.8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-11Z"/>
        </svg>
      </div>
      <div class="folder-card-name">${escapeHtml(f.name)}</div>
      <button class="folder-delete-btn" data-id="${f.id}" title="Delete folder">&times;</button>
    </div>
  `).join('');
  foldersGrid.querySelectorAll('.folder-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.folder-delete-btn')) return;
      openFolderDetail(card.dataset.id);
    });
  });
  foldersGrid.querySelectorAll('.folder-delete-btn').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this folder? Its videos and documents stay in your library, just ungrouped.')) return;
      await fetch(`/api/folders/${btn.dataset.id}`, { method: 'DELETE' });
      loadFoldersHome();
    });
  });
}

newFolderForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = newFolderInput.value.trim();
  if (!name) return;
  await fetch('/api/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  newFolderInput.value = '';
  loadFoldersHome();
});

function kindBadge(kind) {
  return kind === 'document'
    ? '<span class="badge doc-badge">Document</span>'
    : '<span class="badge">Video</span>';
}

async function openFolderDetail(folderId) {
  state.currentFolderId = folderId;
  state.folderChatHistory = [];
  folderChatThread.innerHTML = '';
  folderBriefEl.innerHTML = '';
  folderUploadProgress.hidden = true;
  foldersHome.hidden = true;
  folderDetailEl.hidden = false;
  await refreshFolderDetail();
}

async function refreshFolderDetail() {
  const folderId = state.currentFolderId;
  if (!folderId) return;
  const res = await fetch(`/api/folders/${folderId}`);
  if (!res.ok) { loadFoldersHome(); return; }
  const folder = await res.json();
  folderDetailTitle.textContent = folder.name;
  if (!folder.sources.length) {
    folderSourcesList.innerHTML = '<div class="empty-state">No videos or documents yet -- drop one in below.</div>';
  } else {
    folderSourcesList.innerHTML = folder.sources.map((s) => `
      <div class="folder-source-row">
        ${kindBadge(s.kind)}
        <span class="folder-source-name">${escapeHtml(s.label)}</span>
        ${s.kind === 'document' ? '' : `<span class="folder-source-meta">${s.section_count || 0} sections</span>`}
      </div>
    `).join('');
  }
}

folderBackBtn.addEventListener('click', loadFoldersHome);

/* Video upload scoped to a folder -- reuses the same global /api/ingest/upload
   and /api/ingest/status endpoints as the Library view (ingestion is a single
   global job either way), just rendered into this folder's own progress bar. */
function uploadFolderVideo(file) {
  const folderId = state.currentFolderId;
  if (!folderId) return;
  folderUploadProgress.hidden = false;
  folderUploadProgress.className = 'ingest-progress';
  folderUploadTitle.textContent = `Uploading ${file.name}...`;

  const formData = new FormData();
  formData.append('file', file);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/ingest/upload?folder_id=${encodeURIComponent(folderId)}`);
  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      folderUploadTitle.textContent = `Uploading ${file.name}... ${pct}%`;
    }
  });
  xhr.addEventListener('load', () => {
    if (xhr.status === 200) {
      folderUploadTitle.textContent = `Processing ${file.name} -- this can take a few minutes for a long recording...`;
      startFolderIngestPolling();
    } else if (xhr.status === 409) {
      folderUploadTitle.textContent = 'A video is already being processed elsewhere -- check back shortly.';
      startFolderIngestPolling();
    } else {
      let detail = 'Upload failed.';
      try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* ignore */ }
      folderUploadProgress.className = 'ingest-progress error';
      folderUploadTitle.textContent = `Failed: ${detail}`;
    }
  });
  xhr.addEventListener('error', () => {
    folderUploadProgress.className = 'ingest-progress error';
    folderUploadTitle.textContent = 'Upload failed -- could not reach the server.';
  });
  xhr.send(formData);
}

async function pollFolderIngestStatus() {
  let data;
  try {
    const res = await fetch('/api/ingest/status');
    data = await res.json();
  } catch {
    return;
  }
  if (data.status === 'running' || data.status === 'uploading') {
    folderUploadTitle.textContent = `Processing ${data.url || 'video'}...`;
  } else if (data.status === 'done') {
    folderUploadProgress.className = 'ingest-progress done';
    folderUploadTitle.textContent = 'Done! Added to this folder.';
    stopFolderIngestPolling();
    await loadGraph();
    await refreshFolderDetail();
  } else if (data.status === 'error') {
    folderUploadProgress.className = 'ingest-progress error';
    folderUploadTitle.textContent = `Failed: ${data.error || 'unknown error'}`;
    stopFolderIngestPolling();
  }
}
function startFolderIngestPolling() {
  if (folderIngestPollTimer) return;
  pollFolderIngestStatus();
  folderIngestPollTimer = setInterval(pollFolderIngestStatus, 2000);
}
function stopFolderIngestPolling() {
  if (folderIngestPollTimer) {
    clearInterval(folderIngestPollTimer);
    folderIngestPollTimer = null;
  }
}

folderVideoDropzone.addEventListener('click', () => folderVideoInput.click());
folderVideoInput.addEventListener('change', () => {
  if (folderVideoInput.files[0]) uploadFolderVideo(folderVideoInput.files[0]);
  folderVideoInput.value = '';
});
['dragenter', 'dragover'].forEach((evt) => {
  folderVideoDropzone.addEventListener(evt, (e) => { e.preventDefault(); folderVideoDropzone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach((evt) => {
  folderVideoDropzone.addEventListener(evt, (e) => { e.preventDefault(); folderVideoDropzone.classList.remove('dragover'); });
});
folderVideoDropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFolderVideo(file);
});

/* Document upload scoped to a folder -- synchronous on the server (no
   background job/polling needed), so just await the response directly. */
function uploadFolderDocument(file) {
  const folderId = state.currentFolderId;
  if (!folderId) return;
  folderUploadProgress.hidden = false;
  folderUploadProgress.className = 'ingest-progress';
  folderUploadTitle.textContent = `Uploading ${file.name}...`;

  const formData = new FormData();
  formData.append('file', file);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/documents/upload?folder_id=${encodeURIComponent(folderId)}`);
  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      folderUploadTitle.textContent = `Uploading ${file.name}... ${pct}%`;
    }
  });
  xhr.addEventListener('load', async () => {
    if (xhr.status === 200) {
      folderUploadProgress.className = 'ingest-progress done';
      folderUploadTitle.textContent = 'Document added.';
      await loadGraph();
      await refreshFolderDetail();
    } else {
      let detail = 'Upload failed.';
      try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* ignore */ }
      folderUploadProgress.className = 'ingest-progress error';
      folderUploadTitle.textContent = `Failed: ${detail}`;
    }
  });
  xhr.addEventListener('error', () => {
    folderUploadProgress.className = 'ingest-progress error';
    folderUploadTitle.textContent = 'Upload failed -- could not reach the server.';
  });
  xhr.send(formData);
}

folderDocDropzone.addEventListener('click', () => folderDocInput.click());
folderDocInput.addEventListener('change', () => {
  if (folderDocInput.files[0]) uploadFolderDocument(folderDocInput.files[0]);
  folderDocInput.value = '';
});
['dragenter', 'dragover'].forEach((evt) => {
  folderDocDropzone.addEventListener(evt, (e) => { e.preventDefault(); folderDocDropzone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach((evt) => {
  folderDocDropzone.addEventListener(evt, (e) => { e.preventDefault(); folderDocDropzone.classList.remove('dragover'); });
});
folderDocDropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFolderDocument(file);
});

/* Structured audit brief: tiny markdown-ish renderer for the ##/-/--- subset
   kgwiki.briefing actually produces -- no need for a full markdown library. */
function renderBriefMarkdown(md) {
  const lines = (md || '').split('\n');
  let html = '';
  let inList = false;
  const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }
    if (line.startsWith('## ')) {
      closeList();
      html += `<h3 class="brief-heading">${escapeHtml(line.slice(3))}</h3>`;
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      if (!inList) { html += '<ul class="brief-list">'; inList = true; }
      html += `<li>${escapeHtml(line.slice(2))}</li>`;
    } else if (line.startsWith('---')) {
      closeList();
      html += '<hr class="brief-hr">';
    } else {
      closeList();
      html += `<p class="brief-p">${escapeHtml(line)}</p>`;
    }
  }
  closeList();
  return html;
}

folderBriefBtn.addEventListener('click', async () => {
  const folderId = state.currentFolderId;
  if (!folderId) return;
  folderBriefBtn.disabled = true;
  folderBriefEl.innerHTML = '<div class="chat-loading"><span class="ingest-spinner"></span> Reading every video and document in this folder... (can take a minute or two the first time, while the local model loads)</div>';
  try {
    const res = await fetchWithTimeout(`/api/folders/${folderId}/brief`, { method: 'POST' }, 220000);
    const data = await res.json();
    folderBriefEl.innerHTML = renderBriefMarkdown(data.brief);
  } catch {
    folderBriefEl.innerHTML = '<div class="empty-state">Could not generate the brief -- try again.</div>';
  } finally {
    folderBriefBtn.disabled = false;
  }
});

/* Folder-scoped chat -- same /api/query endpoint as the main Ask view, just
   with folder_id set so retrieval and overviews are filtered to this folder. */
folderChatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = folderChatInput.value.trim();
  const folderId = state.currentFolderId;
  if (!q || !folderId) return;
  folderChatInput.value = '';

  const turn = document.createElement('div');
  turn.className = 'chat-turn';
  turn.innerHTML = `
    <div class="chat-user-msg">${escapeHtml(q)}</div>
    <div class="chat-loading"><span class="ingest-spinner"></span> Thinking...</div>
  `;
  folderChatThread.appendChild(turn);
  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });

  let data;
  try {
    const res = await fetchWithTimeout(
      '/api/query',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q, history: state.folderChatHistory, folder_id: folderId }),
      },
      75000,
    );
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    data = await res.json();
  } catch (err) {
    const isAbort = err && err.name === 'AbortError';
    turn.querySelector('.chat-loading').outerHTML = `<div class="empty-state">${
      isAbort ? 'That took too long and timed out -- try again.' : "Couldn't reach the server for an answer."
    }</div>`;
    return;
  }

  const loadingEl = turn.querySelector('.chat-loading');
  if (data.answer) {
    const sourceLabel = SOURCE_LABELS[data.answer_source];
    const answerEl = document.createElement('div');
    answerEl.className = 'query-answer';
    answerEl.innerHTML = `
      <span class="answer-label">Answer${sourceLabel ? ` <span class="answer-source">· ${sourceLabel}</span>` : ''}</span>
      <div class="answer-text">${escapeHtml(data.answer)}</div>
    `;
    loadingEl.replaceWith(answerEl);
    if (data.answer_source && data.answer_source !== 'none') {
      state.folderChatHistory.push({ role: 'user', content: q });
      state.folderChatHistory.push({ role: 'assistant', content: data.answer });
    }
  } else {
    loadingEl.remove();
  }
  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });
});

document.querySelector('.nav-icon[data-view="folders"]').addEventListener('click', () => {
  if (!folderDetailEl.hidden) return; // a folder is already open -- stay on it
  loadFoldersHome();
});

/* ---------------- Boot ---------------- */

loadGraph();

/* Video Knowledge Graph -- frontend */

const TYPE_COLOR = { video: '#2dd4bf', section: '#8b9cff', entity: '#f0b429' };

const state = {
  nodesById: new Map(),
  highlightNodes: new Set(),
  highlightLinks: new Set(),
  hoverNode: null,
  Graph: null,
};

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
        const dim = state.highlightNodes.size > 0 && !state.highlightNodes.has(node);
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

/* ---------------- Query view ---------------- */

const queryForm = document.getElementById('query-form');
const queryInput = document.getElementById('query-input');
const queryAnswer = document.getElementById('query-answer');
const queryResults = document.getElementById('query-results');

queryForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = queryInput.value.trim();
  if (!q) return;

  queryResults.innerHTML = '<div class="empty-state">Searching…</div>';
  queryAnswer.hidden = true;

  const res = await fetch('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q }),
  });
  const data = await res.json();

  if (data.answer) {
    queryAnswer.hidden = false;
    queryAnswer.innerHTML = `<span class="answer-label">Answer</span>${escapeHtml(data.answer)}`;
  }

  if (!data.matches || !data.matches.length) {
    queryResults.innerHTML = '<div class="empty-state">No matching sections yet. Try different wording, or ingest more videos with vidblog.</div>';
    return;
  }

  queryResults.innerHTML = data.matches.map(renderResultCard).join('');
  queryResults.querySelectorAll('.result-card').forEach((card) => {
    card.addEventListener('click', () => {
      const node = state.nodesById.get(card.dataset.id);
      if (node) locateInGraph(node);
    });
  });
});

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

/* ---------------- Boot ---------------- */

loadGraph();

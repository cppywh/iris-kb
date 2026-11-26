(() => {
  'use strict';

  const state = {
    cards: [],
    filtered: [],
    activeId: null,
    query: '',
    domain: '',
    status: '',
    confidence: '',
    theme: localStorage.getItem('iris.theme') || '',
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- 主题 ----------
  function applyTheme() {
    const t = state.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = t;
    $('theme-toggle').textContent = t === 'dark' ? '浅色' : '深色';
  }

  // ---------- 搜索 ----------
  const lower = (v) => (Array.isArray(v) ? v.join(' ') : String(v || '')).toLowerCase();

  function prepare(cards) {
    for (const c of cards) {
      c._title = lower(c.title);
      c._alias = lower(c.aliases);
      c._tag = lower(c.tags) + ' ' + lower(c.cve);
      c._path = lower(c.path) + ' ' + lower(c.domain) + ' ' + lower(c.technique);
    }
  }

  function tokenize(q) {
    const raw = q.toLowerCase().trim();
    if (!raw) return [];
    const parts = raw.split(/[\s,，、;；]+/).filter(Boolean);
    const out = new Set();
    for (const p of parts) {
      out.add(p);
      if (/[\u4e00-\u9fff]/.test(p) && p.length > 2) {
        for (let i = 0; i < p.length - 1; i++) out.add(p.slice(i, i + 2));
      }
    }
    return [...out];
  }

  function scoreCard(c, tokens) {
    let hit = 0, score = 0;
    for (const t of tokens) {
      let s = 0;
      if (c._title.includes(t)) s += 6;
      if (c._alias.includes(t)) s += 3;
      if (c._tag.includes(t)) s += 2;
      if (c._path.includes(t)) s += 1;
      if (s) { hit++; score += s; }
    }
    return { hit, score };
  }

  function run() {
    const tokens = tokenize(state.query);
    const pool = state.cards.filter((c) =>
      (!state.domain || c.domain === state.domain) &&
      (!state.status || c.status === state.status) &&
      (!state.confidence || c.confidence === state.confidence)
    );

    let ranked;
    if (!tokens.length) {
      ranked = pool.map((c) => ({ c, score: 0, hit: 0 }));
    } else {
      ranked = [];
      for (const c of pool) {
        const r = scoreCard(c, tokens);
        if (r.hit) ranked.push({ c, score: r.score, hit: r.hit });
      }
      const all = ranked.filter((r) => r.hit === tokens.length);
      if (all.length) ranked = all;
      ranked.sort((a, b) => b.score - a.score || b.hit - a.hit || a.c.title.length - b.c.title.length);
    }

    state.filtered = ranked.map((r) => r.c);
    renderResults(tokens.length ? ranked : null);
  }

  function renderResults(ranked) {
    const list = $('results');
    const total = state.filtered.length;
    $('result-count').textContent = total ? `${total} 张` : '无结果';

    const slice = state.filtered.slice(0, 200);
    const frag = document.createDocumentFragment();

    slice.forEach((c, i) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'result' + (c.id === state.activeId ? ' active' : '');
      btn.dataset.id = c.id;
      const rank = ranked ? `<b>${ranked[i].score}</b> · ` : '';
      btn.innerHTML =
        `<span class="t">${esc(c.title || c.id)}</span>` +
        `<span class="m">${rank}${esc(c.domain || '')} · ${esc(c.status || '')} · ${esc(c.confidence || '')}` +
        (c.affected_versions ? ` · ${esc(String(c.affected_versions).slice(0, 42))}` : '') +
        `</span>`;
      btn.addEventListener('click', () => openCard(c));
      li.append(btn);
      frag.append(li);
    });

    if (!slice.length) {
      const li = document.createElement('li');
      li.className = 'result';
      li.style.color = 'var(--muted)';
      li.textContent = '没有匹配的卡片，换个关键词或放宽筛选。';
      frag.append(li);
    }

    list.replaceChildren(frag);
  }

  // ---------- Markdown ----------
  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  function renderMarkdown(md) {
    md = md.replace(/\r\n?/g, '\n');
    const blocks = [];
    md = md.replace(/```[^\n]*\n([\s\S]*?)```/g, (_, code) => {
      blocks.push(`<pre><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
      return `\u0000${blocks.length - 1}\u0000`;
    });

    const lines = md.split('\n');
    const out = [];
    let para = [], list = null, i = 0;

    const flushPara = () => {
      if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; }
    };
    const flushList = () => {
      if (list) { out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join('')}</${list.tag}>`); list = null; }
    };

    while (i < lines.length) {
      const line = lines[i];

      if (/^\u0000\d+\u0000$/.test(line.trim())) {
        flushPara(); flushList();
        out.push(blocks[Number(line.trim().replace(/\u0000/g, ''))]);
        i++; continue;
      }
      if (!line.trim()) { flushPara(); flushList(); i++; continue; }

      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) { flushPara(); flushList(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); i++; continue; }

      if (/^\s*\|.*\|\s*$/.test(line)) {
        flushPara(); flushList();
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((x) => x.trim());
        const head = cells(rows[0]);
        const body = rows.slice(rows[1].some((r) => /^[\s|:-]+$/.test(r)) ? 2 : 1);
        out.push(
          `<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>` +
          body.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('') +
          `</tbody></table>`
        );
        continue;
      }

      const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
      if (ul) {
        flushPara();
        if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
        list.items.push(ul[1]); i++; continue;
      }

      const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (ol) {
        flushPara();
        if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
        list.items.push(ol[1]); i++; continue;
      }

      if (/^>\s?/.test(line)) {
        flushPara(); flushList();
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { quote.push(lines[i].replace(/^>\s?/, '')); i++; }
        out.push(`<blockquote>${inline(quote.join(' '))}</blockquote>`);
        continue;
      }

      para.push(line.trim());
      i++;
    }
    flushPara(); flushList();
    return out.join('\n');
  }

  // ---------- 卡片 ----------
  function stripFrontmatter(text) {
    if (text.startsWith('---')) {
      const end = text.indexOf('\n---', 3);
      if (end > 0) return text.slice(text.indexOf('\n', end + 1) + 1);
    }
    return text;
  }

  async function openCard(card) {
    state.activeId = card.id;
    history.replaceState(null, '', '#' + encodeURIComponent(card.id));
    document.querySelectorAll('.result').forEach((el) => el.classList.remove('active'));

    const reader = $('reader');
    const chips = [
      `<span class="chip">${esc(card.domain || '—')}</span>`,
      `<span class="chip ${card.status === 'verified' ? 'ok' : 'warn'}">${esc(card.status || 'unknown')}</span>`,
      `<span class="chip">${esc(card.confidence || '—')}</span>`,
      card.severity ? `<span class="chip">${esc(card.severity)}</span>` : '',
      ...(card.cve || []).map((c) => `<span class="chip ok">${esc(c)}</span>`),
      ...(card.tags || []).slice(0, 8).map((t) => `<span class="chip">${esc(t)}</span>`),
    ].join('');

    reader.innerHTML = `
      <div class="meta-top">
        <h2>${esc(card.title || card.id)}</h2>
        <div class="chips">${chips}</div>
        <div class="pathline">${esc(card.path || '')} · <code>${esc(card.filename || '')}</code>` +
        (card.updated ? ` · 更新 ${esc(card.updated)}` : '') +
        (card.affected_versions ? `<br>适用版本：${esc(card.affected_versions)}` : '') +
        `</div>
      </div>
      <article class="article"><p style="color:var(--muted)">正在读取…</p></article>`;

    try {
      const res = await fetch(card.url, { cache: 'no-store' });
      if (!res.ok) throw Error(`HTTP ${res.status}`);
      const md = stripFrontmatter(await res.text());
      reader.querySelector('.article').innerHTML = renderMarkdown(md);
      reader.scrollTop = 0;
    } catch (err) {
      reader.querySelector('.article').innerHTML =
        `<p style="color:var(--warn)">正文读取失败：${esc(err.message)}。请通过 HTTP 服务打开本站（见 site/README）。</p>`;
    }

    const btn = [...document.querySelectorAll('.result')].find((b) => b.dataset.id === card.id);
    if (btn) btn.classList.add('active');
  }

  // ---------- 启动 ----------
  async function boot() {
    applyTheme();

    let data;
    try {
      const res = await fetch('data/cards.json', { cache: 'no-store' });
      if (!res.ok) throw Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      $('stat-line').textContent = `索引加载失败：${err.message}`;
      return;
    }

    state.cards = data.cards;
    prepare(state.cards);

    const stats = data.stats || {};
    const doms = Object.entries(stats.domains || {});
    $('stat-line').textContent =
      `${stats.cards || state.cards.length} 张卡片 · ${doms.length} 个域 · 只读`;

    const sel = $('domain');
    for (const [name, n] of doms) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = `${name} (${n})`;
      sel.append(opt);
    }

    const q = new URLSearchParams(location.search).get('q');
    if (q) { state.query = q; $('q').value = q; }

    run();

    const hash = decodeURIComponent(location.hash.slice(1));
    if (hash) {
      const card = state.cards.find((c) => c.id === hash);
      if (card) openCard(card);
    }
  }

  // ---------- 事件 ----------
  let timer;
  $('q').addEventListener('input', (e) => {
    clearTimeout(timer);
    const v = e.target.value;
    timer = setTimeout(() => { state.query = v; run(); }, 120);
  });

  $('clear').addEventListener('click', () => { $('q').value = ''; state.query = ''; run(); $('q').focus(); });
  $('domain').addEventListener('change', (e) => { state.domain = e.target.value; run(); });
  $('status').addEventListener('change', (e) => { state.status = e.target.value; run(); });
  $('confidence').addEventListener('change', (e) => { state.confidence = e.target.value; run(); });

  $('random').addEventListener('click', () => {
    const pool = state.filtered.length ? state.filtered : state.cards;
    if (!pool.length) return;
    openCard(pool[Math.floor(Math.random() * pool.length)]);
  });

  $('theme-toggle').addEventListener('click', () => {
    const now = document.documentElement.dataset.theme;
    state.theme = now === 'dark' ? 'light' : 'dark';
    localStorage.setItem('iris.theme', state.theme);
    applyTheme();
  });

  boot();
})();

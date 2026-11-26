(() => {
  'use strict';

  const state = {
    cards: [],
    tree: null,
    query: '',
    scope: '',
    activeId: null,
    theme: localStorage.getItem('iris.theme') || 'dark',
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const lower = (v) => (Array.isArray(v) ? v.join(' ') : String(v || '')).toLowerCase();

  let world = null;

  // ---------- 主题 ----------
  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    $('theme-toggle').textContent = state.theme === 'dark' ? '☀' : '◐';
    world?.setTheme(state.theme);
  }

  // ---------- 搜索 ----------
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
    const out = new Set();
    for (const part of raw.split(/[\s,，、;；]+/).filter(Boolean)) {
      out.add(part);
      if (/[\u4e00-\u9fff]/.test(part) && part.length > 2) {
        for (let i = 0; i < part.length - 1; i++) out.add(part.slice(i, i + 2));
      }
    }
    return [...out];
  }

  function score(c, tokens) {
    let hit = 0, total = 0;
    for (const t of tokens) {
      let s = 0;
      if (c._title.includes(t)) s += 6;
      if (c._alias.includes(t)) s += 3;
      if (c._tag.includes(t)) s += 2;
      if (c._path.includes(t)) s += 1;
      if (s) { hit++; total += s; }
    }
    return { hit, total };
  }

  function inScope(path) {
    return !state.scope || path === state.scope || path.startsWith(state.scope + '/');
  }

  function runSearch() {
    const tokens = tokenize(state.query);
    const treeEl = $('dir-tree');
    const listEl = $('results');

    if (!tokens.length) {
      listEl.hidden = true;
      treeEl.hidden = false;
      return;
    }

    const pool = state.cards.filter((c) => inScope(c.path || ''));
    let ranked = [];
    for (const c of pool) {
      const r = score(c, tokens);
      if (r.hit) ranked.push({ c, ...r });
    }
    const full = ranked.filter((r) => r.hit === tokens.length);
    if (full.length) ranked = full;
    ranked.sort((a, b) => b.total - a.total || a.c.title.length - b.c.title.length);

    treeEl.hidden = true;
    listEl.hidden = false;

    const frag = document.createDocumentFragment();
    const head = document.createElement('li');
    head.className = 'result-head-row';
    head.textContent = `${ranked.length} 张 · ${state.scope || '全部'}`;
    frag.append(head);

    for (const { c, total } of ranked.slice(0, 150)) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'result' + (c.id === state.activeId ? ' active' : '');
      btn.dataset.id = c.id;
      btn.innerHTML =
        `<span class="t">${esc(c.title || c.id)}</span>` +
        `<span class="m"><b>${total}</b> · ${esc(c.domain || '')} · ${esc(c.status || '')} · ${esc(c.confidence || '')}</span>`;
      btn.addEventListener('click', () => openCard(c));
      li.append(btn);
      frag.append(li);
    }
    if (!ranked.length) {
      const li = document.createElement('li');
      li.className = 'result-empty';
      li.textContent = '没有匹配，换个词或放宽范围。';
      frag.append(li);
    }
    listEl.replaceChildren(frag);
  }

  // ---------- 侧栏目录 ----------
  function renderTree() {
    const host = $('dir-tree');
    const frag = document.createDocumentFragment();

    const makeRow = (node, depth) => {
      const wrap = document.createElement('div');
      wrap.className = 'dir-node';

      const row = document.createElement('div');
      row.className = 'dir-row';
      row.style.paddingLeft = `${6 + depth * 13}px`;
      row.dataset.path = node.path;

      const hasKids = node.children && node.children.length;
      const caret = document.createElement('button');
      caret.type = 'button';
      caret.className = 'caret' + (hasKids ? '' : ' leaf');
      caret.textContent = hasKids ? '▸' : '·';
      caret.setAttribute('aria-label', hasKids ? '展开' : '无子目录');

      const name = document.createElement('button');
      name.type = 'button';
      name.className = 'dir-name';
      name.innerHTML = `<span>${esc(node.title || node.name)}</span><b>${node.count}</b>`;

      row.append(caret, name);

      const kids = document.createElement('div');
      kids.className = 'dir-kids';
      kids.hidden = true;

      if (hasKids) {
        caret.addEventListener('click', (e) => {
          e.stopPropagation();
          kids.hidden = !kids.hidden;
          caret.textContent = kids.hidden ? '▸' : '▾';
          caret.classList.toggle('open', !kids.hidden);
        });
        for (const child of node.children) kids.append(makeRow(child, depth + 1));
      }

      name.addEventListener('click', () => {
        selectScope(node.path === '' ? '' : node.path, node);
        if (hasKids && kids.hidden) {
          kids.hidden = false;
          caret.textContent = '▾';
          caret.classList.add('open');
        }
      });

      wrap.append(row, kids);
      return wrap;
    };

    for (const child of state.tree.children || []) frag.append(makeRow(child, 0));
    host.replaceChildren(frag);
    markScope();
  }

  function markScope() {
    for (const row of document.querySelectorAll('.dir-row')) {
      row.classList.toggle('is-current', row.dataset.path === state.scope);
    }
  }

  function selectScope(path, node) {
    state.scope = path || '';
    $('scope-label').textContent = `范围：${path ? (node?.title || node?.name || path) : '全部知识'}`;
    $('scope-reset').hidden = !path;
    world?.setScope(state.scope);
    markScope();
    if (state.query) runSearch();
    renderSideDetail(node);
  }

  function renderSideDetail(node) {
    const host = $('side-detail');
    if (!node) {
      host.innerHTML = '<p class="hint">单击枝干节点收窄范围，双击放大；点外圈星点打开卡片。</p>';
      return;
    }
    const own = state.cards.filter((c) => c.path === node.path).length;
    host.innerHTML = `
      <div class="detail-title">${esc(node.title || node.name)}</div>
      <div class="detail-meta">${node.count} 张卡 · 直属 ${own} 张</div>
      <div class="detail-path">${esc(node.path || '/')}</div>
    `;
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

    const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
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
          '</tbody></table>'
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

  function stripFrontmatter(text) {
    text = text.replace(/^\uFEFF/, '');
    if (text.startsWith('---')) {
      const end = text.indexOf('\n---', 3);
      if (end > 0) return text.slice(text.indexOf('\n', end + 1) + 1);
    }
    return text;
  }

  // ---------- 阅读 ----------
  async function openCard(card) {
    state.activeId = card.id;
    history.replaceState(null, '', '#' + encodeURIComponent(card.id));
    world?.setSelected(card.id);
    for (const el of document.querySelectorAll('.result')) el.classList.toggle('active', el.dataset.id === card.id);

    const reader = $('reader');
    reader.hidden = false;
    document.querySelector('.app').classList.add('has-reader');

    const chips = [
      `<span class="chip">${esc(card.domain || '—')}</span>`,
      `<span class="chip ${card.status === 'verified' ? 'ok' : 'warn'}">${esc(card.status || 'unknown')}</span>`,
      `<span class="chip">${esc(card.confidence || '—')}</span>`,
      card.severity ? `<span class="chip">${esc(card.severity)}</span>` : '',
      ...(card.cve || []).map((c) => `<span class="chip ok">${esc(c)}</span>`),
      ...(card.tags || []).slice(0, 8).map((t) => `<span class="chip">${esc(t)}</span>`),
    ].join('');

    $('reader-crumb').textContent = card.path || '场景手记';
    $('reader-meta').innerHTML = `
      <h2>${esc(card.title || card.id)}</h2>
      <div class="chips">${chips}</div>
      <div class="pathline">${esc(card.path || '')} · <code>${esc(card.filename || '')}</code>` +
      (card.updated ? ` · 更新 ${esc(card.updated)}` : '') +
      (card.affected_versions ? `<br>适用版本：${esc(card.affected_versions)}` : '') +
      `</div>`;

    const article = $('article');
    article.innerHTML = '<p class="loading">正在读取…</p>';

    try {
      const res = await fetch(card.url, { cache: 'no-store' });
      if (!res.ok) throw Error(`HTTP ${res.status}`);
      article.innerHTML = renderMarkdown(stripFrontmatter(await res.text()));
      reader.querySelector('.reader-scroll').scrollTop = 0;
    } catch (err) {
      article.innerHTML = `<p class="error">正文读取失败：${esc(err.message)}</p>`;
    }
  }

  function closeReader() {
    $('reader').hidden = true;
    document.querySelector('.app').classList.remove('has-reader');
    state.activeId = null;
    world?.setSelected(null);
    history.replaceState(null, '', location.pathname + location.search);
    for (const el of document.querySelectorAll('.result')) el.classList.remove('active');
  }

  // ---------- 启动 ----------
  async function boot() {
    const params = new URLSearchParams(location.search);
    const themeParam = params.get('theme');
    if (themeParam === 'light' || themeParam === 'dark') state.theme = themeParam;
    applyTheme();

    let cardsData, treeData;
    try {
      const [a, b] = await Promise.all([
        fetch('data/cards.json', { cache: 'no-store' }),
        fetch('data/tree.json', { cache: 'no-store' }),
      ]);
      if (!a.ok || !b.ok) throw Error(`HTTP ${a.status}/${b.status}`);
      cardsData = await a.json();
      treeData = await b.json();
    } catch (err) {
      $('stat-line').textContent = `索引加载失败：${err.message}`;
      return;
    }

    state.cards = cardsData.cards;
    state.tree = treeData;
    prepare(state.cards);

    const stats = cardsData.stats || {};
    $('stat-line').textContent = `${stats.cards || state.cards.length} 张卡片 · ${(treeData.children || []).length} 个域`;

    world = new window.WorldTree($('world'), {
      onCard: openCard,
      onNode: (node) => selectScope(node.path, node),
      onBlank: () => {},
      onHover: (item, kind) => {
        const tip = $('hover-tip');
        if (!item) { tip.hidden = true; return; }
        tip.hidden = false;
        tip.textContent = kind === 'card' ? item.title : `${item.title || item.name} · ${item.count}`;
      },
    });
    world.load(treeData, state.cards);
    world.setTheme(state.theme);

    renderTree();

    const scopeParam = params.get('scope');
    if (scopeParam) {
      const node = world.byPath.get(scopeParam);
      if (node) selectScope(scopeParam, node);
    }

    const q = params.get('q');
    if (q) { state.query = q; $('q').value = q; runSearch(); }

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
    timer = setTimeout(() => { state.query = v; runSearch(); }, 120);
  });

  $('clear').addEventListener('click', () => {
    $('q').value = '';
    state.query = '';
    runSearch();
    $('q').focus();
  });

  $('scope-reset').addEventListener('click', () => selectScope('', null));
  $('reader-close').addEventListener('click', closeReader);
  $('zoom-in').addEventListener('click', () => world?.zoomAt(1.3, world.w / 2, world.h / 2));
  $('zoom-out').addEventListener('click', () => world?.zoomAt(1 / 1.3, world.w / 2, world.h / 2));
  $('fit').addEventListener('click', () => world?.fit());

  $('theme-toggle').addEventListener('click', () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('iris.theme', state.theme);
    applyTheme();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeReader();
    if (e.key === '/' && document.activeElement !== $('q')) { e.preventDefault(); $('q').focus(); }
  });

  boot();
})();

/* 径向宇宙树：中心为根，枝干向外辐射，7060 张卡片作为外围星尘。 */
(() => {
  'use strict';

  const TAU = Math.PI * 2;
  const START = -Math.PI / 2;

  const RING = [0, 250, 450, 620, 760];
  const RING_STEP = 130;
  const CARD_BASE = 78;
  const CARD_JITTER = 46;
  const PAD_RATIO = 0.06;

  const DOMAIN_COLOR = {
    web: [126, 176, 198],
    pentest: [201, 141, 104],
    ctf: [186, 154, 208],
    'code-analysis': [143, 179, 191],
    binary: [120, 160, 190],
    'ai-security': [168, 168, 214],
    defense: [146, 186, 160],
    crypto: [198, 178, 120],
    cloud: [136, 186, 200],
    mobile: [196, 150, 150],
    iot: [150, 190, 170],
    tools: [150, 150, 160],
    blockchain: [190, 160, 130],
  };

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 10000) / 10000;
  }

  class WorldTree {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.opts = opts;
      this.nodes = [];
      this.cards = [];
      this.byPath = new Map();
      this.grid = new Map();
      this.cell = 46;

      this.view = { s: 0.7, tx: 0, ty: 0 };
      this.hover = null;
      this.selected = null;
      this.scope = '';
      this.theme = 'dark';
      this.drawSeq = 0;

      this.pointers = new Map();
      this.dragging = false;
      this.moved = 0;

      this._bind();
      this._resize();
      new ResizeObserver(() => {
        const before = this.w;
        this._resize();
        if (!this.w || !this.h) return;
        if (!this._fitted) { this.fit(); return; }
        if (before && this.w !== before) {
          this.view.tx += (this.w - before) / 2;
        }
        this.draw();
      }).observe(canvas.parentElement);
    }

    // ---------- 数据 ----------
    load(tree, cards) {
      this._resize();
      this.cards = cards.map((c, i) => {
        const h = hash(c.id);
        return { ...c, _i: i, _h: h, x: 0, y: 0, r: 0, a: 0 };
      });

      const rootCardMap = new Map();
      for (const c of this.cards) {
        const key = c.path || '';
        if (!rootCardMap.has(key)) rootCardMap.set(key, []);
        rootCardMap.get(key).push(c);
      }

      this.nodes = [];
      const build = (src, parent, depth) => {
        const node = {
          name: src.name,
          title: src.title || src.name,
          path: src.path,
          count: src.count,
          depth,
          parent,
          children: [],
          cards: rootCardMap.get(src.path) || [],
          a0: 0, a1: 0, a: 0, r: 0, x: 0, y: 0,
          _h: hash(src.path || 'root'),
        };
        this.nodes.push(node);
        this.byPath.set(node.path, node);
        for (const child of src.children || []) node.children.push(build(child, node, depth + 1));
        return node;
      };
      this.root = build(tree, null, 0);

      this._layout(this.root, START, START + TAU, 0);
      this._placeCards();
      this._buildGrid();
      this.fit();
      return this;
    }

    _layout(node, a0, a1, depth) {
      node.a0 = a0;
      node.a1 = a1;
      node.a = (a0 + a1) / 2;
      node.depth = depth;
      node.r = depth < RING.length ? RING[depth] : RING[RING.length - 1] + (depth - RING.length + 1) * RING_STEP;
      node.x = Math.cos(node.a) * node.r;
      node.y = Math.sin(node.a) * node.r;

      const kids = node.children;
      if (!kids.length) return;

      // 基础份额保证小域也占得住角度，剩余按卡片数分配
      const total = kids.reduce((s, k) => s + Math.max(1, k.count), 0);
      const span = a1 - a0;
      const base = depth <= 1 ? 0.34 : 0.12;
      const pad = (span * PAD_RATIO) / Math.max(1, kids.length - 1 || 1);
      let cur = a0;
      for (const child of kids) {
        const share =
          (base / kids.length) + (1 - base) * (Math.max(1, child.count) / total);
        const width = Math.max(span * 0.004, span * share - pad);
        this._layout(child, cur, cur + width, depth + 1);
        cur += span * share;
      }
    }

    _placeCards() {
      for (const node of this.nodes) {
        const cards = node.cards;
        if (!cards.length) continue;
        const span = node.a1 - node.a0;
        const step = span / cards.length;
        cards.forEach((c, i) => {
          c.a = node.a0 + step * (i + 0.5);
          c.parentPath = node.path;
          c.domain = c.domain || node.path.split('/')[0];
          c.r = node.r + CARD_BASE + c._h * CARD_JITTER + (node.depth > 2 ? 18 : 0);
          c.x = Math.cos(c.a) * c.r;
          c.y = Math.sin(c.a) * c.r;
        });
      }
    }

    _buildGrid() {
      this.grid.clear();
      for (const c of this.cards) {
        const gx = Math.floor(c.x / this.cell);
        const gy = Math.floor(c.y / this.cell);
        const k = gx + ',' + gy;
        if (!this.grid.has(k)) this.grid.set(k, []);
        this.grid.get(k).push(c);
      }
    }

    // ---------- 视口 ----------
    _resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.canvas.parentElement.getBoundingClientRect();
      this.w = rect.width;
      this.h = rect.height;
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.canvas.style.width = rect.width + 'px';
      this.canvas.style.height = rect.height + 'px';
      this.dpr = dpr;
    }

    fit(padding = 1.14) {
      if (!this.w || !this.h) return;
      const extent = Math.max(...this.nodes.map((n) => n.r), ...this.cards.map((c) => c.r)) + 60;
      const s = Math.min(this.w, this.h) / (extent * 2) / padding;
      this.view = { s: clamp(s, 0.05, 4), tx: this.w / 2, ty: this.h / 2 };
      this._fitted = true;
      this.draw();
    }

    zoomAt(factor, cx, cy) {
      const v = this.view;
      const s = clamp(v.s * factor, 0.08, 14);
      const k = s / v.s;
      this.view = { s, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k };
      this.draw();
    }

    pan(dx, dy) {
      this.view.tx += dx;
      this.view.ty += dy;
      this.draw();
    }

    screenToWorld(x, y) {
      return { x: (x - this.view.tx) / this.view.s, y: (y - this.view.ty) / this.view.s };
    }

    // ---------- 命中 ----------
    hitTest(x, y) {
      const p = this.screenToWorld(x, y);
      const r = 14 / this.view.s;

      let best = null;
      let bestD = Infinity;
      for (const n of this.nodes) {
        const d = Math.hypot(n.x - p.x, n.y - p.y);
        const hit = (n.depth === 0 ? 26 : 12) / this.view.s + 4;
        if (d < hit && d < bestD) { best = n; bestD = d; }
      }
      if (best) return { kind: 'node', node: best };

      const reach = Math.max(12, 10 / this.view.s);
      const gx = Math.floor(p.x / this.cell);
      const gy = Math.floor(p.y / this.cell);
      let card = null;
      let cd = Infinity;
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          const bucket = this.grid.get(gx + dx + ',' + (gy + dy));
          if (!bucket) continue;
          for (const c of bucket) {
            const d = Math.hypot(c.x - p.x, c.y - p.y);
            if (d < reach && d < cd) { card = c; cd = d; }
          }
        }
      }
      return card ? { kind: 'card', card } : null;
    }

    // ---------- 交互 ----------
    _bind() {
      const el = this.canvas;

      el.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = el.getBoundingClientRect();
        this.zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
      }, { passive: false });

      el.addEventListener('pointerdown', (e) => {
        el.setPointerCapture(e.pointerId);
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        this.dragging = true;
        this.moved = 0;
        el.classList.add('is-grabbing');
      });

      el.addEventListener('pointermove', (e) => {
        const rect = el.getBoundingClientRect();
        const px = e.clientX - rect.left;
        const py = e.clientY - rect.top;

        if (this.pointers.size === 1 && this.dragging) {
          const prev = this.pointers.get(e.pointerId);
          const dx = e.clientX - prev.x;
          const dy = e.clientY - prev.y;
          this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          this.moved += Math.abs(dx) + Math.abs(dy);
          this.pan(dx, dy);
          return;
        }

        if (this.pointers.size === 2) {
          const pts = [...this.pointers.values()];
          const before = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
          this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          const now = [...this.pointers.values()];
          const after = Math.hypot(now[0].x - now[1].x, now[0].y - now[1].y);
          if (before > 0) this.zoomAt(after / before, px, py);
          return;
        }

        const hit = this.hitTest(px, py);
        const key = hit ? (hit.kind === 'card' ? hit.card.id : hit.node.path) : null;
        if (key !== this._hoverKey) {
          this._hoverKey = key;
          this.hover = hit;
          el.style.cursor = hit ? 'pointer' : 'grab';
          this.opts.onHover?.(hit?.kind === 'card' ? hit.card : hit?.node || null, hit?.kind);
          this.draw();
        }
      });

      const end = (e) => {
        this.pointers.delete(e.pointerId);
        if (!this.pointers.size) {
          this.dragging = false;
          el.classList.remove('is-grabbing');
        }
        if (this.moved < 6 && e.type === 'pointerup') {
          const rect = el.getBoundingClientRect();
          const hit = this.hitTest(e.clientX - rect.left, e.clientY - rect.top);
          if (hit?.kind === 'card') this.opts.onCard?.(hit.card);
          else if (hit?.kind === 'node') this.opts.onNode?.(hit.node);
          else this.opts.onBlank?.();
        }
      };
      el.addEventListener('pointerup', end);
      el.addEventListener('pointercancel', end);
      el.addEventListener('pointerleave', () => {
        if (this.hover) { this.hover = null; this._hoverKey = null; this.draw(); }
      });
      el.addEventListener('dblclick', (e) => {
        const rect = el.getBoundingClientRect();
        const hit = this.hitTest(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.kind === 'node') this.zoomAt(1.8, e.clientX - rect.left, e.clientY - rect.top);
      });
    }

    setTheme(theme) {
      this.theme = theme;
      this.draw();
    }

    setScope(path) {
      this.scope = path || '';
      this.draw();
    }

    setSelected(id) {
      this.selected = id || null;
      this.draw();
    }

    // ---------- 绘制 ----------
    draw() {
      const ctx = this.ctx;
      const dpr = this.dpr;
      const dark = this.theme === 'dark';

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, this.w, this.h);

      // 背景
      const bg = ctx.createRadialGradient(this.w / 2, this.h / 2, 0, this.w / 2, this.h / 2, Math.max(this.w, this.h) * 0.75);
      if (dark) {
        bg.addColorStop(0, '#16232b');
        bg.addColorStop(0.42, '#0d151a');
        bg.addColorStop(1, '#080b0e');
      } else {
        bg.addColorStop(0, '#fdfcf8');
        bg.addColorStop(0.55, '#f6f4ec');
        bg.addColorStop(1, '#eceade');
      }
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, this.w, this.h);

      ctx.save();
      ctx.translate(this.view.tx, this.view.ty);
      ctx.scale(this.view.s, this.view.s);

      this._drawEdges(dark);
      this._drawCards(dark);
      this._drawNodes(dark);

      ctx.restore();
      this._drawLegend(dark);
    }

    _inScope(node) {
      if (!this.scope) return true;
      return node.path === this.scope || node.path.startsWith(this.scope + '/');
    }

    _drawEdges(dark) {
      const ctx = this.ctx;
      const s = this.view.s;

      for (const node of this.nodes) {
        if (!node.parent) continue;
        const p = node.parent;
        const active = this._inScope(node);
        const hovered = this.hover?.kind === 'node' && (this.hover.node === node || this.hover.node === p);

        // 二次贝塞尔，让枝干先沿父半径外扩再转向子角度
        const midR = (p.r + node.r) / 2;
        const mx = Math.cos(node.a) * midR;
        const my = Math.sin(node.a) * midR;

        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.quadraticCurveTo(mx, my, node.x, node.y);

        const width = clamp(4.6 - node.depth * 0.9, 0.7, 4.6) / Math.max(0.6, Math.sqrt(s));
        if (dark) {
          ctx.strokeStyle = hovered
            ? 'rgba(214,236,245,.95)'
            : active
              ? `rgba(122,176,198,${0.5 - node.depth * 0.07})`
              : 'rgba(96,120,132,.16)';
        } else {
          ctx.strokeStyle = hovered
            ? 'rgba(40,72,86,.9)'
            : active
              ? `rgba(66,103,121,${0.5 - node.depth * 0.06})`
              : 'rgba(120,124,112,.18)';
        }
        ctx.lineWidth = width;
        ctx.stroke();
      }
    }

    _drawCards(dark) {
      const ctx = this.ctx;
      const s = this.view.s;
      if (s < 0.16) return;

      const radius = clamp(1.05 + s * 1.7, 0.9, 4.2);
      const groups = new Map();

      for (const c of this.cards) {
        const inScope = !this.scope || c.path === this.scope || c.path.startsWith(this.scope + '/');
        const key = (c.domain || 'other') + (inScope ? '' : '|dim');
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(c);
      }

      for (const [key, list] of groups) {
        const dim = key.endsWith('|dim');
        const domain = key.replace('|dim', '');
        const rgb = DOMAIN_COLOR[domain] || [140, 150, 158];
        ctx.beginPath();
        for (const c of list) {
          ctx.moveTo(c.x + radius, c.y);
          ctx.arc(c.x, c.y, radius, 0, TAU);
        }
        if (dark) {
          ctx.fillStyle = dim ? `rgba(${rgb},.12)` : `rgba(${rgb},.82)`;
        } else {
          ctx.fillStyle = dim ? `rgba(${rgb},.2)` : `rgba(${rgb.join(',')},.92)`;
        }
        ctx.fill();
      }

      // 选中卡片
      if (this.hover?.kind === 'card') {
        const c = this.hover.card;
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius * 3.4, 0, TAU);
        ctx.fillStyle = dark ? 'rgba(255,232,190,.9)' : 'rgba(166,111,81,.9)';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius * 7, 0, TAU);
        ctx.fillStyle = dark ? 'rgba(255,232,190,.16)' : 'rgba(166,111,81,.14)';
        ctx.fill();
      }
    }

    _drawNodes(dark) {
      const ctx = this.ctx;
      const s = this.view.s;

      // 第一遍：世界坐标画节点圆
      for (const node of this.nodes) {
        const isRoot = node.depth === 0;
        const active = this._inScope(node);
        const isHover = this.hover?.kind === 'node' && this.hover.node === node;
        const base = isRoot ? 15 : clamp(7.5 - node.depth * 1.1, 2.4, 7.5);

        ctx.beginPath();
        ctx.arc(node.x, node.y, base, 0, TAU);
        if (isRoot) {
          const g = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, base * 3.4);
          g.addColorStop(0, dark ? 'rgba(255,238,206,.98)' : 'rgba(166,111,81,.98)');
          g.addColorStop(0.35, dark ? 'rgba(255,224,176,.34)' : 'rgba(166,111,81,.26)');
          g.addColorStop(1, 'rgba(166,111,81,0)');
          ctx.fillStyle = g;
          ctx.arc(node.x, node.y, base * 3.4, 0, TAU);
        } else {
          ctx.fillStyle = isHover
            ? dark ? '#ffe8be' : '#a66f51'
            : active
              ? dark ? 'rgba(214,236,245,.92)' : 'rgba(66,103,121,.92)'
              : 'rgba(120,130,130,.3)';
        }
        ctx.fill();
      }

      // 第二遍：反缩放绘制标签，字号固定在屏幕像素，并按占位避让
      ctx.save();
      ctx.scale(1 / s, 1 / s);
      ctx.textBaseline = 'middle';

      const taken = [];
      const hits = (r) =>
        taken.some((t) => r.x < t.x + t.w && r.x + r.w > t.x && r.y < t.y + t.h && r.y + r.h > t.y);

      for (const node of this.nodes) {
        const isRoot = node.depth === 0;
        const active = this._inScope(node);
        const size = isRoot ? 15 : node.depth === 1 ? 11.5 : 9.5;

        const show =
          isRoot ||
          (node.depth === 1 && s > 0.22) ||
          (node.depth === 2 && s > 0.62) ||
          (node.depth >= 3 && s > 1.5);
        if (!show) continue;

        const sx = node.x * s;
        const sy = node.y * s;
        const rb = (isRoot ? 15 : clamp(7.5 - node.depth * 1.1, 2.4, 7.5)) * s;
        const label = isRoot
          ? 'Iris'
          : node.depth === 1
            ? `${node.title || node.name} · ${node.count}`
            : node.title || node.name;

        ctx.textAlign = 'center';
        ctx.font = isRoot
          ? `italic 600 ${size}px Georgia, "Noto Serif CJK SC", serif`
          : `500 ${size}px "Segoe UI","PingFang SC",sans-serif`;
        ctx.fillStyle = dark
          ? isRoot
            ? 'rgba(255,240,214,.98)'
            : active ? `rgba(228,240,246,${0.92 - node.depth * 0.1})` : 'rgba(150,160,162,.26)'
          : isRoot
            ? 'rgba(48,59,64,.98)'
            : active ? `rgba(48,59,64,${0.92 - node.depth * 0.1})` : 'rgba(120,124,112,.38)';

        const w = ctx.measureText(label).width;
        const h = size * 1.45;

        if (isRoot) {
          ctx.fillText(label, sx, sy - rb - size * 0.95);
          taken.push({ x: sx - w / 2, y: sy - rb - size * 1.7, w, h });
          continue;
        }

        if (node.depth > 1) {
          const ty = sy - rb - size * 0.95;
          ctx.fillText(label, sx, ty);
          taken.push({ x: sx - w / 2, y: ty - h / 2, w, h });
          continue;
        }

        // 一级域：优先放节点上方，冲突时沿半径向外挪
        const ca = Math.cos(node.a);
        const sa = Math.sin(node.a);
        const spots = [[sx, sy - rb - size * 0.95]];
        for (let k = 1; k <= 7; k++) {
          const d = rb + size * (1.2 + k * 1.5);
          spots.push([sx + ca * d, sy + sa * d]);
        }
        let chosen = spots[0];
        for (const spot of spots) {
          const box = { x: spot[0] - w / 2, y: spot[1] - h / 2, w, h };
          if (!hits(box)) { chosen = spot; taken.push(box); break; }
        }
        ctx.fillText(label, chosen[0], chosen[1]);
      }

      ctx.restore();
    }

    _drawLegend(dark) {
      const ctx = this.ctx;
      ctx.save();
      ctx.font = '11px "Segoe UI",sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = dark ? 'rgba(150,168,174,.55)' : 'rgba(116,122,121,.7)';
      ctx.fillText(`缩放 ${(this.view.s * 100).toFixed(0)}%  ·  拖拽平移  ·  滚轮缩放  ·  双击目录放大`, 22, this.h - 22);
      ctx.restore();
    }
  }

  window.WorldTree = WorldTree;
})();

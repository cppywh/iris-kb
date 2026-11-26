/* 径向星图：中心为根，枝干向外辐射，7060 张卡片作为外圈墨点。
   视觉取向：纸感手稿 —— 细线、衬线字、低饱和墨色，而不是发光深空。 */
(() => {
  'use strict';

  const TAU = Math.PI * 2;
  const START = -Math.PI / 2;

  const RING = [0, 250, 450, 620, 760];
  const RING_STEP = 130;
  const CARD_BASE = 82;
  const CARD_JITTER = 44;
  const PAD_RATIO = 0.06;

  // 墨色调：全部低饱和，浅色下压在纸面上，深色下提亮
  const DOMAIN_INK = {
    web: [62, 100, 118],
    pentest: [158, 105, 74],
    ctf: [112, 98, 136],
    'code-analysis': [82, 106, 116],
    binary: [70, 94, 118],
    'ai-security': [114, 102, 134],
    defense: [88, 116, 98],
    crypto: [142, 122, 80],
    cloud: [74, 110, 124],
    mobile: [142, 94, 94],
    iot: [94, 120, 106],
    tools: [104, 104, 108],
    blockchain: [132, 104, 80],
  };

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 10000) / 10000;
  }

  function inkOf(domain, dark) {
    const base = DOMAIN_INK[domain] || [110, 112, 116];
    if (!dark) return base;
    return base.map((v) => clamp(Math.round(v * 1.35 + 58), 0, 255));
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
      this.theme = 'light';
      this.grow = 0;
      this._growing = false;

      this.pointers = new Map();
      this.dragging = false;
      this.moved = 0;

      this._bind();
      this._resize();
      new ResizeObserver(() => {
        const beforeW = this.w;
        const beforeH = this.h;
        this._resize();
        if (!this.w || !this.h) return;
        if (!this._fitted || !this._fitW) { this.fit(); return; }
        if (Math.abs(this.w - this._fitW) / this._fitW > 0.15) { this.fit(); return; }
        if (beforeW && beforeH) {
          this.view.tx += (this.w - beforeW) / 2;
          this.view.ty += (this.h - beforeH) / 2;
        }
        this.draw();
      }).observe(canvas.parentElement);
    }

    // ---------- 数据 ----------
    load(tree, cards) {
      this._resize();
      this.cards = cards.map((c, i) => {
        const h = hash(c.id);
        return { ...c, _h: h, x: 0, y: 0, r: 0, a: 0 };
      });

      const groups = new Map();
      for (const c of this.cards) {
        const key = c.path || '';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(c);
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
          cards: groups.get(src.path) || [],
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
      if (this.opts.still) {
        this.grow = 1;
        this.draw();
      } else {
        this.grow = 0;
        this._animate();
      }
      return this;
    }

    _layout(node, a0, a1, depth) {
      node.a0 = a0;
      node.a1 = a1;
      node.a = (a0 + a1) / 2;
      node.depth = depth;
      node.r = depth < RING.length
        ? RING[depth]
        : RING[RING.length - 1] + (depth - RING.length + 1) * RING_STEP;
      node.x = Math.cos(node.a) * node.r;
      node.y = Math.sin(node.a) * node.r;

      const kids = node.children;
      if (!kids.length) return;

      const total = kids.reduce((s, k) => s + Math.max(1, k.count), 0);
      const span = a1 - a0;
      const base = depth <= 1 ? 0.34 : 0.12;
      const pad = (span * PAD_RATIO) / Math.max(1, kids.length - 1 || 1);
      let cur = a0;
      for (const child of kids) {
        const share = base / kids.length + (1 - base) * (Math.max(1, child.count) / total);
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
          c.pa = node.a;
          c.pr = node.r;
          c.domain = c.domain || node.path.split('/')[0];
          c.r = node.r + CARD_BASE + c._h * CARD_JITTER + (node.depth > 2 ? 16 : 0);
          c.x = Math.cos(c.a) * c.r;
          c.y = Math.sin(c.a) * c.r;
        });
      }
    }

    _buildGrid() {
      this.grid.clear();
      for (const c of this.cards) {
        const k = Math.floor(c.x / this.cell) + ',' + Math.floor(c.y / this.cell);
        if (!this.grid.has(k)) this.grid.set(k, []);
        this.grid.get(k).push(c);
      }
    }

    // ---------- 视口 ----------
    _resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.canvas.parentElement.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      this.w = rect.width;
      this.h = rect.height;
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.canvas.style.width = rect.width + 'px';
      this.canvas.style.height = rect.height + 'px';
      this.dpr = dpr;
    }

    fit(padding = 1.03) {
      if (!this.w || !this.h) return;
      let extent = 0;
      for (const n of this.nodes) if (n.r > extent) extent = n.r;
      for (const c of this.cards) if (c.r > extent) extent = c.r;
      extent += 70;
      const s = Math.min(this.w, this.h) / (extent * 2) / padding;
      this.view = { s: clamp(s, 0.05, 4), tx: this.w / 2, ty: this.h / 2 };
      this._fitted = true;
      this._fitW = this.w;
      this._fitH = this.h;
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

      let best = null;
      let bestD = Infinity;
      for (const n of this.nodes) {
        const d = Math.hypot(n.x - p.x, n.y - p.y);
        const hit = (n.depth === 0 ? 26 : 12) / this.view.s + 4;
        if (d < hit && d < bestD) { best = n; bestD = d; }
      }
      if (best) return { kind: 'node', node: best };

      const reach = Math.max(11, 9 / this.view.s);
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
        if (hit?.kind === 'node') this.zoomAt(1.9, e.clientX - rect.left, e.clientY - rect.top);
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

    // ---------- 动画 ----------
    _animate() {
      if (this._growing) return;
      this._growing = true;
      const start = performance.now();
      const dur = 850;
      const step = (t) => {
        const k = clamp((t - start) / dur, 0, 1);
        this.grow = 1 - Math.pow(1 - k, 3);
        this.draw();
        if (k < 1) requestAnimationFrame(step);
        else { this.grow = 1; this._growing = false; this.draw(); }
      };
      requestAnimationFrame(step);
    }

    // ---------- 绘制 ----------
    draw() {
      if (!this.w || !this.h) return;
      const ctx = this.ctx;
      const dpr = this.dpr;
      const dark = this.theme === 'dark';

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, this.w, this.h);

      this._drawPaper(dark);

      ctx.save();
      ctx.translate(this.view.tx, this.view.ty);
      ctx.scale(this.view.s, this.view.s);

      this._drawGraticule(dark);
      this._drawDensity(dark);
      this._drawEdges(dark);
      this._drawCards(dark);
      this._drawNodes(dark);

      ctx.restore();
      this._drawFooter(dark);
    }

    _drawPaper(dark) {
      const ctx = this.ctx;
      const cx = this.view.tx;
      const cy = this.view.ty;
      const r = Math.max(this.w, this.h) * 0.8;

      ctx.fillStyle = dark ? '#101416' : '#faf9f6';
      ctx.fillRect(0, 0, this.w, this.h);

      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      if (dark) {
        g.addColorStop(0, 'rgba(46,72,84,.34)');
        g.addColorStop(0.55, 'rgba(20,28,32,.16)');
        g.addColorStop(1, 'rgba(8,10,12,0)');
      } else {
        g.addColorStop(0, 'rgba(255,253,244,.95)');
        g.addColorStop(0.5, 'rgba(246,242,230,.55)');
        g.addColorStop(1, 'rgba(236,232,220,0)');
      }
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, this.w, this.h);
    }

    _drawGraticule(dark) {
      const ctx = this.ctx;
      const s = this.view.s;
      const fade = this.grow;
      const line = dark ? 'rgba(130,160,170,.13)' : 'rgba(66,103,121,.13)';

      ctx.lineWidth = 1 / s;
      ctx.setLineDash([2 / s, 7 / s]);
      for (let i = 1; i < RING.length; i++) {
        ctx.beginPath();
        ctx.arc(0, 0, RING[i], 0, TAU);
        ctx.strokeStyle = line;
        ctx.globalAlpha = fade;
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      // 最外圈刻度：每 6°，整 30° 加长
      const outer = RING[RING.length - 1] + 46;
      ctx.beginPath();
      for (let deg = 0; deg < 360; deg += 6) {
        const a = (deg * Math.PI) / 180;
        const long = deg % 30 === 0;
        const r0 = outer - (long ? 18 : 8);
        ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
        ctx.lineTo(Math.cos(a) * outer, Math.sin(a) * outer);
      }
      ctx.strokeStyle = line;
      ctx.globalAlpha = fade;
      ctx.stroke();
      ctx.globalAlpha = 1;

      // 四个方位短标注
      const dirs = [['N', -Math.PI / 2], ['E', 0], ['S', Math.PI / 2], ['W', Math.PI]];
      ctx.save();
      ctx.scale(1 / s, 1 / s);
      ctx.font = '9px Georgia, "Noto Serif CJK SC", serif';
      ctx.fillStyle = dark ? 'rgba(140,170,180,.42)' : 'rgba(66,103,121,.42)';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.globalAlpha = fade;
      for (const [ch, a] of dirs) {
        const rr = (outer + 14) * s;
        ctx.fillText(ch, Math.cos(a) * rr, Math.sin(a) * rr);
      }
      ctx.restore();
      ctx.globalAlpha = 1;
    }

    _drawDensity(dark) {
      const ctx = this.ctx;
      const fade = clamp((this.grow - 0.4) / 0.6, 0, 1);
      if (fade <= 0) return;

      for (const node of this.nodes) {
        if (node.depth < 2 || node.count < 4) continue;
        const a0 = node.a0 + 0.004;
        const a1 = node.a1 - 0.004;
        if (a1 <= a0) continue;
        const rr = node.r + 26;
        const w = clamp(Math.log10(node.count) * 4.2, 3, 15);
        const rgb = inkOf(node.path.split('/')[0], dark);

        ctx.beginPath();
        ctx.arc(0, 0, rr, a0, a1);
        ctx.arc(0, 0, rr + w, a1, a0, true);
        ctx.closePath();
        ctx.fillStyle = `rgba(${rgb.join(',')},${dark ? 0.12 : 0.1})`;
        ctx.globalAlpha = fade;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    _inScope(node) {
      if (!this.scope) return true;
      return node.path === this.scope || node.path.startsWith(this.scope + '/');
    }

    _drawEdges(dark) {
      const ctx = this.ctx;
      const s = this.view.s;
      const grow = this.grow;

      ctx.lineCap = 'round';

      for (const node of this.nodes) {
        if (!node.parent) continue;
        const p = node.parent;
        const active = this._inScope(node);
        const hovered = this.hover?.kind === 'node' && (this.hover.node === node || this.hover.node === p);

        const ex = p.x + (node.x - p.x) * grow;
        const ey = p.y + (node.y - p.y) * grow;
        const midR = (p.r + node.r * grow) / 2;
        const mx = Math.cos(node.a) * midR;
        const my = Math.sin(node.a) * midR;

        const baseW = clamp(4.2 - node.depth * 0.85, 0.6, 4.2);

        // 底影：稍宽一点、极淡，制造压痕
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.quadraticCurveTo(mx, my, ex, ey);
        ctx.strokeStyle = dark ? 'rgba(0,0,0,.28)' : 'rgba(120,112,92,.10)';
        ctx.lineWidth = baseW + 1.6 / s;
        ctx.stroke();

        // 主线
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.quadraticCurveTo(mx, my, ex, ey);
        ctx.strokeStyle = hovered
          ? dark ? 'rgba(226,240,244,.96)' : 'rgba(38,66,78,.96)'
          : active
            ? dark ? `rgba(160,198,208,${0.7 - node.depth * 0.07})` : `rgba(58,94,112,${0.78 - node.depth * 0.07})`
            : dark ? 'rgba(110,130,140,.14)' : 'rgba(120,116,104,.16)';
        ctx.lineWidth = baseW;
        ctx.stroke();
      }
    }

    _drawCards(dark) {
      const ctx = this.ctx;
      const s = this.view.s;
      const fade = clamp((this.grow - 0.55) / 0.45, 0, 1);
      if (fade <= 0 || s < 0.14) return;

      const radius = clamp(1.0 + s * 1.5, 0.85, 3.6);
      const groups = new Map();

      for (const c of this.cards) {
        const inScope = !this.scope || c.path === this.scope || c.path.startsWith(this.scope + '/');
        const key = (c.domain || 'other') + (inScope ? '' : '|d');
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(c);
      }

      let stride = s < 0.16 ? 4 : s < 0.24 ? 2 : 1;
      if (this._growing) stride *= 2;
      const batch = 400;
      const d = radius * 2;

      for (const [key, list] of groups) {
        const dim = key.endsWith('|d');
        const domain = key.replace('|d', '');
        const rgb = inkOf(domain, dark);
        ctx.fillStyle = dark
          ? `rgba(${rgb.join(',')},${dim ? 0.08 : 0.8})`
          : `rgba(${rgb.join(',')},${dim ? 0.1 : 0.74})`;
        ctx.globalAlpha = fade;

        for (let i = 0; i < list.length; i += batch * stride) {
          ctx.beginPath();
          for (let j = i; j < Math.min(i + batch * stride, list.length); j += stride) {
            const c = list[j];
            ctx.rect(c.x - radius, c.y - radius, d, d);
          }
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      }

      if (this.hover?.kind === 'card') {
        const c = this.hover.card;
        const rgb = inkOf(c.domain, dark);
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius * 3.2, 0, TAU);
        ctx.fillStyle = dark ? 'rgba(240,226,200,.92)' : 'rgba(166,111,81,.92)';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius * 7.5, 0, TAU);
        ctx.fillStyle = `rgba(${rgb.join(',')},.16)`;
        ctx.fill();
      }
    }

    _nodeRadius(node) {
      if (node.depth === 0) return 13;
      if (node.depth === 1) return clamp(3.6 + Math.sqrt(node.count) * 0.2, 4, 12);
      return clamp(3.4 - node.depth * 0.5, 1.6, 3.4);
    }

    _drawNodes(dark) {
      const ctx = this.ctx;
      const s = this.view.s;
      const grow = this.grow;
      const maxCount = Math.max(...this.nodes.filter((n) => n.depth === 1).map((n) => n.count), 1);

      // 节点本体
      for (const node of this.nodes) {
        const isRoot = node.depth === 0;
        const active = this._inScope(node);
        const isHover = this.hover?.kind === 'node' && this.hover.node === node;
        const base = this._nodeRadius(node);
        const appears = isRoot ? clamp(grow * 3, 0, 1) : clamp((grow - node.depth * 0.12) * 3, 0, 1);
        if (appears <= 0) continue;

        const nx = node.x * grow;
        const ny = node.y * grow;

        ctx.globalAlpha = appears;

        if (isRoot) {
          // 中心：三圈同心细环 + 实心点
          ctx.beginPath();
          ctx.arc(nx, ny, base * 2.9, 0, TAU);
          ctx.strokeStyle = dark ? 'rgba(160,190,198,.22)' : 'rgba(66,103,121,.22)';
          ctx.lineWidth = 1 / s;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(nx, ny, base * 1.9, 0, TAU);
          ctx.strokeStyle = dark ? 'rgba(160,190,198,.34)' : 'rgba(66,103,121,.32)';
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(nx, ny, base * 0.62, 0, TAU);
          ctx.fillStyle = dark ? 'rgba(240,226,200,.95)' : 'rgba(48,59,64,.9)';
          ctx.fill();
        } else {
          const rgb = inkOf(node.path.split('/')[0], dark);
          ctx.beginPath();
          ctx.arc(nx, ny, base, 0, TAU);
          ctx.fillStyle = isHover
            ? dark ? '#f2e3c8' : '#a66f51'
            : active
              ? `rgba(${rgb.join(',')},${dark ? 0.95 : 0.88})`
              : dark ? 'rgba(120,130,134,.22)' : 'rgba(130,126,114,.26)';
          ctx.fill();

          // 一级域：容量弧环
          if (node.depth === 1 && active) {
            const ringR = base + 5;
            const frac = node.count / maxCount;
            ctx.beginPath();
            ctx.arc(nx, ny, ringR, -Math.PI / 2, -Math.PI / 2 + TAU * frac);
            ctx.strokeStyle = `rgba(${rgb.join(',')},${isHover ? 0.85 : 0.42})`;
            ctx.lineWidth = 1.4 / s;
            ctx.stroke();
          }
        }
        ctx.globalAlpha = 1;
      }

      // 标签：反缩放，固定屏幕字号，按占位避让
      ctx.save();
      ctx.scale(1 / s, 1 / s);
      ctx.textBaseline = 'middle';

      const taken = [];
      const hits = (r) =>
        taken.some((t) => r.x < t.x + t.w && r.x + r.w > t.x && r.y < t.y + t.h && r.y + r.h > t.y);

      let order = 0;
      for (const node of this.nodes) {
        const isRoot = node.depth === 0;
        const active = this._inScope(node);
        const size = isRoot ? 15 : node.depth === 1 ? 11 : 9;

        const show =
          isRoot ||
          (node.depth === 1 && s > 0.2 && grow > 0.6) ||
          (node.depth === 2 && s > 0.6 && grow > 0.9) ||
          (node.depth >= 3 && s > 1.5 && grow > 0.9);
        if (!show) continue;

        const sx = node.x * grow * s;
        const sy = node.y * grow * s;
        const rb = this._nodeRadius(node) * s;
        let label;
        if (isRoot) label = 'Iris';
        else if (node.depth === 1) label = `${node.title || node.name} · ${node.count}`;
        else label = node.title || node.name;

        ctx.font = isRoot
          ? `italic 500 ${size}px Georgia, "Noto Serif CJK SC", serif`
          : `${node.depth === 1 ? 500 : 400} ${size}px ${node.depth === 1 ? 'Georgia, "Noto Serif CJK SC", serif' : '"Segoe UI","PingFang SC",sans-serif'}`;
        ctx.textAlign = 'center';
        ctx.fillStyle = isRoot
          ? dark ? 'rgba(238,228,204,.96)' : 'rgba(48,59,64,.94)'
          : active
            ? dark ? `rgba(214,228,232,${0.95 - node.depth * 0.06})` : `rgba(38,48,52,${0.95 - node.depth * 0.06})`
            : dark ? 'rgba(140,150,152,.22)' : 'rgba(132,128,116,.3)';

        const w = ctx.measureText(label).width;
        const h = size * 1.5;

        if (isRoot) {
          ctx.fillText(label, sx, sy - rb * 2.9 - size * 0.9);
          taken.push({ x: sx - w / 2, y: sy - rb * 2.9 - size * 1.7, w, h });
          continue;
        }

        if (node.depth > 1) {
          const ty = sy - rb - size * 0.95;
          ctx.fillText(label, sx, ty);
          taken.push({ x: sx - w / 2, y: ty - h / 2, w, h });
          continue;
        }

        // 一级域：统一标注在外圈环上，沿切向错开
        const lblR = 292 * grow;
        let placed = null;
        for (let k = 0; k < 18; k++) {
          const step = Math.ceil(k / 2) * 0.05 * (k % 2 ? 1 : -1);
          const aa = node.a + step;
          const px = Math.cos(aa) * lblR;
          const py = Math.sin(aa) * lblR;
          const align = Math.cos(aa) >= 0 ? 'left' : 'right';
          const off = align === 'left' ? 7 : -7;
          const box = { x: align === 'left' ? px + off : px + off - w, y: py - h / 2, w, h };
          if (!hits(box)) { placed = { px, py, align, off, box }; break; }
        }
        if (placed) {
          ctx.textAlign = placed.align;
          ctx.fillText(label, placed.px + placed.off, placed.py);
          taken.push(placed.box);

          // 引线：节点到标注
          ctx.beginPath();
          ctx.moveTo(sx + Math.cos(node.a) * (rb + 2), sy + Math.sin(node.a) * (rb + 2));
          ctx.lineTo(placed.px, placed.py);
          ctx.strokeStyle = dark ? 'rgba(130,160,170,.22)' : 'rgba(66,103,121,.2)';
          ctx.lineWidth = 0.8;
          ctx.stroke();

          ctx.font = `300 ${size * 0.72}px "Segoe UI",sans-serif`;
          ctx.fillStyle = dark ? 'rgba(150,168,174,.45)' : 'rgba(120,116,104,.5)';
        }
        order++;
      }

      ctx.restore();
    }

    _drawFooter(dark) {
      const ctx = this.ctx;
      ctx.save();
      ctx.font = '10.5px Georgia, "Noto Serif CJK SC", serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = dark ? 'rgba(140,160,168,.5)' : 'rgba(116,112,100,.62)';
      ctx.fillText(`${(this.view.s * 100).toFixed(0)}%`, this.w - 20, this.h - 24);
      ctx.restore();
    }
  }

  window.WorldTree = WorldTree;
})();

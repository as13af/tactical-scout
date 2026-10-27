/* ─────────────────────────────────────────────────────────────────────────────
   Scatter export — turns a live Chart.js scatter into a shareable PNG / PDF.

   The chart is re-rendered at a higher pixel ratio (so the current zoom, the
   highlight and the label plugins come out exactly as on screen), then
   composited with a header, filter chips, a legend, the highlighted entity's
   card and a footer onto one canvas painted in the page's theme colours.

   Usage:
     ScatterExport.download({
       chart, format: 'png' | 'pdf', filename,
       title, subtitle, chips: [..], legend: [{type, color, border, label}],
       card: null | { title, badge: {text, color}, meta,
                      blocks: [{label, value, sub, tag: {text, color}, note}],
                      table:  {columns: [..], align: [..], rows: [[..]]} },
       footerLeft, footerRight,
       copyright,                        // optional; defaults to <meta name="copyright">
       beforeCapture(), afterCapture()   // optional hooks around the hi-res render
     });
   ───────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  const SCALE  = 3;      // export pixel ratio
  const PAD    = 32;     // outer padding (CSS px)
  const JSPDF  = 'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js';

  // ── Theme ────────────────────────────────────────────────────────────────────
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }

  function readTheme() {
    return {
      bg:     cssVar('--bg',     '#0A0A0A'),
      bg3:    cssVar('--bg3',    '#161616'),
      text:   cssVar('--text',   '#F2F2F2'),
      text2:  cssVar('--text2',  '#ABABAB'),
      text3:  cssVar('--text3',  '#6B6B6B'),
      border: cssVar('--border', '#232323'),
      font:   getComputedStyle(document.body).fontFamily || 'sans-serif',
    };
  }

  // The app's notice, from <meta name="copyright"> set once in base.html
  function pageCopyright() {
    const m = document.querySelector('meta[name="copyright"]');
    return m ? m.content : '';
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  const _scripts = {};
  function loadScript(src) {
    if (!_scripts[src]) {
      _scripts[src] = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = () => { delete _scripts[src]; reject(new Error('Failed to load ' + src)); };
        document.head.appendChild(s);
      });
    }
    return _scripts[src];
  }

  function slug(s) {
    return String(s || '')
      .normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  // ['player_scatter', 'Liga 1', '2025/26'] → 'player_scatter_Liga_1_2025_26'
  function fileName(parts) {
    return parts.filter(Boolean).map(slug).filter(Boolean).join('_');
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // Split text into lines that fit maxW.
  function wrap(ctx, text, maxW) {
    const words = String(text).split(' ');
    const lines = [];
    let line = '';
    words.forEach(w => {
      const test = line ? line + ' ' + w : w;
      if (line && ctx.measureText(test).width > maxW) { lines.push(line); line = w; }
      else line = test;
    });
    if (line) lines.push(line);
    return lines;
  }

  // ── Chart capture ────────────────────────────────────────────────────────────
  // Re-render the live chart at SCALE, copy the bitmap, then restore it.
  function captureChart(chart, opts) {
    const prev = chart.options.devicePixelRatio;
    if (opts.beforeCapture) opts.beforeCapture();
    chart.options.devicePixelRatio = SCALE;
    chart.resize();
    chart.update('none');
    const out = document.createElement('canvas');
    out.width  = chart.canvas.width;
    out.height = chart.canvas.height;
    out.getContext('2d').drawImage(chart.canvas, 0, 0);
    if (opts.afterCapture) opts.afterCapture();
    chart.options.devicePixelRatio = prev;
    chart.resize();
    chart.update('none');
    return { img: out, w: chart.width, h: chart.height };
  }

  // ── Layout + paint ───────────────────────────────────────────────────────────
  // Every section is a {h, draw(ctx, y)} pair so heights are known before the
  // final canvas is sized.
  function buildSections(ctx, opts, t, W, shot) {
    const innerW = W - PAD * 2;
    const F = (size, weight) => (weight ? weight + ' ' : '') + size + 'px ' + t.font;
    const sections = [];

    // Title + subtitle
    ctx.font = F(22, 'bold');
    const titleLines = wrap(ctx, opts.title || '', innerW);
    ctx.font = F(13);
    const subLines = opts.subtitle ? wrap(ctx, opts.subtitle, innerW) : [];
    sections.push({
      h: titleLines.length * 28 + subLines.length * 19 + 4,
      draw(c, y) {
        c.fillStyle = t.text; c.font = F(22, 'bold');
        titleLines.forEach((l, i) => c.fillText(l, PAD, y + 22 + i * 28));
        y += titleLines.length * 28;
        c.fillStyle = t.text2; c.font = F(13);
        subLines.forEach((l, i) => c.fillText(l, PAD, y + 16 + i * 19));
      },
    });

    // Filter chips (pills that wrap)
    const chips = (opts.chips || []).filter(Boolean);
    if (chips.length) {
      ctx.font = F(11, '600');
      const placed = [];
      let cx = 0, row = 0;
      chips.forEach(ch => {
        const w = ctx.measureText(ch).width + 18;
        if (cx && cx + w > innerW) { cx = 0; row++; }
        placed.push({ text: ch, x: cx, row, w });
        cx += w + 6;
      });
      sections.push({
        h: (row + 1) * 28 + 4,
        draw(c, y) {
          c.font = F(11, '600');
          placed.forEach(p => {
            const px = PAD + p.x, py = y + 6 + p.row * 28;
            roundRect(c, px, py, p.w, 22, 11);
            c.fillStyle = t.bg3; c.fill();
            c.strokeStyle = t.border; c.lineWidth = 1; c.stroke();
            c.fillStyle = t.text2;
            c.fillText(p.text, px + 9, py + 15);
          });
        },
      });
    }

    // Legend
    const legend = opts.legend || [];
    if (legend.length) {
      ctx.font = F(12);
      const placed = [];
      let cx = 0, row = 0;
      legend.forEach(item => {
        const w = 22 + ctx.measureText(item.label).width + 18;
        if (cx && cx + w > innerW) { cx = 0; row++; }
        placed.push(Object.assign({ x: cx, row }, item));
        cx += w;
      });
      sections.push({
        h: (row + 1) * 22 + 8,
        draw(c, y) {
          c.font = F(12);
          placed.forEach(p => {
            const mx = PAD + p.x + 7, my = y + 14 + p.row * 22;
            c.save();
            if (p.type === 'dash') {
              c.setLineDash([4, 3]); c.strokeStyle = p.color; c.lineWidth = 1.5;
              c.beginPath(); c.moveTo(mx - 7, my); c.lineTo(mx + 9, my); c.stroke();
            } else if (p.type === 'cross') {
              c.strokeStyle = p.color; c.lineWidth = 2;
              c.beginPath(); c.moveTo(mx - 5, my - 5); c.lineTo(mx + 5, my + 5);
              c.moveTo(mx + 5, my - 5); c.lineTo(mx - 5, my + 5); c.stroke();
            } else {
              c.beginPath(); c.arc(mx, my, 6, 0, Math.PI * 2);
              c.fillStyle = p.color; c.fill();
              c.strokeStyle = p.border || p.color; c.lineWidth = 1.5; c.stroke();
            }
            c.restore();
            c.fillStyle = t.text2;
            c.fillText(p.label, PAD + p.x + 22, my + 4);
          });
        },
      });
    }

    // Chart
    const chartX = PAD + (innerW - shot.w) / 2;
    sections.push({
      h: shot.h + 16,
      draw(c, y) {
        roundRect(c, chartX - 8, y + 4, shot.w + 16, shot.h + 8, 8);
        c.strokeStyle = t.border; c.lineWidth = 1; c.stroke();
        c.drawImage(shot.img, chartX, y + 8, shot.w, shot.h);
      },
    });

    // Entity card
    if (opts.card) sections.push(cardSection(ctx, opts.card, t, F, innerW));

    // Footer: source line, then the copyright notice
    const copyright = opts.copyright !== undefined ? opts.copyright : pageCopyright();
    sections.push({
      h: copyright ? 52 : 34,
      draw(c, y) {
        c.strokeStyle = t.border; c.lineWidth = 1;
        c.beginPath(); c.moveTo(PAD, y + 8); c.lineTo(W - PAD, y + 8); c.stroke();
        c.font = F(11); c.fillStyle = t.text3;
        c.textAlign = 'left';  c.fillText(opts.footerLeft  || '', PAD, y + 26);
        c.textAlign = 'right'; c.fillText(opts.footerRight || '', W - PAD, y + 26);
        if (copyright) {
          c.font = F(10); c.textAlign = 'center';
          c.fillText(copyright, W / 2, y + 45);
        }
        c.textAlign = 'left';
      },
    });

    return sections;
  }

  function cardSection(ctx, card, t, F, innerW) {
    const CP = 16;                        // card padding
    const blocks = card.blocks || [];
    const table  = card.table  || null;
    const BW = 190, BH = 92;              // stat block size
    const perRow = Math.max(1, Math.floor((innerW - CP * 2) / BW));
    const blockRows = Math.ceil(blocks.length / perRow);
    const ROW_H = 22;
    const tableH = table ? (table.rows.length + 1) * ROW_H + 6 : 0;
    const head = 26 + (card.meta ? 20 : 0) + 8;
    const h = CP * 2 + head + blockRows * BH + tableH;

    return {
      h: h + 12,
      draw(c, y) {
        const x0 = PAD, y0 = y + 6;
        roundRect(c, x0, y0, innerW, h, 8);
        c.fillStyle = t.bg3; c.fill();
        c.strokeStyle = t.text3; c.lineWidth = 1; c.stroke();

        let cy = y0 + CP;
        c.font = F(16, 'bold'); c.fillStyle = t.text;
        c.fillText(card.title || '', x0 + CP, cy + 16);
        if (card.badge) {
          const tw = c.measureText(card.title || '').width;
          c.font = F(10, 'bold');
          const bw = c.measureText(card.badge.text).width + 12;
          const bx = x0 + CP + tw + 10;
          roundRect(c, bx, cy + 3, bw, 16, 3);
          c.fillStyle = card.badge.color; c.globalAlpha = 0.2; c.fill(); c.globalAlpha = 1;
          c.fillStyle = card.badge.color; c.fillText(card.badge.text, bx + 6, cy + 15);
        }
        cy += 26;
        if (card.meta) {
          c.font = F(12); c.fillStyle = t.text3;
          c.fillText(card.meta, x0 + CP, cy + 12);
          cy += 20;
        }
        cy += 8;

        blocks.forEach((b, i) => {
          const bx = x0 + CP + (i % perRow) * BW;
          const by = cy + Math.floor(i / perRow) * BH;
          c.font = F(10); c.fillStyle = t.text3;
          c.fillText(String(b.label || '').toUpperCase(), bx, by + 10);
          c.font = F(20, 'bold'); c.fillStyle = t.text;
          c.fillText(String(b.value), bx, by + 34);
          let ly = by + 50;
          if (b.sub) { c.font = F(11); c.fillStyle = t.text2; c.fillText(b.sub, bx, ly); ly += 16; }
          if (b.tag) {
            c.font = F(10, 'bold');
            const tw = c.measureText(b.tag.text.toUpperCase()).width + 14;
            roundRect(c, bx, ly - 10, tw, 15, 7);
            c.fillStyle = b.tag.color; c.globalAlpha = 0.15; c.fill(); c.globalAlpha = 1;
            c.fillStyle = b.tag.color; c.fillText(b.tag.text.toUpperCase(), bx + 7, ly + 1);
            ly += 18;
          }
          if (b.note) { c.font = F(11); c.fillStyle = t.text2; c.fillText(b.note, bx, ly); }
        });
        cy += blockRows * BH;

        if (table) {
          const cols = table.columns.length;
          const tw = Math.min(innerW - CP * 2, 640);
          const firstW = Math.max(tw * 0.4, tw - (cols - 1) * 140);
          const restW = cols > 1 ? (tw - firstW) / (cols - 1) : 0;
          const colX = i => x0 + CP + (i === 0 ? 0 : firstW + (i - 1) * restW);
          const drawRow = (cells, ry, header) => {
            cells.forEach((cell, i) => {
              const right = (table.align || [])[i] === 'right';
              c.textAlign = right ? 'right' : 'left';
              c.fillText(String(cell), right ? colX(i) + (i === 0 ? firstW : restW) : colX(i), ry);
            });
            c.textAlign = 'left';
          };
          c.font = F(10); c.fillStyle = t.text3;
          drawRow(table.columns.map(s => String(s).toUpperCase()), cy + 14, true);
          c.strokeStyle = t.border; c.lineWidth = 1;
          c.beginPath(); c.moveTo(x0 + CP, cy + 20); c.lineTo(x0 + CP + tw, cy + 20); c.stroke();
          c.font = F(12); c.fillStyle = t.text;
          table.rows.forEach((r, i) => drawRow(r, cy + 20 + (i + 1) * ROW_H - 6, false));
        }
      },
    };
  }

  function compose(opts, shot) {
    const t = readTheme();
    const W = Math.max(shot.w + PAD * 2, 720);
    const measure = document.createElement('canvas').getContext('2d');
    const sections = buildSections(measure, opts, t, W, shot);
    const H = PAD + sections.reduce((s, sec) => s + sec.h + 10, 0) + PAD / 2;

    const out = document.createElement('canvas');
    out.width  = Math.round(W * SCALE);
    out.height = Math.round(H * SCALE);
    const ctx = out.getContext('2d');
    ctx.scale(SCALE, SCALE);
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = t.bg;
    ctx.fillRect(0, 0, W, H);

    let y = PAD;
    sections.forEach(sec => { sec.draw(ctx, y); y += sec.h + 10; });
    return { canvas: out, w: W, h: H };
  }

  // ── Output ───────────────────────────────────────────────────────────────────
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  async function download(opts) {
    if (!opts || !opts.chart) return;
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const shot = captureChart(opts.chart, opts);
    const { canvas, w, h } = compose(opts, shot);
    const base = opts.filename || 'scatter';

    if (opts.format === 'pdf') {
      await loadScript(JSPDF);
      const { jsPDF } = window.jspdf;
      const pdf = new jsPDF({
        orientation: w >= h ? 'landscape' : 'portrait',
        unit: 'px', format: [w, h], hotfixes: ['px_scaling'],
      });
      pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, w, h, undefined, 'FAST');
      pdf.save(base + '.pdf');
      return;
    }
    await new Promise(resolve => canvas.toBlob(blob => { saveBlob(blob, base + '.png'); resolve(); }, 'image/png'));
  }

  // Local calendar date as YYYY-MM-DD (toISOString would give the UTC date)
  function today() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  window.ScatterExport = { download, fileName, today };
})();

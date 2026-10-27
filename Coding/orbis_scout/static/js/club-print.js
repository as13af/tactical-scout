/* ─────────────────────────────────────────────────────────────────────────────
   Club page → PDF (print-to-PDF).

   The club page loads most of its cards after first paint (form, passmap,
   heatmap, league rank) and hides two of its three overview cards inside a
   carousel, so a plain Ctrl+P prints placeholders and a third of the
   overview. ClubPrint.export() waits for every loader, adds the print-only
   extras (the 18-Zones heatmap, the full squad table, an "Exported" line),
   then opens the print dialog; club-print.css unrolls the carousel and
   strips the controls. Everything added is removed again on afterprint.

   Loaders register with PrintReady.track(promise) so the export knows when
   the page has finished filling in. Must load before the page's own scripts.

   The player page reuses the same flow: its ⬇ Export PDF button carries
   data-player-print, and it adds its own print-only extras through
   PrintReady.onExport(prepare, restore).
   ───────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  const WAIT_MS = 15000;   // never hang the export on a slow or failed loader

  // ── Readiness registry ───────────────────────────────────────────────────────
  const pending = new Set();
  const exportHooks = [];
  window.PrintReady = {
    // Returns the promise unchanged so it can wrap an existing chain in place.
    track(p) {
      const q = Promise.resolve(p).catch(() => {}).finally(() => pending.delete(q));
      pending.add(q);
      return p;
    },
    // Loaders can start other loaders (form → passmap), so keep waiting until
    // nothing new was registered while the last batch settled.
    async all() {
      while (pending.size) await Promise.all([...pending]);
    },
    // Page-specific extras. prepare(added) runs once the loaders have settled;
    // nodes it pushes onto `added` are removed after printing, and it may
    // return images to wait for. restore() undoes anything else it changed.
    onExport(prepare, restore) {
      exportHooks.push({ prepare, restore });
    },
  };

  function withTimeout(p, ms) {
    return Promise.race([p, new Promise(r => setTimeout(r, ms))]);
  }

  function imgReady(img) {
    if (img.complete) return Promise.resolve();
    return new Promise(r => {
      img.addEventListener('load', r, { once: true });
      img.addEventListener('error', r, { once: true });
    });
  }

  // ── Naming (same scheme as scatter-export.js) ────────────────────────────────
  function slug(s) {
    return String(s || '')
      .normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }
  function today() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // ── Print-only extras ────────────────────────────────────────────────────────
  function el(tag, cls, html) {
    const n = document.createElement(tag);
    n.className = 'print-only ' + (cls || '');
    if (html != null) n.innerHTML = html;
    return n;
  }

  // Both heatmap versions, labelled. The on-screen image shows whichever one
  // the toggle last picked, so it is hidden while printing and replaced.
  function addHeatmaps(added) {
    const urls = window.__clubHeatmapUrls;
    const wrap = document.getElementById('heatmap-wrap');
    if (!urls || !wrap) return [];
    const box = el('div', 'print-heatmaps');
    const imgs = [];
    [['Standard', urls.standard], ['18-Zones', urls.zones]].forEach(([label, src]) => {
      if (!src) return;
      const fig = document.createElement('figure');
      fig.innerHTML = `<figcaption>${label}</figcaption>`;
      const img = document.createElement('img');
      img.alt = `Average Team Heatmap — ${label}`;
      img.src = src;
      fig.appendChild(img);
      box.appendChild(fig);
      imgs.push(img);
    });
    wrap.after(box);
    added.push(box);
    return imgs;
  }

  function addSquad(added) {
    if (typeof window.buildSquadPrintTable !== 'function') return;
    const table = window.buildSquadPrintTable();
    const screenCard = document.getElementById('squad-card');
    if (!table || !screenCard) return;
    const section = el('div', 'card print-squad-section');
    section.appendChild(table);
    const notice = document.querySelector('meta[name="copyright"]');
    if (notice && notice.content) {
      const c = document.createElement('div');
      c.className = 'print-copyright';
      c.textContent = notice.content;
      section.appendChild(c);
    }
    screenCard.after(section);
    added.push(section);
  }

  function addMeta(added, btn) {
    const header = document.getElementById('club-page-header') || document.getElementById('player-page-header');
    const host = header && header.querySelector('h1')?.parentElement;
    if (!host) return;
    const parts = ['Exported ' + today(), btn.dataset.competition, btn.dataset.season].filter(Boolean);
    const line = el('div', 'sub print-meta', parts.map(p => p.replace(/_/g, ' ')).join(' · '));
    host.appendChild(line);
    added.push(line);
  }

  // ── Export ───────────────────────────────────────────────────────────────────
  let busy = false;

  async function exportPdf(btn) {
    if (busy) return;
    busy = true;
    const label = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Preparing PDF…';
    const added = [];
    const prevTitle = document.title;

    const cleanup = () => {
      added.forEach(n => n.remove());
      exportHooks.forEach(h => { if (h.restore) h.restore(); });
      document.documentElement.classList.remove('club-printing');
      document.title = prevTitle;
      btn.innerHTML = label;
      btn.disabled = false;
      busy = false;
    };

    try {
      await withTimeout(window.PrintReady.all(), WAIT_MS);

      const extraImgs = addHeatmaps(added);
      addSquad(added);
      addMeta(added, btn);
      exportHooks.forEach(h => extraImgs.push(...(h.prepare(added) || [])));

      const container = document.querySelector('.container') || document.body;
      const imgs = [...container.querySelectorAll('img')].filter(i => i.getAttribute('src'));
      await withTimeout(Promise.all(imgs.concat(extraImgs).map(imgReady)), WAIT_MS);
      if (document.fonts && document.fonts.ready) await document.fonts.ready;

      document.documentElement.classList.add('club-printing');
      // The browser offers document.title as the PDF's file name.
      document.title = [btn.dataset.club || btn.dataset.name, btn.dataset.competition, btn.dataset.season, today()]
        .filter(Boolean).map(slug).filter(Boolean).join('_');

      window.addEventListener('afterprint', cleanup, { once: true });
      window.print();
    } catch (e) {
      console.error('[club-print]', e);
      cleanup();
    }
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-club-print], [data-player-print]');
    if (btn) { e.preventDefault(); exportPdf(btn); }
  });

  window.ClubPrint = { export: exportPdf };
})();

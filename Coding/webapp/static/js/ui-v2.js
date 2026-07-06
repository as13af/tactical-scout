/* ═══════════════════════════════════════════════════════════════════════════
   ui-v2.js — reusable UI behaviours for the v2 interface
   ---------------------------------------------------------------------------
   Carousel: auto-initialises any element marked `data-carousel`. Its direct
   "slides" are children matching `.stat-pane` or `[data-carousel-slide]`; each
   slide may carry a `data-label` used for the title + dot aria-labels.

   Built controls: ‹ / › arrows (disabled at the ends — no wrap), a title that
   reflects the current slide, and position dots. Supports keyboard (←/→ when
   focused) and pointer swipe / drag. Honors prefers-reduced-motion.

   Safe to load on every page — it does nothing unless a `data-carousel`
   element exists. Re-runnable via window.initCarousels().
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  function prefersReducedMotion() {
    return window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function initCarousel(root) {
    if (root.__carouselInit) return;
    root.__carouselInit = true;

    var slides = Array.prototype.slice.call(
      root.querySelectorAll(':scope > .stat-pane, :scope > [data-carousel-slide]')
    );
    // Fallback for browsers without :scope support
    if (slides.length === 0) {
      slides = Array.prototype.slice.call(
        root.querySelectorAll('.stat-pane, [data-carousel-slide]')
      ).filter(function (el) { return el.parentNode === root; });
    }
    if (slides.length === 0) return;

    // ── Build structure ──────────────────────────────────────────────────────
    var viewport = document.createElement('div');
    viewport.className = 'carousel-viewport';
    var track = document.createElement('div');
    track.className = 'carousel-track';
    slides.forEach(function (s) {
      s.classList.add('carousel-slide');
      track.appendChild(s);
    });
    viewport.appendChild(track);

    var head = document.createElement('div');
    head.className = 'carousel-head';

    var prev = document.createElement('button');
    prev.type = 'button';
    prev.className = 'carousel-arrow carousel-prev';
    prev.setAttribute('aria-label', 'Previous');
    prev.innerHTML = '‹';

    var title = document.createElement('div');
    title.className = 'carousel-title';

    var next = document.createElement('button');
    next.type = 'button';
    next.className = 'carousel-arrow carousel-next';
    next.setAttribute('aria-label', 'Next');
    next.innerHTML = '›';

    var dots = document.createElement('div');
    dots.className = 'carousel-dots';

    // Some carousels (e.g. club cards) keep each slide's own header, so the
    // shared head only needs arrows + dots — hide the redundant title.
    if (root.hasAttribute('data-carousel-notitle')) title.style.display = 'none';

    head.appendChild(prev);
    head.appendChild(title);
    head.appendChild(next);
    head.appendChild(dots);

    var dotEls = slides.map(function (s, i) {
      var d = document.createElement('button');
      d.type = 'button';
      d.className = 'carousel-dot';
      d.setAttribute('aria-label', s.getAttribute('data-label') || ('Slide ' + (i + 1)));
      d.addEventListener('click', function () { go(i); });
      dots.appendChild(d);
      return d;
    });

    root.innerHTML = '';
    root.appendChild(head);
    root.appendChild(viewport);

    var single = slides.length < 2;
    if (single) {
      prev.style.display = 'none';
      next.style.display = 'none';
      dots.style.display = 'none';
    }

    // ── State + rendering ────────────────────────────────────────────────────
    var index = 0;

    function syncHeight() {
      // Lock viewport height to the active slide so shorter categories don't
      // leave a tall gap and switching glides between heights.
      viewport.style.height = slides[index].offsetHeight + 'px';
    }

    function render(animate) {
      var noMotion = !animate || prefersReducedMotion();
      if (noMotion) track.style.transition = 'none';
      track.style.transform = 'translateX(' + (-index * 100) + '%)';
      if (noMotion) {
        // Force reflow then restore so the *next* move animates.
        void track.offsetWidth;
        track.style.transition = '';
      }
      title.textContent = slides[index].getAttribute('data-label') || '';
      dotEls.forEach(function (d, i) { d.classList.toggle('active', i === index); });
      prev.disabled = index === 0;
      next.disabled = index === slides.length - 1;
      syncHeight();
    }

    function go(i, animate) {
      i = Math.max(0, Math.min(slides.length - 1, i));
      if (i === index && animate !== false) { syncHeight(); return; }
      index = i;
      render(animate !== false);
    }

    prev.addEventListener('click', function () { go(index - 1); });
    next.addEventListener('click', function () { go(index + 1); });

    // ── Keyboard (←/→ when the carousel has focus) ───────────────────────────
    root.setAttribute('tabindex', '0');
    root.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft')  { e.preventDefault(); go(index - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(index + 1); }
    });

    // ── Pointer swipe / drag ─────────────────────────────────────────────────
    if (!single) {
      var startX = 0, startY = 0, dragging = false, locked = false, vpWidth = 0;

      viewport.addEventListener('pointerdown', function (e) {
        if (e.button && e.button !== 0) return;
        dragging = true; locked = false;
        startX = e.clientX; startY = e.clientY;
        vpWidth = viewport.clientWidth || 1;
        track.classList.add('dragging');
      });

      viewport.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var dx = e.clientX - startX;
        var dy = e.clientY - startY;
        if (!locked) {
          // Ignore mostly-vertical gestures so the page can still scroll.
          if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 8) {
            dragging = false; track.classList.remove('dragging'); return;
          }
          if (Math.abs(dx) > 6) {
            locked = true;
            try { viewport.setPointerCapture(e.pointerId); } catch (err) {}
          } else { return; }
        }
        var pct = (dx / vpWidth) * 100;
        // Light resistance past the ends.
        if ((index === 0 && dx > 0) || (index === slides.length - 1 && dx < 0)) pct *= 0.35;
        track.style.transition = 'none';
        track.style.transform = 'translateX(' + (-index * 100 + pct) + '%)';
      });

      function endDrag(e) {
        if (!dragging) return;
        dragging = false;
        track.classList.remove('dragging');
        track.style.transition = '';
        var dx = (e.clientX || startX) - startX;
        var threshold = (viewport.clientWidth || 1) * 0.18;
        if (dx <= -threshold) go(index + 1);
        else if (dx >= threshold) go(index - 1);
        else go(index);           // snap back
      }
      viewport.addEventListener('pointerup', endDrag);
      viewport.addEventListener('pointercancel', endDrag);
    }

    // ── Re-sync height when the active slide's own content changes ───────────
    // (e.g. the player page's "vs Position Avg" toggle adds a line to each row).
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].target === slides[index]) { syncHeight(); break; }
        }
      });
      slides.forEach(function (s) { ro.observe(s); });
    }

    // ── Init + keep height correct on resize ─────────────────────────────────
    requestAnimationFrame(function () { render(false); });
    // A second pass once fonts/tables have settled.
    window.addEventListener('load', function () { render(false); });

    var resizeTimer = null;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        track.style.transition = 'none';
        syncHeight();
        track.style.transform = 'translateX(' + (-index * 100) + '%)';
        void track.offsetWidth;
        track.style.transition = '';
      }, 120);
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     Password show / hide toggle
     Enhances any <input type="password" data-pw-toggle>: wraps it and adds an
     eye button that flips the field between password and text.
     ═══════════════════════════════════════════════════════════════════════ */
  var EYE_OPEN  = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_SHUT  = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-7 0-11-7-11-7a18.4 18.4 0 0 1 5.1-5.9m3.3-1.6A10.4 10.4 0 0 1 12 5c7 0 11 7 11 7a18.5 18.5 0 0 1-2.2 3.2M1 1l22 22"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  function initPasswordToggles() {
    var inputs = document.querySelectorAll('input[type="password"][data-pw-toggle]');
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.__pwToggle) return;
      input.__pwToggle = true;

      var wrap = document.createElement('div');
      wrap.className = 'pw-wrap';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pw-toggle';
      btn.setAttribute('aria-label', 'Show password');
      btn.innerHTML = EYE_OPEN;
      wrap.appendChild(btn);

      btn.addEventListener('click', function () {
        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.innerHTML = show ? EYE_SHUT : EYE_OPEN;
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        input.focus();
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     Table keyboard navigation
     For any <table data-keynav>: ↑/↓ move a "focused" row, Enter activates it
     (clicks a link inside, or the row itself). Makes long squad / ranking
     tables keyboard-friendly. Rows opt out with .no-keynav.
     ═══════════════════════════════════════════════════════════════════════ */
  function initTableKeyNav() {
    var tables = document.querySelectorAll('table[data-keynav]');
    Array.prototype.forEach.call(tables, function (table) {
      if (table.__keynav) return;
      table.__keynav = true;
      table.setAttribute('tabindex', '0');

      function rows() {
        return Array.prototype.filter.call(
          table.querySelectorAll('tbody > tr'),
          function (r) { return !r.classList.contains('no-keynav') && r.offsetParent !== null; }
        );
      }
      function focusedIndex(rs) {
        for (var i = 0; i < rs.length; i++) {
          if (rs[i].classList.contains('keynav-focus')) return i;
        }
        return -1;
      }
      function setFocus(rs, i) {
        rs.forEach(function (r) { r.classList.remove('keynav-focus'); });
        if (i >= 0 && i < rs.length) {
          rs[i].classList.add('keynav-focus');
          rs[i].scrollIntoView({ block: 'nearest' });
        }
      }

      table.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Enter') return;
        var rs = rows();
        if (!rs.length) return;
        var i = focusedIndex(rs);
        if (e.key === 'ArrowDown') { e.preventDefault(); setFocus(rs, Math.min(i + 1, rs.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setFocus(rs, Math.max(i - 1, 0)); }
        else if (e.key === 'Enter' && i >= 0) {
          e.preventDefault();
          var link = rs[i].querySelector('a[href]');
          if (link) link.click(); else rs[i].click();
        }
      });
    });
  }

  function initAll() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-carousel]'), initCarousel);
    initPasswordToggles();
    initTableKeyNav();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
  window.initCarousels = initAll;
  window.initUIv2 = initAll;
})();

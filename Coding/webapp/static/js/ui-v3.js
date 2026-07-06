/* ── Tactical Manuscript — ui-v3.js ─────────────────────────────────────── */

/* 1. Animated fill bars on scroll into view */
(function () {
  function animateBars() {
    var els = document.querySelectorAll('.comp-bar-fill[data-fill], .rating-bar-fill[data-fill]');
    if (!els.length) return;

    if (!('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.style.width = el.dataset.fill || '0%'; });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var el = entry.target;
          el.style.width = el.dataset.fill || '0%';
          observer.unobserve(el);
        }
      });
    }, { threshold: 0.1 });

    els.forEach(function (el) {
      el.style.width = '0%';
      observer.observe(el);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', animateBars);
  } else {
    animateBars();
  }
})();

/* 2. Sidebar collapse toggle */
(function () {
  function initSidebar() {
    var btn = document.getElementById('sidebarToggle');
    if (!btn) return;
    var shell = document.querySelector('.page-with-sidebar');
    if (!shell) return;

    if (localStorage.getItem('tm-sidebar-collapsed') === '1') {
      shell.classList.add('sidebar-collapsed');
    }

    btn.addEventListener('click', function () {
      shell.classList.toggle('sidebar-collapsed');
      localStorage.setItem('tm-sidebar-collapsed',
        shell.classList.contains('sidebar-collapsed') ? '1' : '0');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSidebar);
  } else {
    initSidebar();
  }
})();

/* 3. Nav search expand / collapse */
(function () {
  function initSearch() {
    var toggleBtn = document.getElementById('navSearchToggle');
    var wrapper   = document.getElementById('navSearchWrapper');
    var input     = document.getElementById('navSearch');
    if (!toggleBtn || !wrapper || !input) return;

    toggleBtn.addEventListener('click', function () {
      wrapper.style.display = 'block';
      toggleBtn.style.display = 'none';
      input.focus();
    });

    input.addEventListener('blur', function () {
      if (input.value.trim() === '') {
        setTimeout(function () {
          wrapper.style.display = 'none';
          toggleBtn.style.display = 'flex';
        }, 150);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSearch);
  } else {
    initSearch();
  }
})();

/* 4. Carousel (stat categories on player / club pages) — carried from ui-v2.js */
(function () {
  function buildCarousel(card, opts) {
    opts = opts || {};
    var slides = Array.from(card.querySelectorAll('.carousel-slide'));
    if (slides.length < 2) return;

    var noTitle = card.hasAttribute('data-carousel-notitle');
    var current = 0;

    /* ── build header ── */
    var head = document.createElement('div');
    head.className = 'carousel-head';

    var prevBtn = document.createElement('button');
    prevBtn.className = 'carousel-arrow';
    prevBtn.innerHTML = '&#8249;';
    prevBtn.setAttribute('aria-label', 'Previous');

    var title = document.createElement('span');
    title.className = 'carousel-title';

    var dots = document.createElement('div');
    dots.className = 'carousel-dots';

    var dotEls = slides.map(function (_, i) {
      var d = document.createElement('button');
      d.className = 'carousel-dot' + (i === 0 ? ' active' : '');
      d.setAttribute('aria-label', 'Slide ' + (i + 1));
      d.addEventListener('click', function () { goTo(i); });
      dots.appendChild(d);
      return d;
    });

    var nextBtn = document.createElement('button');
    nextBtn.className = 'carousel-arrow';
    nextBtn.innerHTML = '&#8250;';
    nextBtn.setAttribute('aria-label', 'Next');

    if (!noTitle) head.appendChild(prevBtn);
    if (!noTitle) head.appendChild(title);
    head.appendChild(dots);
    if (!noTitle) head.appendChild(nextBtn);
    card.insertBefore(head, card.firstChild);

    /* ── build viewport / track ── */
    var vp = document.createElement('div');
    vp.className = 'carousel-viewport';
    var track = document.createElement('div');
    track.className = 'carousel-track';
    slides.forEach(function (s) { track.appendChild(s); });
    vp.appendChild(track);
    card.appendChild(vp);

    function syncHeight() {
      vp.style.height = slides[current].offsetHeight + 'px';
    }

    function goTo(idx) {
      current = (idx + slides.length) % slides.length;
      track.style.transform = 'translateX(-' + (current * 100) + '%)';
      if (!noTitle) title.textContent = slides[current].dataset.label || '';
      dotEls.forEach(function (d, i) { d.classList.toggle('active', i === current); });
      if (prevBtn) prevBtn.disabled = current === 0;
      if (nextBtn) nextBtn.disabled = current === slides.length - 1;
      setTimeout(syncHeight, 0);
    }

    if (prevBtn) prevBtn.addEventListener('click', function () { goTo(current - 1); });
    if (nextBtn) nextBtn.addEventListener('click', function () { goTo(current + 1); });

    /* touch/drag */
    var startX = 0;
    track.addEventListener('pointerdown', function (e) { startX = e.clientX; track.classList.add('dragging'); track.setPointerCapture(e.pointerId); });
    track.addEventListener('pointerup', function (e) {
      var dx = e.clientX - startX;
      track.classList.remove('dragging');
      if (Math.abs(dx) > 40) goTo(dx < 0 ? current + 1 : current - 1);
      else goTo(current);
    });

    /* keyboard */
    card.setAttribute('tabindex', '0');
    card.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft')  { goTo(current - 1); e.preventDefault(); }
      if (e.key === 'ArrowRight') { goTo(current + 1); e.preventDefault(); }
    });

    goTo(0);
    window.addEventListener('resize', syncHeight);
  }

  function initCarousels() {
    document.querySelectorAll('.stat-carousel, .cards-carousel').forEach(function (card) {
      buildCarousel(card);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCarousels);
  } else {
    initCarousels();
  }
})();

/* 5. Password show / hide */
(function () {
  function initPwToggles() {
    document.querySelectorAll('input[type="password"]').forEach(function (inp) {
      if (inp.closest('.pw-wrap')) return;
      var wrap = document.createElement('div');
      wrap.className = 'pw-wrap';
      inp.parentNode.insertBefore(wrap, inp);
      wrap.appendChild(inp);
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pw-toggle';
      btn.setAttribute('aria-label', 'Show password');
      btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
      wrap.appendChild(btn);
      btn.addEventListener('click', function () {
        var show = inp.type === 'password';
        inp.type = show ? 'text' : 'password';
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPwToggles);
  } else {
    initPwToggles();
  }
})();

/* 6. Table keyboard navigation (data-keynav) */
(function () {
  function initKeyNav() {
    document.querySelectorAll('table[data-keynav]').forEach(function (table) {
      table.setAttribute('tabindex', '0');
      var rows = Array.from(table.querySelectorAll('tbody tr'));
      var cur = -1;

      function setFocus(idx) {
        if (cur >= 0 && rows[cur]) rows[cur].classList.remove('keynav-focus');
        cur = Math.max(0, Math.min(idx, rows.length - 1));
        if (rows[cur]) { rows[cur].classList.add('keynav-focus'); rows[cur].scrollIntoView({ block: 'nearest' }); }
      }

      table.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown')  { setFocus(cur + 1); e.preventDefault(); }
        if (e.key === 'ArrowUp')    { setFocus(cur - 1); e.preventDefault(); }
        if (e.key === 'Enter' && cur >= 0) {
          var link = rows[cur].querySelector('a');
          if (link) link.click();
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initKeyNav);
  } else {
    initKeyNav();
  }
})();

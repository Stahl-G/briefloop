/* Standalone report: progressive enhancement only. Everything below must be
   removable — the document stays fully readable and printable without it. */
(function () {
  'use strict';
  var manifest = {};
  try {
    manifest = JSON.parse(document.getElementById('briefloop-manifest').textContent);
  } catch (e) { manifest = {}; }

  /* --- TOC: wide screens keep it open; collapse it on narrow ones --- */
  var tocDetails = document.getElementById('toc-details');
  if (tocDetails && window.matchMedia && matchMedia('(max-width: 1099px)').matches) {
    tocDetails.removeAttribute('open');
  }

  /* --- TOC scrollspy --- */
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll('.toc-list a[href^="#"]'));
  var targets = tocLinks
    .map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); })
    .filter(Boolean);
  if ('IntersectionObserver' in window && targets.length) {
    var current = null;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        if (current) current.classList.remove('active');
        var link = tocLinks[targets.indexOf(entry.target)];
        if (link) { link.classList.add('active'); current = link; }
      });
    }, { rootMargin: '0px 0px -70% 0px' });
    targets.forEach(function (t) { observer.observe(t); });
  }

  /* --- citation popover --- */
  var popover = null;
  var viewEntry = document.body.getAttribute('data-view-entry') || 'View appendix entry';
  function closePopover() {
    if (popover) { popover.remove(); popover = null; }
  }
  function line(cls, text) {
    if (!text) return null;
    var el = document.createElement('div');
    el.className = cls;
    el.textContent = text;
    return el;
  }
  function openPopover(anchor) {
    closePopover();
    var box = document.createElement('div');
    box.className = 'cite-popover';
    box.setAttribute('role', 'dialog');
    var n = anchor.getAttribute('data-ref');
    box.id = 'cite-pop-' + n;
    [line('pop-title', anchor.getAttribute('data-title')),
     line('pop-domain', anchor.getAttribute('data-domain')),
     line('pop-locator', anchor.getAttribute('data-locator')),
     line('pop-excerpt', anchor.getAttribute('data-excerpt'))]
      .forEach(function (el) { if (el) box.appendChild(el); });
    var link = document.createElement('a');
    link.href = '#ref-' + n;
    link.textContent = viewEntry;
    box.appendChild(link);
    document.body.appendChild(box);
    var rect = anchor.getBoundingClientRect();
    var top = rect.bottom + window.scrollY;
    var left = Math.min(rect.left + window.scrollX,
                        window.scrollX + document.documentElement.clientWidth - 330);
    box.style.top = top + 'px';
    box.style.left = Math.max(4, left) + 'px';
    box.addEventListener('mouseleave', function (e) {
      if (!(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('a.cite'))) {
        closePopover();
      }
    });
    anchor.setAttribute('aria-describedby', box.id);
    popover = box;
  }
  document.addEventListener('mouseover', function (e) {
    var a = e.target.closest ? e.target.closest('a.cite') : null;
    if (a) openPopover(a);
  });
  document.addEventListener('focusin', function (e) {
    var a = e.target.closest ? e.target.closest('a.cite') : null;
    if (a) openPopover(a);
  });
  document.addEventListener('focusout', function (e) {
    if (e.target.closest && e.target.closest('a.cite')) closePopover();
  });
  document.addEventListener('mouseout', function (e) {
    if (e.target.closest && e.target.closest('a.cite') &&
        !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.cite-popover'))) {
      closePopover();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closePopover();
  });
  document.addEventListener('click', function (e) {
    if (popover && !(e.target.closest && e.target.closest('.cite-popover, a.cite'))) closePopover();
  });

  /* --- clicking a reference entry highlights the citing paragraphs --- */
  document.addEventListener('click', function (e) {
    var item = e.target.closest ? e.target.closest('.ref-list > li[id^="ref-"]') : null;
    if (!item || (e.target.closest && e.target.closest('a'))) return;
    var n = item.id.slice(4);
    var stale = document.querySelectorAll('.cite-hit');
    Array.prototype.forEach.call(stale, function (el) { el.classList.remove('cite-hit'); });
    document.querySelectorAll('a.cite[data-ref="' + n + '"]').forEach(function (a) {
      var host = a.closest('p,li,h1,h2,h3,h4,h5,h6,blockquote,td,th');
      if (host) host.classList.add('cite-hit');
    });
  });
})();

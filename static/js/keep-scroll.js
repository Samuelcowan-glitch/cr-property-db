/* Stay where you were.
 *
 * Moving a photograph with the arrows, setting a cover image or uploading one
 * all post a form and reload the page, which lands you back at the top. On a
 * long project record the photographs are a long way down, so reordering six
 * of them meant scrolling back six times.
 *
 * The position is written down before the form goes, and put back when the
 * page returns. It is keyed to the path, so it only ever restores the page it
 * was taken on, and it is cleared as soon as it is used — coming back to the
 * same record later starts at the top, as you would expect.
 *
 * Applies to any form inside an element marked data-keep-scroll, and to any
 * form carrying that attribute itself. Nothing else on the page is affected.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  var KEY = 'cr-scroll';

  function store() {
    try {
      sessionStorage.setItem(KEY, JSON.stringify({
        path: location.pathname,
        y: window.scrollY || document.documentElement.scrollTop || 0,
        at: Date.now()
      }));
    } catch (e) { /* private browsing — the jump is a nuisance, not a fault */ }
  }

  function restore() {
    var raw;
    try {
      raw = sessionStorage.getItem(KEY);
      sessionStorage.removeItem(KEY);      /* once only */
    } catch (e) { return; }
    if (!raw) { return; }

    var saved;
    try { saved = JSON.parse(raw); } catch (e) { return; }
    if (!saved || saved.path !== location.pathname) { return; }
    /* Stale entries are ignored: this is for a round trip, not a session. */
    if (!saved.at || Date.now() - saved.at > 30000) { return; }
    if (!saved.y) { return; }

    /* The browser restores its own position first, and images loading below
       shift the page, so the position is reasserted for a moment afterwards. */
    var until = Date.now() + 1200;
    (function settle() {
      window.scrollTo(0, saved.y);
      if (Date.now() < until) { window.requestAnimationFrame(settle); }
    }());
  }

  function wire() {
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form || form.tagName !== 'FORM') { return; }
      if (form.matches('[data-keep-scroll]') || form.closest('[data-keep-scroll]')) {
        store();
      }
    }, true);

    /* A button can post a form that lives elsewhere on the page, so the click
       is watched too — the form= attribute means closest() would miss it. */
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('button[type=submit], input[type=submit]');
      if (!btn) { return; }
      if (btn.matches('[data-keep-scroll]') || btn.closest('[data-keep-scroll]')) {
        store();
      }
    }, true);

    restore();
  }

  /* Restoring needs the page laid out, so it waits for load rather than
     DOMContentLoaded — otherwise the height is not yet what it will be. */
  if (document.readyState === 'complete') {
    wire();
  } else {
    window.addEventListener('load', wire);
    document.addEventListener('DOMContentLoaded', function () {
      document.addEventListener('submit', function (e) {
        var form = e.target;
        if (form && form.tagName === 'FORM' &&
            (form.matches('[data-keep-scroll]') || form.closest('[data-keep-scroll]'))) {
          store();
        }
      }, true);
    });
  }
}());

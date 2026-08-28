/* Choosing and arranging the photographs for a set of particulars.
 *
 * Dragging changes the order they appear in; unticking leaves one out. The
 * order in the page is the order sent, so what you arrange is what you get.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  /* The key terms chosen for this document. Dragging sets the order the
     bullets print in; the cap is the same one the server applies. */
  function wireTerms(list) {
    var MAX = 6;
    var dragged = null;

    function rows() {
      return Array.prototype.slice.call(list.querySelectorAll('[data-pt-term]'));
    }

    function mark() {
      var n = 0;
      rows().forEach(function (row) {
        var box = row.querySelector('input[type=checkbox]');
        var over = box && box.checked && n >= MAX;
        if (box && box.checked) { n += 1; }
        row.classList.toggle('is-over', Boolean(over));
      });
      var warn = document.querySelector('[data-pt-terms-warn]');
      if (warn) { warn.hidden = n <= MAX; }
    }

    list.addEventListener('dragstart', function (e) {
      var row = e.target.closest('[data-pt-term]');
      if (!row) { return; }
      dragged = row;
      row.classList.add('is-dragging');
      if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; }
    });
    list.addEventListener('dragover', function (e) {
      if (!dragged) { return; }
      e.preventDefault();
      var over = e.target.closest('[data-pt-term]');
      if (!over || over === dragged) { return; }
      var box = over.getBoundingClientRect();
      list.insertBefore(dragged, (e.clientY - box.top) > box.height / 2
                                 ? over.nextSibling : over);
    });
    list.addEventListener('dragend', function () {
      if (dragged) { dragged.classList.remove('is-dragging'); }
      dragged = null;
      mark();
    });
    list.addEventListener('change', mark);
    mark();
  }

  function wire(grid) {
    var dragged = null;

    function tiles() {
      return Array.prototype.slice.call(grid.querySelectorAll('[data-pt-photo]'));
    }

    /* Where a photograph lands, not merely what number it is. These match
       the server's own split: one cover, three on page two, then page three. */
    var COVER = 1, DETAIL = 3, GALLERY = 6;

    function pages() {
      var four = document.querySelector('input[name=pages][value="4"]');
      return (four && four.checked) ? 4 : 2;
    }

    function renumber() {
      var n = 0, four = pages() === 4, gallery = 0;
      tiles().forEach(function (tile) {
        var box = tile.querySelector('input[type=checkbox]');
        var label = tile.querySelector('[data-pt-pos]');
        tile.classList.remove('is-cover', 'is-detail', 'is-gallery', 'is-out');
        if (!box || !box.checked) {
          if (label) { label.textContent = 'Not used'; }
          tile.classList.add('is-out');
          return;
        }
        n += 1;
        var where, cls;
        if (n <= COVER) { where = 'Cover'; cls = 'is-cover'; }
        else if (n <= COVER + DETAIL) { where = 'Page 2'; cls = 'is-detail'; }
        else if (four && gallery < GALLERY) { where = 'Page 3'; cls = 'is-gallery'; gallery += 1; }
        else { where = 'Not used'; cls = 'is-out'; }
        if (label) { label.textContent = where; }
        tile.classList.add(cls);
      });
      var warn = document.querySelector('[data-pt-warn]');
      if (warn) { warn.hidden = n >= 3; }
      /* Page three needs its own photographs; none is ever repeated to fill it. */
      var short = document.querySelector('[data-pt-gallery-warn]');
      if (short) { short.hidden = !(four && gallery > 0 && gallery < 2) && !(four && gallery === 0); }
    }

    /* Changing the format changes where the photographs land. */
    Array.prototype.forEach.call(
      document.querySelectorAll('input[name=pages]'),
      function (radio) { radio.addEventListener('change', renumber); });

    grid.addEventListener('dragstart', function (e) {
      var tile = e.target.closest('[data-pt-photo]');
      if (!tile) { return; }
      dragged = tile;
      tile.classList.add('is-dragging');
      e.dataTransfer.effectAllowed = 'move';
    });

    grid.addEventListener('dragover', function (e) {
      if (!dragged) { return; }
      e.preventDefault();
      var over = e.target.closest('[data-pt-photo]');
      if (!over || over === dragged) { return; }
      var box = over.getBoundingClientRect();
      var after = (e.clientX - box.left) > box.width / 2;
      grid.insertBefore(dragged, after ? over.nextSibling : over);
    });

    grid.addEventListener('dragend', function () {
      if (!dragged) { return; }
      dragged.classList.remove('is-dragging');
      dragged = null;
      renumber();
    });

    grid.addEventListener('change', renumber);
    renumber();
  }

  /* One press is one document: a second click while the first is still going
     would otherwise produce two. */
  /* Guarding the action buttons.
   *
   * The previous version set btn.disabled = true inside the button's own click
   * handler. A disabled submit button cannot activate its form, so the browser
   * abandoned the submission and NOTHING was sent — which is why Download and
   * Save to Brochure did nothing while Preview, which had no guard, worked.
   *
   * So the guard now hangs off the form's submit event and disables on the
   * next tick, once the submission is already under way. Each button has its
   * own state: running one must not disable the others, and every one comes
   * back if the request fails or the user returns to the page.
   */
  function guardActions() {
    var form = document.getElementById('particulars-form');
    if (!form) { return; }
    var busy = null;

    function release(btn) {
      if (!btn) { return; }
      btn.disabled = false;
      if (btn.dataset.ptLabel) { btn.textContent = btn.dataset.ptLabel; }
      btn.removeAttribute('aria-busy');
      if (busy === btn) { busy = null; }
    }

    /* Which button submitted, remembered before the submit event fires. */
    var clicked = null;
    var confirmed = null;
    document.querySelectorAll('[data-pt-once]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        /* An existing brochure is never overwritten without being asked. */
        var dialogId = btn.getAttribute('data-pt-confirm');
        if (dialogId && confirmed !== btn) {
          var dlg = document.getElementById(dialogId);
          if (dlg && dlg.showModal) {
            e.preventDefault();
            dlg.showModal();
            dlg.addEventListener('click', function once(ev) {
              if (ev.target.closest('[data-pt-cancel]')) {
                dlg.close();
                dlg.removeEventListener('click', once);
              } else if (ev.target.closest('[data-pt-go]')) {
                dlg.close();
                dlg.removeEventListener('click', once);
                confirmed = btn;
                btn.click();          /* now it goes through */
              }
            });
            return;
          }
        }
        confirmed = null;
        clicked = btn;
      });
    });

    form.addEventListener('submit', function () {
      var btn = clicked;
      clicked = null;
      if (!btn) { return; }                 /* Preview guards itself */
      /* On the next tick, so the submission this click started is not the one
         being cancelled. */
      window.setTimeout(function () {
        if (!btn.dataset.ptLabel) { btn.dataset.ptLabel = btn.textContent; }
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        btn.textContent = btn.getAttribute('data-pt-busy') || 'Working…';
        busy = btn;
      }, 0);

      /* A download does not navigate, so nothing would ever put the button
         back. A save navigates and the page is rebuilt anyway. */
      window.setTimeout(function () { release(btn); }, 8000);
    });

    /* Coming back to the page from a download or from history must never leave
       a button stuck. */
    window.addEventListener('pageshow', function () {
      document.querySelectorAll('[data-pt-once]').forEach(release);
    });
    window.addEventListener('focus', function () {
      if (busy) { window.setTimeout(function () { release(busy); }, 1200); }
    });
  }

  function start() {
    document.querySelectorAll('[data-pt-terms]').forEach(wireTerms);
    var grid = document.querySelector('[data-pt-photos]');
    if (grid) { wire(grid); }
    guardActions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());

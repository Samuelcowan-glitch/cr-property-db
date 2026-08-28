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
  function guardOnce() {
    document.querySelectorAll('[data-pt-once]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var original = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Working…';
        setTimeout(function () {
          btn.disabled = false;
          btn.textContent = original;
        }, 6000);
      });
    });
  }

  function start() {
    document.querySelectorAll('[data-pt-terms]').forEach(wireTerms);
    var grid = document.querySelector('[data-pt-photos]');
    if (grid) { wire(grid); }
    guardOnce();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());

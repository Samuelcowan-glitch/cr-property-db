/* Choosing and arranging the photographs for a set of particulars.
 *
 * Dragging changes the order they appear in; unticking leaves one out. The
 * order in the page is the order sent, so what you arrange is what you get.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  function wire(grid) {
    var dragged = null;

    function tiles() {
      return Array.prototype.slice.call(grid.querySelectorAll('[data-pt-photo]'));
    }

    function renumber() {
      var n = 0;
      tiles().forEach(function (tile) {
        var box = tile.querySelector('input[type=checkbox]');
        var label = tile.querySelector('[data-pt-pos]');
        if (box && box.checked) {
          n += 1;
          if (label) { label.textContent = n === 1 ? 'Cover' : n; }
        } else if (label) {
          label.textContent = 'Not used';
        }
      });
      var warn = document.querySelector('[data-pt-warn]');
      if (warn) { warn.hidden = n >= 3; }
    }

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
    var grid = document.querySelector('[data-pt-photos]');
    if (grid) { wire(grid); }
    guardOnce();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());

/* The strapline counter.
 *
 * Counts what will actually be sent — whitespace collapsed, as the feed does —
 * against Zoopla's limit, and says plainly when it is over. Nothing is
 * shortened here: the wording is the office's to choose.
 */
(function () {
  'use strict';

  function start() {
    document.querySelectorAll('[data-strapline]').forEach(function (box) {
      var out = document.querySelector('[data-strapline-count]');
      if (!out) { return; }
      var limit = parseInt(box.getAttribute('data-limit'), 10) || 2000;

      function count() {
        var text = box.value.split(/\s+/).filter(Boolean).join(' ');
        var n = text.length;
        out.textContent = n + ' of ' + limit + ' characters';
        out.classList.toggle('is-over', n > limit);
        if (n > limit) {
          out.textContent += ' — too long for Zoopla; shorten it here.';
        }
      }

      box.addEventListener('input', count);
      count();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());

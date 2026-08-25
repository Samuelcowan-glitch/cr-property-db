/* Photo manager on the Project page.
 *
 * Collapsed it is one compact box; expanded it takes photos by drag-and-drop or
 * by browsing, and the thumbnails can be dragged into the order the property is
 * marketed in. The first photo leads the listing everywhere, so the order set
 * here is the order used by the website, Zoopla and the CRM gallery.
 */
(function () {
  'use strict';

  function init(root) {
    var listingId = root.dataset.listing;
    var grid = root.querySelector('[data-photo-grid]');
    var drop = root.querySelector('[data-drop]');
    var input = root.querySelector('input[type=file]');
    var form = root.querySelector('[data-upload-form]');
    var status = root.querySelector('[data-photo-status]');

    /* ── expand / collapse ────────────────────────────────────────────────*/
    var toggle = root.querySelector('[data-photo-toggle]');
    if (toggle) {
      toggle.addEventListener('click', function () {
        var open = root.classList.toggle('is-open');
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }

    /* ── uploading ────────────────────────────────────────────────────────*/
    if (drop && input && form) {
      ['dragenter', 'dragover'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          e.preventDefault(); e.stopPropagation();
          drop.classList.add('is-over');
        });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          e.preventDefault(); e.stopPropagation();
          if (ev === 'dragleave' && drop.contains(e.relatedTarget)) return;
          drop.classList.remove('is-over');
        });
      });
      drop.addEventListener('drop', function (e) {
        if (!e.dataTransfer || !e.dataTransfer.files.length) return;
        input.files = e.dataTransfer.files;      // hand the files to the real input
        form.submit();
      });
      drop.addEventListener('click', function (e) {
        if (e.target.closest('button, a, input')) return;
        input.click();
      });
      input.addEventListener('change', function () {
        if (input.files.length) form.submit();
      });
    }

    /* ── dragging thumbnails into order ───────────────────────────────────*/
    if (!grid) return;
    var dragged = null;

    function tiles() {
      return Array.prototype.slice.call(grid.querySelectorAll('[data-photo-id]'));
    }

    function markPositions() {
      tiles().forEach(function (t, i) {
        t.classList.toggle('is-main', i === 0);
        var n = t.querySelector('[data-pos]');
        if (n) n.textContent = i === 0 ? 'Main Photo' : (i + 1);
      });
    }

    grid.addEventListener('dragstart', function (e) {
      var tile = e.target.closest('[data-photo-id]');
      if (!tile) return;
      dragged = tile;
      tile.classList.add('is-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', tile.dataset.photoId);
    });

    grid.addEventListener('dragover', function (e) {
      if (!dragged) return;
      e.preventDefault();
      var over = e.target.closest('[data-photo-id]');
      if (!over || over === dragged) return;
      // Insert before or after depending on which half of the tile we are over,
      // so the gap shows exactly where the photo will land.
      var box = over.getBoundingClientRect();
      var after = (e.clientX - box.left) > box.width / 2;
      grid.insertBefore(dragged, after ? over.nextSibling : over);
      markPositions();
    });

    grid.addEventListener('dragend', function () {
      if (!dragged) return;
      dragged.classList.remove('is-dragging');
      dragged = null;
      save();
    });

    function save() {
      var order = tiles().map(function (t) { return t.dataset.photoId; }).join(',');
      var body = new FormData();
      body.append('order', order);
      if (status) status.textContent = 'Saving order…';
      fetch('/listings/' + listingId + '/photos/order', {
        method: 'POST', body: body, credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'fetch',
          'X-CSRF-Token': (document.querySelector('meta[name=csrf-token]') || {}).content || ''
        },
      }).then(function (r) {
        if (status) status.textContent = r.ok ? 'Order saved' : 'Could not save the order';
        if (status) setTimeout(function () { status.textContent = ''; }, 2000);
      }).catch(function () {
        if (status) status.textContent = 'Could not save the order';
      });
    }

    /* ── click a thumbnail for a larger look ──────────────────────────────*/
    grid.addEventListener('click', function (e) {
      if (e.target.closest('button, form, a')) return;
      var tile = e.target.closest('[data-photo-id]');
      if (!tile) return;
      var full = tile.dataset.full;
      if (full) window.open(full, '_blank', 'noopener');
    });

    markPositions();
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-photo-manager]'), init);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

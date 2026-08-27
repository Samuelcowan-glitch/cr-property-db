/* The photograph viewer, shared by the Project and Property Overviews.
 *
 * Clicking a photograph — or the Photos box itself — opens a dialog over the
 * page. The Overview stays where it is, dimmed behind; nothing is navigated to
 * and nothing reloads.
 *
 * A native <dialog> does the work, as the diary already does, so Escape, the
 * backdrop and focus handling come from the browser rather than being
 * reimplemented and got subtly wrong.
 *
 * Viewing is deliberately separate from managing: a click that was really the
 * start of a drag never opens the gallery, so reordering still works.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  var dlg, img, frame, thumbs, countEl, titleEl, capEl, loadingEl, missingEl,
      prevBtn, nextBtn;
  var photos = [], at = 0;

  function grab() {
    dlg = document.getElementById('photo-gallery');
    if (!dlg) { return false; }
    img = dlg.querySelector('[data-pg-image]');
    frame = dlg.querySelector('[data-pg-frame]');
    thumbs = dlg.querySelector('[data-pg-thumbs]');
    countEl = dlg.querySelector('[data-pg-count]');
    titleEl = dlg.querySelector('[data-pg-title]');
    capEl = dlg.querySelector('[data-pg-caption]');
    loadingEl = dlg.querySelector('[data-pg-loading]');
    missingEl = dlg.querySelector('[data-pg-missing]');
    prevBtn = dlg.querySelector('[data-pg-prev]');
    nextBtn = dlg.querySelector('[data-pg-next]');
    return true;
  }

  function show(index) {
    if (!photos.length) { return; }
    at = Math.max(0, Math.min(index, photos.length - 1));
    var photo = photos[at];

    missingEl.hidden = true;
    img.hidden = false;
    /* The frame keeps its height while the next photograph arrives, so the
       dialog does not jump about as you move through them. */
    loadingEl.hidden = false;
    img.src = photo.src;
    img.alt = photo.caption || 'Photograph ' + (at + 1);

    countEl.textContent = (at + 1) + ' of ' + photos.length;
    capEl.textContent = photo.caption || '';
    capEl.hidden = !photo.caption;

    /* An arrow with nowhere to go is disabled rather than left to be pressed. */
    prevBtn.disabled = at === 0;
    nextBtn.disabled = at === photos.length - 1;
    prevBtn.hidden = photos.length < 2;
    nextBtn.hidden = photos.length < 2;

    Array.prototype.forEach.call(thumbs.children, function (t, i) {
      t.classList.toggle('is-on', i === at);
      if (i === at) { t.setAttribute('aria-current', 'true'); }
      else { t.removeAttribute('aria-current'); }
    });
  }

  function drawThumbs() {
    thumbs.innerHTML = '';
    thumbs.hidden = photos.length < 2;
    photos.forEach(function (photo, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'pg-thumb';
      b.setAttribute('aria-label', 'Photograph ' + (i + 1));
      var t = document.createElement('img');
      t.src = photo.thumb || photo.src;
      t.alt = '';
      t.loading = 'lazy';
      b.appendChild(t);
      b.addEventListener('click', function () { show(i); });
      thumbs.appendChild(b);
    });
  }

  function open(list, title, index) {
    if (!grab() || !list || !list.length) { return; }
    photos = list;
    titleEl.textContent = title || '';
    drawThumbs();
    show(index || 0);
    if (dlg.showModal) { dlg.showModal(); } else { dlg.setAttribute('open', ''); }
    /* The page behind must not scroll while the gallery is over it. */
    document.body.classList.add('pg-open');
  }

  function close() {
    if (!dlg) { return; }
    if (dlg.close) { dlg.close(); } else { dlg.removeAttribute('open'); }
    document.body.classList.remove('pg-open');
  }

  function readPhotos(el) {
    var raw = el.getAttribute('data-gallery');
    if (!raw) { return null; }
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  /* Which element was asked to open the gallery, and at which photograph. */
  function trigger(target) {
    var tile = target.closest('[data-gallery-index]');
    var holder = target.closest('[data-gallery]');
    if (!holder) { return null; }
    var list = readPhotos(holder);
    if (!list || !list.length) { return null; }
    var index = 0;
    if (tile && tile.getAttribute('data-gallery-index')) {
      index = parseInt(tile.getAttribute('data-gallery-index'), 10) || 0;
    }
    return {list: list, title: holder.getAttribute('data-gallery-title') || '',
            index: index};
  }

  function wire() {
    if (!grab()) { return; }

    dlg.addEventListener('click', function (e) {
      /* Clicking the dimmed area closes; clicking inside never does. */
      if (e.target === dlg) { close(); return; }
      if (e.target.closest('[data-pg-close]')) { close(); }
      else if (e.target.closest('[data-pg-prev]')) { show(at - 1); }
      else if (e.target.closest('[data-pg-next]')) { show(at + 1); }
    });
    dlg.addEventListener('close', function () {
      document.body.classList.remove('pg-open');
    });

    document.addEventListener('keydown', function (e) {
      if (!dlg.open) { return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); show(at - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); show(at + 1); }
      /* Escape is the browser's own; nothing to add. */
    });

    img.addEventListener('load', function () { loadingEl.hidden = true; });
    img.addEventListener('error', function () {
      loadingEl.hidden = true;
      img.hidden = true;
      missingEl.hidden = false;
    });

    /* Swiping on a touchscreen moves through the photographs. */
    var startX = null;
    frame.addEventListener('touchstart', function (e) {
      startX = e.touches.length === 1 ? e.touches[0].clientX : null;
    }, {passive: true});
    frame.addEventListener('touchend', function (e) {
      if (startX === null) { return; }
      var moved = e.changedTouches[0].clientX - startX;
      startX = null;
      if (Math.abs(moved) < 40) { return; }
      show(moved < 0 ? at + 1 : at - 1);
    });

    /* Opening it. A click that was really a drag is ignored, so reordering a
       photograph never opens the viewer on top of it. */
    var downAt = null;
    document.addEventListener('pointerdown', function (e) {
      downAt = {x: e.clientX, y: e.clientY};
    }, true);

    document.addEventListener('click', function (e) {
      if (dlg.open) { return; }
      /* Anything that manages photographs keeps its own click. */
      if (e.target.closest('button, a, form, input, select, textarea, label')) { return; }
      if (e.target.closest('.is-dragging')) { return; }
      if (downAt) {
        var moved = Math.abs(e.clientX - downAt.x) + Math.abs(e.clientY - downAt.y);
        if (moved > 8) { return; }        /* that was a drag, not a click */
      }
      var found = trigger(e.target);
      if (!found) { return; }
      e.preventDefault();
      open(found.list, found.title, found.index);
    });
  }

  window.CRPhotoGallery = {open: open, close: close};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else { wire(); }
}());

/* Diary calendar.
 *
 * Placement, dragging and resizing happen here; the times themselves are
 * decided on the server, which stores UTC and renders Europe/London, so an
 * appointment keeps its hour either side of the clocks changing.
 */
(function () {
  'use strict';

  var SLOT = 30;             // minutes per row
  var PX_PER_MIN = 0.8;      // grid height per minute
  var DAY_START = 7 * 60;    // grid runs 07:00–21:00
  var DAY_END = 21 * 60;

  function minutesToTop(min) { return (Math.max(min, DAY_START) - DAY_START) * PX_PER_MIN; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function hhmm(min) { return pad(Math.floor(min / 60)) + ':' + pad(min % 60); }

  function csrf() {
    var m = document.querySelector('meta[name=csrf-token]');
    return m ? m.content : '';
  }

  /* ── lay out overlapping appointments side by side ──────────────────────*/
  function place(column) {
    var cards = Array.prototype.slice.call(column.querySelectorAll('.cal-event'));
    cards.sort(function (a, b) { return (+a.dataset.startMin) - (+b.dataset.startMin); });

    var clusters = [], current = [], clusterEnd = -1;
    cards.forEach(function (card) {
      var s = +card.dataset.startMin, e = +card.dataset.endMin;
      if (current.length && s >= clusterEnd) { clusters.push(current); current = []; clusterEnd = -1; }
      current.push(card);
      clusterEnd = Math.max(clusterEnd, e);
    });
    if (current.length) clusters.push(current);

    clusters.forEach(function (group) {
      // Columns within a cluster: an appointment takes the first free lane, so
      // nothing is hidden behind anything else.
      var lanes = [];
      group.forEach(function (card) {
        var s = +card.dataset.startMin, e = +card.dataset.endMin;
        var lane = 0;
        while (lanes[lane] !== undefined && lanes[lane] > s) lane++;
        lanes[lane] = e;
        card.dataset.lane = lane;
      });
      var width = 100 / lanes.length;
      group.forEach(function (card) {
        var lane = +card.dataset.lane;
        card.style.left = (lane * width) + '%';
        card.style.width = 'calc(' + width + '% - 3px)';
      });
    });
  }

  function layout(root) {
    root.querySelectorAll('.cal-event').forEach(function (card) {
      var s = +card.dataset.startMin, e = +card.dataset.endMin;
      var height = Math.max((Math.min(e, DAY_END) - Math.max(s, DAY_START)) * PX_PER_MIN, 18);
      card.style.top = minutesToTop(s) + 'px';
      card.style.height = height + 'px';
      // A half-hour card has room for one line, not three: put the time and the
      // title side by side and drop the location rather than clipping the text.
      card.classList.toggle('is-compact', height < 38);
      card.classList.toggle('is-tiny', height < 24);
    });
    root.querySelectorAll('.cal-daycol').forEach(place);
  }

  /* ── the line showing the time now ──────────────────────────────────────*/
  function nowLine(root) {
    var line = root.querySelector('.cal-now');
    if (!line) return;
    function put() {
      var mins = +root.dataset.nowMinutes;
      if (mins < DAY_START || mins > DAY_END) { line.style.display = 'none'; return; }
      line.style.display = '';
      line.style.top = minutesToTop(mins) + 'px';
    }
    put();
    // Nudge it on, so a diary left open all afternoon stays honest.
    setInterval(function () {
      root.dataset.nowMinutes = String((+root.dataset.nowMinutes) + 1);
      put();
    }, 60000);
  }

  /* ── click an empty slot to book something ──────────────────────────────*/
  function clickToCreate(root) {
    var form = document.getElementById('new-event-form');
    if (!form) return;
    root.querySelectorAll('.cal-daycol').forEach(function (col) {
      col.addEventListener('click', function (e) {
        if (e.target.closest('.cal-event')) return;         // opening one, not making one
        var box = col.getBoundingClientRect();
        var min = DAY_START + Math.floor((e.clientY - box.top) / PX_PER_MIN / SLOT) * SLOT;
        min = Math.max(DAY_START, Math.min(min, DAY_END - SLOT));
        var day = col.dataset.date;
        form.querySelector('[name=start]').value = day + 'T' + hhmm(min);
        form.querySelector('[name=end]').value = day + 'T' + hhmm(Math.min(min + 60, DAY_END));
        form.querySelector('[name=title]').value = '';
        var dlg = document.getElementById('new-event');
        if (dlg && dlg.showModal) dlg.showModal(); else form.hidden = false;
        form.querySelector('[name=title]').focus();
      });
    });
  }

  /* ── dragging to another time, and resizing ─────────────────────────────*/
  function dragAndResize(root) {
    var active = null;

    function save(card, startMin, endMin, dayISO) {
      var body = JSON.stringify({start: dayISO + 'T' + hhmm(startMin), end: dayISO + 'T' + hhmm(endMin)});
      card.classList.add('is-saving');
      fetch('/diary/event/' + card.dataset.id + '/move', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf()},
        body: body
      }).then(function (r) { return r.json(); }).then(function (out) {
        card.classList.remove('is-saving');
        if (!out.ok) { window.location.reload(); return; }
        card.dataset.startMin = startMin; card.dataset.endMin = endMin;
        var t = card.querySelector('.cal-time');
        if (t) t.textContent = hhmm(startMin) + '–' + hhmm(endMin);
        layout(root);
      }).catch(function () { window.location.reload(); });
    }

    root.addEventListener('mousedown', function (e) {
      var card = e.target.closest('.cal-event');
      if (!card || card.dataset.id === '') return;           // all-day items are fixed
      var resizing = e.target.classList.contains('cal-grip');
      active = {
        card: card, resizing: resizing, y: e.clientY,
        start: +card.dataset.startMin, end: +card.dataset.endMin,
        col: card.closest('.cal-daycol'), moved: false
      };
      card.classList.add(resizing ? 'is-resizing' : 'is-dragging');
      e.preventDefault();
    });

    window.addEventListener('mousemove', function (e) {
      if (!active) return;
      var delta = Math.round((e.clientY - active.y) / PX_PER_MIN / SLOT) * SLOT;
      if (delta === 0) return;
      active.moved = true;
      var card = active.card, s = active.start, en = active.end;
      if (active.resizing) {
        en = Math.min(Math.max(active.end + delta, s + SLOT), DAY_END);
      } else {
        var length = active.end - active.start;
        s = Math.min(Math.max(active.start + delta, DAY_START), DAY_END - length);
        en = s + length;
      }
      card.dataset.startMin = s; card.dataset.endMin = en;
      var t = card.querySelector('.cal-time');
      if (t) t.textContent = hhmm(s) + '–' + hhmm(en);
      layout(root);
    });

    window.addEventListener('mouseup', function () {
      if (!active) return;
      var card = active.card;
      card.classList.remove('is-dragging', 'is-resizing');
      if (active.moved) {
        save(card, +card.dataset.startMin, +card.dataset.endMin, active.col.dataset.date);
      }
      active = null;
    });

    // A click that was not a drag opens the appointment.
    root.addEventListener('click', function (e) {
      var card = e.target.closest('.cal-event');
      if (!card || e.target.classList.contains('cal-grip')) return;
      if (card.dataset.url) window.location.href = card.dataset.url;
    });
  }

  function start() {
    var root = document.querySelector('[data-calendar]');
    if (!root) return;
    layout(root);
    nowLine(root);
    clickToCreate(root);
    dragAndResize(root);
    window.addEventListener('resize', function () { layout(root); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

/* The Business Rates Calculator.
 *
 * One job: ask the server for the breakdown whenever an input changes, and
 * draw what comes back. The local authority list is a plain <select> carrying
 * data-search, so searchable-select.js makes it searchable exactly as it does
 * for contacts and organisations — there is no second implementation here.
 *
 * The arithmetic is deliberately NOT done here. A rates figure can end up on a
 * brochure, and JavaScript numbers are binary floats — 0.1 + 0.2 is not 0.3 —
 * so a total worked out in the browser can be a penny out and nobody would
 * see why. The server does the sums in whole pence and this file only ever
 * displays the answer. That also means the screen and the saved record can
 * never disagree: they came from the same calculation.
 *
 * Saving is a separate button. Changing an input redraws the estimate but
 * saves nothing until the user says so.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  /* ── The calculator ───────────────────────────────────────────────────── */
  function wireCalculator(root) {
    var calcUrl = root.getAttribute('data-calc-url');
    var suggestUrl = root.getAttribute('data-suggest-url');
    var resultBox = root.querySelector('[data-br-result]');
    var linesBox = root.querySelector('[data-br-lines]');
    var totalEl = root.querySelector('[data-br-total]');
    var monthlyEl = root.querySelector('[data-br-monthly]');
    var assumeBox = root.querySelector('[data-br-assumptions]');
    var errorEl = root.querySelector('[data-br-error]');
    var typeEl = root.querySelector('[data-br-type]');
    var whyEl = root.querySelector('[data-br-why]');
    var saveBtn = root.querySelector('[data-br-save]');
    var calcBtn = root.querySelector('[data-br-calc]');
    var fields = root.querySelectorAll('[data-br]');

    function field(name) { return root.querySelector('[data-br="' + name + '"]'); }

    function values() {
      var data = new FormData();
      Array.prototype.forEach.call(fields, function (el) {
        var name = el.getAttribute('data-br');
        if (el.type === 'checkbox') {
          if (el.checked) { data.append(name, '1'); }
        } else {
          data.append(name, el.value);
        }
      });
      return data;
    }

    function token() {
      var meta = document.querySelector('meta[name="csrf-token"]');
      return meta ? meta.getAttribute('content') : '';
    }

    function draw(payload) {
      var r = payload.result;
      if (!r) {
        resultBox.hidden = true;
        if (saveBtn) { saveBtn.disabled = true; }
        var messages = Object.keys(payload.errors || {}).map(function (k) {
          return payload.errors[k];
        });
        errorEl.hidden = messages.length === 0;
        errorEl.textContent = messages.join(' ');
        return;
      }
      errorEl.hidden = true;

      linesBox.innerHTML = '';
      r.lines.forEach(function (pair, i) {
        var row = document.createElement('div');
        row.className = 'br-line' + (i === r.lines.length - 1 ? ' br-line--total' : '');
        var label = document.createElement('span');
        label.textContent = pair[0];
        var value = document.createElement('span');
        value.className = 'br-line-value';
        value.textContent = pair[1];
        row.appendChild(label);
        row.appendChild(value);
        linesBox.appendChild(row);
      });

      totalEl.textContent = r.total;
      monthlyEl.textContent = r.monthly;
      if (typeEl) {
        typeEl.textContent = r.overridden ? 'Entered by hand' : (r.multiplier_type || '—');
      }
      if (whyEl && r.overridden) {
        whyEl.textContent = 'Entered by hand: ' + r.multiplier + '.';
      }

      assumeBox.innerHTML = '';
      if (payload.assumptions && payload.assumptions.length) {
        var head = document.createElement('div');
        head.className = 'br-assume-head';
        head.textContent = 'Before you save, note:';
        assumeBox.appendChild(head);
        var list = document.createElement('ul');
        payload.assumptions.forEach(function (line) {
          var li = document.createElement('li');
          li.textContent = line;
          list.appendChild(li);
        });
        assumeBox.appendChild(list);
      }

      resultBox.hidden = false;
      /* Saving is only offered once there is something correct to save. */
      if (saveBtn) { saveBtn.disabled = !payload.ok; }
    }

    var pending = null;
    function recalculate() {
      if (!calcUrl) { return; }
      /* The rateable value is typed a digit at a time; asking on every
         keystroke would be a request per character. */
      window.clearTimeout(pending);
      pending = window.setTimeout(function () {
        fetch(calcUrl, {
          method: 'POST', body: values(), credentials: 'same-origin',
          headers: {'X-CSRF-Token': token(), 'X-Requested-With': 'XMLHttpRequest'}
        }).then(function (r) {
          return r.ok ? r.json() : null;
        }).then(function (payload) {
          if (payload) { draw(payload); }
        }).catch(function () {
          /* Offline or refused: say so rather than showing a stale figure. */
          resultBox.hidden = true;
          if (saveBtn) { saveBtn.disabled = true; }
          errorEl.hidden = false;
          errorEl.textContent = 'Could not reach the server to work this out. '
            + 'The figure shown may be out of date.';
        });
      }, 250);
    }

    /* Suggest the multiplier for the year and rateable value, without ever
       choosing one the user has overridden or picked themselves. */
    var touchedMultiplier = false;
    var multiplierSelect = field('multiplier_id');
    if (multiplierSelect) {
      multiplierSelect.addEventListener('change', function () { touchedMultiplier = true; });
    }

    function suggest() {
      if (!suggestUrl || touchedMultiplier) { return; }
      var override = field('multiplier_override');
      if (override && override.checked) { return; }
      var year = field('tax_year');
      var rv = field('rateable_value');
      var url = suggestUrl + '?tax_year=' + encodeURIComponent(year ? year.value : '')
        + '&rateable_value=' + encodeURIComponent(rv ? rv.value : '');
      fetch(url, {credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) {
          if (!payload || !multiplierSelect) { return; }
          /* A year with nothing on record says so. Silently hiding every
             option leaves an empty list and looks like a broken calculator. */
          var emptyEl = root.querySelector('[data-br-empty]');
          if (emptyEl) {
            emptyEl.hidden = !payload.empty;
            emptyEl.textContent = payload.message || '';
          }
          /* Only options for this tax year can be chosen. */
          Array.prototype.forEach.call(multiplierSelect.options, function (o) {
            if (!o.value) { return; }
            var ids = payload.options.map(function (x) { return String(x.id); });
            o.hidden = ids.indexOf(o.value) === -1;
          });
          if (payload.suggested_id && !multiplierSelect.value) {
            multiplierSelect.value = String(payload.suggested_id);
          }
          if (whyEl && payload.why) {
            whyEl.textContent = 'Suggested: ' + payload.why
              + (payload.verified ? '.' : '. This multiplier has not been verified.');
          }
          recalculate();
        }).catch(function () { /* the suggestion is a convenience, not a requirement */ });
    }

    Array.prototype.forEach.call(fields, function (el) {
      var event = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
      el.addEventListener(event, function () {
        var name = el.getAttribute('data-br');
        if (name === 'tax_year' || name === 'rateable_value') { suggest(); }
        recalculate();
      });
    });
    if (calcBtn) { calcBtn.addEventListener('click', recalculate); }

    /* An estimate already on the record is redrawn on load, so the breakdown
       is visible without touching anything. */
    var rv = field('rateable_value');
    if (rv && rv.value.trim()) { recalculate(); } else { suggest(); }
  }

  function wire() {
    document.querySelectorAll('[data-rates]').forEach(wireCalculator);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else { wire(); }
}());

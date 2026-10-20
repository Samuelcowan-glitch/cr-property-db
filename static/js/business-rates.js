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
    var noteEl = root.querySelector('[data-br-relief-note]');
    var noYearEl = root.querySelector('[data-br-noyear]');
    var pctField = root.querySelector('[data-br="relief_percent"]');
    /* Whether anybody has actually asked for a figure yet — by typing in the
       calculator or pressing Calculate. Until they have, a complaint about a
       missing input has nothing to do with anything they did. */
    var asked = false;
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

    /* Everything outside the breakdown box that still has to match the
       answer. These sit outside [data-br-result], so hiding the breakdown
       does not clear them and they would otherwise keep showing the previous
       state — the stale "Entered by hand" and the stale multiplier type. */
    function drawSurrounds(payload) {
      var r = payload.result;
      if (noteEl && payload.relief_note) { noteEl.textContent = payload.relief_note; }
      if (noYearEl) {
        var missing = payload.has_multipliers === false;
        noYearEl.hidden = !missing;
        noYearEl.innerHTML = '';
        if (missing) {
          var strong = document.createElement('strong');
          strong.textContent = 'No multiplier is on record for '
            + (payload.tax_year || 'this tax year') + '.';
          noYearEl.appendChild(strong);
          noYearEl.appendChild(document.createTextNode(
            ' No figure can be worked out for it. Add the published'
            + ' multipliers under More \u2192 Business rates, or choose a year'
            + ' that has them \u2014 but do not quote another year\u2019s rate'
            + ' as this year\u2019s.'));
        }
      }
      /* The automatic figure is shown as the box's placeholder rather than
         typed into it: a value in the box is a manual override by definition,
         and prefilling it would turn the automatic relief into one. */
      if (pctField) {
        pctField.placeholder = (r && r.sbrr_percent)
          ? r.sbrr_percent + '% automatic' : '%';
      }
      if (typeEl) {
        typeEl.textContent = !r ? '\u2014'
          : (r.overridden ? 'Entered by hand' : (r.multiplier_type || '\u2014'));
      }
      if (whyEl) {
        if (!r) { whyEl.textContent = ''; }
        else if (r.overridden) { whyEl.textContent = 'Entered by hand: ' + r.multiplier + '.'; }
        else { whyEl.textContent = 'Using ' + r.multiplier_name + ' at ' + r.multiplier + '.'; }
      }
    }

    function draw(payload) {
      var r = payload.result;
      drawSurrounds(payload);
      if (!r) {
        resultBox.hidden = true;
        if (saveBtn) { saveBtn.disabled = true; }
        var messages = Object.keys(payload.errors || {}).map(function (k) {
          return payload.errors[k];
        });
        /* An untouched calculator is not a mistake. Telling somebody to enter
           the rateable value before they have had the chance to reads as an
           error on a page they have only just opened. */
        errorEl.hidden = messages.length === 0 || !asked;
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

    function fail(message) {
      resultBox.hidden = true;
      if (saveBtn) { saveBtn.disabled = true; }
      errorEl.hidden = false;
      errorEl.textContent = message;
    }

    /* Responses are drawn in the order they ARRIVE, which is not the order
       they were asked for. clearTimeout cancels a timer but never a request
       already in flight, so two overlapping calculations could paint the older
       answer last and leave a figure that matches none of the inputs. Each
       request takes a number and only the newest one is allowed to draw. */
    var pending = null;
    var asking = 0;
    function recalculate() {
      if (!calcUrl) { return; }
      /* The rateable value is typed a digit at a time; asking on every
         keystroke would be a request per character. */
      window.clearTimeout(pending);
      pending = window.setTimeout(function () {
        var mine = ++asking;
        fetch(calcUrl, {
          method: 'POST', body: values(), credentials: 'same-origin',
          headers: {'X-CSRF-Token': token(), 'X-Requested-With': 'XMLHttpRequest'}
        }).then(function (r) {
          if (r.ok) { return r.json(); }
          /* A refusal used to be swallowed, leaving the old figure on screen
             and nothing to explain why nothing moved. A view-only user gets
             403 here, and an expired page gets 400. */
          return {__status: r.status};
        }).then(function (payload) {
          if (mine !== asking) { return; }        /* a newer answer is coming */
          if (!payload) { return; }
          if (payload.__status) {
            fail(payload.__status === 403
              ? 'You do not have permission to calculate business rates, so '
                + 'these figures cannot be worked out.'
              : 'The server would not work this out (error '
                + payload.__status + '). Reload the page and try again.');
            return;
          }
          draw(payload);
        }).catch(function () {
          if (mine !== asking) { return; }
          /* Offline or refused: say so rather than showing a stale figure. */
          fail('Could not reach the server to work this out. '
            + 'The figure shown may be out of date.');
        });
      }, 250);
    }

    /* Suggest the multiplier for the year and rateable value.
     *
     * This used to give up for good the moment the user touched the multiplier
     * dropdown. But the server refuses a multiplier that does not belong to
     * the chosen year, so picking a multiplier and then changing the year
     * produced an error and a hidden breakdown that no further fiddling could
     * recover. The suggestion therefore always runs; what it will not do is
     * overrule a pick that is still VALID for the year on screen. */
    var multiplierSelect = field('multiplier_id');
    /* True while this file is setting a value itself, so the change it has to
       dispatch (the searchable dropdown's label listens for it) is not mistaken
       for the user asking for a figure. */
    var setting = false;

    function suggest() {
      if (!suggestUrl) { return; }
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
          /* Only options for this tax year can be chosen. Hidden alone was
             not enough: multiplier_id matches the record-picker rule in
             searchable-select.js, so the native dropdown is replaced by a
             panel that reads the options itself. It skips hidden options now,
             and disabling them stops any path that does not. */
          var ids = payload.options.map(function (x) { return String(x.id); });
          var valid = false;
          Array.prototype.forEach.call(multiplierSelect.options, function (o) {
            if (!o.value) { return; }
            var fits = ids.indexOf(o.value) !== -1;
            o.hidden = !fits;
            o.disabled = !fits;
            if (fits && o.value === multiplierSelect.value) { valid = true; }
          });
          /* Replace the selection only when it cannot be used: either nothing
             is chosen, or what is chosen belongs to another year. A valid pick
             the user made themselves is left alone. */
          if (!valid) {
            var want = payload.suggested_id ? String(payload.suggested_id) : '';
            if (multiplierSelect.value !== want) {
              setting = true;
              multiplierSelect.value = want;
              /* The searchable dropdown keeps its button label in step by
                 listening for change on the real select. */
              multiplierSelect.dispatchEvent(new Event('change', {bubbles: true}));
              setting = false;
            }
          }
          if (whyEl && payload.why && !valid) {
            whyEl.textContent = 'Suggested: ' + payload.why
              + (payload.verified ? '.' : '. This multiplier has not been verified.');
          }
          recalculate();
        }).catch(function () { /* the suggestion is a convenience, not a requirement */ });
    }

    Array.prototype.forEach.call(fields, function (el) {
      var event = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
      el.addEventListener(event, function () {
        if (setting) { return; }   /* this file's own doing, not the user's */
        asked = true;
        var name = el.getAttribute('data-br');
        /* The year and the rateable value decide which multipliers may be
           used, so both ask for the suggestion again. The recalculation is
           fired as well and the newest answer wins, so a slow suggestion can
           no longer leave the breakdown in an error state. */
        if (name === 'tax_year' || name === 'rateable_value'
            || name === 'multiplier_override') { suggest(); }
        recalculate();
      });
    });
    if (calcBtn) {
      calcBtn.addEventListener('click', function () { asked = true; recalculate(); });
    }

    /* An estimate already on the record is redrawn on load, so the breakdown
       is visible without touching anything. */
    var rv = field('rateable_value');
    suggest();
    if (rv && rv.value.trim()) { recalculate(); }
  }

  function wire() {
    document.querySelectorAll('[data-rates]').forEach(wireCalculator);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else { wire(); }
}());

/* The Key Terms editor.
 *
 * A list of rows rather than a paragraph, because these are separate terms and
 * everything downstream — the particulars bullets, the website panel, the
 * Zoopla feature slots — needs them separate. Typing them into one box and
 * hoping a separator can be guessed later is what went wrong before.
 *
 * Add, edit, remove, reorder, one term per row. Pasting a bullet list splits
 * it into rows. What is posted is a single textarea with one term to a line,
 * which is how the column already stores it, so nothing server-side changed
 * and the field still works with this file absent.
 *
 * Wording, capitalisation and order are left exactly as typed. Nothing here
 * rewrites a term.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  /* The separators someone might paste. A hyphen counts only at the start of a
     line, or "Self-contained" would be split in half. */
  var BULLET = /^[\s]*[•·▪●‣⁃*\-–—]+\s*/;
  var NUMBER = /^[\s]*\d{1,2}[.)]\s+/;
  var SPLIT = /[\n\r•·▪●‣⁃|;]+/;

  function tidy(text) {
    return String(text).replace(NUMBER, '').replace(BULLET, '')
      .replace(/\s+/g, ' ').replace(/^[\s.;,·]+|[\s.;,·]+$/g, '');
  }

  function split(text) {
    return String(text).split(SPLIT).map(tidy).filter(Boolean);
  }

  function wire(root) {
    var list = root.querySelector('[data-kt-list]');
    var store = root.querySelector('[data-kt-value]');
    var addBtn = root.querySelector('[data-kt-add]');
    var countEl = root.querySelector('[data-kt-count]');
    var formId = root.getAttribute('data-form');
    var max = parseInt(root.getAttribute('data-max') || '0', 10);
    if (!list || !store) { return; }

    function rows() {
      return Array.prototype.slice.call(list.querySelectorAll('[data-kt-row]'));
    }

    /* The textarea is the thing that gets posted, so it is rebuilt from the
       rows after every change rather than kept in step by hand. */
    function sync() {
      var seen = {};
      var values = [];
      rows().forEach(function (row) {
        var input = row.querySelector('[data-kt-input]');
        var value = input ? input.value.trim() : '';
        if (!value) { return; }
        var key = value.toLowerCase();
        row.classList.toggle('is-duplicate', Object.prototype.hasOwnProperty.call(seen, key));
        if (Object.prototype.hasOwnProperty.call(seen, key)) { return; }
        seen[key] = true;
        values.push(value);
      });
      store.value = values.join('\n');
      if (countEl) {
        var over = max && values.length > max;
        countEl.textContent = values.length
          ? values.length + ' term' + (values.length === 1 ? '' : 's')
            + (over ? ' — only the first ' + max + ' appear on the particulars' : '')
          : 'No terms yet';
        countEl.classList.toggle('is-over', Boolean(over));
      }
      rows().forEach(function (row, i) {
        var input = row.querySelector('[data-kt-input]');
        if (input) { input.setAttribute('aria-label', 'Key term ' + (i + 1)); }
      });
    }

    function makeRow(value) {
      var li = document.createElement('li');
      li.className = 'kt-row';
      li.setAttribute('data-kt-row', '');
      li.setAttribute('draggable', 'true');

      var grip = document.createElement('span');
      grip.className = 'kt-grip';
      grip.setAttribute('data-kt-grip', '');
      grip.title = 'Drag to reorder';
      grip.setAttribute('aria-hidden', 'true');
      grip.textContent = '⠿';

      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'kt-input';
      input.setAttribute('data-kt-input', '');
      input.value = value || '';
      if (formId) { input.setAttribute('form', formId); }
      /* The row inputs must never be posted — only the textarea is. */
      input.removeAttribute('name');

      var x = document.createElement('button');
      x.type = 'button';
      x.className = 'kt-x';
      x.setAttribute('data-kt-remove', '');
      x.setAttribute('aria-label', 'Remove this term');
      x.innerHTML = '&times;';

      li.appendChild(grip);
      li.appendChild(input);
      li.appendChild(x);
      return li;
    }

    function add(value, after) {
      var row = makeRow(value);
      if (after && after.nextSibling) { list.insertBefore(row, after.nextSibling); }
      else { list.appendChild(row); }
      sync();
      return row;
    }

    if (addBtn) {
      addBtn.addEventListener('click', function () {
        var row = add('');
        var input = row.querySelector('[data-kt-input]');
        if (input) { input.focus(); }
      });
    }

    list.addEventListener('click', function (e) {
      var remove = e.target.closest('[data-kt-remove]');
      if (!remove) { return; }
      var row = remove.closest('[data-kt-row]');
      if (row) { row.remove(); sync(); }
    });

    list.addEventListener('input', sync);

    list.addEventListener('keydown', function (e) {
      var row = e.target.closest('[data-kt-row]');
      if (!row || !e.target.matches('[data-kt-input]')) { return; }
      if (e.key === 'Enter') {
        /* Enter starts the next term rather than submitting the whole form. */
        e.preventDefault();
        var next = add('', row);
        next.querySelector('[data-kt-input]').focus();
      } else if (e.key === 'Backspace' && !e.target.value && rows().length > 1) {
        e.preventDefault();
        var prev = row.previousElementSibling;
        row.remove();
        sync();
        if (prev) {
          var input = prev.querySelector('[data-kt-input]');
          if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
        }
      }
    });

    /* Pasting a list splits it across rows instead of dropping it all in one. */
    list.addEventListener('paste', function (e) {
      var input = e.target.closest('[data-kt-input]');
      if (!input) { return; }
      var text = (e.clipboardData || window.clipboardData).getData('text') || '';
      var parts = split(text);
      if (parts.length < 2) { return; }      /* a single term pastes normally */
      e.preventDefault();
      var row = input.closest('[data-kt-row]');
      input.value = parts[0];
      var anchor = row;
      parts.slice(1).forEach(function (part) { anchor = add(part, anchor); });
      sync();
    });

    /* Reordering. */
    var dragging = null;
    list.addEventListener('dragstart', function (e) {
      var row = e.target.closest('[data-kt-row]');
      if (!row) { return; }
      dragging = row;
      row.classList.add('is-dragging');
      if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; }
    });
    list.addEventListener('dragend', function () {
      if (dragging) { dragging.classList.remove('is-dragging'); }
      dragging = null;
      sync();
    });
    list.addEventListener('dragover', function (e) {
      if (!dragging) { return; }
      e.preventDefault();
      var over = e.target.closest('[data-kt-row]');
      if (!over || over === dragging) { return; }
      var box = over.getBoundingClientRect();
      var below = (e.clientY - box.top) > box.height / 2;
      list.insertBefore(dragging, below ? over.nextSibling : over);
    });

    /* The textarea is the field and the no-JavaScript editor both. Now that
       the rows are running it is hidden, but it is still the only control
       posted — there is no second copy to disagree with it. */
    store.hidden = true;
    store.setAttribute('aria-hidden', 'true');
    store.tabIndex = -1;

    if (!rows().length) { add(''); }
    sync();
  }

  function start() {
    document.querySelectorAll('[data-key-terms]').forEach(wire);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());

/* Searchable dropdowns for the CRM.
 *
 * Turns a long <select> into a button that opens a panel with a search box at
 * the top: type to filter instantly, or scroll the full list as before. The
 * original <select> stays in the DOM and keeps the value, so every form post,
 * server-side handler and existing onchange script carries on working — this
 * only changes how the value is picked.
 *
 * Applied automatically to every single-select with at least MIN_OPTIONS
 * entries. To opt a select out, add data-no-search. To force it on a short
 * list, add data-search.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  var MIN_OPTIONS = 8;      // below this, scrolling is not a problem
  var MAX_RENDERED = 300;   // keep the panel fast on very large lists

  function text(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim(); }

  // Option labels are database values — contact names arrive from the website
  // form and from portal lead emails, so they are never trusted as markup.
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  function shouldEnhance(sel) {
    if (sel.multiple || sel.disabled) return false;
    if (sel.hasAttribute('data-no-search')) return false;
    if (sel.dataset.ssApplied) return false;
    if (sel.hasAttribute('data-search')) return true;
    // Record pickers (property_id, contact_id, organisation_id, project_id,
    // applicant_id …) always get search: they are short today and long once the
    // database fills up, and they are exactly what people hunt for by name.
    if (/(^|_)id$/.test(sel.name || '')) return true;
    return sel.options.length >= MIN_OPTIONS;
  }

  function enhance(sel) {
    if (!shouldEnhance(sel)) return;
    sel.dataset.ssApplied = '1';

    var wrap = document.createElement('div');
    wrap.className = 'ss-wrap';
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.classList.add('ss-native');
    sel.setAttribute('tabindex', '-1');

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'ss-button';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    if (sel.id) button.setAttribute('aria-labelledby', 'label-for-' + sel.id);
    wrap.appendChild(button);

    var panel = document.createElement('div');
    panel.className = 'ss-panel';
    panel.hidden = true;
    panel.innerHTML =
      '<div class="ss-search"><input type="text" class="ss-input" placeholder="Search…" ' +
      'autocomplete="off" spellcheck="false" aria-label="Search options"></div>' +
      '<div class="ss-list" role="listbox"></div>' +
      '<div class="ss-more" hidden></div>';
    wrap.appendChild(panel);

    var input = panel.querySelector('.ss-input');
    var list  = panel.querySelector('.ss-list');
    var more  = panel.querySelector('.ss-more');

    var items = [];      // {value, label, group, disabled, index}
    var shown = [];      // currently rendered items
    var active = -1;

    function readOptions() {
      items = [];
      Array.prototype.forEach.call(sel.options, function (opt, i) {
        var group = opt.parentNode && opt.parentNode.tagName === 'OPTGROUP'
          ? opt.parentNode.label : '';
        items.push({
          value: opt.value,
          label: text(opt) || opt.value,
          search: ((group ? group + ' ' : '') + text(opt)).toLowerCase(),
          group: group,
          disabled: opt.disabled,
          index: i,
        });
      });
    }

    function syncButton() {
      var opt = sel.options[sel.selectedIndex];
      var label = opt ? text(opt) : '';
      var placeholder = !opt || opt.value === '';
      button.textContent = label || 'Select…';
      button.classList.toggle('ss-placeholder', placeholder);
      button.title = label;
    }

    function render(query) {
      var q = (query || '').trim().toLowerCase();
      shown = q ? items.filter(function (it) { return it.search.indexOf(q) !== -1; }) : items;
      var slice = shown.slice(0, MAX_RENDERED);
      var html = '';
      var lastGroup = null;
      slice.forEach(function (it, i) {
        if (it.group && it.group !== lastGroup) {
          html += '<div class="ss-group">' + esc(it.group) + '</div>';
          lastGroup = it.group;
        }
        html += '<div class="ss-option' +
          (it.index === sel.selectedIndex ? ' is-selected' : '') +
          (it.disabled ? ' is-disabled' : '') +
          '" role="option" data-i="' + i + '">' + esc(it.label) + '</div>';
      });
      list.innerHTML = html || '<div class="ss-empty">No matches</div>';
      more.hidden = shown.length <= MAX_RENDERED;
      if (!more.hidden) {
        more.textContent = 'Showing the first ' + MAX_RENDERED + ' of ' +
          shown.length + ' — keep typing to narrow it down.';
      }
      active = slice.length ? 0 : -1;
      highlight();
    }

    function highlight() {
      var rows = list.querySelectorAll('.ss-option');
      Array.prototype.forEach.call(rows, function (row, i) {
        row.classList.toggle('is-active', i === active);
      });
      if (active >= 0 && rows[active]) {
        var row = rows[active];
        var top = row.offsetTop, bottom = top + row.offsetHeight;
        if (top < list.scrollTop) list.scrollTop = top;
        else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
      }
    }

    function choose(i) {
      var it = shown[i];
      if (!it || it.disabled) return;
      sel.selectedIndex = it.index;
      // Existing page scripts listen for change on the real select.
      sel.dispatchEvent(new Event('change', {bubbles: true}));
      syncButton();
      close();
    }

    function open() {
      if (!panel.hidden) return;
      readOptions();
      render('');
      panel.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      // Flip upwards if there is not enough room below.
      var room = window.innerHeight - button.getBoundingClientRect().bottom;
      panel.classList.toggle('ss-up', room < 260);
      input.value = '';
      input.focus();
    }

    function close() {
      if (panel.hidden) return;
      panel.hidden = true;
      button.setAttribute('aria-expanded', 'false');
    }

    button.addEventListener('click', function (e) {
      e.preventDefault();
      panel.hidden ? open() : close();
    });

    input.addEventListener('input', function () { render(input.value); });

    input.addEventListener('keydown', function (e) {
      var rows = list.querySelectorAll('.ss-option');
      if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, rows.length - 1); highlight(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
      else if (e.key === 'Enter') { e.preventDefault(); choose(active); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); button.focus(); }
    });

    list.addEventListener('click', function (e) {
      var row = e.target.closest('.ss-option');
      if (row) choose(parseInt(row.dataset.i, 10));
    });

    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) close();
    });

    // Some pages rebuild a select's options in JavaScript (the enquiry form's
    // use-class list, for one). Keep the button label in step.
    new MutationObserver(function () {
      readOptions();
      syncButton();
      if (!panel.hidden) render(input.value);
    }).observe(sel, {childList: true, subtree: true});

    sel.addEventListener('change', syncButton);

    readOptions();
    syncButton();
  }

  function enhanceAll(root) {
    var selects = (root || document).querySelectorAll('select');
    Array.prototype.forEach.call(selects, enhance);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { enhanceAll(); });
  } else {
    enhanceAll();
  }

  // Selects added later (modals, injected rows) get picked up too.
  new MutationObserver(function (records) {
    records.forEach(function (r) {
      Array.prototype.forEach.call(r.addedNodes, function (n) {
        if (n.nodeType !== 1) return;
        if (n.tagName === 'SELECT') enhance(n);
        else if (n.querySelectorAll) enhanceAll(n);
      });
    });
  }).observe(document.documentElement, {childList: true, subtree: true});

  window.CRSearchableSelect = {enhance: enhance, enhanceAll: enhanceAll};
})();

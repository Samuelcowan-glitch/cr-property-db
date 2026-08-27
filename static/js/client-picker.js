/* Choosing the person a record is for.
 *
 * Searches the people already in the CRM and links the one picked. The name
 * shown carries their company in brackets, taken from their own record, so it
 * is never a second copy of the company's name.
 *
 * The choice is written into a hidden field belonging to the record's own
 * form, so it saves with everything else rather than on its own.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function show(pick, person) {
    var current = pick.querySelector('.clientpick-current');
    var choose = pick.querySelector('.clientpick-choose');
    var value = pick.querySelector('[data-client-value]');
    var act = current.querySelector('.clientpick-act');

    value.value = person ? person.id : '';
    current.innerHTML = '';

    if (person) {
      var name = el('a', 'clientpick-name', person.label);
      name.href = person.url;
      name.title = person.label;          /* the whole name on hover */
      current.appendChild(name);
      /* Only somebody who may edit sees the search box at all, so anything
         drawn here follows a choice they were allowed to make. */
      var bits = [person.job_title, person.email].filter(Boolean).join(' · ');
      if (bits) { current.appendChild(el('span', 'clientpick-meta', bits)); }
    }
    if (act) { current.appendChild(act); }

    current.hidden = !person;
    if (choose) { choose.hidden = !!person; }
  }

  function results(pick, rows) {
    var box = pick.querySelector('[data-client-results]');
    box.innerHTML = '';
    if (!rows.length) {
      box.appendChild(el('div', 'clientpick-empty', 'Nobody found by that name, company, email or number.'));
      box.hidden = false;
      return;
    }
    rows.forEach(function (row) {
      var hit = el('button', 'clientpick-hit');
      hit.type = 'button';
      hit.title = row.label;
      hit.appendChild(el('span', 'clientpick-hit-name', row.label));
      var bits = [row.job_title, row.email, row.phone].filter(Boolean).join(' · ');
      if (bits) { hit.appendChild(el('span', 'clientpick-hit-meta', bits)); }
      hit.addEventListener('click', function () {
        show(pick, row);
        box.hidden = true;
      });
      box.appendChild(hit);
    });
    box.hidden = false;
  }

  function wire(pick) {
    if (pick.dataset.clientReady) { return; }
    pick.dataset.clientReady = '1';

    var search = pick.querySelector('[data-client-search]');
    var timer = null;

    if (search) {
      search.addEventListener('input', function () {
        clearTimeout(timer);
        var q = search.value.trim();
        var box = pick.querySelector('[data-client-results]');
        if (q.length < 2) { box.hidden = true; return; }
        timer = setTimeout(function () {
          fetch('/api/contacts?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (rows) { results(pick, rows); })
            .catch(function () { box.hidden = true; });
        }, 180);
      });
      search.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); }
      });
    }

    pick.addEventListener('click', function (e) {
      var hit = e.target.closest('button');
      if (!hit || !pick.contains(hit)) { return; }
      if (hit.hasAttribute('data-client-change')) {
        pick.querySelector('.clientpick-current').hidden = true;
        pick.querySelector('.clientpick-choose').hidden = false;
        if (search) { search.value = ''; search.focus(); }
      } else if (hit.hasAttribute('data-client-clear')) {
        show(pick, null);
      }
    });
  }

  function start(root) {
    Array.prototype.forEach.call(
      (root || document).querySelectorAll('[data-client-picker]'), wire);
  }

  window.CRClientPicker = {start: start};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { start(); });
  } else { start(); }
}());

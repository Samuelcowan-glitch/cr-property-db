/* Select an existing organisation, or add one, without leaving the page.
 *
 * Every landlord, tenant and client field in the CRM uses this. It links an
 * organisation's id rather than copying its details, so the same company can
 * hold a different role somewhere else without a second record.
 *
 * Nothing typed on the page behind the picker is touched: linking and creating
 * both post on their own, and the page is never navigated away from.
 *
 * No dependencies, no CDN.
 */
(function () {
  'use strict';

  var TOKEN = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': TOKEN },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function say(pick, message, kind) {
    var note = pick.querySelector('.orgpick-note');
    if (!note) { return; }
    note.textContent = message || '';
    note.className = 'orgpick-note' + (kind ? ' is-' + kind : '');
    note.hidden = !message;
  }

  function target(pick) {
    var body = { role: pick.dataset.role };
    body[pick.dataset.targetKind + '_id'] = pick.dataset.targetId;
    return body;
  }

  /* Draw the linked organisation. Replaces the search box with a card that
     names the company, its status and who to speak to about this role. */
  function showLinked(pick, link) {
    var current = pick.querySelector('.orgpick-current');
    var choose = pick.querySelector('.orgpick-choose');
    current.innerHTML = '';

    var card = el('div', 'orgpick-card' + (link.do_not_contact ? ' is-dnc' : ''));
    var main = el('div', 'orgpick-main');

    var name = el('a', 'orgpick-name', link.trading_name || link.name);
    name.href = link.url;
    main.appendChild(name);

    var meta = el('div', 'orgpick-meta');
    var status = el('span',
      'org-status is-' + String(link.status || 'Prospect').toLowerCase().replace(/ /g, '-'),
      link.status || 'Prospect');
    meta.appendChild(status);
    (link.types || []).slice(0, 3).forEach(function (t) {
      meta.appendChild(el('span', 'org-type-tag', t));
    });
    main.appendChild(meta);

    var row = el('div', 'orgpick-contact');
    row.appendChild(document.createTextNode(
      'Contact for this ' + String(pick.dataset.role).toLowerCase() + ': '));
    var select = el('select', 'orgpick-contact-pick');
    select.appendChild(new Option(
      link.main_contact ? link.main_contact + ' (main contact)' : 'Not set', ''));
    row.appendChild(select);
    main.appendChild(row);

    var act = el('div', 'orgpick-act');
    ['Change', 'Unlink'].forEach(function (label) {
      var b = el('button', 'btn btn-outline btn-xs orgpick-' + label.toLowerCase(), label);
      b.type = 'button';
      act.appendChild(b);
    });

    card.appendChild(main);
    card.appendChild(act);
    current.appendChild(card);

    if (link.do_not_contact) {
      var warn = el('div', 'orgpick-dnc');
      warn.innerHTML = 'Marked <strong>Do not contact</strong> — keep out of bulk sends.';
      current.appendChild(warn);
    }

    current.hidden = false;
    choose.hidden = true;

    /* Fill the contact list from the organisation itself. */
    fetch('/api/organisations/' + link.organisation_id + '/contacts')
      .then(function (r) { return r.json(); })
      .then(function (people) {
        people.forEach(function (p) {
          var option = new Option(p.job_title ? p.name + ' — ' + p.job_title : p.name, p.id);
          option.selected = String(p.id) === String(link.contact_id);
          select.appendChild(option);
        });
      })
      .catch(function () { /* the card still works without the list */ });
  }

  function results(pick, rows) {
    var box = pick.querySelector('.orgpick-results');
    box.innerHTML = '';
    if (!rows.length) {
      box.appendChild(el('div', 'orgpick-empty',
        'Nothing found. Add it as a new organisation if it is not on the system.'));
      box.hidden = false;
      return;
    }
    rows.forEach(function (row) {
      var hit = el('button', 'orgpick-hit');
      hit.type = 'button';

      var top = el('span', 'orgpick-hit-name', row.trading_name || row.name);
      hit.appendChild(top);

      var meta = el('span', 'orgpick-hit-meta');
      meta.appendChild(el('span',
        'org-status is-' + String(row.status || 'Prospect').toLowerCase().replace(/ /g, '-'),
        row.status || 'Prospect'));
      (row.types || []).slice(0, 2).forEach(function (t) {
        meta.appendChild(el('span', 'org-type-tag', t));
      });
      if (row.main_contact) {
        meta.appendChild(el('span', 'orgpick-hit-contact', row.main_contact));
      }
      hit.appendChild(meta);

      hit.addEventListener('click', function () {
        var body = target(pick);
        body.organisation_id = row.id;
        post('/api/organisations/link', body).then(function (res) {
          if (res.data && res.data.ok) {
            showLinked(pick, res.data.link);
            say(pick, '');
          } else {
            say(pick, (res.data && res.data.error) || 'That could not be linked.', 'bad');
          }
        });
      });
      box.appendChild(hit);
    });
    box.hidden = false;
  }

  function search(pick) {
    var q = pick.querySelector('.orgpick-q').value.trim();
    var box = pick.querySelector('.orgpick-results');
    if (q.length < 2) { box.hidden = true; return; }
    fetch('/api/organisations?q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (rows) { results(pick, rows); })
      .catch(function () { say(pick, 'Could not search just now.', 'bad'); });
  }

  function showDuplicates(pick, rows) {
    var box = pick.querySelector('.orgpick-dupes');
    box.innerHTML = '';
    box.appendChild(el('div', 'orgpick-dupes-head', 'This may already be on the system'));
    rows.forEach(function (row) {
      var line = el('div', 'orgpick-dupe');
      var link = el('a', '', row.name);
      link.href = row.url;
      line.appendChild(link);
      line.appendChild(el('span', 'orgpick-dupe-why', 'Matches on ' + row.why.join(', ')));

      var use = el('button', 'btn btn-secondary btn-xs', 'Link this one');
      use.type = 'button';
      use.addEventListener('click', function () {
        var body = target(pick);
        body.organisation_id = row.id;
        post('/api/organisations/link', body).then(function (res) {
          if (res.data && res.data.ok) { showLinked(pick, res.data.link); }
        });
      });
      line.appendChild(use);
      box.appendChild(line);
    });

    var anyway = el('button', 'btn btn-outline btn-xs', 'It is a different company — create it');
    anyway.type = 'button';
    anyway.addEventListener('click', function () { create(pick, true); });
    box.appendChild(anyway);
    box.hidden = false;
  }

  function create(pick, confirmed) {
    var form = pick.querySelector('.orgpick-new');
    var types = [];
    form.querySelectorAll('.np-type:checked').forEach(function (c) { types.push(c.value); });
    var body = {
      name: form.querySelector('.np-name').value.trim(),
      trading_name: form.querySelector('.np-trading').value.trim(),
      fee_earner: form.querySelector('.np-earner').value.trim(),
      status: form.querySelector('.np-status').value,
      company_number: form.querySelector('.np-number').value.trim(),
      email: form.querySelector('.np-email').value.trim(),
      types: types
    };
    if (confirmed) { body.confirm_new = '1'; }

    post('/api/organisations/quick', body).then(function (res) {
      if (res.status === 409 && res.data.duplicates) {
        showDuplicates(pick, res.data.duplicates);
        return;
      }
      if (!res.data || !res.data.ok) {
        say(pick, (res.data && res.data.error) || 'That could not be created.', 'bad');
        return;
      }
      var link = target(pick);
      link.organisation_id = res.data.organisation.id;
      post('/api/organisations/link', link).then(function (r2) {
        if (r2.data && r2.data.ok) {
          showLinked(pick, r2.data.link);
        } else {
          say(pick, 'Created, but it could not be linked.', 'bad');
        }
      });
    });
  }

  function wire(pick) {
    var box = pick.querySelector('.orgpick-q');
    var timer = null;
    if (box) {
      box.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { search(pick); }, 180);
      });
      box.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); search(pick); }
      });
    }

    pick.addEventListener('click', function (e) {
      var hit = e.target.closest('button');
      if (!hit || !pick.contains(hit)) { return; }

      if (hit.classList.contains('orgpick-change')) {
        pick.querySelector('.orgpick-current').hidden = true;
        pick.querySelector('.orgpick-choose').hidden = false;
        if (box) { box.focus(); }
      } else if (hit.classList.contains('orgpick-unlink')) {
        if (!confirm('Unlink this organisation? The relationship is kept in its history.')) { return; }
        post('/api/organisations/unlink', target(pick)).then(function () {
          pick.querySelector('.orgpick-current').hidden = true;
          pick.querySelector('.orgpick-current').innerHTML = '';
          pick.querySelector('.orgpick-choose').hidden = false;
        });
      } else if (hit.classList.contains('orgpick-add')) {
        pick.querySelector('.orgpick-new').hidden = false;
        pick.querySelector('.orgpick-results').hidden = true;
        var name = pick.querySelector('.np-name');
        if (box && box.value.trim() && !name.value) { name.value = box.value.trim(); }
        name.focus();
      } else if (hit.classList.contains('orgpick-cancel')) {
        pick.querySelector('.orgpick-new').hidden = true;
        pick.querySelector('.orgpick-dupes').hidden = true;
      } else if (hit.classList.contains('orgpick-create')) {
        create(pick, false);
      }
    });

    /* Changing who handles this relationship saves on its own. */
    pick.addEventListener('change', function (e) {
      if (!e.target.classList.contains('orgpick-contact-pick')) { return; }
      var body = target(pick);
      var card = pick.querySelector('.orgpick-name');
      if (!card) { return; }
      body.organisation_id = (card.getAttribute('href') || '').split('/').pop();
      body.contact_id = e.target.value;
      post('/api/organisations/link', body).then(function (res) {
        say(pick, res.data && res.data.ok ? 'Contact saved.' : 'That could not be saved.',
            res.data && res.data.ok ? 'good' : 'bad');
        setTimeout(function () { say(pick, ''); }, 2500);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.orgpick').forEach(wire);
  });
}());

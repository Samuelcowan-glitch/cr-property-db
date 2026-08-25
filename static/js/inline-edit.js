/* Inline editing on record pages.
 *
 * A record page is itself the edit form: values are already in fields, and this
 * script looks after the surrounding behaviour —
 *
 *   • Save stays disabled until something actually changes
 *   • a warning before leaving with unsaved changes
 *   • one submission only, however many times Save is clicked
 *   • Ctrl/Cmd+S saves
 *
 * Mark the form with data-inline-edit and put a [data-save] button inside it.
 */
(function () {
  'use strict';

  function snapshot(form) {
    // Serialising the controls gives a value to compare against later without
    // needing to track each field individually.
    var out = [];
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.disabled || el.type === 'submit' || el.type === 'button') return;
      if (el.type === 'checkbox' || el.type === 'radio') out.push(el.name + '=' + (el.checked ? '1' : '0'));
      else out.push(el.name + '=' + el.value);
    });
    return out.join('');
  }

  function setup(form) {
    var save = form.querySelector('[data-save]');
    var status = form.querySelector('[data-save-status]');
    var initial = snapshot(form);
    var submitting = false;

    function dirty() { return snapshot(form) !== initial; }

    function refresh() {
      var changed = dirty();
      if (save) {
        save.disabled = !changed || submitting;
        save.classList.toggle('is-dirty', changed);
      }
      if (status) status.textContent = changed ? 'Unsaved changes' : '';
    }

    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);

    form.addEventListener('submit', function (e) {
      if (submitting) { e.preventDefault(); return; }   // no double posts
      submitting = true;
      if (save) {
        save.disabled = true;
        save.dataset.label = save.textContent;
        save.textContent = 'Saving…';
      }
      initial = snapshot(form);                          // stop the exit warning
    });

    window.addEventListener('beforeunload', function (e) {
      if (!dirty() || submitting) return;
      e.preventDefault();
      e.returnValue = '';                                // browsers show their own wording
    });

    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        if (!dirty()) return;
        e.preventDefault();
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      }
    });

    refresh();
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll('form[data-inline-edit]'), setup);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

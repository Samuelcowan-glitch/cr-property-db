/*
   Selecting several contacts at once.

   The bar is hidden until something is ticked, so the ordinary case — open a
   contact — is unchanged. Everything the bar does is a real form submission;
   this only counts what is selected and shows or hides the bar, so with
   scripting off the checkboxes and buttons still work, they are just always
   on screen.
*/
(function () {
  'use strict';

  var form = document.getElementById('bulk-form');
  if (!form) return;

  function boxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll('input[name="ids"][form="bulk-form"]'));
  }

  function refresh() {
    var picked = boxes().filter(function (b) { return b.checked; });
    var count = form.querySelector('[data-bulk-count]');
    if (count) count.textContent = String(picked.length);
    form.hidden = picked.length === 0;
    boxes().forEach(function (b) {
      var card = b.closest('.ct-card');
      if (card) card.classList.toggle('is-picked', b.checked);
    });
  }

  document.addEventListener('change', function (e) {
    if (e.target && e.target.name === 'ids') refresh();
  });

  document.addEventListener('click', function (e) {
    var all = e.target.closest && e.target.closest('[data-bulk-all]');
    var none = e.target.closest && e.target.closest('[data-bulk-none]');
    if (!all && !none) return;
    e.preventDefault();
    boxes().forEach(function (b) { b.checked = !!all; });
    refresh();
  });

  // A tag or a role is only ever added, never removed, so the confirmation is
  // about the number of people rather than about losing anything.
  form.addEventListener('submit', function (e) {
    var n = boxes().filter(function (b) { return b.checked; }).length;
    if (!n) { e.preventDefault(); return; }
    var action = (e.submitter && e.submitter.value) || '';
    if (action !== 'export' && n > 20 &&
        !window.confirm('Apply this to ' + n + ' contacts?')) {
      e.preventDefault();
    }
  });

  refresh();
}());

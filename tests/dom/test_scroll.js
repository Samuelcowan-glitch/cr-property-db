/* Does the page keep its place when a photo is moved? */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db";
const JS = fs.readFileSync(`${ROOT}/static/js/keep-scroll.js`, 'utf8');

let fails = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${ok ? '' : `\n        got ${JSON.stringify(got)}, wanted ${JSON.stringify(want)}`}`);
};

function build(url, scrollY, store) {
  const dom = new JSDOM(`<!doctype html><html><body>
    <div style="height:4000px">filler</div>
    <div class="box photo-box" data-keep-scroll>
      <form id="reorder" method="post" action="/reorder"><button type="submit">Move right</button></form>
      <form id="cover" method="post" action="/cover"><button type="submit">Make cover</button></form>
    </div>
    <form id="outside" method="post" action="/elsewhere"><button type="submit">Save something else</button></form>
    <button type="submit" form="upload-form" data-keep-scroll>Upload</button>
    <form id="upload-form" data-keep-scroll method="post" action="/upload"></form>
  </body></html>`, { url, runScripts: 'outside-only', pretendToBeVisual: true });

  const { window } = dom;
  window.HTMLFormElement.prototype.submit = () => {};
  const mem = store || {};
  Object.defineProperty(window, 'sessionStorage', {
    value: {
      getItem: k => (k in mem ? mem[k] : null),
      setItem: (k, v) => { mem[k] = String(v); },
      removeItem: k => { delete mem[k]; },
    }, configurable: true
  });
  let scrolledTo = null;
  window.scrollTo = (x, y) => { scrolledTo = y; };
  Object.defineProperty(window, 'scrollY', { value: scrollY, configurable: true });
  window.eval(JS);
  window.dispatchEvent(new window.Event('load'));
  return { window, mem, scrolled: () => scrolledTo };
}

console.log('\nSaving the position when a photo control is used:');
{
  const a = build('https://crm.test/projects/7', 2480, {});
  a.window.document.getElementById('reorder')
    .dispatchEvent(new a.window.Event('submit', { bubbles: true, cancelable: true }));
  const saved = JSON.parse(a.mem['cr-scroll'] || '{}');
  check('the arrows save the position', saved.y, 2480);
  check('saved against this page', saved.path, '/projects/7');
}
{
  const b = build('https://crm.test/projects/7', 1900, {});
  b.window.document.getElementById('cover')
    .dispatchEvent(new b.window.Event('submit', { bubbles: true, cancelable: true }));
  check('setting a cover saves it', JSON.parse(b.mem['cr-scroll'] || '{}').y, 1900);
}
{
  const c = build('https://crm.test/projects/7', 3100, {});
  c.window.document.querySelector('button[form=upload-form]').click();
  check('uploading saves it (button outside its form)',
        JSON.parse(c.mem['cr-scroll'] || '{}').y, 3100);
}
{
  const d = build('https://crm.test/projects/7', 1200, {});
  d.window.document.getElementById('outside')
    .dispatchEvent(new d.window.Event('submit', { bubbles: true, cancelable: true }));
  check('an unrelated form saves nothing', d.mem['cr-scroll'], undefined);
}

console.log('\nPutting it back when the page returns:');
{
  const saved = { 'cr-scroll': JSON.stringify({ path: '/projects/7', y: 2480, at: Date.now() }) };
  const e = build('https://crm.test/projects/7', 0, saved);
  check('the page returns to where it was', e.scrolled(), 2480);
  check('and the note is cleared, so a later visit starts at the top',
        e.mem['cr-scroll'], undefined);
}
{
  const saved = { 'cr-scroll': JSON.stringify({ path: '/projects/7', y: 2480, at: Date.now() }) };
  const f = build('https://crm.test/projects/99', 0, saved);
  check('a different record is not moved', f.scrolled(), null);
}
{
  const stale = { 'cr-scroll': JSON.stringify({ path: '/projects/7', y: 2480, at: Date.now() - 120000 }) };
  const g = build('https://crm.test/projects/7', 0, stale);
  check('a stale position from minutes ago is ignored', g.scrolled(), null);
}

console.log(fails ? `\n${fails} FAILURES` : '\nALL SCROLL CHECKS PASSED');
process.exit(fails ? 1 : 0);

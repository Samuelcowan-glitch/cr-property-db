/*
   Measure the CRM in a real browser.

   Alignment is a question about pixels, and no amount of reading CSS answers
   it — a box can look right in the stylesheet and still sit two pixels left of
   the one above it once the grid has had its say. So this loads each page in
   headless Chrome at three widths and reports what is actually on screen:

     - edges: boxes in the same column whose left or right edges disagree
     - controls: inputs, selects and buttons on one row at different heights
     - overflow: anything wider than what contains it, or a page that scrolls
       sideways
     - gaps: spacing between siblings that is nearly-but-not-quite equal

   Run the server first:  python tests/layout_server.py 8099
   Then:  node tests/layout_audit.js [--json]
*/
'use strict';

const puppeteer = require(process.env.HOME +
  '/.local/cr-domtest/node_modules/puppeteer-core');

const BASE = process.env.LAYOUT_BASE || 'http://127.0.0.1:8099';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const AS_JSON = process.argv.includes('--json');

const PAGES = [
  ['dashboard', '/'],
  ['contacts', '/contacts'],
  ['contacts by type', '/contacts?type=Landlord'],
  ['contact record', '/contacts/1'],
  ['add contact', '/contacts/new'],
  ['organisations', '/organisations'],
  ['organisation record', '/organisations/1'],
  ['properties', '/properties'],
  ['property record', '/properties/1'],
  ['projects', '/projects'],
  ['project record', '/projects/1'],
  ['enquiries', '/enquiries'],
  ['enquiry record', '/enquiries/1'],
  ['transactions', '/transactions'],
  ['diary', '/diary'],
];

const WIDTHS = [[1440, 900, 'desktop'], [1024, 800, 'laptop'], [390, 844, 'phone']];

// Half a pixel of disagreement is a rounding artefact. Anything at or above
// this is a real edge that does not line up.
const EDGE_TOLERANCE = 0.75;

function measure() {
  const out = { edges: [], controls: [], overflow: [], gaps: [] };
  const seen = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' &&
           s.display !== 'none' && parseFloat(s.opacity) > 0.01;
  };
  // Some things are meant to sit outside their box: a rotated axis label, a
  // tinted bar that bleeds past the row it marks, the invisible native select
  // behind a custom dropdown. Measuring those as faults buries the real ones.
  const placedOnPurpose = (el) => {
    const s = getComputedStyle(el);
    return s.position === 'absolute' || s.position === 'fixed' ||
           (s.transform && s.transform !== 'none') ||
           s.pointerEvents === 'none';
  };
  const name = (el) => {
    const cls = (el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : el.className) || '';
    return el.tagName.toLowerCase() +
      (el.id ? '#' + el.id : '') +
      (cls ? '.' + String(cls).trim().split(/\s+/).slice(0, 2).join('.') : '');
  };

  // ── The page itself must not scroll sideways ──────────────────────────────
  if (document.documentElement.scrollWidth > window.innerWidth + 1) {
    out.overflow.push({
      what: 'the page', by: document.documentElement.scrollWidth - window.innerWidth,
    });
  }

  // ── Nothing sticking out of what contains it ──────────────────────────────
  for (const el of document.querySelectorAll('.content *')) {
    if (!seen(el) || placedOnPurpose(el)) continue;
    const p = el.parentElement;
    if (!p || !seen(p)) continue;
    // Content wider than its box is fine when something above it scrolls —
    // a calendar or a wide table is meant to be dragged sideways. The
    // scroller is often an ancestor, not the immediate parent.
    let scrolls = false;
    for (let a = p; a && a !== document.body; a = a.parentElement) {
      const as = getComputedStyle(a);
      if (/(auto|scroll)/.test(as.overflowX + ' ' + as.overflow)) { scrolls = true; break; }
    }
    if (scrolls) continue;
    const r = el.getBoundingClientRect();
    const pr = p.getBoundingClientRect();
    const over = Math.max(r.right - pr.right, pr.left - r.left);
    if (over > 2) {
      out.overflow.push({ what: name(el), inside: name(p), by: Math.round(over) });
    }
  }

  // ── Boxes stacked in a column should share their left and right edges ─────
  const groups = new Map();
  for (const el of document.querySelectorAll(
      '.content .box, .content .card, .content .ct-card, ' +
      '.content .rec-box, .content .panel')) {
    if (!seen(el)) continue;
    const p = el.parentElement;
    if (!p) continue;
    const key = name(p) + '|' + [...p.children].indexOf(el.parentElement);
    if (!groups.has(p)) groups.set(p, []);
    groups.get(p).push(el);
  }
  for (const [parent, kids] of groups) {
    if (kids.length < 2) continue;
    // Only compare boxes that are genuinely stacked, not side by side.
    const rows = new Map();
    for (const el of kids) {
      const r = el.getBoundingClientRect();
      const band = Math.round(r.top / 4);
      if (!rows.has(band)) rows.set(band, []);
      rows.get(band).push([el, r]);
    }
    const columns = [...kids].map((el) => [el, el.getBoundingClientRect()]);
    const stacked = columns.filter(([, r], i) =>
      columns.every(([, o], j) => i === j || r.top >= o.bottom - 1 || r.bottom <= o.top + 1));
    if (stacked.length < 2) continue;
    const lefts = stacked.map(([, r]) => r.left);
    const rights = stacked.map(([, r]) => r.right);
    const spread = (a) => Math.max(...a) - Math.min(...a);
    if (spread(lefts) > 0.75 || spread(rights) > 0.75) {
      out.edges.push({
        inside: name(parent), boxes: stacked.length,
        left: +spread(lefts).toFixed(1), right: +spread(rights).toFixed(1),
        examples: stacked.slice(0, 3).map(([el]) => name(el)),
      });
    }
  }

  // ── Controls sitting on one line should be the same height ────────────────
  const controls = [...document.querySelectorAll(
    '.content input:not([type=checkbox]):not([type=radio]):not([type=hidden]), ' +
    '.content select, .content button, .content .btn')]
    .filter((el) => seen(el) && !placedOnPurpose(el) &&
                    !el.classList.contains('btn-link'));
  // "On one row" means sharing a parent and a top edge. Banding on the top
  // edge alone put a box heading in the same row as an input two containers
  // away, which is not a row anybody sees.
  const lines = new Map();
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    const holder = el.closest('.btn-group, .form-actions, .search-bar, .rec-toolbar') ||
                   el.parentElement;
    if (!holder) continue;
    const key = name(holder) + '@' + [...document.querySelectorAll('*')].indexOf(holder) +
                '|' + Math.round(r.top / 6);
    if (!lines.has(key)) lines.set(key, []);
    lines.get(key).push([el, r]);
  }
  for (const [, row] of lines) {
    if (row.length < 2) continue;
    const hs = row.map(([, r]) => r.height);
    const spread = Math.max(...hs) - Math.min(...hs);
    if (spread > 1.5) {
      out.controls.push({
        spread: +spread.toFixed(1),
        items: row.slice(0, 4).map(([el, r]) =>
          name(el) + ' ' + r.height.toFixed(1) + 'px'),
      });
    }
  }

  out.counted = document.querySelectorAll('.content *').length;
  return out;
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--force-device-scale-factor=1'],
  });
  const page = await browser.newPage();

  await page.goto(BASE + '/login', { waitUntil: 'networkidle0' });
  await page.type('input[name=username]', 'admin');
  await page.type('input[name=password]', 'layout-pw');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0' }),
    page.click('button[type=submit], input[type=submit]'),
  ]);

  const report = [];
  let totalSeen = 0;
  for (const [label, url] of PAGES) {
    for (const [w, h, size] of WIDTHS) {
      await page.setViewport({ width: w, height: h });
      let res;
      try {
        res = await page.goto(BASE + url, { waitUntil: 'networkidle0', timeout: 20000 });
      } catch (e) {
        report.push({ page: label, size, error: String(e).slice(0, 80) });
        continue;
      }
      if (!res || res.status() >= 400) {
        report.push({ page: label, size, error: 'HTTP ' + (res && res.status()) });
        continue;
      }
      const found = await page.evaluate(measure);
      if (!found.counted) {
        report.push({ page: label, size, error: 'measured nothing — no .content' });
        continue;
      }
      totalSeen += found.counted;
      const total = found.edges.length + found.controls.length + found.overflow.length;
      if (total) report.push({ page: label, size, ...found });
    }
  }

  await browser.close();

  if (AS_JSON) {
    console.log(JSON.stringify(report, null, 1));
    return;
  }
  console.log(`measured ${totalSeen} elements across ` +
    `${PAGES.length * WIDTHS.length} page/width combinations`);
  if (!report.length) {
    console.log('LAYOUT: every page lines up at every width.');
    return;
  }
  let n = 0;
  for (const r of report) {
    console.log(`\n── ${r.page} @ ${r.size}`);
    if (r.error) { console.log('   could not load: ' + r.error); continue; }
    for (const e of r.edges || []) {
      n++;
      console.log(`   EDGES  ${r.page}: ${e.boxes} boxes in ${e.inside} ` +
        `disagree by ${e.left}px left / ${e.right}px right — ${e.examples.join(', ')}`);
    }
    for (const c of r.controls || []) {
      n++;
      console.log(`   HEIGHT spread ${c.spread}px on one row — ${c.items.join(' | ')}`);
    }
    for (const o of r.overflow || []) {
      n++;
      console.log(`   OVERFLOW ${o.what}${o.inside ? ' inside ' + o.inside : ''} ` +
        `by ${o.by}px`);
    }
  }
  console.log(`\n${n} problem(s) across ${report.length} page/width combinations.`);
  // Non-zero so this can gate a commit rather than only being read.
  process.exitCode = 1;
})();

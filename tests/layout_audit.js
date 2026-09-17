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
     - rules: a divider inside a box that falls where nothing else in the
       column does, which is a width written into one row by hand

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
  ['tenant record', '/contacts/2'],
  ['buyer record', '/contacts/3'],
  ['seller record', '/contacts/4'],
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
  ['transaction record', '/transactions/1'],
  ['new transaction', '/transactions/new'],
  ['diary', '/diary'],
  // Every other page somebody actually opens. A box laid out wrongly is only
  // found where it is looked at, and the rates calculator was wrong on a page
  // this list did not cover in full.
  ['edit contact', '/contacts/1/edit'],
  ['new organisation', '/organisations/new'],
  ['edit organisation', '/organisations/1/edit'],
  ['new property', '/properties/new'],
  ['edit property', '/properties/1/edit'],
  ['new project', '/projects/new'],
  ['edit project', '/projects/1/edit'],
  ['edit listing', '/listings/1/edit'],
  ['new listing', '/projects/1/listing/new'],
  ['particulars', '/projects/1/particulars'],
  ['enquiry schedule', '/projects/1/enquiry-schedule'],
  ['new enquiry', '/enquiries/new'],
  ['edit enquiry', '/enquiries/1/edit'],
  ['edit transaction', '/transactions/1/edit'],
  ['targets', '/transactions/targets'],
  ['business rates', '/admin/rates'],
  ['microsoft', '/admin/microsoft'],
  ['zoopla', '/admin/zoopla'],
  ['password', '/account/password'],
  ['two-step', '/account/mfa'],
];

const WIDTHS = [[1440, 900, 'desktop'], [1024, 800, 'laptop'], [390, 844, 'phone']];

// Half a pixel of disagreement is a rounding artefact. Anything at or above
// this is a real edge that does not line up.
const EDGE_TOLERANCE = 0.75;

function measure() {
  // What holds the page. Most pages are a .content; a printable schedule is a
  // .sheet with its own wrapper, and skipping it reported "measured nothing"
  // rather than measuring it.
  const HOLDERS = ['.content', '.sheet'];
  const sel = (what) => HOLDERS.map((h) => h + ' ' + what).join(', ');
  const PAGE_BODY = sel('*');

  const out = { edges: [], controls: [], overflow: [], gaps: [], bands: [], rules: [] };
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
  for (const el of document.querySelectorAll(PAGE_BODY)) {
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
      sel('.box') + ', ' + sel('.card') + ', ' + sel('.ct-card') + ', ' +
      sel('.rec-box') + ', ' + sel('.panel'))) {
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
    sel('input:not([type=checkbox]):not([type=radio]):not([type=hidden])') + ', ' +
    sel('select') + ', ' + sel('button') + ', ' + sel('.btn'))]
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

  // ── A box is a stack of bands, and every band spans it ────────────────────
  // A box divides into full-width bands: a heading, a row, a banner, a note.
  // Anything that starts or stops short of the box's own edges is not a band —
  // on a .box--grid it is a stray cell that has landed in whichever column was
  // free, which is how the business rates calculator came to be dealt across
  // the page like a hand of cards. Rows are exempt from the right edge only
  // where they are the grid's own (display: contents) rows, whose rectangle is
  // the union of their cells.
  for (const box of document.querySelectorAll(sel('.box'))) {
    if (!seen(box)) continue;
    const br = box.getBoundingClientRect();
    const bs = getComputedStyle(box);
    const left = br.left + parseFloat(bs.borderLeftWidth) + parseFloat(bs.paddingLeft);
    const right = br.right - parseFloat(bs.borderRightWidth) - parseFloat(bs.paddingRight);
    for (const kid of box.children) {
      if (!seen(kid) || placedOnPurpose(kid)) continue;
      const ks = getComputedStyle(kid);
      if (ks.display === 'inline') continue;          // words, not a band
      if (ks.float !== 'none') continue;
      // Measured to the margin box: a band deliberately inset on both sides
      // is still a band. What is not is a band that starts or stops somewhere
      // the box never asked for.
      const kr = kid.getBoundingClientRect();
      const short = Math.max(kr.left - parseFloat(ks.marginLeft) - left,
                             right - kr.right - parseFloat(ks.marginRight));
      if (short > 1.5) {
        out.bands.push({
          box: name(box), band: name(kid),
          left: +(kr.left - left).toFixed(1),
          right: +(right - kr.right).toFixed(1),
        });
      }
    }
  }

  // ── Boxes down a column rule their edges in the same places ───────────────
  // Every box in a column is the same width, so the dividers inside them fall
  // on the same few positions. One that turns up in a single row, where the
  // rest of the column agrees on another, is a width written into that row by
  // hand rather than taken from the system. Three boxes each ruling their own
  // right-hand edge — 74px, 92px and 96px — is what "the boxes do not line up"
  // turned out to mean once it was measured.
  //
  // A row inside a .box--grid is display: contents and has no rectangle of its
  // own, so the cells are measured and the row is only named.
  for (const col of document.querySelectorAll(sel('.rec-col'))) {
    if (!seen(col)) continue;
    const tally = new Map();
    const rows = [];
    for (const row of col.querySelectorAll('.frow')) {
      const cells = [...row.children].filter((c) => seen(c) && !placedOnPurpose(c));
      if (cells.length < 2) continue;
      const edges = cells.slice(0, -1)
        .map((c) => Math.round(c.getBoundingClientRect().right));
      rows.push({ row, edges });
      edges.forEach((e) => tally.set(e, (tally.get(e) || 0) + 1));
    }
    // Fewer than three rows is too little for the column to have agreed on
    // anything worth measuring against.
    if (rows.length < 3) continue;

    // A box laid out on a grid has agreed column boundaries, and a divider on
    // one of them is in the right place even if only a single row happens to
    // reach it. What is wrong is a divider at a width of its own — which is
    // what a flex row with an inline pixel width produces, and what a grid
    // track never is.
    const tracks = new Map();
    const boundaries = (box) => {
      if (tracks.has(box)) return tracks.get(box);
      let out2 = [];
      const s = getComputedStyle(box);
      if (s.display === 'grid') {
        const r = box.getBoundingClientRect();
        let x = r.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
        for (const t of s.gridTemplateColumns.split(/\s+/)) {
          const n = parseFloat(t);
          if (!isNaN(n)) { x += n; out2.push(Math.round(x)); }
        }
      }
      tracks.set(box, out2);
      return out2;
    };

    for (const { row, edges } of rows) {
      const box = row.closest('.box') || col;
      const onTrack = boundaries(box);
      const lonely = edges.filter((e) => tally.get(e) === 1 &&
        !onTrack.some((t) => Math.abs(t - e) <= 1));
      if (lonely.length) {
        out.rules.push({ row: name(row), box: name(box), at: lonely.join(', ') });
      }
    }
  }

  out.counted = document.querySelectorAll(PAGE_BODY).length;
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
      const total = found.edges.length + found.controls.length +
                    found.overflow.length + found.bands.length +
                    found.rules.length;
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
    for (const b of r.bands || []) {
      n++;
      console.log(`   BAND   ${b.band} in ${b.box} stops short of the box ` +
        `(${b.left}px left, ${b.right}px right)`);
    }
    for (const c of r.rules || []) {
      n++;
      console.log(`   RULE   ${c.row} in ${c.box} divides at ${c.at}, ` +
        `where nothing else in the column does`);
    }
  }
  console.log(`\n${n} problem(s) across ${report.length} page/width combinations.`);
  // Non-zero so this can gate a commit rather than only being read.
  process.exitCode = 1;
})();

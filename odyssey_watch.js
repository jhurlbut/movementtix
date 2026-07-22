#!/usr/bin/env node
/*
 * odyssey_watch.js
 * Checks AMC Metreon 16 for "The Odyssey" in IMAX 70mm and reports NIGHT shows
 * (>= 5:00pm) that have two AVAILABLE seats together in row C or deeper (i.e. not
 * the front two rows; configurable via WATCH_MIN_ROW) and not wheelchair/companion.
 *
 * Target days: every day of the week by default (override with WATCH_DOW),
 * starting tomorrow (no date floor by default; set WATCH_START to add one).
 * Scans the next N qualifying dates.
 *
 * Runs headless Chromium through the session egress proxy. AMC sits behind a
 * Queue-it waiting room + Cloudflare WAF that 403s the JS bundles, but the page
 * ships its showtime + seat-map data server-rendered in the HTML (Next.js RSC),
 * so we parse that directly.
 *
 * Output: a JSON block between <RESULT> ... </RESULT> plus a human summary.
 * Exit 0 always (errors are reported in JSON).
 */
const { chromium } = require('/opt/node22/lib/node_modules/playwright');

const THEATER = 'amc-metreon-16';
const THEATER_PATH = 'san-francisco/amc-metreon-16';
const MOVIE = 'the-odyssey';
const FORMAT = 'imax70mm';
const START_DATE = process.env.WATCH_START || '2000-01-01'; // inclusive floor; default = no floor (scan from tomorrow onward)
// Days of week to scan (Sun=0 .. Sat=6). Default: all 7 days. Override with WATCH_DOW="0,1,3,6".
const TARGET_DOW = new Set((process.env.WATCH_DOW || '0,1,2,3,4,5,6').split(',').map(n => parseInt(n.trim(), 10)));
const NIGHT_MIN_HOUR = 17; // 5:00pm local and later
const MIN_ROW = (process.env.WATCH_MIN_ROW || 'C').toUpperCase(); // exclude rows nearer the screen than this (A=front)
const MAX_DATES = parseInt(process.env.WATCH_MAX_DATES || '6', 10);
const PROXY = process.env.HTTPS_PROXY || 'http://127.0.0.1:44409';

const LAUNCH = {
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  headless: true,
  proxy: { server: PROXY },
  args: ['--no-sandbox', '--ssl-version-max=tls1.2', '--disable-http2',
    '--disable-features=PostQuantumKyber,X25519Kyber768Draft00,X25519MLKEM768,EncryptedClientHello'],
};
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

function pad(n) { return String(n).padStart(2, '0'); }
function fmtDate(d) { return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; }

// Next MAX_DATES qualifying dates on/after max(today+1, START_DATE)
function targetDates() {
  const today = new Date();
  const startMin = new Date(START_DATE + 'T00:00:00Z');
  let cur = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
  cur.setUTCDate(cur.getUTCDate() + 1);
  if (cur < startMin) cur = startMin;
  const out = [];
  for (let i = 0; i < 120 && out.length < MAX_DATES; i++) {
    // getUTCDay on a YYYY-MM-DD midnight-UTC date gives the calendar weekday
    if (TARGET_DOW.has(cur.getUTCDay())) out.push(fmtDate(cur));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

function rscBlob(html) {
  const pieces = [];
  const re = /self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)/g;
  let m;
  while ((m = re.exec(html))) pieces.push(m[1]);
  let s = pieces.join('');
  // decode the JS string escapes
  try { s = JSON.parse('"' + s.replace(/"/g, '\\"') + '"'); } catch (e) {
    s = s.replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\\\/g, '\\').replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
  }
  return s;
}

function parseShowtimes(blob) {
  const pat = /"showtimeId":(\d+),"policyCodes":\[[^\]]*\],"hasTrailers":\w+,"status":"([^"]+)","showDateTimeUtc":"([^"]+)","display":\{"time":"([^"]+)","amPm":"([^"]+)"\}[\s\S]*?"aria-describedby":"([^"]+)"/g;
  const out = [];
  let m;
  while ((m = pat.exec(blob))) {
    out.push({ id: m[1], status: m[2], utc: m[3], time: m[4] + m[5], amPm: m[5], hour12: parseInt(m[4].split(':')[0], 10), aria: m[6] });
  }
  return out;
}

function isNight(s) {
  let h = s.hour12 % 12;
  if (s.amPm === 'pm') h += 12;
  return h >= NIGHT_MIN_HOUR;
}

function parseSeatLayout(blob) {
  const m = /"seatingLayout":\{"columns":(\d+),"rows":(\d+),"seats":\[/.exec(blob);
  if (!m) return null;
  let start = m.index + m[0].length - 1, depth = 0, end = -1;
  for (let k = start; k < blob.length; k++) {
    const c = blob[k];
    if (c === '[') depth++;
    else if (c === ']') { depth--; if (depth === 0) { end = k + 1; break; } }
  }
  if (end < 0) return null;
  try { return JSON.parse(blob.slice(start, end)); } catch (e) { return null; }
}

// Find adjacent available regular-seat pairs in row MIN_ROW or deeper; best few by centrality.
function findPairs(seats) {
  const disp = seats.filter(s => s.shouldDisplay);
  const byRow = {};
  for (const s of disp) {
    const letter = (s.name.match(/^([A-Z]+)/) || [])[1] || '?';
    (byRow[letter] = byRow[letter] || []).push(s);
  }
  const pairs = [];
  for (const [letter, rs] of Object.entries(byRow)) {
    if (letter.length > 1 || letter < MIN_ROW) continue; // exclude rows nearer the screen than MIN_ROW (A=front)
    rs.sort((a, b) => a.column - b.column);
    const nums = rs.map(s => parseInt(s.name.slice(letter.length), 10)).filter(n => !isNaN(n));
    const center = (Math.min(...nums) + Math.max(...nums)) / 2;
    for (let i = 0; i < rs.length - 1; i++) {
      const a = rs[i], b = rs[i + 1];
      if (a.column + 1 !== b.column) continue;
      if (a.available && b.available && a.type === 'CanReserve' && b.type === 'CanReserve') {
        const na = parseInt(a.name.slice(letter.length), 10), nb = parseInt(b.name.slice(letter.length), 10);
        const dist = (Math.abs(na - center) + Math.abs(nb - center)) / 2;
        pairs.push({ row: letter, seats: [a.name, b.name], centerDist: +dist.toFixed(1) });
      }
    }
  }
  pairs.sort((x, y) => x.centerDist - y.centerDist);
  return pairs;
}

async function fetchHtml(ctx, url, needle) {
  const page = await ctx.newPage();
  let status = 0, endedInQueue = false;
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    status = resp ? resp.status() : 0;
    for (let i = 0; i < 40; i++) {
      const u = page.url();
      if (!/queue|waitingroom/i.test(u) && (u.includes('/showtimes') || u.includes('/seats'))) break;
      await page.waitForTimeout(3000);
    }
    endedInQueue = /queue|waitingroom/i.test(page.url());
    await page.waitForTimeout(2500);
    let html = await page.content();
    if (needle && !html.includes(needle)) { await page.waitForTimeout(4000); html = await page.content(); }
    return { html, status, endedInQueue };
  } finally { await page.close().catch(() => {}); }
}

// Canary: hit the live showtimes page (redirects to "today", always packed with
// on-sale shows). If it does not come back with a healthy, parseable set of
// showtimes, the scraper is blocked or AMC's markup changed — i.e. broken, and
// a "NONE" result can no longer be trusted.
async function healthCheck(ctx) {
  const url = `https://www.amctheatres.com/movie-theatres/${THEATER_PATH}/showtimes`;
  let c;
  try { c = await fetchHtml(ctx, url); }
  catch (e) { return { ok: false, reason: `canary fetch failed: ${e.message}`, canaryShowtimes: 0 }; }
  const blob = rscBlob(c.html);
  const shows = parseShowtimes(blob).length;
  const hasMovies = /\/movies\/[a-z0-9-]+-\d+/.test(blob);
  const info = { canaryStatus: c.status, canaryBlobBytes: blob.length, canaryShowtimes: shows, canaryHasMovies: hasMovies };
  if (c.endedInQueue) return { ok: false, reason: 'stuck in the Queue-it waiting room (never cleared)', ...info };
  if (c.status && c.status >= 400) return { ok: false, reason: `canary page returned HTTP ${c.status}`, ...info };
  if (blob.length < 5000 || !hasMovies) return { ok: false, reason: `canary returned no usable page data (blob ${blob.length} bytes, movies present: ${hasMovies}) — likely blocked/WAF`, ...info };
  if (shows === 0) return { ok: false, reason: 'canary page loaded but parsed 0 showtimes for any movie — AMC markup may have changed (parser broken)', ...info };
  return { ok: true, reason: '', ...info };
}

async function mapLimit(items, limit, fn) {
  const res = new Array(items.length);
  let idx = 0;
  async function worker() { while (idx < items.length) { const i = idx++; res[i] = await fn(items[i], i); } }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return res;
}

(async () => {
  const result = { checkedAtUtc: new Date().toISOString(), start: START_DATE, dates: [], finds: [], errors: [] };
  const dates = targetDates();
  result.datesChecked = dates;
  const browser = await chromium.launch(LAUNCH);
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, userAgent: UA, viewport: { width: 1200, height: 1400 }, locale: 'en-US', timezoneId: 'America/Los_Angeles' });
  try {
    // Phase 0: health / block detection via a known-busy canary page
    result.health = await healthCheck(ctx);

    // Phase 1: showtimes per date (concurrency 3)
    const perDate = await mapLimit(dates, 3, async (date) => {
      try {
        const url = `https://www.amctheatres.com/movie-theatres/${THEATER_PATH}/showtimes/all/${date}/${THEATER}/all`;
        const { html } = await fetchHtml(ctx, url);
        const blob = rscBlob(html);
        const shows = parseShowtimes(blob).filter(s => s.aria.includes(MOVIE) && s.aria.includes(FORMAT));
        const night = shows.filter(isNight);
        return { date, total: shows.length, night: night.map(s => ({ id: s.id, time: s.time, status: s.status })) };
      } catch (e) { result.errors.push(`showtimes ${date}: ${e.message}`); return { date, total: 0, night: [], error: true }; }
    });
    result.dates = perDate;

    // Phase 2: seat maps for night shows that are not sold out (concurrency 3)
    const toCheck = [];
    for (const d of perDate) for (const s of d.night) if (!/sold\s*out/i.test(s.status)) toCheck.push({ date: d.date, ...s });
    result.seatChecks = toCheck.length;
    await mapLimit(toCheck, 3, async (s) => {
      try {
        const { html } = await fetchHtml(ctx, `https://www.amctheatres.com/showtimes/${s.id}/seats`, 'seatingLayout');
        const seats = parseSeatLayout(rscBlob(html));
        if (!seats) { result.errors.push(`no layout ${s.date} ${s.time}`); return; }
        const pairs = findPairs(seats);
        if (pairs.length) result.finds.push({ date: s.date, time: s.time, status: s.status, id: s.id, bestPairs: pairs.slice(0, 4), pairCount: pairs.length });
      } catch (e) { result.errors.push(`seats ${s.date} ${s.time}: ${e.message}`); }
    });
  } finally { await browser.close().catch(() => {}); }

  // sort finds by date then time
  result.finds.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
  console.log('<RESULT>' + JSON.stringify(result) + '</RESULT>');
  console.log('\n=== Odyssey IMAX 70mm — non-front-row pairs watch ===');
  console.log('Checked at:', result.checkedAtUtc, '| dates:', dates.join(', '));
  const h = result.health || { ok: false, reason: 'health check did not run' };
  if (!h.ok) {
    console.log(`HEALTH: BROKEN — ${h.reason}`);
    console.log('RESULT: UNKNOWN — the watcher is blocked or broken, so "no seats" cannot be trusted. This needs attention.');
    return;
  }
  console.log(`HEALTH: OK (canary showtimes: ${h.canaryShowtimes})`);
  if (result.finds.length === 0) {
    const anyShows = result.dates.some(d => d.total > 0);
    console.log(anyShows
      ? 'RESULT: NONE — shows are listed but no two-together non-front-row seats are available.'
      : 'RESULT: NONE — no Odyssey IMAX 70mm night showtimes on sale yet for these dates.');
  } else {
    console.log(`RESULT: FOUND ${result.finds.length} show(s) with non-front-row pairs:`);
    for (const f of result.finds) {
      const p = f.bestPairs.map(x => `${x.seats[0]}+${x.seats[1]} (row ${x.row})`).join(', ');
      console.log(`  ${f.date} ${f.time} [${f.status}] — ${f.pairCount} pair(s); best: ${p}`);
    }
  }
  if (result.errors.length) console.log('Notes:', result.errors.join(' | '));
})();

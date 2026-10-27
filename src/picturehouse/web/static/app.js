/* PictureHouse web console.
   Vanilla JS, no build step. Ported from docs/design/ui-mockup.html and wired to docs/api.md.
   Every piece of API-provided text goes through esc() before it is put into HTML. */
(() => {
'use strict';
const $ = s => document.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => v == null ? '' : String(v).replace(/[&<>"']/g, c => ESC_MAP[c]);
const enc = encodeURIComponent;
const sleep = ms => new Promise(r => setTimeout(r, ms));
/* Inside the PictureHouse Android app the page gets a native bridge (see Android APK/). */
const ANDROID = window.PictureHouseAndroid || null;
const androidKeepAwake = on => { try { if (ANDROID) ANDROID.keepScreenOn(!!on); } catch (e) { /* bridge gone */ } };

/* HLS sessions are per player (c=), so two tabs never share and close one encoder. */
const PLAYER_ID = Math.random().toString(36).slice(2, 10);
const hlsUrl = (d, audio) => {
  const q = [`c=${PLAYER_ID}`];
  if (audio != null) q.push(`audio=${enc(audio)}`);
  return d.hls_url + (d.hls_url.includes('?') ? '&' : '?') + q.join('&');
};

/* ---------- Icons ---------- */
const I = {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  films: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  tv: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 21h8M9 3l3 3 3-3"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  'home-videos': '<rect x="3" y="7" width="13" height="10" rx="2"/><path d="M16 10.5l5-3v9l-5-3"/>',
  library: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"/>',
  cast: '<path d="M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><path d="M2 12a9 9 0 0 1 9 9M2 16a5 5 0 0 1 5 5M2 20h.01"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'
};
const ico = (k, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${I[k]}</svg>`;
const PLAY = '<svg class="ico fill" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE = '<svg class="ico fill" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5" width="4" height="14" rx="1"/><rect x="13.5" y="5" width="4" height="14" rx="1"/></svg>';
const NAV = { home: 'Home', films: 'Films', tv: 'TV', music: 'Music', 'home-videos': 'Home videos', library: 'Library' };
Object.keys(NAV).forEach(k => {
  const b = $('#nav-' + k);
  b.innerHTML = ico(k) + `<span>${k === 'home-videos' ? 'Videos' : NAV[k]}</span>`;
  b.setAttribute('aria-label', NAV[k]);
});

/* ---------- Formatting ---------- */
const clock = sec => {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
};
function fmtDur(sec) {
  if (sec == null || !isFinite(sec) || sec <= 0) return '';
  if (sec < 60) return clock(sec);
  const tm = Math.round(sec / 60), h = Math.floor(tm / 60), m = tm % 60;
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}
const fmtNum = n => (n || 0).toLocaleString('en-GB');
const pl = (n, one, many) => `${fmtNum(n)} ${n === 1 ? one : (many || one + 's')}`;
function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + ' ' + u[i];
}
function fmtRate(bps) {
  if (!bps) return '';
  return bps >= 1e6 ? (bps / 1e6).toFixed(1) + ' Mbps' : Math.round(bps / 1e3) + ' kbps';
}
// The API contract says every time is UTC; some fields arrive without an offset, so treat bare ISO times as UTC.
const toDate = iso => {
  if (!iso) return null;
  let s = String(iso);
  if (/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(s)) s += 'Z';
  const d = new Date(s); return isNaN(d) ? null : d;
};
const fmtHM = iso => { const d = toDate(iso); return d ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : ''; };
function fmtWhen(iso) {
  const d = toDate(iso); if (!d) return '';
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtHM(iso);
  const opts = { day: 'numeric', month: 'short' };
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString('en-GB', opts) + ' ' + fmtHM(iso);
}
function relTime(iso) {
  const d = toDate(iso); if (!d) return '';
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 90) return 'Now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 172800) return 'Yesterday';
  return fmtWhen(iso);
}
function dayLabel(iso) {
  const d = toDate(iso); if (!d) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const that = new Date(d); that.setHours(0, 0, 0, 0);
  const days = Math.round((today - that) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return d.toLocaleDateString('en-GB', { weekday: 'long' });
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}
const epCode = it => {
  const parts = [];
  if (it.season != null) parts.push('S' + it.season);
  if (it.episode != null) parts.push('E' + it.episode + (it.episode_end != null && it.episode_end !== it.episode ? '–E' + it.episode_end : ''));
  return parts.join(' · ');
};
const seasonName = n => n == null ? 'Episodes' : n === 0 ? 'Specials' : `Season ${n}`;
const qualityLabel = it => it.quality ? it.quality + (it.hdr ? ' HDR' : '') : (it.hdr ? 'HDR' : '');
const resumable = it => !!(it && it.progress && it.progress.position > 0 && !it.progress.completed);
const pctOf = it => it && it.progress && it.duration ? Math.min(100, Math.max(0, it.progress.position / it.duration * 100)) : 0;

const CODECS = { h264: 'H.264', hevc: 'HEVC', h265: 'HEVC', mpeg4: 'MPEG-4', mpeg2video: 'MPEG-2', vp8: 'VP8', vp9: 'VP9', av1: 'AV1', msmpeg4v3: 'DivX',
  aac: 'AAC', ac3: 'AC-3', eac3: 'E-AC-3', dts: 'DTS', truehd: 'TrueHD', flac: 'FLAC', mp3: 'MP3', opus: 'Opus', vorbis: 'Vorbis', alac: 'ALAC',
  pcm_s16le: 'PCM', pcm_s24le: 'PCM 24-bit', subrip: 'SRT', ass: 'ASS', ssa: 'SSA', mov_text: 'MP4 text', webvtt: 'WebVTT',
  hdmv_pgs_subtitle: 'PGS', dvd_subtitle: 'VobSub', dvb_subtitle: 'DVB' };
const codec = c => c ? (CODECS[c] || String(c).toUpperCase()) : '';
const CHANNELS = { 1: 'mono', 2: 'stereo', 6: '5.1', 7: '6.1', 8: '7.1' };
const LANGS = { eng: 'English', en: 'English', fre: 'French', fra: 'French', fr: 'French', ger: 'German', deu: 'German', de: 'German', spa: 'Spanish', es: 'Spanish',
  ita: 'Italian', it: 'Italian', jpn: 'Japanese', ja: 'Japanese', und: '' };
let langNames = null; try { langNames = new Intl.DisplayNames(['en'], { type: 'language' }); } catch (e) { /* older browser */ }
function langName(code) {
  if (!code) return '';
  const c = String(code).toLowerCase();
  if (c in LANGS) return LANGS[c];
  try { const n = langNames && langNames.of(c); if (n && n.toLowerCase() !== c) return n; } catch (e) { /* not a valid tag */ }
  return code;
}
function streamLabel(st) {
  const bits = [langName(st.language) || (st.type === 'subtitle' ? 'Unknown language' : '')];
  if (st.type === 'audio') bits.push([codec(st.codec), CHANNELS[st.channels] || (st.channels ? st.channels + 'ch' : '')].filter(Boolean).join(' '));
  else bits.push(codec(st.codec));
  let s = bits.filter(Boolean).join(' · ') || 'Track ' + st.index;
  if (st.title) s += ` (${st.title})`;
  if (st.forced) s += ' · forced';
  return s;
}
const MODES = {
  direct: { label: 'Direct play', cls: 'good' },
  remux: { label: 'Remux', cls: 'mid' },
  transcode: { label: 'Transcode', cls: 'warn' }
};
const modeOf = m => MODES[m] || { label: m || 'Unknown', cls: 'mid' };

/* ---------- API ---------- */
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { Accept: 'application/json' } };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  if (opts.keepalive) init.keepalive = true;
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new ApiError('Could not reach the server. Check that it is running.', 0); }
  if (r.status === 204) return null;
  const text = await r.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { /* non-JSON body */ } }
  if (!r.ok) {
    const d = data && data.detail;
    const msg = typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ') : `${r.status} ${r.statusText || 'error'}`;
    throw new ApiError(msg, r.status);
  }
  return data;
}

/* ---------- Generated artwork (fallback when the server has no image) ---------- */
function hash(s) { let h = 2166136261; s = String(s); for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function mix(h1, h2, t) { const p = s => [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16)); const A = p(h1), B = p(h2); return 'rgb(' + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(',') + ')'; }
function hsl(h, s, l) {
  h = ((h % 360) + 360) % 360; s /= 100; l /= 100;
  const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return '#' + [f(0), f(8), f(4)].map(x => Math.round(x * 255).toString(16).padStart(2, '0')).join('');
}
const STYLES = ['horizon', 'mountains', 'orbit', 'grid', 'bloom', 'lines'];
function lookFor(seed) {
  const h = hash(seed), R = rng(h ^ 0x9e3779b9);
  const hue = h % 360, hue2 = hue + 15 + R() * 50, hue3 = hue + (R() < 0.5 ? 30 + R() * 30 : 160 + R() * 60);
  return {
    style: STYLES[(h >>> 9) % STYLES.length],
    pal: [hsl(hue, 40 + R() * 20, 7 + R() * 6), hsl(hue2, 35 + R() * 25, 24 + R() * 14), hsl(hue3, 70 + R() * 25, 64 + R() * 14), hsl(hue, 45, 4 + R() * 6)]
  };
}
const DIMS = { p: [360, 540], b: [1280, 600], s: [420, 420], e: [480, 270] };
const ART_CACHE = new Map();
function genArt(seed, kind) {
  seed = String(seed || 'untitled'); kind = DIMS[kind] ? kind : 'p';
  const key = kind + '|' + seed;
  if (ART_CACHE.has(key)) return ART_CACHE.get(key);
  let url = '';
  try { const look = lookFor(seed); url = paint({ id: seed, pal: look.pal, style: look.style }, DIMS[kind][0], DIMS[kind][1]); } catch (e) { url = ''; }
  ART_CACHE.set(key, url);
  return url;
}
function paint(it, w, h) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d'); const R = rng(hash(it.id)); const [a, b, s, d] = it.pal; const m = Math.min(w, h);
  const circle = (cx, cy, r) => { x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill(); };
  const g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, a); g.addColorStop(1, b); x.fillStyle = g; x.fillRect(0, 0, w, h);
  if (it.style === 'horizon') {
    const hy = h * (0.52 + R() * 0.12), sx = w * (0.3 + R() * 0.4), sr = m * (0.12 + R() * 0.08), sy = hy - sr * 0.5;
    const sg = x.createRadialGradient(sx, sy, 0, sx, sy, sr * 3.4); sg.addColorStop(0, s + 'aa'); sg.addColorStop(1, s + '00'); x.fillStyle = sg; x.fillRect(0, 0, w, h);
    x.fillStyle = s; circle(sx, sy, sr);
    x.fillStyle = d; x.fillRect(0, hy, w, h - hy);
    for (let i = 0; i < 46; i++) { const y = hy + (h - hy) * Math.pow(R(), 1.6); const len = w * (0.04 + R() * 0.22) * (1 - (y - hy) / (h - hy) * 0.6);
      x.globalAlpha = 0.12 + R() * 0.4; x.fillStyle = s; x.fillRect(sx - len / 2 + (R() - 0.5) * sr, y, len, 1 + R() * 2.5); }
    x.globalAlpha = 1;
  } else if (it.style === 'mountains') {
    x.globalAlpha = 0.9; x.fillStyle = s; circle(w * (0.2 + R() * 0.6), h * (0.16 + R() * 0.12), m * 0.07); x.globalAlpha = 1;
    for (let L = 0; L < 5; L++) {
      const base = h * (0.42 + L * 0.12); x.fillStyle = mix(b, d, L / 4);
      x.beginPath(); x.moveTo(0, h); x.lineTo(0, base);
      for (let px = 0; px <= w + w / 10; px += w / 10) x.lineTo(px, base - R() * h * 0.13 * (1 - L * 0.12));
      x.lineTo(w, h); x.closePath(); x.fill();
    }
  } else if (it.style === 'orbit') {
    for (let i = 0; i < 180; i++) { x.globalAlpha = 0.2 + R() * 0.7; x.fillStyle = '#ffffff'; x.fillRect(R() * w, R() * h * 0.8, R() < 0.9 ? 1 : 2, R() < 0.9 ? 1 : 2); }
    x.globalAlpha = 1;
    const px = w * (0.5 + R() * 0.3), py = h * (0.78 + R() * 0.15), pr = m * (0.42 + R() * 0.2);
    const pg = x.createRadialGradient(px - pr * 0.4, py - pr * 0.5, pr * 0.1, px, py, pr); pg.addColorStop(0, s); pg.addColorStop(1, d); x.fillStyle = pg; circle(px, py, pr);
    x.save(); x.translate(px, py); x.rotate(-0.32); x.scale(1, 0.2); x.globalAlpha = 0.55; x.strokeStyle = s; x.lineWidth = 9; x.beginPath(); x.arc(0, 0, pr * 1.55, 0, Math.PI * 2); x.stroke(); x.restore();
    x.globalAlpha = 1; x.fillStyle = mix(b, s, 0.5); circle(w * (0.15 + R() * 0.25), h * (0.2 + R() * 0.2), m * 0.04);
  } else if (it.style === 'grid') {
    const hz = x.createRadialGradient(w * 0.5, h, 0, w * 0.5, h, h * 0.9); hz.addColorStop(0, s + '55'); hz.addColorStop(1, s + '00'); x.fillStyle = hz; x.fillRect(0, 0, w, h);
    let bx = -R() * 20; const ws = Math.max(2, m * 0.012);
    while (bx < w) {
      const bw = w * (0.06 + R() * 0.1), bh = h * (0.22 + R() * 0.48);
      x.fillStyle = mix(a, '#000000', 0.25); x.fillRect(bx, h - bh, bw, bh);
      for (let wy = h - bh + ws * 2; wy < h - ws; wy += ws * 2.4) for (let wx = bx + ws; wx < bx + bw - ws; wx += ws * 2.2)
        if (R() < 0.3) { x.globalAlpha = 0.5 + R() * 0.5; x.fillStyle = R() < 0.82 ? s : d; x.fillRect(wx, wy, ws, ws * 1.2); }
      x.globalAlpha = 1; bx += bw + 2;
    }
  } else if (it.style === 'bloom') {
    x.globalCompositeOperation = 'lighter';
    [b, s, d, b, s, d].forEach(col => { const cx = R() * w, cy = R() * h, r = m * (0.3 + R() * 0.45);
      const bg = x.createRadialGradient(cx, cy, 0, cx, cy, r); bg.addColorStop(0, col + '99'); bg.addColorStop(1, col + '00'); x.fillStyle = bg; x.fillRect(0, 0, w, h); });
    x.globalCompositeOperation = 'source-over';
  } else {
    x.save(); x.translate(w / 2, h / 2); x.rotate(-0.55 + R() * 0.3); const band = m * (0.08 + R() * 0.06);
    for (let i = -14; i <= 14; i++) { x.globalAlpha = 0.18 + R() * 0.35; x.fillStyle = [b, s, d][(i + 30) % 3]; x.fillRect(i * band * 1.4, -h * 1.5, band * (0.4 + R() * 0.6), h * 3); }
    x.restore(); x.globalAlpha = 1; x.fillStyle = s; circle(w * (0.25 + R() * 0.5), h * (0.3 + R() * 0.3), m * 0.12);
  }
  const v = x.createRadialGradient(w / 2, h / 2, m * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75); v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, 'rgba(0,0,0,.5)'); x.fillStyle = v; x.fillRect(0, 0, w, h);
  for (let i = 0, n = (w * h) / 90; i < n; i++) { x.fillStyle = R() < 0.5 ? 'rgba(255,255,255,.06)' : 'rgba(0,0,0,.09)'; x.fillRect(R() * w, R() * h, 1, 1); }
  return c.toDataURL('image/jpeg', 0.82);
}

/* Real artwork first; on 404 (or no URL) swap in generated art. URLs known to be missing skip straight to generated. */
const MISSING = new Set();
function imgTag(url, seed, kind, extra = '') {
  if (!url || MISSING.has(url)) return `<img src="${genArt(seed, kind)}" alt="" data-gen="1" ${extra}>`;
  return `<img src="${esc(url)}" alt="" loading="lazy" data-seed="${esc(seed)}" data-k="${kind}" ${extra}>`;
}
const isGen = url => !url || MISSING.has(url);
function setImg(el, url, seed, kind) {
  el.removeAttribute('data-gen');
  el.dataset.seed = seed || ''; el.dataset.k = kind;
  if (isGen(url)) { el.dataset.gen = '1'; el.src = genArt(seed, kind); } else el.src = url;
}
document.addEventListener('error', e => {
  const el = e.target;
  if (!(el instanceof HTMLImageElement) || el.dataset.gen || el.dataset.seed == null) return;
  const src = el.getAttribute('src'); if (src) MISSING.add(src);
  el.dataset.gen = '1'; el.removeAttribute('loading');
  el.src = genArt(el.dataset.seed, el.dataset.k);
  const box = el.closest('.art'); if (box) box.classList.add('gen');
}, true);

/* ---------- State ---------- */
const S = {
  route: { name: 'home', arg: null, q: new URLSearchParams() },
  tok: 0, current: null, feat: 0, season: {}, status: null,
  lib: { adding: false, justAdded: null, confirm: null },
  sheet: null, undo: null, msettings: null
};
const ITEMS = new Map(); // id -> item, filled as cards are drawn (used to build music queues)
const remember = it => { if (it && it.id != null) ITEMS.set(String(it.id), it); return it; };

/* ---------- Cards ---------- */
function artBox(url, seed, kind, inner = '') {
  return `<span class="art${isGen(url) ? ' gen' : ''}">${imgTag(url, seed, kind)}${inner}</span>`;
}
function posterCard({ href, title, sub, url, seed, tag, unav, square }) {
  const label = square ? '' : `<span class="ptitle">${esc(title)}</span>`;
  const tags = (tag ? `<span class="tag">${esc(tag)}</span>` : '') + (unav ? '<span class="tag off">Offline</span>' : '');
  return `<button class="card ${square ? 'square' : 'poster'}${unav ? ' unav' : ''}" data-nav data-href="${esc(href)}" aria-label="${esc(title)}">
    ${artBox(url, seed, square ? 's' : 'p', label + tags)}
    <span class="cap"><b>${esc(title)}</b><span>${esc(sub)}</span></span></button>`;
}
function landCard({ href, play, title, sub, url, seed, pct, unav }) {
  const act = play != null ? `data-play="${esc(play)}"` : `data-href="${esc(href)}"`;
  const inner = (play != null ? `<span class="playhint"><span>${PLAY}</span></span>` : '')
    + (pct != null ? `<span class="prog"><i style="width:${pct.toFixed(1)}%"></i></span>` : '')
    + (unav ? '<span class="tag off">Offline</span>' : '');
  return `<button class="card land${unav ? ' unav' : ''}" data-nav ${act} aria-label="${esc(title)}">
    ${artBox(url, seed, 'e', inner)}
    <span class="cap"><b>${esc(title)}</b><span>${esc(sub)}</span></span></button>`;
}
const join = (...a) => a.filter(x => x != null && x !== '').join(' · ');
const epSeed = it => `${it.show || ''} ${epCode(it)} ${it.title || ''}`;
const albumSeed = al => `${al.artist || ''} – ${al.title || al.album || ''}`;
const filmCard = it => (remember(it), posterCard({ href: `#/item/${it.id}`, title: it.title, sub: join(it.year, fmtDur(it.duration)),
  url: it.art && it.art.poster, seed: it.title, tag: it.quality === '4K' ? qualityLabel(it) : '', unav: it.available === false }));
const showCard = sh => posterCard({ href: `#/show/${enc(sh.key)}`, title: sh.title, sub: join(pl(sh.seasons, 'season'), pl(sh.episodes, 'episode')),
  url: sh.art && sh.art.poster, seed: sh.title });
const albumCard = al => posterCard({ href: `#/album/${enc(al.key)}`, title: al.title, sub: join(al.artist, al.year),
  url: al.art && al.art.poster, seed: albumSeed(al), square: true });
const videoCard = it => (remember(it), landCard({ href: `#/item/${it.id}`, title: it.title, sub: join(it.year, fmtDur(it.duration)),
  url: it.art && it.art.thumb, seed: it.title, unav: it.available === false }));
const episodeCard = it => (remember(it), landCard({ href: `#/item/${it.id}`, title: it.show || it.title, sub: join(epCode(it), it.title),
  url: it.art && it.art.thumb, seed: epSeed(it), unav: it.available === false }));
function continueCard(it) {
  remember(it);
  const left = it.duration && it.progress ? fmtDur(it.duration - it.progress.position) : '';
  const isEp = it.kind === 'episode';
  return landCard({ play: it.id, title: isEp ? (it.show || it.title) : it.title,
    sub: join(isEp ? `${epCode(it)} ${it.title || ''}`.trim() : '', left ? left + ' left' : ''),
    url: it.art && (it.kind === 'film' ? it.art.backdrop : it.art.thumb), seed: isEp ? epSeed(it) : it.title, pct: pctOf(it), unav: it.available === false });
}
const row = (title, href, cards) => cards ? `<section class="sec"><div class="sec-head"><h2>${esc(title)}</h2>${href ? `<button class="link" data-nav data-href="${esc(href)}">See all</button>` : ''}</div><div class="row">${cards}</div></section>` : '';
const emptyBox = (msg, withLink = true) => `<div class="panel empty"><p>${esc(msg)}</p>${withLink ? '<button class="btn small" data-nav data-href="#/library">Open Library</button>' : ''}</div>`;
const backBtn = () => `<button class="back" data-nav data-back>${ico('back')} Back</button>`;
/* ---------- Metadata (Phase 2) ---------- */
const fmtRating = r => r != null && isFinite(+r) && +r > 0 ? `★ ${(+r).toFixed(1)}` : '';
const genreText = g => g ? String(g).split(',').map(s => s.trim()).filter(Boolean).slice(0, 3).join(', ') : '';
const clip = (s, n) => { s = String(s || '').trim(); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, '') + '…' : s; };
const metaSpans = list => list.filter(x => x != null && x !== '').map(x => `<span>${esc(x)}</span>`).join('');
function creditsHTML(md, isShow) {
  const p = md && md.people; if (!p) return '';
  const dir = (p.director || []).filter(Boolean), cast = (p.cast || []).filter(Boolean).slice(0, 6);
  if (!dir.length && !cast.length) return '';
  return `<dl class="credits">${dir.length ? `<dt>${isShow ? 'Created by' : dir.length > 1 ? 'Directors' : 'Director'}</dt><dd>${esc(dir.join(', '))}</dd>` : ''}${cast.length ? `<dt>Cast</dt><dd>${esc(cast.join(', '))}</dd>` : ''}</dl>`;
}
/* Pill text for a metadata object (null = never looked at). */
function matchPill(md, kind) {
  if (kind === 'home_video' || (md && md.status === 'skipped')) return '<span class="pill quiet">Home video · not matched online</span>';
  if (md && (md.status === 'ambiguous' || md.status === 'unmatched')) return '<span class="pill off">Needs a match</span>';
  if (md && md.source === 'manual') return '<span class="pill">Edited by you</span>';
  if (!md || md.status === 'pending') return '<span class="pill quiet">Not matched yet</span>';
  // Edits lock fields without changing the source, so say so alongside where the rest came from.
  const edited = (md.locked || []).some(f => f !== 'match') ? ' · edited by you' : '';
  if (md.source === 'nfo') return `<span class="pill">From .nfo file${edited}</span>`;
  if (md.source === 'tmdb') return `<span class="pill">Matched via TMDb${md.confidence != null ? ' · ' + Math.round(md.confidence * 100) + '%' : ''}${edited}</span>`;
  return `<span class="pill quiet">${edited ? 'Edited by you' : 'Title from the file name'}</span>`;
}
/* t = { type: 'item'|'show', id, title, year } — the thing the links act on (an episode acts on its show). */
// Unknown until the first status call; the server enforces it either way.
const isAdmin = () => !(S.status && S.status.access) || S.status.access.admin;
function matchLine(md, kind, t) {
  const canFix = t && isAdmin() && (kind === 'film' || kind === 'show' || kind === 'episode');
  const tAttrs = t ? `data-t="${t.type}" data-id="${esc(t.id)}"` : '';
  const confirming = S.undo && t && S.undo === t.type + ':' + t.id;
  const links = canFix ? `<button class="link" data-nav data-act="fix" ${tAttrs}>Fix match</button>`
    + (t.editId != null ? `<button class="link" data-nav data-act="edit" data-t="${t.editType}" data-id="${esc(t.editId)}">Edit metadata</button>` : '')
    + (md && md.locked && md.locked.length ? `<button class="link${confirming ? ' danger' : ''}" data-nav data-act="unlock" data-t="${t.editType || t.type}" data-id="${esc(t.editId != null ? t.editId : t.id)}">${confirming ? 'Confirm: undo my changes' : 'Undo my changes'}</button>` : '') : '';
  return `<div class="matchline">${matchPill(md, kind)}${links}</div>`;
}
const TARGETS = new Map(); // "item:12" / "show:key" -> { type, id, title, year, cands } for the fix/edit sheets
const regTarget = t => { TARGETS.set(t.type + ':' + t.id, t); return t; };

function decisionLine(dec, where) {
  if (!dec) return '';
  const m = modeOf(dec.mode);
  return `<p class="decision ${m.cls}"><span class="dotc"></span>${esc(m.label)} ${esc(where)}${dec.reason ? `<span class="sub">· ${esc(dec.reason)}</span>` : ''}</p>`;
}

/* ---------- Views ---------- */
const VIEWS = {};

/* Home */
VIEWS.home = {
  title: 'Home',
  async load() {
    const [recent, cont, films, shows, albums, hv] = await Promise.all([
      api('/api/recent?limit=20'), api('/api/continue'), api('/api/films?sort=added'),
      api('/api/shows?sort=added'), api('/api/albums?sort=added'), api('/api/home-videos?sort=added')]);
    const showList = shows.shows || [];
    const byTitle = new Map(showList.map(s => [s.title, s]));
    let hero = [];
    const seen = new Set();
    for (const it of recent.items || []) {
      if (hero.length >= 12) break;
      if (it.kind === 'film' && !seen.has('i' + it.id)) { seen.add('i' + it.id); hero.push({ type: 'film', it }); }
      else if (it.kind === 'episode') {
        const sh = byTitle.get(it.show);
        if (sh && !seen.has('s' + sh.key)) { seen.add('s' + sh.key); hero.push({ type: 'show', sh, ep: it }); }
      }
    }
    // Prefer things with a synopsis (they've been matched, so they usually have real artwork too); keep recency within each group.
    const rich = f => (f.type === 'film' ? f.it.overview : f.sh.overview) ? 1 : 0;
    hero = hero.map((f, i) => [f, i]).sort((a, b) => rich(b[0]) - rich(a[0]) || a[1] - b[1]).slice(0, 3).map(x => x[0]);
    return { hero, cont: cont.items || [], films: films.items || [], shows: showList, albums: albums.albums || [], hv: hv.items || [] };
  },
  draw(d) {
    const nothing = !d.films.length && !d.shows.length && !d.albums.length && !d.hv.length;
    if (nothing) {
      return `<section class="hero" aria-label="Welcome"><img class="hero-img" src="${genArt('PictureHouse', 'b')}" alt=""><div class="hero-shade"></div>
        <div class="hero-body"><p class="eyebrow">Welcome</p><h1 class="hero-title">Your library is empty</h1>
        <p class="hero-syn">Add a folder of films, series, music or home videos on the Library page, then scan it. Everything you add shows up here.</p>
        <div class="actions"><button class="btn primary" data-nav data-href="#/library">Open Library</button></div></div></section>`;
    }
    const L = 20;
    return heroHTML(d.hero)
      + row('Continue watching', null, d.cont.map(continueCard).join(''))
      + row('Recently added films', '#/films', d.films.slice(0, L).map(filmCard).join(''))
      + row('Series', '#/tv', d.shows.slice(0, L).map(showCard).join(''))
      + row('Music', '#/music', d.albums.slice(0, L).map(albumCard).join(''))
      + row('Home videos', '#/home-videos', d.hv.slice(0, L).map(videoCard).join(''));
  }
};
function heroHTML(list) {
  if (!list.length) return '';
  if (S.feat >= list.length) S.feat = 0;
  const f = list[S.feat];
  let img, eyebrow, title, meta, syn = '', actions;
  if (f.type === 'film') {
    const it = remember(f.it);
    img = imgTag(it.art && it.art.backdrop, it.title, 'b', 'class="hero-img"');
    eyebrow = `Added ${dayLabel(it.added_at)} · Film`; title = it.title;
    meta = [it.year, it.certification, genreText(it.genres), fmtDur(it.duration), qualityLabel(it)];
    syn = it.overview || '';
    const resume = resumable(it);
    actions = `<button class="btn primary" data-nav data-play="${it.id}">${PLAY} ${resume ? 'Resume from ' + clock(it.progress.position) : 'Play'}</button>
      <button class="btn" data-nav data-href="#/item/${it.id}">More info</button>`;
  } else {
    const sh = f.sh, ep = remember(f.ep);
    img = imgTag(sh.art && sh.art.backdrop, sh.title, 'b', 'class="hero-img"');
    eyebrow = 'New episode · Series'; title = sh.title;
    meta = [sh.year, sh.certification, genreText(sh.genres), pl(sh.seasons, 'season'), pl(sh.episodes, 'episode')];
    syn = sh.overview || `Latest: ${join(epCode(ep), ep.title)}`;
    actions = `<button class="btn primary" data-nav data-play="${ep.id}">${PLAY} ${resumable(ep) ? 'Resume' : 'Play'} ${esc(epCode(ep).replace(' · ', ' '))}</button>
      <button class="btn" data-nav data-href="#/show/${enc(sh.key)}">More info</button>`;
  }
  return `<section class="hero" aria-label="Featured">${img}<div class="hero-shade"></div>
    <div class="hero-body">
      <p class="eyebrow">${esc(eyebrow)}</p>
      <h1 class="hero-title">${esc(title)}</h1>
      <p class="meta">${meta.filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('')}</p>
      ${syn ? `<p class="hero-syn">${esc(syn)}</p>` : ''}
      <div class="actions">${actions}</div>
    </div>
    ${list.length > 1 ? `<div class="hero-dots">${list.map((x, i) => `<button class="dot ${i === S.feat ? 'on' : ''}" data-nav data-feat="${i}" aria-label="Show ${esc(x.type === 'film' ? x.it.title : x.sh.title)}"></button>`).join('')}</div>` : ''}
  </section>`;
}

/* Grids */
const SORTS = [['added', 'Recently added'], ['az', 'A–Z'], ['year', 'Year']];
const GRIDS = {
  films: { title: 'Films', url: s => `/api/films?sort=${s}`, pick: d => d.items, card: filmCard,
    head: l => pl(l.length, 'film'), empty: 'No films yet — add a folder on the Library page.' },
  tv: { title: 'TV', url: s => `/api/shows?sort=${s}`, pick: d => d.shows, card: showCard,
    head: l => join(pl(l.length, 'series', 'series'), pl(l.reduce((n, s) => n + (s.episodes || 0), 0), 'episode')), empty: 'No series yet — add a TV folder on the Library page.' },
  music: { title: 'Music', url: s => `/api/albums?sort=${s}`, pick: d => d.albums, card: albumCard,
    head: l => join(pl(l.length, 'album'), pl(l.reduce((n, a) => n + (a.tracks || 0), 0), 'track')), empty: 'No music yet — add a music folder on the Library page.' },
  'home-videos': { title: 'Home videos', url: s => `/api/home-videos?sort=${s}`, pick: d => d.items, card: videoCard, wide: true,
    head: l => pl(l.length, 'video'), empty: 'No home videos yet — add a folder on the Library page.' }
};
Object.entries(GRIDS).forEach(([k, g]) => {
  VIEWS[k] = {
    title: g.title,
    async load(r) { const sort = curSort(r); return { sort, list: g.pick(await api(g.url(sort))) || [] }; },
    draw(d) {
      return `<div class="page-head"><div><h1>${esc(g.title)}</h1><p>${esc(g.head(d.list))}</p></div>
        <div class="chips">${SORTS.map(([s, l]) => `<button class="chip ${d.sort === s ? 'on' : ''}" data-nav data-sort="${s}">${l}</button>`).join('')}</div></div>
        ${d.list.length ? `<div class="grid ${g.wide ? 'wide' : ''}">${d.list.map(g.card).join('')}</div>` : emptyBox(g.empty)}`;
    }
  };
});
function curSort(r) { const s = r.q.get('sort'); return SORTS.some(x => x[0] === s) ? s : 'added'; }

/* Item detail (film, home video, episode, track) */
VIEWS.item = {
  title: 'Details',
  async load(r) {
    const it = remember(await api(`/api/items/${enc(r.arg)}`));
    if (it.kind === 'episode' && it.show) {
      // Matching and editing act on the episode's series; find its (opaque) key and metadata.
      try {
        if (it.show_key) it._show = await api(`/api/shows/${enc(it.show_key)}`);
      } catch (e) { /* the page still works without the series link */ }
    }
    return it;
  },
  draw(it) {
    const isHome = it.kind === 'home_video', isEp = it.kind === 'episode', isTrack = it.kind === 'track';
    const v = it.video || {};
    const streams = it.streams || [];
    const audio = streams.filter(s => s.type === 'audio'), subs = streams.filter(s => s.type === 'subtitle');
    const seed = isEp ? epSeed(it) : isTrack ? albumSeed(it) : it.title;
    const poster = isHome || isEp ? '' : `<div class="d-poster ${isTrack ? 'sq' : ''}">${imgTag(it.art && it.art.poster, isTrack ? albumSeed(it) : it.title, isTrack ? 's' : 'p')}</div>`;
    const meta = isHome ? ['Home video', it.year, genreText(it.genres), fmtDur(it.duration), qualityLabel(it)]
      : isEp ? [epCode(it), it.year, it.certification, fmtDur(it.duration), qualityLabel(it), fmtRating(it.rating)]
      : isTrack ? [it.artist, it.album, it.year, fmtDur(it.duration)]
      : [it.year, it.certification, genreText(it.genres), fmtDur(it.duration), qualityLabel(it), fmtRating(it.rating)];
    const sh = it._show;
    const md = isEp && sh ? sh.metadata : it.metadata;
    let target = null;
    if (it.kind === 'film') target = regTarget({ type: 'item', id: String(it.id), title: it.title, year: it.year, overview: it.overview, genres: it.genres,
      certification: it.certification, cands: md && md.candidates, editType: 'item', editId: String(it.id) });
    else if (isEp) {
      if (sh) regTarget({ type: 'show', id: sh.key, title: sh.title, year: sh.year, overview: sh.overview, genres: sh.genres, certification: sh.certification, cands: md && md.candidates });
      target = regTarget({ type: 'item', id: String(it.id), title: it.show || it.title, year: sh ? sh.year : null, cands: md && md.candidates, show: true,
        editType: sh ? 'show' : null, editId: sh ? sh.key : null });
    }
    const matchHTML = isTrack ? '' : matchLine(md, it.kind, target);
    const showLink = isEp && it.show ? (sh ? `<button class="show-link" data-nav data-href="#/show/${enc(sh.key)}">${esc(it.show)}</button>` : `<span class="show-link">${esc(it.show)}</span>`) : '';
    const badges = [it.quality, codec(v.codec), v.profile, v.width && v.height ? `${v.width}×${v.height}` : '', it.hdr ? 'HDR' : (v.codec ? 'SDR' : ''),
      ...[...new Set(audio.map(a => [codec(a.codec), CHANNELS[a.channels]].filter(Boolean).join(' ')))],
      subs.length ? pl(subs.length, 'subtitle track') : ''].filter(Boolean);
    const pr = it.progress, resume = resumable(it);
    const unav = it.available === false;
    const probeBad = it.probe && it.probe.ok === false;
    const actions = unav
      ? '<span class="pill off">Unavailable — its folder is offline</span>'
      : probeBad ? '<span class="pill bad">Cannot be played — the file could not be read</span>'
      : `<button class="btn primary" data-nav data-play="${it.id}">${PLAY} ${resume ? 'Resume from ' + clock(pr.position) : 'Play'}</button>`
        + (resume ? `<button class="btn" data-nav data-play="${it.id}" data-from="0">Play from start</button>` : '')
        + (pr && pr.completed ? '<span class="pill quiet">Watched</span>' : '');
    const pb = it.playback || {};
    const vline = [codec(v.codec), v.profile, v.width && v.height ? `${v.width}×${v.height}` : '', v.fps ? `${+(+v.fps).toFixed(3)} fps` : '', v.pix_fmt].filter(Boolean).join(' · ');
    const panels = `<div class="d-cols" style="margin-top:22px">
      <div class="panel"><h3>File and streams</h3><dl class="kv">
        ${it.library ? `<dt>Library</dt><dd>${esc(it.library)}</dd>` : ''}
        <dt>File</dt><dd>${esc(it.file || '—')}</dd>
        <dt>Size</dt><dd>${esc(fmtBytes(it.size))}</dd>
        ${it.container ? `<dt>Container</dt><dd>${esc(it.container)}</dd>` : ''}
        ${it.bitrate ? `<dt>Bitrate</dt><dd>${esc(fmtRate(it.bitrate))}</dd>` : ''}
        ${vline ? `<dt>Video</dt><dd>${esc(vline)}</dd>` : ''}
        <dt>Audio</dt><dd>${audio.length ? audio.map(a => esc(streamLabel(a))).join('<br>') : 'None'}</dd>
        ${isTrack ? '' : `<dt>Subtitles</dt><dd>${subs.length ? subs.map(s => esc(streamLabel(s))).join('<br>') : 'None'}</dd>`}
        ${probeBad ? `<dt>Probe</dt><dd style="color:var(--bad)">${esc(it.probe.error || 'failed')}</dd>` : ''}
      </dl></div>
      <div class="panel"><h3>Playback</h3><dl class="kv">
        <dt>This browser</dt><dd>${decisionKV(pb.browser)}</dd>
        <dt>TVs (DLNA)</dt><dd>${decisionKV(pb.dlna)}</dd>
      </dl></div></div>`;
    return `${backBtn()}
      <div class="d-back">${imgTag(it.art && it.art.backdrop, seed, 'b')}</div>
      <div class="d-main ${poster ? '' : 'noposter'}">${poster}
        <div class="d-info">
          ${showLink}
          <h1 class="d-title">${esc(it.title)}</h1>
          <p class="meta">${metaSpans(meta)}</p>
          ${badges.length ? `<div class="badges">${badges.map(b => `<span class="badge">${esc(b)}</span>`).join('')}</div>` : ''}
          <div class="actions">${actions}</div>
          ${unav || probeBad ? '' : decisionLine(pb.browser, 'in this browser')}
          ${probeBad ? `<p class="note" style="color:var(--bad)">The scanner could not read this file: ${esc(it.probe.error || 'unknown error')}</p>` : ''}
          ${it.overview ? `<p class="d-syn">${esc(it.overview)}</p>` : ''}
          ${isTrack ? '' : creditsHTML(md, isEp)}
          ${matchHTML}
        </div>
      </div>${panels}`;
  }
};
function decisionKV(dec) {
  if (!dec) return '<span class="sub">Not reported</span>';
  const m = modeOf(dec.mode);
  return `<span class="decision ${m.cls}"><span class="dotc"></span>${esc(m.label)}</span>${dec.reason ? `<span class="sub">${esc(dec.reason)}</span>` : ''}`;
}

/* TV show */
VIEWS.show = {
  title: 'Series',
  async load(r) {
    const sh = await api(`/api/shows/${enc(r.arg)}`);
    (sh.season_list || []).forEach(s => (s.episodes || []).forEach(remember));
    if (sh.next_up) remember(sh.next_up);
    if (!(sh.key in S.season)) {
      const seasons = (sh.season_list || []).map(s => s.season);
      S.season[sh.key] = sh.next_up && seasons.includes(sh.next_up.season) ? sh.next_up.season : seasons[0];
    }
    return sh;
  },
  draw(sh) {
    const list = sh.season_list || [];
    let cur = list.find(s => s.season === S.season[sh.key]) || list[0];
    const nx = sh.next_up || (list[0] && list[0].episodes && list[0].episodes[0]);
    const nxLabel = nx ? (epCode(nx).replace(' · ', ' ') || nx.title) : '';
    const actions = nx
      ? `<button class="btn primary" data-nav data-play="${nx.id}">${PLAY} ${resumable(nx) ? 'Resume' : 'Play'} ${esc(nxLabel)}</button>`
      : '';
    const eps = cur ? (cur.episodes || []).map((e, i) => {
      const pct = pctOf(e), done = e.progress && e.progress.completed, inProg = resumable(e);
      const unav = e.available === false;
      const st = unav ? '<span class="st offline">Offline</span>' : done ? '<span class="st done">Watched</span>' : inProg ? `<span class="st">${Math.round(pct)}% watched</span>` : `<span class="st">${esc(fmtDur(e.duration))}</span>`;
      return `<button class="ep${unav ? ' unav' : ''}" data-nav data-play="${e.id}" aria-label="${esc(join(epCode(e), e.title))}">
        <span class="n">${esc(e.episode != null ? e.episode : i + 1)}</span>
        <span class="thumb">${imgTag(e.art && e.art.thumb, epSeed(e), 'e')}${inProg ? `<span class="prog"><i style="width:${pct.toFixed(1)}%"></i></span>` : ''}</span>
        <span class="t"><b>${esc(e.title)}</b><span>${esc(join(e.season != null ? `${seasonName(e.season)}, episode ${e.episode != null ? e.episode : i + 1}` : '', fmtDur(e.duration)))}</span></span>${st}</button>`;
    }).join('') : '';
    return `${backBtn()}
      <div class="d-back">${imgTag(sh.art && sh.art.backdrop, sh.title, 'b')}</div>
      <div class="d-main"><div class="d-poster">${imgTag(sh.art && sh.art.poster, sh.title, 'p')}</div>
        <div class="d-info">
          <h1 class="d-title">${esc(sh.title)}</h1>
          <p class="meta">${metaSpans([sh.year, sh.certification, genreText(sh.genres), pl(sh.seasons, 'season'), pl(sh.episodes, 'episode'), fmtRating(sh.rating)])}</p>
          <div class="actions">${actions}</div>
          ${sh.next_up ? `<p class="note">Up next: ${esc(join(epCode(sh.next_up), sh.next_up.title))}</p>` : ''}
          ${sh.overview ? `<p class="d-syn">${esc(sh.overview)}</p>` : ''}
          ${creditsHTML(sh.metadata, true)}
          ${matchLine(sh.metadata, 'show', regTarget({ type: 'show', id: sh.key, title: sh.title, year: sh.year, overview: sh.overview, genres: sh.genres,
            certification: sh.certification, cands: sh.metadata && sh.metadata.candidates, editType: 'show', editId: sh.key }))}
        </div>
      </div>
      ${list.length > 1 ? `<div class="seasons">${list.map(s => `<button class="chip ${cur && s.season === cur.season ? 'on' : ''}" data-nav data-season="${esc(s.season)}">${esc(seasonName(s.season))}</button>`).join('')}</div>` : '<div class="seasons"></div>'}
      ${eps ? `<div class="eps">${eps}</div>` : emptyBox('No episodes found for this series.', false)}`;
  }
};

/* Album */
VIEWS.album = {
  title: 'Album',
  async load(r) { const al = await api(`/api/albums/${enc(r.arg)}`); (al.track_list || []).forEach(remember); return al; },
  draw(al) {
    const tracks = al.track_list || [];
    const playing = M && M.queue[M.i] ? String(M.queue[M.i].id) : null;
    return `${backBtn()}
      <div class="d-back">${imgTag(null, albumSeed(al), 'b')}</div>
      <div class="d-main"><div class="d-poster sq">${imgTag(al.art && al.art.poster, albumSeed(al), 's')}</div>
        <div class="d-info">
          <h1 class="d-title">${esc(al.title)}</h1>
          <p class="meta">${[al.artist, al.year, pl(al.tracks != null ? al.tracks : tracks.length, 'track'), fmtDur(al.duration)].filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('')}</p>
          <div class="actions">${tracks.length ? `<button class="btn primary" data-nav data-playall>${PLAY} Play album</button>` : ''}</div>
        </div>
      </div>
      ${tracks.length ? `<div class="tracks">${tracks.map((t, i) => trackRow(t, i + 1, playing, false)).join('')}</div>` : emptyBox('This album has no playable tracks.', false)}`;
  }
};
function trackRow(t, n, playing, withSub) {
  const unav = t.available === false;
  return `<button class="trk${String(t.id) === playing ? ' on' : ''}${unav ? ' unav' : ''}" data-nav data-track="${t.id}">
    <span class="n">${esc(t.track_no != null ? t.track_no : n)}</span>
    <b>${esc(t.title)}${withSub ? `<span class="tsub">${esc(join(t.artist, t.album))}</span>` : ''}</b>
    <span>${unav ? 'Offline' : esc(clock(t.duration))}</span></button>`;
}

/* Search */
VIEWS.search = {
  title: 'Search',
  async load(r) {
    const q = (r.q.get('q') || '').trim();
    if (!q) return { q, empty: true };
    const d = await api(`/api/search?q=${enc(q)}`);
    return { q, d };
  },
  draw({ q, d, empty }) {
    if (empty) return `<div class="page-head"><div><h1>Search</h1><p>Type a title, a series or an artist.</p></div></div>`;
    const g = k => d[k] || [];
    const total = ['films', 'shows', 'albums', 'episodes', 'tracks', 'home_videos'].reduce((n, k) => n + g(k).length, 0);
    const sec = (title, body) => body ? `<section class="sec"><div class="sec-head"><h2>${esc(title)}</h2></div>${body}</section>` : '';
    const grid = (list, fn, wide) => list.length ? `<div class="grid ${wide ? 'wide' : ''}">${list.map(fn).join('')}</div>` : '';
    g('tracks').forEach(remember);
    return `<div class="page-head"><div><h1>Search</h1><p>${pl(total, 'result')} for “${esc(q)}”</p></div></div>
      ${total ? '' : '<p style="color:var(--muted)">Nothing matches. Try part of a title, a series or an artist.</p>'}
      ${sec('Films', grid(g('films'), filmCard))}
      ${sec('Series', grid(g('shows'), showCard))}
      ${sec('Albums', grid(g('albums'), albumCard))}
      ${sec('Episodes', grid(g('episodes'), episodeCard, true))}
      ${sec('Tracks', g('tracks').length ? `<div class="tracks">${g('tracks').map((t, i) => trackRow(t, i + 1, M && M.queue[M.i] ? String(M.queue[M.i].id) : null, true)).join('')}</div>` : '')}
      ${sec('Home videos', grid(g('home_videos'), videoCard, true))}`;
  }
};

/* Library */
const KINDS = [['films', 'Films'], ['tv', 'TV series'], ['music', 'Music'], ['home_videos', 'Home videos'], ['mixed', 'Mixed']];
const mediaHint = () => S.status && S.status.server && S.status.server.media_hint;
const kindLabel = k => (KINDS.find(x => x[0] === k) || [k, k || '—'])[1];
VIEWS.library = {
  title: 'Library',
  async load() {
    const soft = p => api(p).catch(e => ({ _err: e.message }));
    const [status, libs, problems, devices, scan, unmatched, admin, themes] = await Promise.all([
      api('/api/status'), api('/api/libraries'), api('/api/problems'), soft('/api/devices'), api('/api/scan'),
      soft('/api/unmatched'), soft('/api/admin'), soft('/api/themes')]);
    // Settings are admin-only; other devices see a note instead of an error.
    const [msettings, ssettings] = admin.admin ? await Promise.all([soft('/api/settings/metadata'), soft('/api/settings/streaming')]) : [null, null];
    S.status = status; S.admin = admin;
    return { status, libs: libs.libraries || [], problems, devices: devices.devices || null, scan, unmatched, msettings, ssettings, admin, themes };
  },
  after(d) {
    if (d.scan && d.scan.running) pollScan();
    if (d.status.metadata && d.status.metadata.running) pollMeta();
    if (S.route.q.get('panel') === 'phones') {
      const sec = $('#phones-sec');
      if (sec) setTimeout(() => { sec.scrollIntoView({ block: 'start' }); const f = sec.querySelector('[data-nav]'); if (f) f.focus({ preventScroll: true }); }, 0);
    }
  },
  draw(d) {
    const c = d.status.counts || {};
    const mst = d.status.metadata || {};
    const stats = [[fmtNum(c.films), 'Films'], [fmtNum(c.episodes), `Episodes in ${pl(c.shows || 0, 'series', 'series')}`],
      [fmtNum(c.albums), `Albums · ${pl(c.tracks || 0, 'track')}`], [fmtNum(c.home_videos), 'Home videos'], [fmtBytes(d.status.total_bytes), 'Indexed']];
    if (mst.needs_attention > 0) stats.push([fmtNum(mst.needs_attention), 'Need a match']);
    if (c.failed) stats.push([fmtNum(c.failed), 'Files with problems']);
    if (c.unavailable) stats.push([fmtNum(c.unavailable), 'Unavailable items']);
    const sc = scanText(d.scan);
    const L = S.lib;
    const addForm = L.adding ? `<form class="panel addform" id="addform" novalidate autocomplete="off">
        <div class="form">
          <label class="field">Name<input name="name" required placeholder="Films" data-nav></label>
          <label class="field">Folder path<span class="pathrow"><input name="path" required placeholder="${esc(mediaHint() ? mediaHint().replace(/\/+$/, '') + '/Films' : 'D:\\Media\\Films')}" data-nav><button type="button" class="btn small" data-nav data-act="browse-open">Browse…</button></span></label>
          <label class="field">Contains<select name="kind" data-nav>${KINDS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></label>
          <div class="formbtns"><button type="submit" class="btn primary small" data-nav>Add folder</button><button type="button" class="btn small" data-nav data-act="cancel-add">Cancel</button></div>
        </div>
        <div class="browser" id="browser" hidden></div>
        <p class="formerr" id="adderr" role="alert" hidden></p>
        <p class="note">Any name works except one containing / or \\. ${mediaHint() ? `PictureHouse runs in a container here: use a folder path as the container sees it, under ${esc(mediaHint())}.` : 'The path is read on the computer running the server. Network shares work as \\\\server\\share.'} Media folders are never written to.</p>
      </form>` : '';
    const added = L.justAdded ? `<div class="panel addok"><span>Added “${esc(L.justAdded)}”. Scan it now to read its files?</span>
        <button class="btn primary small" data-nav data-act="scan-one" data-lib="${esc(L.justAdded)}">Scan now</button>
        <button class="btn small" data-nav data-act="dismiss-added">Later</button></div>` : '';
    const rows = d.libs.map(l => {
      const status = l.available
        ? (l.last_scan_at ? `<span class="pill">Available</span>` : `<span class="pill quiet">Not scanned yet</span>`)
        : `<span class="pill off">Offline${l.unavailable_since ? ' since ' + esc(fmtWhen(l.unavailable_since)) : ''}</span>`;
      const confirming = L.confirm === l.name;
      return `<tr><td>${esc(l.name)}</td><td class="mono">${esc(l.path)}</td><td>${esc(kindLabel(l.kind))}</td>
        <td class="num">${fmtNum(l.items)}</td><td class="num">${esc(fmtBytes(l.bytes))}</td><td>${status}</td>
        <td class="num"><button class="rowbtn${confirming ? ' danger' : ''}" data-nav data-act="remove" data-lib="${esc(l.name)}">${confirming ? 'Confirm remove' : 'Remove'}</button></td></tr>`;
    }).join('');
    const table = d.libs.length
      ? `<div class="panel tablewrap" style="padding-block:4px"><table><thead><tr><th>Name</th><th>Location</th><th>Type</th><th style="text-align:right">Items</th><th style="text-align:right">Size</th><th>Status</th><th><span hidden>Actions</span></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="panel empty"><p>No folders yet. Add one to start building your library.</p>${L.adding ? '' : '<button class="btn primary small" data-nav data-act="add">Add folder</button>'}</div>`;
    const pf = (d.problems && d.problems.files) || [], plib = (d.problems && d.problems.libraries) || [];
    const paud = (d.problems && d.problems.audio) || [];
    const problems = plib.map(l => `<div class="issue"><code>${esc(l.path)}</code>
        <p>“${esc(l.name)}” can't be reached${l.unavailable_since ? ' (since ' + esc(fmtWhen(l.unavailable_since)) + ')' : ''}. Its ${pl(l.items || 0, 'item')} are marked unavailable and will come back when it does. Nothing was removed.</p>
        <div class="cands"><button class="cand" data-nav data-act="scan-one" data-lib="${esc(l.name)}">Check now</button></div></div>`).join('')
      + pf.map(f => `<div class="issue"><code>${esc(f.library)} · ${esc(f.file)}</code><p>${esc(f.error || 'Unknown error')}</p>
        <small>${esc(join(f.retry_count ? `Tried ${pl(f.retry_count, 'time')}` : '', f.failed_at ? 'last ' + fmtWhen(f.failed_at) : ''))}</small>
        <div class="cands"><button class="cand" data-nav data-act="retry" data-id="${esc(f.id)}" data-file="${esc(f.file)}">Retry</button></div></div>`).join('')
      + paud.map(a => `<div class="issue"><code>${esc(a.library)} · ${esc(a.file)}</code><p><b>Sound problem:</b> ${esc(a.note || 'The audio looks broken.')}</p>
        <div class="cands"><button class="cand" data-nav data-href="#/item/${esc(a.id)}">Open</button>
          <button class="cand" data-nav data-act="audio-recheck" data-id="${esc(a.id)}">Check again</button>
          <button class="cand" data-nav data-act="audio-dismiss" data-id="${esc(a.id)}" data-file="${esc(a.file)}">It's fine, dismiss</button></div></div>`).join('');
    const dlna = d.status.dlna || {};
    const name = (d.status.server && d.status.server.name) || 'PictureHouse';
    const dev = (d.devices || []).map(x => `<tr><td>${esc(x.name || x.address)}</td><td>${x.via === 'dlna' ? 'DLNA' : x.via === 'web' ? 'Web browser' : esc(x.via)}</td>
        <td class="mono">${esc(x.address)}</td><td class="ua" title="${esc(x.user_agent)}">${esc(x.user_agent || '—')}</td><td>${esc(relTime(x.last_seen))}</td></tr>`).join('');
    const ro = d.admin && !d.admin._err && !d.admin.admin;
    return `${ro ? '<div class="viewonly">' : '<div>'}<div class="page-head"><div><h1>Library</h1><p>Folders, scans, matching and the devices on your network.</p></div></div>
      ${adminHTML(d.admin)}
      <div class="stats">${stats.map(([b, s]) => `<div class="stat"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('')}</div>
      <div class="panel scan"><div class="what"><b id="scanTitle">${esc(sc.title)}</b><span id="scanSub" style="color:var(--muted);font-size:13.5px">${esc(sc.sub)}</span><div class="bar"><i id="scanBar" style="width:${sc.pct}%"></i></div></div>
        <button class="btn primary small" id="rescan" data-nav data-act="scan-all" ${d.scan && d.scan.running ? 'disabled' : ''}>${d.scan && d.scan.running ? 'Scanning…' : 'Scan all folders'}</button></div>
      <section class="sec"><div class="sec-head"><h2>Folders</h2>${L.adding || !d.libs.length ? '' : '<button class="link" data-nav data-act="add">Add folder</button>'}</div>
        ${added}${addForm}${table}</section>
      <div class="two">
        <div class="panel" id="needs"><h3>Needs a match</h3>${needsHTML(d.unmatched)}</div>
        <div class="panel"><h3>Problems found in the last scan</h3>${problems || '<p class="note">No problems. Every folder was reachable and every file could be read.</p>'}</div>
      </div>
      <section class="sec"><div class="sec-head"><h2>Online metadata</h2></div>${d.admin.admin ? metaPanelHTML(d.msettings, mst)
        : `<div class="panel"><p class="note">${esc(metaStatus(mst).pill)}. Keys and matching settings can be changed on the PC running PictureHouse${d.admin.pin_set ? ', or here after entering the admin PIN above' : ''}.</p></div>`}</section>
      <section class="sec" id="appearance-sec"><div class="sec-head"><h2>Appearance</h2></div>${appearanceHTML(d.themes, d.admin.admin)}</section>
      <section class="sec"><div class="sec-head"><h2>DLNA</h2></div>
        <div class="panel">${dlna.enabled
          ? `${(dlna.addresses || []).length ? `<ul class="addrs">${dlna.addresses.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}<p class="note">TVs and apps such as VLC find this server automatically as “${esc(name)}”.</p>`
          : '<p class="note">DLNA is off on this server, so TVs will not see it. Restart the server with DLNA enabled to share to TVs and apps such as VLC.</p>'}</div>
        ${streamingHTML(d.ssettings, d.status)}</section>
      <section class="sec" id="phones-sec"><div class="sec-head"><h2>Phones &amp; tablets</h2></div>${androidHTML(d.status)}${phonesHTML(d.status)}</section>
      <section class="sec"><div class="sec-head"><h2>Devices on your network</h2></div>
        ${!d.devices ? '<div class="panel"><p class="note">The device list is shown on the PC running PictureHouse, or here after entering the admin PIN.</p></div>'
          : d.devices.length ? `<div class="panel tablewrap" style="padding-block:4px"><table><thead><tr><th>Device</th><th>Connects by</th><th>Address</th><th>Software</th><th>Last seen</th></tr></thead><tbody>${dev}</tbody></table></div>`
          : '<div class="panel"><p class="note">No devices seen yet. Browsers and TVs appear here once they connect.</p></div>'}</section></div>`;
  }
};

/* Themes (docs/themes.md): each device can pick its own; the admin sets everyone's default.
   The choice is the #themecss <link> (index.html), so switching needs no reload. */
const deviceTheme = () => { try { return localStorage.getItem('ph.theme'); } catch (e) { return null; } };
function applyTheme(id) {
  try { if (id) localStorage.setItem('ph.theme', id); else localStorage.removeItem('ph.theme'); } catch (e) { /* private mode */ }
  const link = $('#themecss');
  if (link) link.href = id ? `/theme.css?id=${enc(id)}` : `/theme.css?d=${Date.now()}`;
}
function syncThemeColor() {
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  const meta = document.querySelector('meta[name="theme-color"]');
  if (bg && meta) meta.content = bg;
}
{ const link = $('#themecss'); if (link) link.addEventListener('load', syncThemeColor); syncThemeColor(); }
function appearanceHTML(t, admin) {
  if (!t || t._err) return `<div class="panel"><p class="note">Themes couldn't be loaded${t && t._err ? `: ${esc(t._err)}` : ''}.</p></div>`;
  const mine = deviceTheme() && t.themes.some(x => x.id === deviceTheme()) ? deviceTheme() : null;
  const active = mine || t.default;
  const cards = t.themes.map(th => {
    const sw = th.swatches;
    const meta = [th.author && `by ${th.author}`, th.version && `v${th.version}`, th.id === t.default && 'default for all devices'].filter(Boolean).join(' · ');
    return `<div class="theme${th.id === active ? ' on' : ''}">
      <div class="tswatch" style="background:${esc(sw.background)}" aria-hidden="true">
        <i style="background:${esc(sw.surface)}"></i><i style="background:${esc(sw.accent)}"></i><i style="background:${esc(sw.text)}"></i>
        <span style="color:${esc(sw.text)}">Aa</span></div>
      <div class="tinfo"><b>${esc(th.name)}</b>${th.description ? `<span>${esc(th.description)}</span>` : ''}<small>${esc(meta)}</small></div>
      <div class="formbtns">
        ${th.id === active ? '<span class="pill">In use on this device</span>'
          : `<button class="btn small" data-nav data-act="theme-use" data-theme="${esc(th.id)}">Use on this device</button>`}
        ${admin && th.id !== t.default ? `<button class="btn small" data-nav data-act="theme-default" data-theme="${esc(th.id)}">Make default for all devices</button>` : ''}
      </div></div>`;
  }).join('');
  const follow = mine
    ? `This device uses its own theme. <button class="linkbtn" data-nav data-act="theme-follow">Use the default instead</button>`
    : 'This device uses the default theme, so it changes when the default does.';
  const problems = admin && t.problems && t.problems.length
    ? `<div class="panel" style="margin-top:12px"><h3>Themes that can't be used</h3>${t.problems.map(p => `<div class="issue"><code>${esc(p.folder)}</code><p>${esc(p.error)}</p></div>`).join('')}</div>` : '';
  return `<div class="themes">${cards}</div><p class="note" style="margin-top:10px">${follow} Make your own theme: see docs/themes.md in the PictureHouse source.</p>${problems}`;
}

/* Streaming limits: a bitrate cap for TVs on Wi-Fi, and how many videos may be converted at once. */
function streamingHTML(s, status) {
  if (!s || s._err) return '';
  const opt = (v, label, cur) => `<option value="${v}"${+cur === v ? ' selected' : ''}>${label}</option>`;
  const rates = [[0, 'Off: send original files'], [8, '8 Mbps'], [12, '12 Mbps (most Wi-Fi TVs)'], [15, '15 Mbps'], [20, '20 Mbps'], [30, '30 Mbps']];
  if (!rates.some(r => r[0] === +s.tv_max_bitrate_mbps)) rates.push([+s.tv_max_bitrate_mbps, `${s.tv_max_bitrate_mbps} Mbps`]);
  const slots = [[1, '1'], [2, '2'], [3, '3'], [4, '4'], [0, 'No limit']];
  if (!slots.some(r => r[0] === +s.max_transcodes)) slots.push([+s.max_transcodes, String(s.max_transcodes)]);
  const t = (status && status.transcodes) || {};
  const now = (t.active || []).length ? `Converting now: ${(t.active || []).map(esc).join(', ')}.` : 'Nothing is being converted right now.';
  return `<form class="panel metaform" id="streamform" novalidate autocomplete="off" style="margin-top:12px">
    <div class="form">
      <label class="field">TV bitrate limit<select name="rate" data-nav>${rates.map(([v, l]) => opt(v, l, s.tv_max_bitrate_mbps)).join('')}</select></label>
      <label class="field">Videos converted at once<select name="slots" data-nav>${slots.map(([v, l]) => opt(v, l, s.max_transcodes)).join('')}</select></label>
    </div>
    <div class="formbtns"><button type="submit" class="btn primary small" data-nav>Save</button></div>
    <p class="formerr" id="streamerr" role="alert" hidden></p>
    <p class="note">TV keeps buffering? It's usually Wi-Fi that can't keep up with a big file. With a limit set, videos above it are converted to fit as they play (the TV can still seek). A network cable to the TV avoids the need. ${esc(now)}</p>
  </form>`;
}
async function submitStreaming(f) {
  const err = $('#streamerr'), btn = f.querySelector('[type=submit]');
  btn.disabled = true; err.hidden = true;
  try {
    await api('/api/settings/streaming', { method: 'PUT', body: { tv_max_bitrate_mbps: +f.elements.rate.value, max_transcodes: +f.elements.slots.value } });
    toast('Saved. TVs pick this up next time they open a video.');
    route({ keepScroll: true, focus: '#streamform [type=submit]' });
  } catch (ex) { btn.disabled = false; err.textContent = ex.message; err.hidden = false; }
}

/* Admin access: this PC can change anything; other devices browse and play,
   and need the admin PIN (set on the PC) to change folders, settings or matches. */
function adminHTML(a) {
  if (!a || a._err) return '';
  if (a.this_pc) {
    return `<form class="panel adminbar" id="pinform" novalidate autocomplete="off">
      <span><b>Admin access.</b> ${a.pin_set
        ? 'Other devices can browse and play. To change folders, settings or matches from a phone or another computer, they enter the admin PIN.'
        : 'Other devices can browse and play, but only this PC can change folders, settings or matches. Set a PIN to allow it from your phone too.'}</span>
      <label class="field">${a.pin_set ? 'New PIN' : 'Admin PIN'}<input name="pin" type="password" inputmode="numeric" pattern="[0-9]*" minlength="4" maxlength="12" autocomplete="new-password" placeholder="4 to 12 digits" data-nav></label>
      <button type="submit" class="btn small" data-nav>${a.pin_set ? 'Change PIN' : 'Set PIN'}</button>
      ${a.pin_set ? '<button type="button" class="btn small" data-nav data-act="pin-remove">Remove PIN</button>' : ''}
      <p class="formerr" id="pinerr" role="alert" hidden></p></form>`;
  }
  if (a.admin) {
    return `<div class="panel adminbar"><span><b>Admin access on.</b> This device can change folders, settings and matches.</span>
      <button class="btn small" data-nav data-act="admin-logout">Sign out</button></div>`;
  }
  if (!a.pin_set) {
    const where = 'in the app\'s settings (on ZimaOS: App Store → My Apps → PictureHouse → ⋮ → Settings → Environment), then reload this page';
    if (a.pin_problem === 'invalid') {
      return `<div class="panel adminbar warn"><span><b>The admin PIN wasn't accepted.</b> PICTUREHOUSE_ADMIN_PIN must be 4 to 12 digits, numbers only. Change it ${where}.</span></div>`;
    }
    if (a.pin_problem === 'blank') {
      return `<div class="panel adminbar warn"><span><b>No admin PIN is set</b>, so folders and settings can't be changed yet. Set PICTUREHOUSE_ADMIN_PIN (4 to 12 digits) ${where}.</span></div>`;
    }
    return '<div class="panel adminbar"><span>You can browse and play here. Folders, settings and matches are changed on the PC running PictureHouse (it can set a PIN to allow changes from this device).</span></div>';
  }
  return `<form class="panel adminbar" id="loginform" novalidate autocomplete="off">
    <span>You can browse and play here. To change folders, settings or matches, enter the admin PIN.</span>
    <label class="field">Admin PIN<input name="pin" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="12" autocomplete="current-password" data-nav></label>
    <button type="submit" class="btn primary small" data-nav>Unlock</button>
    <p class="formerr" id="pinerr" role="alert" hidden></p></form>`;
}
async function submitPin(f, login) {
  const pin = f.elements.pin.value.trim(), err = $('#pinerr');
  const show = m => { err.textContent = m; err.hidden = false; f.elements.pin.focus(); };
  if (!/^\d{4,12}$/.test(pin)) { show('The PIN is 4 to 12 digits.'); return; }
  const btn = f.querySelector('[type=submit]'); btn.disabled = true; err.hidden = true;
  try {
    if (login) await api('/api/admin/login', { method: 'POST', body: { pin } });
    else await api('/api/admin/pin', { method: 'PUT', body: { pin } });
    toast(login ? 'Admin access on for this device' : 'PIN saved. Other devices were signed out.');
    route({ keepScroll: true });
  } catch (ex) { btn.disabled = false; f.elements.pin.value = ''; show(ex.message); }
}

/* Needs a match: films and series the matcher couldn't settle on its own. */
const candLabel = c => `${c.title || 'Untitled'}${c.year ? ` (${c.year})` : ''}`;
const pct = s => s != null && isFinite(+s) ? Math.round(+s * 100) + '%' : '';
function needsHTML(u) {
  if (!u || u._err) return `<p class="note">Couldn't load the list of unmatched items${u && u._err ? ': ' + esc(u._err) : ''}.</p>`;
  const offline = u.tmdb === false && !u.omdb;
  const off = offline ? '<p class="note">Online matching is off until a TMDb or OMDb key is added under Online metadata below. Titles come from file names and .nfo files.</p>' : '';
  const rows = [
    ...(u.films || []).map(f => ({ type: 'item', id: String(f.id), code: join(f.library, f.file), title: f.title, year: f.year, md: f.metadata, f })),
    ...(u.shows || []).map(s => ({ type: 'show', id: s.key, code: `${s.title}${s.year ? ` (${s.year})` : ''} · series`, title: s.title, year: s.year, md: s.metadata, s }))
  ];
  if (!rows.length) return off + `<p class="note">${offline ? 'Nothing is waiting for a match.' : 'Everything is matched.'}</p>`;
  return off + rows.map(r => {
    const md = r.md || {}, src = r.f || r.s;
    const cands = [...(md.candidates || [])].sort((a, b) => (b.score || 0) - (a.score || 0));
    regTarget({ type: r.type, id: r.id, title: r.title, year: r.year, cands, overview: src.overview, genres: src.genres, certification: src.certification,
      editType: r.type, editId: r.id });
    const reason = md.status === 'ambiguous' ? (cands.length ? `${pl(cands.length, 'possible match', 'possible matches')}. Pick the right one.` : 'Several possible matches.')
      : 'No match found on TMDb. Search for it by another title.';
    const tA = `data-t="${r.type}" data-id="${esc(r.id)}"`;
    return `<div class="issue"><code>${esc(r.code)}</code><p>${esc(reason)}</p>
      <div class="cands">${cands.slice(0, 3).map((c, i) => `<button class="cand${i === 0 ? ' best' : ''}" data-nav data-act="apply" ${tA} data-tmdb="${esc(c.tmdb_id ?? '')}" data-imdb="${esc(c.imdb_id ?? '')}" data-name="${esc(candLabel(c))}">${esc(candLabel(c))}${pct(c.score) ? ` <em>${esc(pct(c.score))}</em>` : ''}</button>`).join('')}
        <button class="cand" data-nav data-act="fix" ${tA}>Search…</button></div></div>`;
  }).join('');
}

/* Online metadata settings + live matcher status */
function metaStatus(m) {
  m = m || {};
  const waiting = m.pending ? `${fmtNum(m.pending)} waiting to be matched` : '', attn = m.needs_attention ? `${fmtNum(m.needs_attention)} need a match` : '';
  if (m.running) return { cls: '', pill: 'Matching now', sub: join(waiting, attn) || 'Looking up titles…' };
  if (!m.tmdb && !m.omdb) return { cls: 'off', pill: 'Online matching off', sub: join('Add a TMDb key to fetch posters, synopses and cast', attn) };
  const pill = m.tmdb ? (m.omdb ? 'Online matching on · TMDb + OMDb' : 'Online matching on') : 'Online matching on · OMDb only';
  return { cls: '', pill, sub: join(waiting, attn) || 'Everything is up to date.' };
}
function metaPanelHTML(s, m) {
  if (!s || s._err) return `<div class="panel"><p class="note">Couldn't load the metadata settings${s && s._err ? ': ' + esc(s._err) : ''}.</p></div>`;
  S.msettings = s;
  const st = metaStatus(m);
  return `<form class="panel metaform" id="metaform" novalidate autocomplete="off">
    <div class="metastat"><span class="pill ${st.cls}" id="metaPill">${esc(st.pill)}</span><span id="metaSub">${esc(st.sub)}</span></div>
    <div class="form">
      <label class="field">TMDb API key<input name="key" type="password" autocomplete="off" spellcheck="false" data-nav
        placeholder="${s.tmdb_api_key_set ? `Saved (ends …${esc(s.tmdb_api_key_hint || '')}) · paste to replace` : 'Paste your key'}"></label>
      <label class="field">OMDb API key · fallback<input name="okey" type="password" autocomplete="off" spellcheck="false" data-nav
        placeholder="${s.omdb_api_key_set ? `Saved (ends …${esc(s.omdb_api_key_hint || '')}) · paste to replace` : 'Optional'}"></label>
      <label class="field">Language<input name="language" value="${esc(s.language || '')}" placeholder="en-GB" spellcheck="false" data-nav></label>
      <label class="field">Region<input name="region" value="${esc(s.region || '')}" placeholder="GB" spellcheck="false" data-nav></label>
    </div>
    <label class="check"><input type="checkbox" name="match_on_scan" ${s.match_on_scan ? 'checked' : ''} data-nav> Match new files after each scan</label>
    <label class="check"><input type="checkbox" name="save_local" ${s.save_local ? 'checked' : ''} data-nav> Save a copy next to the files</label>
    <p class="note" style="margin-top:-6px">Writes <code>Film.nfo</code>, <code>Film-poster.jpg</code> and <code>Film-fanart.jpg</code> beside each matched film, and <code>tvshow.nfo</code> + <code>poster.jpg</code> in series folders, so titles and artwork come back without internet or after a reinstall (Kodi and Jellyfin read them too). Files you made yourself are never replaced.${s.save_local_running ? ' Saving now…' : ''}</p>
    <div class="formbtns"><button type="submit" class="btn primary small" data-nav>Save</button>
      <button type="button" class="btn small" data-nav data-act="meta-match" ${m && m.running ? 'disabled' : ''}>Match new items</button>
      <button type="button" class="btn small" data-nav data-act="meta-refresh" ${m && m.running ? 'disabled' : ''}>Refresh all metadata</button></div>
    <p class="formerr" id="metaerr" role="alert" hidden></p>
    <p class="note">${s.tmdb_api_key_set ? `A TMDb key ending …${esc(s.tmdb_api_key_hint || '')} is saved.` : 'No TMDb key saved yet.'} Posters, synopses and cast come from TMDb.
      <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener" data-nav>Get a free key at themoviedb.org</a></p>
    <p class="note">${s.omdb_api_key_set ? `An OMDb key ending …${esc(s.omdb_api_key_hint || '')} is saved.` : 'No OMDb key.'} OMDb (IMDb data) is used when TMDb isn't set up or finds nothing: titles, plots and ratings, but no backdrops or episode plots, and about 1,000 lookups a day.
      <a href="https://www.omdbapi.com/apikey.aspx" target="_blank" rel="noopener" data-nav>Get a free OMDb key</a></p>
  </form>`;
}
function updateMetaUI(m) {
  const st = metaStatus(m), P_ = $('#metaPill'), U = $('#metaSub');
  if (P_) { P_.textContent = st.pill; P_.className = 'pill ' + st.cls; }
  if (U) U.textContent = st.sub;
  $$('[data-act="meta-match"], [data-act="meta-refresh"]').forEach(b => { b.disabled = !!m.running; });
}
const META = { polling: false, expect: false };
async function pollMeta() {
  if (META.polling) return;
  META.polling = true;
  let fails = 0, early = 0;
  try {
    for (;;) {
      if (S.route.name !== 'library') break;
      let m;
      try { m = await api('/api/metadata'); fails = 0; } catch (e) { if (++fails >= 5) break; await sleep(1500); continue; }
      updateMetaUI(m);
      if (m.running) { META.expect = true; META.seen = true; await sleep(1500); continue; }
      if (META.expect && !META.seen && early++ < 2) { await sleep(800); continue; } // the background thread may not have flagged itself yet
      if (META.expect) {
        META.expect = false; META.seen = false;
        toast('Matching finished' + (m.needs_attention ? ` · ${pl(m.needs_attention, 'item needs', 'items need')} a match` : ''));
        refreshStatus();
        const f = document.activeElement;
        const typing = f && f.closest && f.closest('#metaform') && f.value;
        if (S.route.name === 'library' && !typing && !S.sheet) route({ keepScroll: true, keepFocus: true });
      }
      break;
    }
  } finally { META.polling = false; }
}
async function startMeta(kind) {
  const url = kind === 'refresh' ? '/api/metadata/refresh' : '/api/metadata/match';
  try {
    const r = await api(url, { method: 'POST', body: kind === 'refresh' ? undefined : { force: false } });
    toast(r && r.started === false ? 'Matching is already running — following it' : kind === 'refresh' ? 'Refreshing metadata…' : 'Matching new items…');
    META.expect = true; META.seen = false; updateMetaUI({ ...(S.status && S.status.metadata), running: true });
    pollMeta();
  } catch (e) { toast((kind === 'refresh' ? 'Could not refresh: ' : 'Could not start matching: ') + e.message); }
}

/* ---------- Fix match / Edit metadata sheet ---------- */
const sheetEl = document.createElement('div');
sheetEl.className = 'sheet'; sheetEl.id = 'sheet'; sheetEl.hidden = true;
sheetEl.setAttribute('role', 'dialog'); sheetEl.setAttribute('aria-modal', 'true'); sheetEl.setAttribute('aria-labelledby', 'sheetTitle');
sheetEl.innerHTML = '<div class="sheet-box" id="sheetBox"></div>';
document.body.appendChild(sheetEl);
const tBase = (type, id) => type === 'show' ? `/api/shows/${enc(id)}` : `/api/items/${enc(id)}`;
function sheetHead(eyebrow, title) {
  return `<div class="sheet-head"><div><p class="eyebrow">${esc(eyebrow)}</p><h2 id="sheetTitle">${esc(title)}</h2></div>
    <button class="btn small" data-nav data-act="sheet-close" aria-label="Close">Close</button></div>`;
}
function openSheet(state) {
  S.sheet = { ...state, opener: document.activeElement };
  sheetEl.hidden = false;
  document.body.classList.add('sheet-open');
}
function closeSheet() {
  if (!S.sheet) return;
  const op = S.sheet.opener;
  S.sheet = null; sheetEl.hidden = true; $('#sheetBox').innerHTML = '';
  document.body.classList.remove('sheet-open');
  if (op && document.contains(op)) op.focus({ preventScroll: true });
  else { const f = $('#view [data-nav]'); if (f) f.focus({ preventScroll: true }); }
}
function openFix(t) {
  openSheet({ mode: 'fix', t, cands: t.cands && t.cands.length ? t.cands : null, msg: null });
  const scope = t.show ? 'Picking a match updates the whole series.' : '';
  $('#sheetBox').innerHTML = `${sheetHead('Fix match', t.title || 'Untitled')}
    <form class="fixsearch" id="fixform" novalidate autocomplete="off">
      <label class="field">Title<input name="q" value="${esc(t.title || '')}" spellcheck="false" data-nav></label>
      <label class="field yr">Year<input name="year" value="${esc(t.year || '')}" inputmode="numeric" maxlength="4" data-nav></label>
      <button type="submit" class="btn small primary" data-nav>Search</button>
    </form>
    <div id="fixres" aria-live="polite"></div>
    <p class="note">Pick the right title to replace the details and artwork with TMDb's. ${scope}</p>`;
  drawFixResults();
  if (!S.sheet.cands) runFixSearch(null);
  const first = $('#fixres .mcand') || $('#fixform input[name=q]'); if (first) first.focus();
}
function drawFixResults() {
  const sh = S.sheet; if (!sh || sh.mode !== 'fix') return;
  const box = $('#fixres'); if (!box) return;
  const t = sh.t, tA = `data-t="${t.type}" data-id="${esc(t.id)}"`;
  if (sh.msg) {
    box.innerHTML = sh.msg.noKey
      ? `<div class="sheet-msg"><p>${esc(sh.msg.text)}</p><p class="note">Online matching needs a TMDb API key. Add one under <b>Online metadata</b> on the Library page.</p>
          ${S.route.name === 'library' ? '' : '<button class="btn small" data-nav data-act="sheet-lib">Open Library settings</button>'}</div>`
      : `<div class="sheet-msg"><p>${esc(sh.msg.text)}</p></div>`;
    return;
  }
  if (sh.loading) { box.innerHTML = '<p class="note">Searching TMDb…</p>'; return; }
  const list = sh.cands || [];
  if (!list.length) { box.innerHTML = '<p class="note">No results. Try a shorter title, or leave the year empty.</p>'; return; }
  box.innerHTML = `<div class="mcands">${list.map(c => `<button class="mcand" data-nav data-act="apply" ${tA} data-tmdb="${esc(c.tmdb_id ?? '')}" data-imdb="${esc(c.imdb_id ?? '')}" data-name="${esc(candLabel(c))}">
      <span class="mthumb">${imgTag(c.poster, c.title, 'p')}</span>
      <span class="mtxt"><span class="mtop"><b>${esc(c.title || 'Untitled')}</b>${c.year ? `<span>${esc(c.year)}</span>` : ''}${pct(c.score) ? `<em class="${(c.score || 0) >= 0.6 ? '' : 'lo'}">${esc(pct(c.score))}</em>` : ''}</span>
        ${c.overview ? `<span class="mov">${esc(clip(c.overview, 140))}</span>` : ''}</span></button>`).join('')}</div>`;
}
async function runFixSearch(q, year) {
  const sh = S.sheet; if (!sh || sh.mode !== 'fix') return;
  const tok = sh.tok = (sh.tok || 0) + 1;
  sh.loading = true; sh.msg = null; drawFixResults();
  const qs = new URLSearchParams();
  if (q) qs.set('q', q);
  if (year) qs.set('year', year);
  try {
    const d = await api(`${tBase(sh.t.type, sh.t.id)}/candidates${qs.toString() ? '?' + qs : ''}`);
    if (S.sheet !== sh || sh.tok !== tok) return;
    sh.cands = d.candidates || [];
  } catch (e) {
    if (S.sheet !== sh || sh.tok !== tok) return;
    sh.msg = e.status === 503 ? { noKey: true, text: `Online metadata isn't available: ${e.message}` } : { text: `Search failed: ${e.message}` };
  }
  sh.loading = false; drawFixResults();
}
function openEdit(t) {
  openSheet({ mode: 'edit', t });
  $('#sheetBox').innerHTML = `${sheetHead(t.type === 'show' ? 'Edit series details' : 'Edit details', t.title || 'Untitled')}
    <form id="editform" class="editform" novalidate autocomplete="off">
      <label class="field">Title<input name="title" value="${esc(t.title || '')}" data-nav></label>
      <div class="form">
        <label class="field">Year<input name="year" value="${esc(t.year || '')}" inputmode="numeric" maxlength="4" data-nav></label>
        <label class="field">Certification<input name="certification" value="${esc(t.certification || '')}" placeholder="12A" data-nav></label>
      </div>
      <label class="field">Genres<input name="genres" value="${esc(t.genres || '')}" placeholder="Drama, Comedy" data-nav></label>
      <label class="field">Synopsis<textarea name="overview" rows="5" data-nav>${esc(t.overview || '')}</textarea></label>
      <p class="formerr" id="editerr" role="alert" hidden></p>
      <p class="note">Your changes are kept when the library is rescanned. Use “Undo my changes” to go back to the matched details.</p>
      <div class="formbtns"><button type="submit" class="btn primary small" data-nav>Save</button><button type="button" class="btn small" data-nav data-act="sheet-close">Cancel</button></div>
    </form>`;
  const f = $('#editform input[name=title]'); if (f) f.focus();
}
/* After a match/edit/unlock: shows may come back with a new key (the title changed). */
function afterChange(type, oldId, res) {
  refreshStatus();
  if (type === 'show' && res && res.key && res.key !== oldId) {
    S.season[res.key] = S.season[oldId];
    if (S.route.name === 'show' && S.route.arg === oldId) { nav(`#/show/${enc(res.key)}`, { replace: true, keepScroll: true, focusFirst: true }); return; }
  }
  route({ keepScroll: true, keepFocus: true });
}
async function applyMatch(btn) {
  const type = btn.dataset.t, id = btn.dataset.id, name = btn.dataset.name;
  const tmdb = btn.dataset.tmdb ? +btn.dataset.tmdb : null, imdb = btn.dataset.imdb || null;
  btn.disabled = true;
  try {
    const body = tmdb != null ? { tmdb_id: tmdb } : { imdb_id: imdb };
    const res = await api(`${tBase(type, id)}/match`, { method: 'POST', body });
    const md = res && res.metadata;
    const applied = !md || (tmdb != null ? md.tmdb_id === tmdb : md.imdb_id === imdb);
    if (res && res.kind === 'film' && !applied) toast(`The server didn't apply “${name}”. Check the keys under Online metadata on the Library page.`);
    else toast(`Matched to “${name}”. Rescans will keep this.`);
    if (S.sheet) closeSheet();
    afterChange(type, id, res);
  } catch (e) {
    btn.disabled = false;
    if (S.sheet && S.sheet.mode === 'fix') { S.sheet.msg = e.status === 503 ? { noKey: true, text: `TMDb isn't available: ${e.message}` } : { text: `Couldn't apply the match: ${e.message}` }; drawFixResults(); }
    else toast(e.status === 503 ? `Online metadata isn't available: ${e.message}. Check the keys under Online metadata.` : `Couldn't apply the match: ${e.message}`);
  }
}

/* ---------- Scan ---------- */
function summarize(results) {
  if (!results || !results.length) return null;
  const t = { new: 0, changed: 0, unchanged: 0, failed: 0, missing: 0, seconds: 0 };
  results.forEach(r => Object.keys(t).forEach(k => { t[k] += +r[k] || 0; }));
  const offline = results.filter(r => r.available === false).map(r => r.library);
  const parts = [`${fmtNum(t.new)} new`, `${fmtNum(t.changed)} changed`, `${fmtNum(t.unchanged)} unchanged`];
  if (t.failed) parts.push(`${fmtNum(t.failed)} failed`);
  if (t.missing) parts.push(`${fmtNum(t.missing)} missing`);
  let long = parts.join(', ') + (t.seconds ? ` in ${t.seconds < 10 ? t.seconds.toFixed(1) : Math.round(t.seconds)} s` : '') + '.';
  if (offline.length) long += ` Offline: ${offline.join(', ')}.`;
  const short = (t.new || t.changed ? `${fmtNum(t.new)} new, ${fmtNum(t.changed)} changed` : 'nothing new') + (t.failed ? `, ${fmtNum(t.failed)} failed` : '') + (offline.length ? `, ${offline.length} offline` : '');
  return { long, short };
}
function scanText(s) {
  if (!s) return { title: 'Scan status unknown', sub: '', pct: 0 };
  if (s.running) {
    return { title: `Scanning ${s.library || '…'}${s.total ? ` — ${fmtNum(s.done)} of ${fmtNum(s.total)}` : ''}`,
      sub: s.current || 'Unchanged files are skipped.', pct: s.total ? Math.min(100, s.done / s.total * 100) : 0 };
  }
  const sum = summarize(s.results);
  return { title: s.finished_at ? `Last scan finished at ${fmtWhen(s.finished_at)}` : 'No scan has run yet',
    sub: sum ? sum.long : 'Scan your folders to pick up new and changed files.', pct: 0 };
}
function updateScanUI(s) {
  const t = scanText(s);
  const T = $('#scanTitle'), U = $('#scanSub'), B = $('#scanBar'), btn = $('#rescan');
  if (T) T.textContent = t.title;
  if (U) U.textContent = t.sub;
  if (B) B.style.width = t.pct + '%';
  if (btn) { btn.disabled = !!s.running; btn.textContent = s.running ? 'Scanning…' : 'Scan all folders'; }
}
const SCAN = { polling: false, expect: false, startedAt: 0 };
async function pollScan() {
  if (SCAN.polling) return;
  SCAN.polling = true;
  let fails = 0, early = 0;
  try {
    for (;;) {
      let s;
      try { s = await api('/api/scan'); fails = 0; } catch (e) { if (++fails >= 5) { toast('Lost contact with the server while scanning'); break; } await sleep(1500); continue; }
      updateScanUI(s);
      if (s.running) { SCAN.expect = true; await sleep(700); continue; }
      // The scan thread may not have flagged itself running yet right after POST /api/scan.
      const fin = toDate(s.finished_at);
      if (SCAN.startedAt && (!fin || fin.getTime() < SCAN.startedAt - 2000) && early++ < 6) { await sleep(700); continue; }
      if (SCAN.expect) { SCAN.expect = false; SCAN.startedAt = 0; scanFinished(s); }
      break;
    }
  } finally { SCAN.polling = false; }
}
async function startScan(libraries) {
  const btn = $('#rescan'); if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }
  try {
    const r = await api('/api/scan', { method: 'POST', body: { libraries: libraries || null, full: false } });
    if (r && r.started === false) toast('A scan is already running — following it');
    SCAN.expect = true; SCAN.startedAt = Date.now();
    pollScan();
  } catch (e) {
    toast('Could not start the scan: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Scan all folders'; }
  }
}
function scanFinished(s) {
  const sum = summarize(s.results);
  toast('Scan finished · ' + (sum ? sum.short : 'done'));
  refreshStatus();
  if (S.route.name === 'library') route({ keepScroll: true, keepFocus: true });
}

/* ---------- Server status pill ---------- */
async function refreshStatus() {
  const el = $('#srv');
  try {
    const s = await api('/api/status');
    S.status = s;
    if ($('#devmenu').hidden) renderDev();
    const sc = s.scan || {};
    const txt = 'Server online · ' + (sc.running ? 'scanning…' : sc.finished_at ? 'scanned ' + fmtHM(sc.finished_at) : 'not scanned yet');
    el.classList.remove('bad');
    el.innerHTML = `<span class="led"></span>${esc(txt)}`;
    if (sc.running && S.route.name === 'library') pollScan();
  } catch (e) {
    el.classList.add('bad');
    el.innerHTML = '<span class="led bad"></span>Server unreachable';
  }
}

/* ---------- Routing ---------- */
function parseRoute() {
  const h = location.hash.replace(/^#\/?/, '');
  const qi = h.indexOf('?');
  const path = qi < 0 ? h : h.slice(0, qi), qs = qi < 0 ? '' : h.slice(qi + 1);
  const parts = path.split('/').filter(Boolean).map(p => { try { return decodeURIComponent(p); } catch (e) { return p; } });
  let name = parts[0] || 'home';
  const arg = parts.slice(1).join('/') || null;
  if (!VIEWS[name] || ((name === 'item' || name === 'show' || name === 'album') && !arg)) name = 'home';
  return { name, arg, q: new URLSearchParams(qs) };
}
const SECTION = { home: 'home', films: 'films', tv: 'tv', show: 'tv', music: 'music', album: 'music', 'home-videos': 'home-videos', library: 'library' };
async function route(o = {}) {
  const r = parseRoute();
  const prevName = S.route.name;
  S.route = r;
  $$('.navbtn').forEach(b => b.classList.toggle('on', b.dataset.sect === SECTION[r.name]));
  const q = $('#q');
  if (document.activeElement !== q) q.value = r.name === 'search' ? (r.q.get('q') || '') : '';
  if (r.name !== 'library') { S.lib.adding = false; S.lib.confirm = null; if (prevName === 'library') S.lib.justAdded = null; }
  const def = VIEWS[r.name];
  const tok = ++S.tok;
  const v = $('#view');
  const focusedSel = o.keepFocus ? focusKey(document.activeElement) : null;
  const slow = setTimeout(() => { if (tok === S.tok) v.innerHTML = '<p class="loading">Loading…</p>'; }, 220);
  let data, err;
  try { data = await def.load(r); } catch (e) { err = e; }
  clearTimeout(slow);
  if (tok !== S.tok) return;
  document.title = (r.name === 'home' ? '' : (data && (data.title || (data.q != null && `Search: ${data.q}`)) || def.title) + ' · ') + 'PictureHouse';
  if (err) { S.current = null; v.innerHTML = errorView(err); if (err.status === 0) refreshStatus(); }
  else {
    S.current = { def, data };
    drawCurrent();
    if (def.after) def.after(data);
  }
  if (!o.keepScroll) window.scrollTo(0, 0);
  if (o.focus) focusSel(o.focus);
  else if (focusedSel) focusSel(focusedSel);
  else if (o.focusFirst) { const f = v.querySelector('[data-nav]'); if (f) f.focus({ preventScroll: true }); }
}
function drawCurrent() {
  const v = $('#view');
  try { v.innerHTML = S.current.def.draw(S.current.data); }
  catch (e) { console.error(e); v.innerHTML = errorView(new Error('This view failed to draw: ' + e.message)); }
}
function redraw(focus) { if (!S.current) return; drawCurrent(); if (focus) focusSel(focus); }
function focusKey(el) {
  if (!el || !el.dataset) return null;
  for (const k of ['act', 'sort', 'season', 'feat', 'href', 'play', 'track']) if (el.dataset[k] != null) return `[data-${k}="${CSS.escape(el.dataset[k])}"]`;
  return el.id ? '#' + CSS.escape(el.id) : null;
}
function focusSel(sel) { const el = typeof sel === 'string' ? document.querySelector(sel) : sel; if (el) el.focus({ preventScroll: true }); }
function errorView(e) {
  if (e && e.status === 404) return `${backBtn()}<div class="panel errbox"><h3>Not found</h3><p>This item isn't in the library any more. It may have been removed or renamed.</p><button class="btn small" data-nav data-href="#/">Go home</button></div>`;
  return `<div class="panel errbox"><h3>Couldn't load this page</h3><p>${esc(e && e.message || 'Unknown error')}</p><button class="btn small" data-nav data-act="reload">Try again</button></div>`;
}
function nav(href, o = {}) {
  const cur = location.hash || '#/';
  if (href === cur && !o.force) { if (o.focusFirst) { const f = $('#view [data-nav]'); if (f) f.focus(); } return; }
  const i = (history.state && history.state.i) || 0;
  if (o.replace) history.replaceState({ i }, '', href);
  else history.pushState({ i: i + 1 }, '', href);
  route(o);
}
function back() {
  if (history.state && history.state.i > 0) history.back();
  else if (S.route.name !== 'home') nav('#/', { replace: true, focusFirst: true });
}
window.addEventListener('popstate', () => route({ focusFirst: true }));

/* ---------- Play-to menu: this browser, a Chromecast (secure context + Chromium), AirPlay from the player in Safari.
   TVs pull from the server over DLNA and don't need picking here. ---------- */
function renderDevBtn() {
  const s = C.ready ? castSession() : null;
  const where = s ? castDevice() : P && !P.cast && airWireless() ? 'AirPlay' : 'This browser';
  $('#devbtn').innerHTML = `${ico('cast')}<span>Play to</span><strong>${esc(where)}</strong>`;
}
/* The https:// address on the same network as the one this page was opened on, if there is one. */
function httpsUrlFor(st) {
  const urls = (st && st.https && st.https.urls) || [];
  if (!urls.length) return null;
  const host = u => { try { return new URL(u).hostname; } catch (e) { return ''; } };
  const h = location.hostname;
  const exact = urls.find(u => host(u) === h);
  if (exact) return exact;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) {
    const net = h.split('.').slice(0, 3).join('.') + '.';
    const same = urls.find(u => host(u).startsWith(net));
    if (same) return same;
  }
  return urls[0];
}
function secureNote() {
  if (window.isSecureContext) return '';
  const st = S.status, https = st && st.https;
  const lib = '<button class="linkbtn" data-nav data-href="#/library?panel=phones">Phones &amp; tablets</button>';
  if (!https || !https.enabled) {
    return `<div class="devnote warn"><p>Casting to a Chromecast and installing the app need the secure (HTTPS) address, which is off on this server. See ${lib} on the Library page to turn it on.</p></div>`;
  }
  const u = httpsUrlFor(st);
  return `<div class="devnote warn"><p>Casting to a Chromecast and installing the app only work on the secure address${u ? `: <a href="${esc(u)}" data-nav>${esc(u.replace(/\/$/, ''))}</a>` : ''}.</p>
    <p>Install this server's certificate on the device first: ${lib} on the Library page.</p></div>`;
}
function renderDev() {
  renderDevBtn();
  const s = C.ready ? castSession() : null;
  let opts = `<button class="devopt ${s ? '' : 'on'}" data-nav data-devsel="local"><b>This browser</b><small>${AIR.available ? 'Web player · AirPlay from the player' : 'Web player'}</small><span class="tick">${ico('check')}</span></button>`;
  let note = '';
  if (C.ready) {
    const none = C.state === 'NO_DEVICES_AVAILABLE';
    opts += `<button class="devopt ${s ? 'on' : ''}" data-nav data-devsel="cast"><b>${esc(s ? castDevice() : 'Chromecast')}</b>
      <small>${s ? (P && !P.cast ? 'Connected · send what\'s playing' : 'Connected') : none ? 'No Chromecast found on this network' : 'Pick a device'}</small><span class="tick">${ico('check')}</span></button>`;
  } else if (castCapable()) {
    note = C.failed ? '<p class="devnote">Chromecast: the Google Cast library couldn\'t load. It needs an internet connection.</p>' : '<p class="devnote">Looking for Chromecasts…</p>';
  } else if (window.isSecureContext) {
    note = AIR.supported ? '' : '<p class="devnote">Casting to a Chromecast works in Chrome or Edge.</p>';
  }
  $('#devmenu').innerHTML = `<h3>Play to</h3>${opts}${note}${secureNote()}
    <p class="devnote">TVs and apps don't need picking here: they find this server on your network over DLNA and play from their own menus.</p>`;
}
function toggleDevMenu(open) {
  const m = $('#devmenu'); const show = open == null ? m.hidden : open;
  if (show && m.hidden) renderDev();
  m.hidden = !show;
  $('#devbtn').setAttribute('aria-expanded', String(show));
  if (show) { const on = m.querySelector('.devopt.on'); if (on) on.focus(); }
}
function pickDevice(which) {
  toggleDevMenu(false); $('#devbtn').focus();
  if (which === 'cast') { requestCast(); return; }
  if (C.ready && castSession()) { castDisconnect(); if (!P) toast('Playing in this browser'); return; }
  toast('Playing in this browser');
}
let toastT;
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 3200); }

/* ---------- Install (PWA) ---------- */
const INSTALL = { prompt: null };
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const standalone = () => (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
$('#installbtn').innerHTML = `${ico('download')}<span class="lbl">Install app</span>`;
$('#installbtn').setAttribute('aria-label', 'Install the PictureHouse app');
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  INSTALL.prompt = e;
  $('#installbtn').hidden = false;
  refreshInstallLine();
});
window.addEventListener('appinstalled', () => { INSTALL.prompt = null; $('#installbtn').hidden = true; refreshInstallLine(); toast('PictureHouse is installed'); });
async function installApp() {
  const e = INSTALL.prompt;
  if (!e) { toast(isIOS() ? 'In Safari: Share → Add to Home Screen' : 'This browser can\'t install the app from here'); return; }
  INSTALL.prompt = null; $('#installbtn').hidden = true; refreshInstallLine();
  try { e.prompt(); const c = await e.userChoice; if (c && c.outcome === 'dismissed') toast('Not installed'); }
  catch (er) { toast('Couldn\'t install: ' + er.message); }
}
function installLine() {
  if (standalone()) return '<p class="note">You\'re using the installed app.</p>';
  if (INSTALL.prompt) return '<div class="installrow"><button class="btn primary small" data-nav data-act="install">Install app</button><span class="note">Adds PictureHouse to this device\'s home screen and app list.</span></div>';
  if (isIOS()) return '<p class="note">To install on iPhone or iPad: open the secure address in Safari, then Share → Add to Home Screen.</p>';
  if (!window.isSecureContext) return '<p class="note">Installing the app needs the secure address.</p>';
  return '';
}
function refreshInstallLine() { const el = $('#installline'); if (el) el.innerHTML = installLine(); }
function registerSW() {
  if (!window.isSecureContext || !('serviceWorker' in navigator)) return;
  const go = () => navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(e => console.warn('Service worker registration failed:', e.message));
  if (document.readyState === 'complete') go(); else window.addEventListener('load', go);
}

/* Library: phones and tablets (HTTPS address, certificate, install). */
/* The address a phone should use. On the PC itself the page is usually on
   127.0.0.1, which means nothing to a phone, so prefer a home-network address. */
function lanAddress(st) {
  const local = /^(127\.|localhost$|\[?::1)/.test(location.hostname);
  if (!local) return location.host;
  const port = (st && st.server && st.server.http_port) || location.port;
  const hosts = ((st && st.dlna && st.dlna.addresses) || []).map(u => { try { return new URL(u).hostname; } catch (e) { return ''; } }).filter(Boolean);
  // Typical home networks first; VPN and virtual adapters (10.x, 172.x) last.
  const pick = hosts.find(h => h.startsWith('192.168.')) || hosts[0];
  return pick ? `${pick}:${port}` : location.host;
}

function androidHTML(st) {
  if (ANDROID) {
    return `<div class="panel"><p class="note">You're using the PictureHouse Android app.</p>
      <div><button class="btn small" data-nav data-act="android-server">Connect to a different server</button></div></div>`;
  }
  if (!st || !st.android_apk) return '';
  return `<div class="panel"><p class="note"><b>Android app.</b> Finds this server on your Wi-Fi by itself, plays full screen, and can hand videos to VLC or other apps (which can cast to a Chromecast).</p>
    <div><a class="btn small" href="/static/downloads/PictureHouse.apk" download="PictureHouse.apk" data-nav>${ico('download')} Get the Android app</a></div>
    <ol class="note"><li>On the phone, open <b>${esc(lanAddress(st))}</b> in the browser, go to Library and tap the button.</li>
      <li>Open the downloaded file. If asked, allow your browser to install apps.</li>
      <li>Open PictureHouse from the app drawer.</li></ol></div>`;
}

function phonesHTML(st) {
  const h = (st && st.https) || {};
  const httpPort = st && st.server && st.server.http_port;
  const install = `<div id="installline">${installLine()}</div>`;
  if (!h.enabled) {
    return `<div class="panel phones" id="phones"><p class="note">The secure (HTTPS) address is off, so phones and tablets can't install the app or cast to a Chromecast.
      To turn it on, set <code>https_port = 9596</code> under <code>[server]</code> in config.toml, then restart the server.</p>${install}</div>`;
  }
  const ca = h.ca_url || '/ca.crt';
  // From an https page, fetch the certificate over plain http so it downloads before the device trusts it.
  const certHref = location.protocol === 'https:' && httpPort ? `http://${location.hostname}:${httpPort}${ca}` : ca;
  const urls = h.urls || [];
  return `<div class="panel phones" id="phones">
    <p class="note">Open the secure address on a phone or tablet to install PictureHouse as an app and to cast to a Chromecast. Install the certificate first so the device trusts it.</p>
    <dl class="kv">
      <dt>Secure address</dt><dd>${urls.length ? urls.map(u => `<a href="${esc(u)}" data-nav>${esc(u)}</a>`).join('<br>') : 'No network address found'}</dd>
      ${h.ca_fingerprint ? `<dt>SHA-256</dt><dd class="fp">${esc(h.ca_fingerprint)}</dd>` : ''}
    </dl>
    <div><a class="btn small" href="${esc(certHref)}" data-nav>${ico('download')} Download certificate</a></div>
    <div class="steps">
      <div><b>iPhone and iPad</b><ol>
        <li>Open the certificate link in Safari and allow the download.</li>
        <li>Settings → General → VPN &amp; Device Management → install the profile.</li>
        <li>Settings → General → About → Certificate Trust Settings → turn on full trust for “PictureHouse local CA”.</li></ol></div>
      <div><b>Android</b><ol>
        <li>Download the certificate.</li>
        <li>Settings → Security → More security settings → Encryption &amp; credentials → Install a certificate → CA certificate, then pick the file.</li></ol></div>
    </div>
    ${install}
  </div>`;
}

/* ---------- Video player ---------- */
const V = $('#pvideo');
const PL = $('#player');
let P = null;
const MEDIA_ERR = { 1: 'playback was aborted', 2: 'a network error interrupted the stream', 3: 'the browser could not decode the video', 4: 'the browser cannot play this format' };
const HLS_MIME = 'application/vnd.apple.mpegurl';
const HLS_JS = '/static/vendor/hls-1.7.3.min.js';

/* hls.js is only fetched the first time a stream needs it. */
let hlsLib = null;
function loadHlsJs() {
  if (!hlsLib) {
    hlsLib = new Promise(resolve => {
      if (window.Hls) { resolve(window.Hls); return; }
      const s = document.createElement('script');
      s.src = HLS_JS; s.async = true;
      s.onload = () => resolve(window.Hls || null);
      s.onerror = () => { hlsLib = null; resolve(null); };
      document.head.appendChild(s);
    });
  }
  return hlsLib;
}
/* How this browser plays an HLS playlist: 'native' (Safari, iOS), 'hlsjs' (Media Source Extensions) or null (use the fMP4 stream).
   Android Chrome also claims native HLS support, so Chromium browsers use hls.js when they can; Safari keeps native playback for AirPlay. */
async function hlsSupport() {
  const mse = !!(window.MediaSource || window.ManagedMediaSource);
  if (V.canPlayType(HLS_MIME) && (!window.chrome || !mse)) return 'native';
  if (!mse) return null;
  const Hls = await loadHlsJs();
  if (Hls && Hls.isSupported()) return 'hlsjs';
  return V.canPlayType(HLS_MIME) ? 'native' : null;
}
function destroyHls(p) {
  if (p && p.hls) { try { p.hls.destroy(); } catch (e) { /* already gone */ } p.hls = null; }
}
function closeHls(sid) {
  if (sid) api(`/api/hls/${enc(sid)}`, { method: 'DELETE', keepalive: true }).catch(() => {});
}
/* Stop whatever the local <video> is doing (hls.js, a playlist or a progressive stream). */
function detachLocal(p) {
  destroyHls(p);
  V.pause();
  if (V.getAttribute('src') || V.currentSrc) { V.removeAttribute('src'); V.load(); }
}

function curPos() {
  if (!P) return 0;
  if (P.hold != null) return P.hold;
  if (P.cast) return castPos();
  return P.offset + (V.currentTime || 0);
}
const isPlaying = () => !!(P && P.play && !P.failed && (P.cast ? P.cast.playing : !V.paused));
function pmsg(html, isErr) { const m = $('#pmsg'); if (html == null) { m.hidden = true; m.innerHTML = ''; return; } m.className = 'pmsg' + (isErr ? ' err' : ''); m.innerHTML = html; m.hidden = false; }
function playerError(msg) {
  if (!P) return;
  if (P.hold == null) P.hold = curPos();
  P.failed = true; P.playing = false;
  if (P.cast) P.cast.playing = false;
  destroyHls(P);
  const mode = P.play ? modeOf(P.play.mode).label : '';
  pmsg(`<b>Playback failed</b><p>${esc(msg)}${mode ? ` (${esc(mode)})` : ''}.</p><button class="btn small" data-nav id="pretry">Try again</button>`, true);
  PL.classList.remove('has-video');
  drawPlayer(); poke();
  const r = $('#pretry'); if (r) r.focus();
}
/* Hand the current video to another Android app (VLC, MX Player, BubbleUPnP...).
   Those apps can also cast to a Chromecast, which a WebView can't. */
function openInApp() {
  const p = P; if (!ANDROID || !p || !p.play) return;
  const origin = location.origin, pos = Math.floor(curPos() || 0);
  let url, mime;
  if (p.play.mode === 'direct') { url = origin + p.play.url; mime = p.play.mime || 'video/*'; }
  else if (p.play.hls_url) {
    // Its own session id: closing this player must not cut the other app off.
    const q = [`c=ext-${Math.random().toString(36).slice(2, 8)}`];
    if (p.audSel != null) q.push(`audio=${enc(p.audSel)}`);
    url = origin + p.play.hls_url + (p.play.hls_url.includes('?') ? '&' : '?') + q.join('&');
    mime = 'application/vnd.apple.mpegurl';
  } else { url = `${origin}/api/items/${enc(p.id)}/stream.mp4?start=${pos}`; mime = 'video/mp4'; }
  const title = (p.item && (p.item.kind === 'episode' && p.item.show ? `${p.item.show} · ${p.item.title}` : p.item.title)) || 'PictureHouse';
  if (!V.paused) V.pause();
  saveProgress(false);
  try { ANDROID.openExternalAt(url, mime, title, p.play.mode === 'direct' || p.play.hls_url ? pos * 1000 : 0); }
  catch (e) { toast("Couldn't open another app: " + e.message); }
}

async function openPlayer(id, from) {
  if (P) closePlayer(true);
  if (!A.paused) A.pause();
  P = { id: String(id), item: null, play: null, offset: 0, total: 0, seekable: true, playing: false, hold: null, pendingSeek: null, tok: 0, sub: 0, aud: 0,
    scrubbing: false, lastSaved: -1, failed: false, how: null, hls: null, sid: null, audSel: null, audStreams: [], cast: null };
  const p = P;
  PL.hidden = false; PL.classList.remove('has-video', 'idle', 'remote'); PL.classList.add('paused');
  document.body.classList.add('player-open');
  $('#pinfo').hidden = true; $('#pinf').setAttribute('aria-expanded', 'false');
  $('#pext').hidden = !ANDROID;
  $('#ptitle').textContent = 'Loading…'; $('#pdev').textContent = '';
  $('#pimg').removeAttribute('src');
  castBox();
  pmsg('<p>Starting…</p>');
  drawPlayer(); $('#pplay').focus();
  try {
    const it = remember(await api(`/api/items/${enc(id)}`));
    if (P !== p) return;
    p.item = it; p.total = it.duration || 0;
    const isEp = it.kind === 'episode';
    $('#ptitle').textContent = isEp ? (it.show || it.title) : it.title;
    setImg($('#pimg'), it.art && it.art.backdrop, isEp ? epSeed(it) : it.title, 'b');
    const streams = it.streams || [];
    p.subs = ['Off', ...streams.filter(s => s.type === 'subtitle').map(streamLabel)];
    const auds = p.audStreams = streams.filter(s => s.type === 'audio');
    p.auds = auds.length ? auds.map(streamLabel) : ['None'];
    p.aud = Math.max(0, auds.findIndex(a => a.default));
    // Without ?audio= the server's HLS encoder takes the first audio stream; ask for the default-flagged one when it isn't first.
    p.audFirst = auds[0] ? auds[0].index : null;
    p.audSel = auds[p.aud] && auds[p.aud].index !== p.audFirst ? auds[p.aud].index : null;
    $('#psubv').textContent = p.subs[0]; $('#paudv').textContent = p.auds[p.aud];
    const start = from != null ? +from : resumable(it) && (!it.duration || it.progress.position < it.duration * 0.98) ? it.progress.position : 0;
    if (castSession()) await startCasting(start);
    else await loadAt(start);
  } catch (e) { if (P === p) playerError(e.message); }
}
function devLine(p, where) {
  const it = p.item;
  return join(it && it.kind === 'episode' ? join(epCode(it), it.title) : '', where);
}
async function loadAt(t) {
  const p = P; if (!p) return;
  if (p.cast) { castLoad(t); return; }
  t = Math.max(0, Math.min(t || 0, p.total ? Math.max(0, p.total - 1) : Infinity));
  const tok = ++p.tok;
  p.hold = t; p.failed = false;
  pmsg('<p>Starting stream…</p>');
  drawPlayer();
  let d;
  try { d = await api(`/api/items/${enc(p.id)}/play?start=${Math.floor(t)}`); }
  catch (e) { if (P === p && tok === p.tok) playerError(e.message); return; }
  if (P !== p || tok !== p.tok) return;
  p.play = d;
  if (d.duration) p.total = d.duration;
  // Anything the browser can't play directly goes over HLS when it can: seeking is native and the audio track can be chosen.
  const how = d.mode !== 'direct' && d.hls_url ? await hlsSupport() : null;
  if (P !== p || tok !== p.tok) return;
  if (how) { await startHls(p, tok, d, t, how); return; }
  detachLocal(p);
  if (p.sid) { closeHls(p.sid); p.sid = null; }
  p.how = d.mode === 'direct' ? 'direct' : 'progressive';
  p.seekable = !!d.seekable;
  if (p.seekable) { p.offset = 0; p.pendingSeek = t > 0 ? t : null; }
  else { p.offset = d.start != null && isFinite(+d.start) ? +d.start : Math.floor(t); p.pendingSeek = null; p.hold = p.offset; }
  $('#pdev').textContent = devLine(p, `${modeOf(d.mode).label} in this browser`);
  V.src = d.url;
  V.play().catch(err => {
    if (P !== p || tok !== p.tok) return;
    if (err && err.name === 'NotAllowedError') { pmsg(null); p.playing = false; drawPlayer(); }
    // Other failures surface through the media element's error event.
  });
  updateInfo(); drawPlayer();
}
async function startHls(p, tok, d, t, how) {
  const url = hlsUrl(d, p.audSel);
  // Fetch the playlist once ourselves: it reports errors clearly and carries the session id we must close later.
  let sid = null;
  try {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) {
      let msg = ''; try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : ''; } catch (e) { /* not JSON */ }
      throw new ApiError(msg || `${r.status} ${r.statusText || 'error'}`, r.status);
    }
    sid = r.headers.get('X-HLS-Session');
    if (!sid) { const m = /\/api\/hls\/([^/\s]+)\//.exec(await r.text()); sid = m && m[1]; }
  } catch (e) {
    if (P === p && tok === p.tok) playerError(e instanceof ApiError ? e.message : 'could not reach the server');
    return;
  }
  if (P !== p || tok !== p.tok) { if (sid && sid !== p.sid) closeHls(sid); return; }
  detachLocal(p);
  if (p.sid && p.sid !== sid) closeHls(p.sid);
  p.sid = sid; p.how = how; p.seekable = true; p.offset = 0; p.netRetry = false; p.mediaRetry = false;
  $('#pdev').textContent = devLine(p, `${modeOf(d.mode).label} in this browser · HLS`);
  const started = () => V.play().catch(err => {
    if (P !== p || tok !== p.tok) return;
    if (err && err.name === 'NotAllowedError') { pmsg(null); p.playing = false; drawPlayer(); }
  });
  if (how === 'native') {
    p.pendingSeek = t > 0 ? t : null;
    V.src = url;
    started();
  } else {
    const Hls = window.Hls;
    p.pendingSeek = null;
    // Segments are encoded on demand; the first one after a seek can take a while, so wait longer than hls.js's default.
    const slow = { maxTimeToFirstByteMs: 50000, maxLoadTimeMs: 120000,
      timeoutRetry: { maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0 },
      errorRetry: { maxNumRetry: 3, retryDelayMs: 1000, maxRetryDelayMs: 8000 } };
    const h = p.hls = new Hls({ startPosition: t > 0 ? t : -1, maxBufferLength: 30, fragLoadPolicy: { default: slow } });
    h.on(Hls.Events.ERROR, (ev, data) => onHlsError(p, h, data));
    h.on(Hls.Events.FRAG_LOADED, () => { p.netRetry = false; });
    h.on(Hls.Events.MANIFEST_PARSED, () => { if (P === p && p.hls === h) started(); });
    h.loadSource(url);
    h.attachMedia(V);
  }
  updateInfo(); drawPlayer();
}
function onHlsError(p, h, data) {
  if (P !== p || p.hls !== h || !data || !data.fatal) return;
  const T = window.Hls.ErrorTypes;
  if (data.type === T.NETWORK_ERROR && !p.netRetry) { p.netRetry = true; console.warn('HLS network error, retrying once:', data.details); h.startLoad(); return; }
  if (data.type === T.MEDIA_ERROR && !p.mediaRetry) { p.mediaRetry = true; console.warn('HLS media error, recovering:', data.details); h.recoverMediaError(); return; }
  const code = data.response && data.response.code ? ` (HTTP ${data.response.code})` : '';
  if (data.response && data.response.code === 503) { playerError(BUSY_MSG); return; }
  playerError(`the stream stopped: ${data.details || data.type}${code}`);
}
function seekTo(t) {
  const p = P; if (!p || !p.play) return;
  t = Math.max(0, Math.min(t, p.total || t));
  if (p.cast) { castSeek(t); return; }
  if (p.seekable) { if (p.failed) { loadAt(t); return; } p.hold = t; V.currentTime = t; drawPlayer(); }
  else loadAt(t);
}
function togglePlay() {
  if (!P) return;
  if (P.failed) { loadAt(curPos()); return; }
  if (!P.play) return;
  if (P.cast) { castToggle(); return; }
  if (V.paused) V.play().catch(() => {}); else V.pause();
}
function drawPlayer() {
  const p = P; if (!p) return;
  const playing = isPlaying();
  PL.classList.toggle('paused', !playing);
  $('#pplay').innerHTML = playing ? PAUSE : PLAY; $('#pplay').setAttribute('aria-label', playing ? 'Pause' : 'Play');
  const pos = curPos();
  $('#pcur').textContent = clock(pos);
  $('#ptot').textContent = clock(p.total);
  const sc = $('#pscrub');
  sc.max = Math.max(1, Math.floor(p.total || 0));
  if (!p.scrubbing) sc.value = Math.floor(pos);
}
const HOW = { direct: 'Native (byte ranges)', native: 'Native (HLS playlist)', hlsjs: 'Native (HLS via hls.js)', progressive: 'Server restarts the stream at the new position' };
function updateInfo() {
  const p = P; if (!p) return;
  const d = p.play || {}, it = p.item || {};
  const m = modeOf(d.mode);
  const hls = p.how === 'native' || p.how === 'hlsjs';
  const url = p.cast ? p.cast.url : hls ? hlsUrl(d, p.audSel) : d.url;
  const audioNote = p.audStreams.length < 2 ? '' : hls ? '' : p.cast ? ' (the Chromecast plays the default track)' : p.how === 'direct' ? ' (direct play uses the file\'s default track)' : ' (needs HLS)';
  $('#pinfobody').innerHTML = `<dt>Delivery</dt><dd>${esc(m.label)}${hls ? ' · HLS' : ''}${p.cast ? ' · Chromecast' : ''}</dd>
    <dt>Reason</dt><dd>${esc(d.reason || '—')}</dd>
    <dt>MIME</dt><dd>${esc(hls ? HLS_MIME : d.mime || '—')}</dd>
    <dt>Seeking</dt><dd>${esc(p.cast ? (p.cast.isStream ? 'The stream restarts at the new position' : 'Native on the Chromecast') : HOW[p.how] || '—')}</dd>
    <dt>URL</dt><dd>${esc(url || '—')}</dd>
    ${it.file ? `<dt>File</dt><dd>${esc(it.file)}</dd>` : ''}
    <dt>Subtitles</dt><dd>${esc((p.subs || ['Off'])[p.sub])} (coming soon)</dd>
    <dt>Audio</dt><dd>${esc((p.auds || ['Default'])[p.aud])}${esc(audioNote)}</dd>`;
}
function saveProgress(final) {
  const p = P; if (!p || !p.item || !p.play || !p.total) return;
  const pos = Math.min(curPos(), p.total);
  if (pos <= 0 && !final) return;
  if (!final && Math.abs(pos - p.lastSaved) < 1) return;
  p.lastSaved = pos;
  const completed = pos > p.total * 0.92;
  api(`/api/items/${enc(p.id)}/progress`, { method: 'PUT', body: { position: Math.round(pos * 10) / 10, completed }, keepalive: true })
    .catch(e => console.warn('Saving progress failed:', e.message));
}
function closePlayer(silent) {
  androidKeepAwake(false);
  if (!P) { PL.hidden = true; return; }
  const p = P;
  saveProgress(true);
  P = null;
  clearTimeout(audT);
  if (p.cast) castStopRemote();
  detachLocal(p);
  closeHls(p.sid);
  pmsg(null); castBox();
  if (fsElement()) exitFs();
  PL.hidden = true; PL.classList.remove('has-video', 'idle', 'remote');
  document.body.classList.remove('player-open');
  clearTimeout(idleT);
  if (silent) return;
  const f = $('#view [data-nav]'); if (f && !document.activeElement.closest('#view')) f.focus({ preventScroll: true });
  // Progress changed: refresh views that show it.
  if (['home', 'item', 'show'].includes(S.route.name)) setTimeout(() => route({ keepScroll: true, keepFocus: true }), 300);
}
/* Audio: HLS streams really switch track (the playlist is re-opened with ?audio=N at the current position). */
let audT;
function cycleAudio() {
  const p = P; if (!p) return;
  p.aud = (p.aud + 1) % p.auds.length;
  $('#paudv').textContent = p.auds[p.aud]; updateInfo();
  if (p.audStreams.length < 2) return;
  const hls = p.how === 'native' || p.how === 'hlsjs';
  if (!hls) {
    toast(p.cast ? 'The Chromecast plays the default audio track' : p.how === 'direct' ? 'This file plays directly, so the browser uses its default audio track' : 'Switching audio needs HLS, which this browser can\'t play');
    return;
  }
  clearTimeout(audT);
  // Wait a moment so pressing Audio several times re-opens the stream once.
  audT = setTimeout(() => {
    if (P !== p) return;
    const st = p.audStreams[p.aud]; if (!st) return;
    const sel = st.index === p.audFirst ? null : st.index;
    if (sel === p.audSel) return;
    p.audSel = sel;
    toast(`Audio: ${p.auds[p.aud]}`);
    loadAt(curPos());
  }, 650);
}
V.addEventListener('loadedmetadata', () => {
  if (!P || P.cast) return;
  if (P.pendingSeek != null) { V.currentTime = P.pendingSeek; P.pendingSeek = null; }
});
V.addEventListener('seeked', () => { if (P && !P.cast && P.pendingSeek == null) { P.hold = null; drawPlayer(); } });
V.addEventListener('loadeddata', () => { if (P && !P.cast && !P.seekable) { P.hold = null; drawPlayer(); } });
V.addEventListener('playing', () => {
  androidKeepAwake(true);
  if (!P || P.cast) return;
  if (P.pendingSeek == null) P.hold = null;
  P.playing = true; pmsg(null);
  if (V.videoWidth) PL.classList.add('has-video');
  drawPlayer(); poke();
});
V.addEventListener('waiting', () => { if (P && !P.cast && !P.failed && P.play) pmsg('<p>Buffering…</p>'); });
V.addEventListener('canplay', () => { if (P && !P.cast && !P.failed && !$('#pmsg').classList.contains('err')) pmsg(null); });
// 'pause' also fires at the end; 'ended' saves then, so don't send a second request at the same moment.
V.addEventListener('pause', () => { androidKeepAwake(false); if (!P || P.cast) return; P.playing = false; drawPlayer(); if (!V.ended) saveProgress(false); poke(); });
V.addEventListener('timeupdate', () => { if (P && !P.cast) drawPlayer(); });
V.addEventListener('ended', () => { if (!P || P.cast) return; P.playing = false; P.hold = P.total || curPos(); saveProgress(true); drawPlayer(); poke(); });
const BUSY_MSG = 'the server is already converting as many videos as it can for other viewers. Try again when one of them finishes.';
V.addEventListener('error', async () => {
  if (!P || P.cast || !V.getAttribute('src')) return;
  if (P.hls && !P.mediaRetry) { P.mediaRetry = true; P.hls.recoverMediaError(); return; }
  const e = V.error, p = P;
  // A refused conversion (503) looks like any other media error to <video>: ask the server whether it is full.
  if (!P.hls && /\/stream\.mp4/.test(V.getAttribute('src'))) {
    try {
      const t = (await api('/api/status')).transcodes || {};
      if (P === p && t.limit > 0 && (t.active || []).length >= t.limit) { playerError(BUSY_MSG); return; }
    } catch (ex) { /* fall through to the media error */ }
    if (P !== p) return;
  }
  playerError(e ? (MEDIA_ERR[e.code] || 'media error ' + e.code) + (e.message ? ` — ${e.message}` : '') : 'unknown media error');
});
// Touch taps are handled below (show/hide controls, double-tap to seek); only mouse clicks toggle play.
V.addEventListener('click', () => { if (lastPointer !== 'touch') togglePlay(); });

/* Full screen: the whole player (controls included) fills the screen. Browsers,
   the Windows/Linux app (its window goes full screen with the page) and the
   Android app (immersive: no status or navigation bar) all honour the standard
   request; an iPhone only lets the <video> itself go full screen. */
const FS_ICONS = { on: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>',
  off: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>' };
const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
function exitFs() {
  const ex = document.exitFullscreen || document.webkitExitFullscreen;
  if (ex) { try { const r = ex.call(document); if (r && r.catch) r.catch(() => {}); } catch (e) { /* already out */ } }
}
async function toggleFullscreen() {
  if (fsElement()) { exitFs(); return; }
  const req = PL.requestFullscreen || PL.webkitRequestFullscreen;
  try {
    if (req) {
      await req.call(PL, { navigationUI: 'hide' });
      // Phones: turn a landscape film sideways too (allowed only once full screen).
      if (V.videoWidth > V.videoHeight && screen.orientation && screen.orientation.lock) screen.orientation.lock('landscape').catch(() => {});
    } else if (V.webkitEnterFullscreen) {
      V.webkitEnterFullscreen();  // iPhone: the video alone, in the system player
    } else {
      toast('This browser can\'t go full screen.');
    }
  } catch (e) { toast('Full screen was blocked by the browser.'); }
}
function fsChanged() {
  const on = fsElement() === PL;
  PL.classList.toggle('fs', on);
  const b = $('#pfs');
  b.innerHTML = on ? FS_ICONS.on : FS_ICONS.off;
  b.setAttribute('aria-label', on ? 'Exit full screen' : 'Full screen');
  b.title = on ? 'Exit full screen (F)' : 'Full screen (F)';
  if (!on && screen.orientation && screen.orientation.unlock) { try { screen.orientation.unlock(); } catch (e) { /* not locked */ } }
}
document.addEventListener('fullscreenchange', fsChanged);
document.addEventListener('webkitfullscreenchange', fsChanged);
// Mouse: double-click the picture. (Touch double-taps seek instead.)
V.addEventListener('dblclick', () => { if (lastPointer !== 'touch') toggleFullscreen(); });
setInterval(() => { if (isPlaying()) saveProgress(false); }, 10000);
window.addEventListener('pagehide', () => { if (P) { saveProgress(true); closeHls(P.sid); } });

const sc = $('#pscrub');
sc.addEventListener('input', () => { if (!P) return; P.scrubbing = true; $('#pcur').textContent = clock(+sc.value); });
sc.addEventListener('change', () => { if (!P) return; P.scrubbing = false; seekTo(+sc.value); });
sc.addEventListener('keydown', e => {
  if (!P || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
  e.preventDefault(); e.stopPropagation();
  scrubKey(e.key === 'ArrowLeft' ? -10 : 10);
});
let scrubT = null;
function scrubKey(delta) {
  // Accumulate key presses so a held arrow key makes one request for non-seekable streams.
  const base = P.scrubbing ? +sc.value : curPos();
  const t = Math.max(0, Math.min(P.total || 0, base + delta));
  P.scrubbing = true; sc.value = Math.floor(t); $('#pcur').textContent = clock(t);
  clearTimeout(scrubT);
  scrubT = setTimeout(() => { if (!P) return; P.scrubbing = false; seekTo(+sc.value); }, P.seekable ? 120 : 450);
}

/* Hide the overlay controls after a few seconds of playback without input. */
let idleT;
function poke() {
  PL.classList.remove('idle'); clearTimeout(idleT);
  if (P && !P.cast && isPlaying()) idleT = setTimeout(() => { if (P && !P.cast && isPlaying() && $('#pinfo').hidden) PL.classList.add('idle'); }, 3500);
}
function toggleControls() {
  if (PL.classList.contains('idle')) poke();
  else { clearTimeout(idleT); if ($('#pinfo').hidden) PL.classList.add('idle'); }
}

/* ---------- Touch: tap shows/hides the controls; double-tap the left third for −10 s, the right third for +30 s ---------- */
let lastPointer = 'mouse';
const onChrome = el => !!(el && el.closest && el.closest('.ptop, .pctrl, .pinfo, .pmsg, .pcast'));
PL.addEventListener('pointerdown', e => {
  lastPointer = e.pointerType || 'mouse';
  if (lastPointer !== 'touch' || onChrome(e.target)) poke();
}, { passive: true });
PL.addEventListener('pointermove', e => { if (e.pointerType === 'mouse' || e.pointerType === 'pen') poke(); }, { passive: true });
const TAP = { t: 0, zone: null, timer: null, streak: null, streakT: 0, sum: 0 };
PL.addEventListener('pointerup', e => {
  if (e.pointerType !== 'touch' || !P || onChrome(e.target)) return;
  const w = PL.clientWidth || window.innerWidth;
  const zone = e.clientX < w / 3 ? 'l' : e.clientX > w * 2 / 3 ? 'r' : 'c';
  const now = performance.now();
  if (zone !== 'c' && TAP.streak === zone && now - TAP.streakT < 700) { tapSeek(zone, now); return; } // keep tapping to keep seeking
  if (zone !== 'c' && TAP.timer && TAP.zone === zone && now - TAP.t < 300) { clearTimeout(TAP.timer); TAP.timer = null; TAP.sum = 0; tapSeek(zone, now); return; }
  clearTimeout(TAP.timer); TAP.timer = null; TAP.streak = null;
  TAP.t = now; TAP.zone = zone;
  if (zone === 'c') { toggleControls(); return; }
  TAP.timer = setTimeout(() => { TAP.timer = null; toggleControls(); }, 300);
});
let seekHintT;
function tapSeek(zone, now) {
  if (!P || !P.play || P.failed) return;
  const delta = zone === 'l' ? -10 : 30;
  TAP.streak = zone; TAP.streakT = now; TAP.sum += delta;
  scrubKey(delta);
  const el = $('#pseek');
  el.textContent = (TAP.sum < 0 ? '−' : '+') + Math.abs(TAP.sum) + ' s';
  el.className = 'pseek ' + zone; el.hidden = false;
  void el.offsetWidth; el.classList.add('show');
  clearTimeout(seekHintT); seekHintT = setTimeout(() => { el.hidden = true; el.classList.remove('show'); }, 800);
}

/* ---------- AirPlay (Safari) ---------- */
const AIRPLAY = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-1"/><path d="M12 15l5 6H7z"/></svg>';
const AIR = { supported: !!window.WebKitPlaybackTargetAvailabilityEvent, available: false };
$('#pair').innerHTML = AIRPLAY;
const airWireless = () => !!V.webkitCurrentPlaybackTargetIsWireless;
if (AIR.supported) {
  V.addEventListener('webkitplaybacktargetavailabilitychanged', e => {
    AIR.available = e.availability === 'available';
    $('#pair').hidden = !AIR.available;
  });
  V.addEventListener('webkitcurrentplaybacktargetiswirelesschanged', () => {
    castBox();
    if (P && P.play && !P.cast) $('#pdev').textContent = devLine(P, airWireless() ? 'Playing on AirPlay' : `${modeOf(P.play.mode).label} in this browser`);
    renderDevBtn();
  });
}

/* ---------- Chromecast (Google Cast web sender, CAF) ----------
   Needs a secure context and a Chromium browser. The Default Media Receiver is an HTTPS page that won't trust our local CA,
   so it gets plain-http progressive MP4 URLs on the LAN (see docs/api.md). */
const C = { ready: false, failed: false, state: 'NO_DEVICES_AVAILABLE', ctx: null, rp: null, rc: null };
const castCapable = () => !!(window.isSecureContext && window.chrome);
function initCastSdk() {
  if (!castCapable()) return;
  window.__onGCastApiAvailable = ok => { if (ok) setupCast(); else { C.failed = true; castUI(); } };
  const s = document.createElement('script');
  s.src = 'https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1';
  s.async = true;
  s.onerror = () => { C.failed = true; castUI(); };
  document.head.appendChild(s);
}
function setupCast() {
  if (!(window.cast && cast.framework && window.chrome && chrome.cast)) { C.failed = true; castUI(); return; } // e.g. offline: the framework didn't load
  try {
    const F = cast.framework;
    C.ctx = F.CastContext.getInstance();
    C.ctx.setOptions({ receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID, autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED });
    C.rp = new F.RemotePlayer();
    C.rc = new F.RemotePlayerController(C.rp);
    C.state = C.ctx.getCastState();
    C.ctx.addEventListener(F.CastContextEventType.CAST_STATE_CHANGED, e => { C.state = e.castState; castUI(); });
    C.ctx.addEventListener(F.CastContextEventType.SESSION_STATE_CHANGED, e => onCastSession(e.sessionState));
    C.rc.addEventListener(F.RemotePlayerEventType.ANY_CHANGE, onRemoteChange);
    C.ready = true;
  } catch (e) { console.warn('Cast setup failed:', e); C.failed = true; }
  castUI();
}
const castSession = () => { try { return C.ctx && C.ctx.getCurrentSession(); } catch (e) { return null; } };
function castDevice() { const s = castSession(); try { return (s && s.getCastDevice().friendlyName) || 'Chromecast'; } catch (e) { return 'Chromecast'; } }
function onCastSession(st) {
  const SS = cast.framework.SessionState;
  if (st === SS.SESSION_STARTED && P && !P.cast && P.item) startCasting(curPos());
  else if (st === SS.SESSION_ENDED && P && P.cast) castEnded();
  else if (st === SS.SESSION_START_FAILED) toast('Couldn\'t connect to the Chromecast');
  castUI();
}
function requestCast() {
  if (!C.ready) { toast('Casting isn\'t available in this browser'); return; }
  if (castSession()) {
    if (P && !P.cast && P.item) { startCasting(curPos()); return; }
    if (!P) { toast(`Connected to ${castDevice()}. Play something to send it there.`); return; }
  }
  C.ctx.requestSession().catch(e => { if (e && e !== 'cancel' && e.code !== 'cancel') toast('Couldn\'t connect to the Chromecast: ' + (e.description || e.code || e)); });
}
/* Pick a LAN address the Chromecast can reach (it can't reach 127.0.0.1 when the console is opened on the server itself). */
function lanHost(st) {
  const h = location.hostname;
  const loop = h === 'localhost' || h.endsWith('.localhost') || /^127\./.test(h) || h === '[::1]' || h === '::1';
  if (!loop) return h;
  const urls = [...((st.https && st.https.urls) || []), ...((st.dlna && st.dlna.addresses) || [])];
  for (const u of urls) { try { const x = new URL(u).hostname; if (x && !/^127\./.test(x) && x !== 'localhost') return x; } catch (e) { /* skip */ } }
  return null;
}
function castOrigin(st) {
  const h = lanHost(st), port = st && st.server && st.server.http_port;
  return h && port ? `http://${h}:${port}` : null;
}
async function startCasting(t) {
  const p = P; if (!p || !p.item) return;
  detachLocal(p);
  if (p.sid) { closeHls(p.sid); p.sid = null; }
  clearTimeout(audT);
  pmsg(null);
  PL.classList.remove('has-video', 'idle');
  p.cast = { device: castDevice(), playing: false, started: false, offset: 0, isStream: false, lastPos: t, url: null };
  p.how = 'cast';
  castBox();
  await castLoad(t);
}
async function castLoad(t) {
  const p = P; if (!p || !p.cast) return;
  const c = p.cast, s = castSession();
  if (!s) { castEnded(); return; }
  t = Math.max(0, Math.min(t || 0, p.total ? Math.max(0, p.total - 1) : Infinity));
  const tok = ++p.tok;
  p.hold = t; p.failed = false; c.started = false; c.playing = false; c.lastPos = t;
  pmsg(null); castBox(); drawPlayer();
  let d, st = S.status;
  try {
    [d, st] = await Promise.all([api(`/api/items/${enc(p.id)}/play?start=${Math.floor(t)}`), st && st.server && st.server.http_port ? st : api('/api/status')]);
  } catch (e) { if (P === p && tok === p.tok) playerError(e.message); return; }
  if (P !== p || tok !== p.tok || p.cast !== c) return;
  S.status = st;
  p.play = d; if (d.duration) p.total = d.duration;
  const base = castOrigin(st);
  if (!base) { playerError('couldn\'t work out a network address the Chromecast can reach'); return; }
  const direct = d.mode === 'direct' && /^video\/(mp4|webm)$/i.test(d.mime || '');
  c.isStream = !direct; c.offset = direct ? 0 : Math.floor(t); p.seekable = direct;
  c.url = base + (direct ? `/api/items/${enc(p.id)}/file` : `/api/items/${enc(p.id)}/stream.mp4?start=${Math.floor(t)}`);
  const it = p.item, isEp = it.kind === 'episode';
  const mi = new chrome.cast.media.MediaInfo(c.url, 'video/mp4');
  mi.streamType = chrome.cast.media.StreamType.BUFFERED;
  if (p.total) mi.duration = Math.max(1, p.total - c.offset);
  const md = new chrome.cast.media.GenericMediaMetadata();
  md.title = isEp ? (it.show || it.title) : it.title;
  md.subtitle = isEp ? join(epCode(it), it.title) : join(it.year, genreText(it.genres));
  const art = it.art && (isEp ? it.art.thumb || it.art.backdrop : it.art.backdrop || it.art.poster);
  if (art) md.images = [new chrome.cast.Image(base + art)];
  mi.metadata = md;
  const req = new chrome.cast.media.LoadRequest(mi);
  req.autoplay = true;
  req.currentTime = direct ? t : 0;
  updateInfo();
  try {
    const err = await s.loadMedia(req);
    if (err) throw err;
  } catch (e) {
    if (P === p && tok === p.tok && p.cast === c) playerError(`the Chromecast couldn't play it (${(e && (e.description || e.code)) || e})`);
    return;
  }
  if (P !== p || tok !== p.tok || p.cast !== c) return;
  c.started = true; c.playing = true;
  castBox(); drawPlayer();
}
function castPos() {
  const c = P.cast;
  if (!c.started || !C.rp || !C.rp.isConnected || !C.rp.isMediaLoaded) return c.lastPos || 0;
  return c.offset + (C.rp.currentTime || 0);
}
function onRemoteChange(e) {
  const p = P;
  if (e && e.field === 'isConnected') castUI();
  if (!p || !p.cast) return;
  const c = p.cast, rp = C.rp;
  if (c.started && rp.isConnected) {
    const ps = rp.playerState;
    if (rp.isMediaLoaded) c.lastPos = c.offset + (rp.currentTime || 0);
    if (ps === 'PLAYING') { c.seen = true; if (p.hold != null && (!c.seekTo || Math.abs(c.lastPos - c.seekTo) < 3)) { p.hold = null; c.seekTo = null; } }
    c.playing = !!ps && ps !== 'IDLE' && !rp.isPaused;
    if (ps === 'IDLE' && c.seen && !c.ended) {
      let reason = null; try { const m = castSession().getMediaSession(); reason = m && m.idleReason; } catch (er) { /* no media session */ }
      if (reason === 'FINISHED') { c.ended = true; p.hold = p.total || c.lastPos; saveProgress(true); }
    }
  }
  if (e && (e.field === 'currentTime' || e.field === 'playerState' || e.field === 'isPaused')) { castBox(); drawPlayer(); }
}
function castToggle() {
  const p = P, c = p.cast;
  if (!c.started || C.rp.playerState === 'IDLE' || !C.rp.isMediaLoaded) { c.ended = false; castLoad(curPos()); return; }
  C.rc.playOrPause();
}
function castSeek(t) {
  const c = P.cast;
  if (c.isStream || !c.started || !C.rp.isMediaLoaded) { c.ended = false; castLoad(t); return; }
  P.hold = t; c.seekTo = t; c.lastPos = t;
  C.rp.currentTime = t; C.rc.seek();
  drawPlayer();
}
function castStopRemote() { try { if (C.rc && C.rp && C.rp.isMediaLoaded) C.rc.stop(); } catch (e) { /* receiver gone */ } }
function castDisconnect() {
  if (!C.ctx) return;
  try { C.ctx.endCurrentSession(true); } catch (e) { castEnded(); }
}
/* The session ended (Disconnect, or from Chrome's own cast menu): carry on in this browser where the TV left off. */
function castEnded() {
  const p = P; if (!p || !p.cast) return;
  const t = p.cast.lastPos != null ? p.cast.lastPos : curPos();
  const ended = p.cast.ended;
  p.cast = null; p.how = null; p.hold = t;
  saveProgress(true);
  castBox(); castUI();
  if (ended) { p.hold = p.total; drawPlayer(); return; }
  toast('Casting ended · playing in this browser');
  loadAt(t);
}
function castBox() {
  const box = $('#pcast'), p = P;
  const air = !!(p && !p.cast && airWireless());
  if (!p || (!p.cast && !air)) { box.hidden = true; PL.classList.remove('remote'); return; }
  box.hidden = false; PL.classList.add('remote'); PL.classList.remove('idle');
  $('#pcastkind').textContent = p.cast ? 'Chromecast' : 'AirPlay';
  $('#pcastdev').textContent = p.cast ? `Playing on ${p.cast.device}` : 'Playing on AirPlay';
  let st = '';
  if (p.cast) {
    const c = p.cast, ps = C.rp && C.rp.playerState;
    st = !c.started ? 'Sending to the TV…' : c.ended ? 'Finished' : ps === 'BUFFERING' ? 'Buffering…' : ps === 'PAUSED' || (C.rp && C.rp.isPaused) ? 'Paused' : ps === 'IDLE' ? 'Stopped' : 'Playing';
    if (c.isStream && c.started) st += ' · seeking restarts the stream';
  } else st = 'Use the AirPlay button to play on this device again.';
  $('#pcastst').textContent = st;
  $('#pcastbtns').hidden = !p.cast;
}
function castUI() {
  const btn = $('#pcastbtn');
  const show = C.ready && C.state !== 'NO_DEVICES_AVAILABLE';
  btn.hidden = !show;
  btn.classList.toggle('on', C.state === 'CONNECTED');
  btn.setAttribute('aria-label', C.state === 'CONNECTED' ? `Casting to ${castDevice()}` : 'Cast to a Chromecast');
  renderDevBtn();
  if ($('#devmenu').hidden) renderDev();
}

/* ---------- Music mini player ---------- */
const A = $('#maudio');
let M = null; // { queue: [Item], i, offset, tok }
async function playTrack(queue, i) {
  const t = queue[i]; if (!t) return;
  if (P) closePlayer(true);
  const m = M = { queue, i, offset: 0, tok: (M ? M.tok : 0) + 1, failed: false };
  $('#mini').hidden = false;
  setImg($('#mimg'), t.art && t.art.poster, albumSeed(t), 's');
  $('#mt').textContent = t.title || 'Untitled';
  $('#ma').textContent = join(t.artist, t.album);
  $('#mprog').style.width = '0%';
  markTracks();
  try {
    const d = await api(`/api/items/${enc(t.id)}/play`);
    if (M !== m) return;
    m.seekable = !!d.seekable; m.duration = d.duration || t.duration || 0;
    A.src = d.url;
    await A.play().catch(err => { if (err && err.name !== 'AbortError' && err.name !== 'NotAllowedError') throw err; });
  } catch (e) { if (M === m) { m.failed = true; toast(`Couldn't play “${t.title}”: ${e.message}`); } }
  drawMini();
}
function drawMini() {
  if (!M) return;
  const playing = !A.paused;
  $('#mplay').innerHTML = playing ? PAUSE : PLAY; $('#mplay').setAttribute('aria-label', playing ? 'Pause' : 'Play');
  const dur = M.duration || (isFinite(A.duration) ? A.duration : 0);
  $('#mprog').style.width = dur ? Math.min(100, (M.offset + A.currentTime) / dur * 100) + '%' : '0%';
}
function markTracks() {
  const id = M && M.queue[M.i] ? String(M.queue[M.i].id) : null;
  $$('.trk[data-track]').forEach(b => b.classList.toggle('on', b.dataset.track === id));
}
A.addEventListener('timeupdate', drawMini);
A.addEventListener('play', drawMini);
A.addEventListener('pause', drawMini);
A.addEventListener('ended', () => {
  if (!M) return;
  let n = M.i + 1;
  while (n < M.queue.length && M.queue[n].available === false) n++;
  if (n < M.queue.length) playTrack(M.queue, n);
  else { drawMini(); $('#mprog').style.width = '100%'; }
});
A.addEventListener('error', () => {
  if (!M || !A.getAttribute('src') || M.failed) return;
  M.failed = true;
  const e = A.error;
  toast(`Couldn't play “${M.queue[M.i].title}”: ${e ? MEDIA_ERR[e.code] || 'media error' : 'media error'}`);
  drawMini();
});
function queueFrom(btn) {
  const box = btn.closest('.tracks');
  const btns = box ? $$('.trk[data-track]', box) : [btn];
  const queue = btns.map(b => ITEMS.get(b.dataset.track)).filter(Boolean);
  const i = Math.max(0, queue.findIndex(t => String(t.id) === btn.dataset.track));
  return { queue, i };
}

/* ---------- Events ---------- */
document.addEventListener('click', e => {
  if (e.target === sheetEl) { closeSheet(); return; }
  const t = e.target.closest('button');
  if (!t) { if (!e.target.closest('.devwrap')) toggleDevMenu(false); return; }
  const ds = t.dataset;
  if (t.id === 'devbtn') { e.stopPropagation(); toggleDevMenu(); return; }
  if (!t.closest('.devwrap')) toggleDevMenu(false);
  if ('devsel' in ds) { pickDevice(ds.devsel); return; }
  if (ds.href && t.closest('#devmenu')) toggleDevMenu(false);
  if (t.id === 'installbtn') { installApp(); return; }
  if (ds.href) { nav(ds.href, { focusFirst: true }); return; }
  if (ds.play) { openPlayer(ds.play, ds.from != null ? +ds.from : null); return; }
  if ('back' in ds) { back(); return; }
  if (ds.feat) { S.feat = +ds.feat; redraw(`[data-feat="${S.feat}"]`); return; }
  if (ds.season != null && S.current) { S.season[S.current.data.key] = ds.season === 'null' ? null : +ds.season; redraw(`[data-season="${CSS.escape(ds.season)}"]`); return; }
  if (ds.sort) { const r = S.route; nav(`#/${r.name}?sort=${ds.sort}`, { replace: true, keepScroll: true, focus: `[data-sort="${ds.sort}"]` }); return; }
  if (ds.track) {
    const { queue, i } = queueFrom(t);
    if (M && M.queue[M.i] && String(M.queue[M.i].id) === ds.track && !M.failed) { if (A.paused) A.play().catch(() => {}); else A.pause(); return; }
    playTrack(queue, i); return;
  }
  if ('playall' in ds) { const first = $('#view .trk[data-track]'); if (first) { const { queue } = queueFrom(first); playTrack(queue, 0); } return; }
  if (ds.act) { act(ds.act, t); return; }
  if (t.id === 'mplay' && M) { if (M.failed) playTrack(M.queue, M.i); else if (A.paused) A.play().catch(() => {}); else A.pause(); return; }
  if (t.id === 'pretry' && P) { loadAt(curPos()); return; }
  const p = P; if (!p) return;
  poke();
  if (t.id === 'pext') { openInApp(); return; }
  if (t.id === 'pclose') closePlayer();
  else if (t.id === 'pplay') togglePlay();
  else if (t.id === 'pback') seekTo(curPos() - 10);
  else if (t.id === 'pfwd') seekTo(curPos() + 30);
  else if (t.id === 'pfs') toggleFullscreen();
  else if (t.id === 'psub') { p.sub = (p.sub + 1) % p.subs.length; $('#psubv').textContent = p.subs[p.sub]; updateInfo(); }
  else if (t.id === 'paud') cycleAudio();
  else if (t.id === 'pair') { try { V.webkitShowPlaybackTargetPicker(); } catch (er) { toast('AirPlay is not available'); } }
  else if (t.id === 'pcastbtn') requestCast();
  else if (t.id === 'pcstop') closePlayer();
  else if (t.id === 'pcdisc') castDisconnect();
  else if (t.id === 'pinf') { const i = $('#pinfo'); i.hidden = !i.hidden; t.setAttribute('aria-expanded', String(!i.hidden)); }
});

let confirmT;
/* Folder browser for the Add folder form: lists the server's folders (as the
   server sees them; in a container, the container's paths) so nobody has to
   guess a path. Re-renders only #browser, keeping what's typed in the form. */
const BR = { path: '' };
const folderName = p => (p.split(/[\\/]/).filter(Boolean).pop() || p).replace(/:$/, ' drive');
function browserHTML(d) {
  if (d.roots) {
    return `<div class="brhead"><b>Choose where to look</b>
        <button type="button" class="btn small" data-nav data-act="browse-close">Close</button></div>
      <div class="dirs">${d.roots.map(r => `<button type="button" class="dir${r.kind === 'drive' ? ' drive' : ''}" data-nav data-act="browse-go" data-path="${esc(r.path)}"><b>${esc(r.name)}</b><span>${esc(r.path)}</span></button>`).join('')}</div>`;
  }
  const dirs = d.dirs.length
    ? d.dirs.map(x => `<button type="button" class="dir" data-nav data-act="browse-go" data-path="${esc(x.path)}"><b>${esc(x.name)}</b></button>`).join('')
    : '<p class="note">No folders inside. If your media files are here, use this folder.</p>';
  return `<div class="brhead"><span class="brpath">${esc(d.path)}</span>
      <span class="formbtns">
        <button type="button" class="btn primary small" data-nav data-act="browse-use">Use this folder</button>
        ${d.parent ? `<button type="button" class="btn small" data-nav data-act="browse-go" data-path="${esc(d.parent)}">Up</button>` : ''}
        <button type="button" class="btn small" data-nav data-act="browse-go" data-path="">Start</button>
        <button type="button" class="btn small" data-nav data-act="browse-close">Close</button></span></div>
    <div class="dirs">${dirs}</div>${d.truncated ? '<p class="note">Only the first 2,000 folders are shown.</p>' : ''}`;
}
async function browseTo(path) {
  const box = $('#browser'); if (!box) return;
  box.hidden = false;
  box.setAttribute('aria-busy', 'true');
  try {
    const d = await api('/api/browse' + (path ? '?path=' + enc(path) : ''));
    BR.path = path;
    box.innerHTML = browserHTML(d);
  } catch (e) {
    box.innerHTML = `<p class="formerr">${esc(e.message)}</p><div class="formbtns">
      <button type="button" class="btn small" data-nav data-act="browse-go" data-path="">Start again</button>
      <button type="button" class="btn small" data-nav data-act="browse-close">Close</button></div>`;
  }
  box.removeAttribute('aria-busy');
  const first = box.querySelector('.dir, [data-act="browse-use"], [data-act="browse-go"]');
  if (first) first.focus();
}
function closeBrowser() {
  const box = $('#browser'); if (box) { box.hidden = true; box.innerHTML = ''; }
  const b = $('[data-act="browse-open"]'); if (b) b.focus();
}
function useBrowsedFolder() {
  const f = $('#addform'); if (!f || !BR.path) return;
  f.elements.path.value = BR.path;
  if (!f.elements.name.value.trim()) f.elements.name.value = folderName(BR.path);
  closeBrowser();
  f.elements.kind.focus();
}

async function act(name, btn) {
  const L = S.lib;
  if (name === 'reload') { route({ keepScroll: true }); return; }
  if (name === 'install') { installApp(); return; }
  if (name === 'fix' || name === 'edit') {
    const t = TARGETS.get(btn.dataset.t + ':' + btn.dataset.id);
    if (!t) { toast('Reload the page and try again'); return; }
    if (name === 'fix') openFix(t); else openEdit(t);
    return;
  }
  if (name === 'sheet-close') { closeSheet(); return; }
  if (name === 'sheet-lib') { closeSheet(); nav('#/library'); setTimeout(() => { const m = $('#metaform'); if (m) { m.scrollIntoView({ block: 'center' }); focusSel('#metaform input[name=key]'); } }, 400); return; }
  if (name === 'apply') { applyMatch(btn); return; }
  if (name === 'meta-match' || name === 'meta-refresh') { startMeta(name === 'meta-refresh' ? 'refresh' : 'match'); return; }
  if (name === 'unlock') {
    const k = btn.dataset.t + ':' + btn.dataset.id;
    if (S.undo !== k) {
      S.undo = k; redraw('[data-act="unlock"]');
      clearTimeout(confirmT); confirmT = setTimeout(() => { if (S.undo === k) { S.undo = null; redraw(focusKey(document.activeElement)); } }, 5000);
      return;
    }
    S.undo = null; btn.disabled = true;
    try {
      const res = await api(`${tBase(btn.dataset.t, btn.dataset.id)}/unlock`, { method: 'POST' });
      toast('Your changes were undone. It will be matched again from scratch.');
      const onShow = btn.dataset.t === 'show' && S.route.name === 'show' && S.route.arg === btn.dataset.id;
      setTimeout(() => {
        // Undoing a title edit changes the series' key; the server returns the new one.
        if (onShow && res && res.key && res.key !== btn.dataset.id) { nav(`#/show/${enc(res.key)}`, { replace: true }); return; }
        route({ keepScroll: true });
      }, 600);
      refreshStatus();
    } catch (e) { btn.disabled = false; toast('Could not undo your changes: ' + e.message); redraw(); }
    return;
  }
  if (name === 'add') { L.adding = true; L.justAdded = null; redraw('#addform input[name=name]'); return; }
  if (name === 'theme-use') { applyTheme(btn.dataset.theme); redraw('#appearance-sec .theme.on'); return; }
  if (name === 'theme-follow') { applyTheme(null); redraw('#appearance-sec .theme.on'); return; }
  if (name === 'theme-default') {
    btn.disabled = true;
    try {
      await api('/api/settings/theme', { method: 'PUT', body: { default: btn.dataset.theme } });
      if (!deviceTheme()) applyTheme(null);  // this device follows the default: show it now
      toast('Default theme changed for all devices');
      await route({ keepScroll: true, focus: '#appearance-sec .theme.on' });
    } catch (e) { btn.disabled = false; toast('Could not change the default theme: ' + e.message); }
    return;
  }
  if (name === 'browse-open') { browseTo(''); return; }
  if (name === 'browse-go') { browseTo(btn.dataset.path || ''); return; }
  if (name === 'browse-close') { closeBrowser(); return; }
  if (name === 'browse-use') { useBrowsedFolder(); return; }
  if (name === 'cancel-add') { L.adding = false; redraw('[data-act="add"]'); return; }
  if (name === 'dismiss-added') { L.justAdded = null; redraw('[data-act="add"]'); return; }
  if (name === 'scan-all') { startScan(null); return; }
  if (name === 'pin-remove' || name === 'admin-logout') {
    btn.disabled = true;
    try {
      if (name === 'pin-remove') { await api('/api/admin/pin', { method: 'PUT', body: { pin: null } }); toast('PIN removed. Only this PC can make changes now.'); }
      else { await api('/api/admin/logout', { method: 'POST' }); toast('Signed out'); }
      route({ keepScroll: true });
    } catch (e) { btn.disabled = false; toast(`Couldn't do that: ${e.message}`); }
    return;
  }
  if (name === 'scan-one') { L.justAdded = null; redraw('#rescan'); startScan([btn.dataset.lib]); return; }
  if (name === 'audio-dismiss' || name === 'audio-recheck') {
    btn.disabled = true;
    try {
      if (name === 'audio-dismiss') {
        await api(`/api/items/${enc(btn.dataset.id)}/audio-check/dismiss`, { method: 'POST' });
        toast(`Dismissed. ${btn.dataset.file} won't be listed again unless the file changes.`);
      } else {
        await api('/api/audio-check', { method: 'POST', body: { item_ids: [+btn.dataset.id] } });
        toast('Checking the sound again…');
        await sleep(2500);
      }
      route({ keepScroll: true });
    } catch (e) { btn.disabled = false; toast(`Couldn't do that: ${e.message}`); }
    return;
  }
  if (name === 'android-server') { if (ANDROID) ANDROID.changeServer(); return; }
  if (name === 'retry') {
    btn.disabled = true;
    try { await api(`/api/items/${enc(btn.dataset.id)}/retry`, { method: 'POST' }); toast(`Retry queued for ${btn.dataset.file}`); SCAN.expect = true; SCAN.startedAt = Date.now(); pollScan(); }
    catch (e) { btn.disabled = false; toast('Retry failed: ' + e.message); }
    return;
  }
  if (name === 'remove') {
    const lib = btn.dataset.lib;
    if (L.confirm !== lib) {
      L.confirm = lib; redraw(`[data-act="remove"][data-lib="${CSS.escape(lib)}"]`);
      clearTimeout(confirmT); confirmT = setTimeout(() => { if (L.confirm === lib && S.route.name === 'library') { L.confirm = null; redraw(focusKey(document.activeElement)); } }, 5000);
      return;
    }
    L.confirm = null; btn.disabled = true;
    try { await api(`/api/libraries/${enc(lib)}`, { method: 'DELETE' }); toast(`Removed “${lib}”. Its items are kept but hidden.`); route({ keepScroll: true }); refreshStatus(); }
    catch (e) { btn.disabled = false; toast('Could not remove the folder: ' + e.message); redraw(); }
  }
}
document.addEventListener('submit', e => {
  const f = e.target;
  if (f.id === 'fixform') {
    e.preventDefault();
    const q = f.elements.q.value.trim(), y = f.elements.year.value.trim();
    if (y && !/^\d{4}$/.test(y)) { S.sheet.msg = { text: 'The year should be four digits, or left empty.' }; drawFixResults(); f.elements.year.focus(); return; }
    runFixSearch(q, y);
  } else if (f.id === 'editform') { e.preventDefault(); submitEdit(f); }
  else if (f.id === 'metaform') { e.preventDefault(); submitMeta(f); }
  else if (f.id === 'streamform') { e.preventDefault(); submitStreaming(f); }
  else if (f.id === 'pinform' || f.id === 'loginform') { e.preventDefault(); submitPin(f, f.id === 'loginform'); }
});
async function submitEdit(f) {
  const sh = S.sheet; if (!sh || sh.mode !== 'edit') return;
  const t = sh.t, err = $('#editerr');
  const show = msg => { err.textContent = msg; err.hidden = false; };
  const body = {};
  const norm = v => v == null ? '' : String(v).trim();
  for (const k of ['title', 'overview', 'genres', 'certification']) {
    const v = norm(f.elements[k].value);
    if (v !== norm(t[k])) body[k] = v || null;
  }
  const y = norm(f.elements.year.value);
  if (y !== norm(t.year)) {
    if (y && !/^\d{4}$/.test(y)) { show('The year should be four digits, or left empty.'); f.elements.year.focus(); return; }
    body.year = y ? +y : null;
  }
  if ('title' in body && !body.title) { show('The title can\'t be empty.'); f.elements.title.focus(); return; }
  if (!Object.keys(body).length) { closeSheet(); toast('Nothing changed'); return; }
  const btn = f.querySelector('[type=submit]'); btn.disabled = true; err.hidden = true;
  try {
    const res = await api(`${tBase(t.type, t.id)}/metadata`, { method: 'PUT', body });
    toast('Saved. Your changes are kept when the library is rescanned.');
    closeSheet();
    afterChange(t.type, t.id, res);
  } catch (ex) { btn.disabled = false; show(ex.message); }
}
async function submitMeta(f) {
  const s = S.msettings || {}, err = $('#metaerr');
  const body = {};
  const key = f.elements.key.value.trim(), okey = f.elements.okey.value.trim();
  if (key) body.tmdb_api_key = key;
  if (okey) body.omdb_api_key = okey;
  const lang = f.elements.language.value.trim(), reg = f.elements.region.value.trim();
  if (lang !== (s.language || '')) body.language = lang;
  if (reg !== (s.region || '')) body.region = reg;
  if (f.elements.match_on_scan.checked !== !!s.match_on_scan) body.match_on_scan = f.elements.match_on_scan.checked;
  if (f.elements.save_local.checked !== !!s.save_local) body.save_local = f.elements.save_local.checked;
  if (!Object.keys(body).length) { toast('Nothing to save'); return; }
  const btn = f.querySelector('[type=submit]'); btn.disabled = true; err.hidden = true;
  try {
    const r = await api('/api/settings/metadata', { method: 'PUT', body });
    f.elements.key.value = ''; f.elements.okey.value = '';
    const anyKey = key || okey;
    toast(anyKey ? 'Key saved. Matching has started.' : body.save_local ? 'Saved. Writing copies next to your files…' : 'Settings saved');
    if (anyKey) { META.expect = true; META.seen = false; }
    await route({ keepScroll: true, focus: '#metaform [type=submit]' });
    refreshStatus();
    if (anyKey) pollMeta();
  } catch (ex) { btn.disabled = false; err.textContent = ex.message; err.hidden = false; }
}
document.addEventListener('submit', async e => {
  const f = e.target; if (f.id !== 'addform') return;
  e.preventDefault();
  const err = $('#adderr');
  const body = { name: f.elements.name.value.trim(), path: f.elements.path.value.trim(), kind: f.elements.kind.value };
  const show = msg => { err.textContent = msg; err.hidden = false; };
  if (!body.name) { show('Give the folder a name.'); f.elements.name.focus(); return; }
  if (!body.path) { show('Enter the folder path.'); f.elements.path.focus(); return; }
  if (/[\/\\\x00-\x1f\x7f]/.test(body.name)) { show('Names can\'t contain / or \\.'); f.elements.name.focus(); return; }
  const btn = f.querySelector('[type=submit]'); btn.disabled = true; err.hidden = true;
  try {
    const lib = await api('/api/libraries', { method: 'POST', body });
    S.lib.adding = false; S.lib.justAdded = (lib && lib.name) || body.name;
    toast(`Added “${S.lib.justAdded}”`);
    await route({ keepScroll: true, focus: '[data-act="scan-one"]' });
    refreshStatus();
  } catch (ex) { btn.disabled = false; show(ex.message); }
});

/* Search box: debounced, results replace the current search entry in history. */
let qT;
$('#q').addEventListener('input', e => {
  clearTimeout(qT);
  const v = e.target.value;
  qT = setTimeout(() => {
    if (v.trim()) nav('#/search?q=' + enc(v.trim()), { replace: S.route.name === 'search' });
    else if (S.route.name === 'search') back();
  }, 250);
});

/* Spatial navigation for TV remotes and keyboards */
function visible(el) { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.closest('[hidden]') && !el.disabled; }
function moveFocus(dir) {
  const scope = !$('#player').hidden ? $('#player') : S.sheet ? sheetEl : !$('#devmenu').hidden ? $('#devmenu') : document;
  const all = [...scope.querySelectorAll('[data-nav]')].filter(visible).filter(el => scope !== document || !el.closest('#player, #devmenu, #sheet'));
  const cur = document.activeElement;
  if (!all.includes(cur)) { if (all[0]) all[0].focus(); return; }
  const a = cur.getBoundingClientRect(), ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of all) {
    if (el === cur) continue;
    const r = el.getBoundingClientRect(), bx = r.left + r.width / 2, by = r.top + r.height / 2;
    const dx = bx - ax, dy = by - ay;
    let main, cross;
    if (dir === 'ArrowRight') { if (r.left < a.right - 4) continue; main = dx; cross = Math.abs(dy); }
    else if (dir === 'ArrowLeft') { if (r.right > a.left + 4) continue; main = -dx; cross = Math.abs(dy); }
    else if (dir === 'ArrowDown') { if (r.top < a.bottom - 4) continue; main = dy; cross = Math.abs(dx); }
    else { if (r.bottom > a.top + 4) continue; main = -dy; cross = Math.abs(dx); }
    const score = main + cross * 2.2;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (best) { best.focus({ preventScroll: true }); best.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' }); }
}
document.addEventListener('keydown', e => {
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'select' || tag === 'textarea';
  const playerOpen = !$('#player').hidden;
  if (playerOpen) poke();
  if (S.sheet && playerOpen === false && e.key === 'Tab') {
    // Keep Tab inside the open dialog.
    const els = [...sheetEl.querySelectorAll('[data-nav]')].filter(visible);
    if (els.length) {
      const i = els.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { els[els.length - 1].focus(); e.preventDefault(); }
      else if (!e.shiftKey && (i === -1 || i === els.length - 1)) { els[0].focus(); e.preventDefault(); }
    }
    return;
  }
  if (e.key === 'Escape' || (e.key === 'Backspace' && !typing)) {
    if (S.sheet && !playerOpen) closeSheet();
    else if (e.key === 'Escape' && typing && e.target.closest && e.target.closest('#metaform')) { /* stay on the page; don't lose a half-typed key */ }
    else if (!$('#devmenu').hidden) { toggleDevMenu(false); $('#devbtn').focus(); }
    else if (playerOpen) closePlayer();
    else if (e.key === 'Escape' && e.target.closest && e.target.closest('#addform')) act('cancel-add');
    else if (e.key === 'Escape' && e.target.id === 'q' && e.target.value) { e.target.value = ''; if (S.route.name === 'search') back(); }
    else back();
    e.preventDefault(); return;
  }
  if (e.key.startsWith('Arrow')) {
    if (tag === 'input' && e.target.type === 'range') return;
    if (tag === 'textarea') return;
    if (tag === 'input' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;
    if (tag === 'select' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) return;
    e.preventDefault(); moveFocus(e.key);
    return;
  }
  if (e.key === ' ' && playerOpen && tag !== 'button' && !typing) { togglePlay(); e.preventDefault(); }
  if ((e.key === 'f' || e.key === 'F') && playerOpen && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { toggleFullscreen(); e.preventDefault(); }
});

/* ---------- Start ---------- */
if (!history.state || typeof history.state.i !== 'number') history.replaceState({ i: 0 }, '');
$('#pcastbtn').innerHTML = ico('cast');
renderDev();
initCastSdk();
// What photos.js (Photos, slideshows, visualiser) builds on. M and P change,
// so they're getters.
window.PHConsole = {
  $, $$, esc, enc, api, join, pl, ico, toast, route, remember, landCard, emptyBox, backBtn,
  sheetHead, sheetEl, closeSheet, playTrack, closePlayer, I, NAV, SECTION, KINDS, VIEWS, S, A,
  PLAY, PAUSE, posterCard, trackRow, albumSeed, curSort, redraw, get M() { return M; }, get P() { return P; }
};
registerSW();
refreshStatus();
setInterval(refreshStatus, 30000);
route();
})();

/* Web console: Photos (albums, viewer, slideshows with music) and the music
   visualiser. Loaded after app.js and uses its helpers (VIEWS, api, esc, ...).
   The slideshow and visualiser engines are shared with the TV app:
   /static/shared/slideshow.js and /static/shared/visualizer.js. */
(() => {
'use strict';
const C = window.PHConsole;
const { $, esc, enc, api, join, pl, ico, toast, route, remember, landCard, emptyBox, backBtn,
  sheetHead, sheetEl, closeSheet, playTrack, closePlayer, I, NAV, SECTION, KINDS, VIEWS, S, A,
  PLAY, PAUSE, posterCard, trackRow, albumSeed } = C;

/* ---------- Music by folder: Artists / Albums / Songs ---------- */
// Artists are a music library's top-level folders, albums the folders in
// them (see queries.music_*). The Music tab opens on Artists.
const MUSIC_VIEWS = [['artists', 'Artists'], ['albums', 'Albums'], ['songs', 'Songs']];
const SONGS_PAGE = 300;
const mview = r => { const v = r.q.get('view') || (() => { try { return localStorage.getItem('musicView'); } catch (e) { return null; } })();
  return MUSIC_VIEWS.some(x => x[0] === v) ? v : 'artists'; };
const artistCard = a => posterCard({ href: `#/artist/${enc(a.key)}`, title: a.name, square: true,
  sub: a.albums ? pl(a.albums, 'album') : pl(a.tracks, 'track'), url: a.art && a.art.poster, seed: a.name });
const folderAlbumCard = al => posterCard({ href: `#/falbum/${enc(al.key)}`, title: al.title, square: true,
  sub: join(al.artist, al.year), url: al.art && al.art.poster, seed: albumSeed(al) });
VIEWS.music = {
  title: 'Music',
  async load(r) {
    const view = mview(r);
    try { localStorage.setItem('musicView', view); } catch (e) { /* private mode */ }
    if (view === 'songs') {
      const d = await api(`/api/music/songs?limit=${SONGS_PAGE}`);
      d.items.forEach(remember);
      return { view, songs: d.items, total: d.total };
    }
    const d = await api(view === 'albums' ? '/api/music/albums?sort=az' : '/api/music/artists');
    return { view, list: d.albums || d.artists, total: d.total };
  },
  draw(d) {
    const chips = `<div class="chips">${MUSIC_VIEWS.map(([v, l]) => `<button class="chip ${d.view === v ? 'on' : ''}" data-nav data-href="#/music?view=${v}">${l}</button>`).join('')}</div>`;
    const head = d.view === 'songs' ? pl(d.total, 'song') : d.view === 'albums' ? pl(d.total, 'album') : pl(d.total, 'artist');
    let body;
    if (d.view === 'songs') {
      body = d.songs.length ? `<div class="tracks">${d.songs.map((t, i) => trackRow(t, i + 1, null, true)).join('')}</div>
        ${d.songs.length < d.total ? `<p><button class="btn small" data-nav data-mus2="more">Show more (${esc(pl(d.total - d.songs.length, 'song'))} to go)</button></p>` : ''}`
        : emptyBox('No music yet — add a music folder on the Library page.');
    } else {
      body = d.list.length ? `<div class="grid">${d.list.map(d.view === 'albums' ? folderAlbumCard : artistCard).join('')}</div>`
        : emptyBox('No music yet — add a music folder on the Library page.');
    }
    return `<div class="page-head"><div><h1>Music</h1><p>${esc(head)}</p></div>${chips}</div>${body}`;
  }
};
VIEWS.artist = {
  title: 'Artist',
  async load(r) { const a = await api(`/api/music/artists/${enc(r.arg)}`); a.tracks.forEach(remember); return a; },
  draw(a) {
    return `${backBtn()}<div class="page-head"><div><h1>${esc(a.name)}</h1><p>${esc(pl(a.albums.length, 'album'))}</p></div></div>
      ${a.albums.length ? `<div class="grid">${a.albums.map(folderAlbumCard).join('')}</div>` : ''}
      ${a.tracks.length ? `<section class="sec"><div class="sec-head"><h2>Other tracks</h2></div><div class="tracks">${a.tracks.map((t, i) => trackRow(t, i + 1, null, false)).join('')}</div></section>` : ''}`;
  }
};
VIEWS.falbum = {
  title: 'Album',
  async load(r) { const al = await api(`/api/music/albums/${enc(r.arg)}`); (al.track_list || []).forEach(remember); return al; },
  draw(al) { return VIEWS.album.draw(al); }
};
SECTION.artist = 'music'; SECTION.falbum = 'music';
document.addEventListener('click', async e => {
  const b = e.target.closest && e.target.closest('[data-mus2="more"]');
  if (!b || !S.current || !S.current.data.songs) return;
  e.stopPropagation(); b.disabled = true;
  const d = S.current.data;
  const more = await api(`/api/music/songs?offset=${d.songs.length}&limit=${SONGS_PAGE}`).catch(() => ({ items: [] }));
  more.items.forEach(remember); d.songs = d.songs.concat(more.items);
  C.redraw(`[data-track="${more.items.length ? more.items[0].id : ''}"]`);
}, true);

/* ---------- Navigation: a Photos section ---------- */
I.photos = '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>';
I.viz = '<path d="M4 14v-4M8 18V6M12 15V9M16 20V4M20 13v-2"/>';
I.prev = '<path d="M18 6L9 12l9 6zM6 6v12"/>';
I.next = '<path d="M6 6l9 6-9 6zM18 6v12"/>';
I.close = '<path d="M6 6l12 12M18 6L6 18"/>';
I.note = '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>';
NAV.photos = 'Photos';
SECTION.photos = 'photos';
SECTION.photoalbum = 'photos';
(function addNav() {
  const after = $('#nav-home-videos');
  const b = document.createElement('button');
  b.className = 'navbtn'; b.id = 'nav-photos';
  b.setAttribute('data-nav', ''); b.dataset.href = '#/photos'; b.dataset.sect = 'photos';
  b.innerHTML = ico('photos') + '<span>Photos</span>'; b.setAttribute('aria-label', 'Photos');
  after.insertAdjacentElement('afterend', b);
})();
KINDS.splice(KINDS.length - 1, 0, ['photos', 'Photos']);

const fmtDay = iso => { try { return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); } catch (e) { return ''; } };
function span(a) {
  const f = fmtDay(a.from), t = fmtDay(a.to);
  return f === t ? f : `${f} – ${t}`;
}

VIEWS.photos = {
  title: 'Photos',
  async load() { return { albums: (await api('/api/photos')).albums || [] }; },
  draw(d) {
    const n = d.albums.reduce((s, a) => s + a.photos, 0);
    return `<div class="page-head"><div><h1>Photos</h1><p>${esc(join(pl(d.albums.length, 'album'), pl(n, 'photo')))}</p></div>
      ${n ? `<div class="chips"><button class="btn primary small" data-nav data-ph="slideshow-all">${PLAY} Slideshow of everything</button>
      <button class="btn small" data-nav data-ph="slideshow-all-music">${ico('note')} With music</button></div>` : ''}</div>
      ${d.albums.length ? `<div class="grid wide">${d.albums.map(a => landCard({ href: `#/photoalbum/${enc(a.key)}`, title: a.title,
        sub: join(pl(a.photos, 'photo'), span(a)), url: a.art && a.art.poster, seed: a.title })).join('')}</div>`
        : emptyBox('No photos yet. On the Library page, add a folder of pictures and choose “Photos”.')}`;
  }
};

VIEWS.photoalbum = {
  title: 'Album',
  async load(r) { const d = await api(`/api/photos/albums/${enc(r.arg)}`); PH.list = d.photos; return d; },
  draw(d) {
    const path = d.path && d.path !== d.title ? `<p class="muted">${esc(d.path)}</p>` : '';
    return `${backBtn()}<div class="page-head"><div><h1>${esc(d.title)}</h1><p>${esc(pl(d.photos.length, 'photo'))}</p>${path}</div>
      <div class="chips"><button class="btn primary small" data-nav data-ph="slideshow">${PLAY} Slideshow</button>
      <button class="btn small" data-nav data-ph="slideshow-music">${ico('note')} Slideshow with music</button></div></div>
      <div class="phgrid">${d.photos.map((p, i) => `<button class="phthumb" data-nav data-ph="open" data-i="${i}" aria-label="${esc(p.title)}">
        <img loading="lazy" src="${esc(p.thumb)}" alt=""></button>`).join('')}</div>`;
  }
};

/* Every photo (compact rows from /api/photos/all) as slideshow records. */
async function allPhotos() {
  const r = await api('/api/photos/all');
  const u = (id, v, size) => `/api/items/${id}/photo?size=${size}&v=${v}`;
  return r.rows.map(([id, v, title, taken]) => ({ id, title, taken_at: taken, thumb: u(id, v, 'thumb'), screen: u(id, v, 'screen') }));
}

/* ---------- Viewer / slideshow ---------- */
const PH = { list: [], show: null, el: null, hideT: null };
function viewerEl() {
  if (PH.el) return PH.el;
  const el = document.getElementById('phv') || document.createElement('div');
  if (el.id === 'phv') { PH.el = el; return el; }
  el.id = 'phv'; el.className = 'phv'; el.hidden = true; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Photo viewer');
  el.innerHTML = `<div class="phv-stage"></div>
    <div class="phv-ui"><div class="phv-top"><div><b id="pvt"></b><span id="pvd"></span></div>
      <button class="phv-btn" data-ph="close" aria-label="Close">${ico('close')}</button></div>
    <div class="phv-bar"><button class="phv-btn" data-ph="prev" aria-label="Previous">${ico('prev')}</button>
      <button class="phv-btn big" data-ph="toggle" id="pvplay" aria-label="Play slideshow">${PLAY}</button>
      <button class="phv-btn" data-ph="next" aria-label="Next">${ico('next')}</button>
      <span class="phv-count" id="pvc"></span>
      <select id="pvspeed" aria-label="Seconds per photo"><option value="4000">4 s</option><option value="6000" selected>6 s</option><option value="10000">10 s</option><option value="20000">20 s</option></select>
      <label class="phv-check"><input type="checkbox" id="pvshuffle"> Shuffle</label>
      <button class="phv-btn" data-ph="music" aria-label="Music for the slideshow">${ico('note')}</button></div></div>`;
  document.body.appendChild(el);
  el.addEventListener('mousemove', pvPoke);
  el.querySelector('#pvspeed').addEventListener('change', e => { if (PH.show) { PH.show.interval = +e.target.value; PH.show.schedule(); } });
  el.querySelector('#pvshuffle').addEventListener('change', e => { if (PH.show) PH.show.setShuffle(e.target.checked); });
  PH.el = el;
  return el;
}
function pvPoke() {
  const el = PH.el; if (!el) return;
  el.classList.remove('idle'); clearTimeout(PH.hideT);
  PH.hideT = setTimeout(() => { if (PH.show && PH.show.playing) el.classList.add('idle'); }, 3000);
}
function openViewer(list, i, play) {
  const el = viewerEl();
  if (C.P) closePlayer(true);  // a video and a slideshow don't mix
  el.hidden = false; document.body.classList.add('phv-open');
  const shuffle = el.querySelector('#pvshuffle').checked;
  PH.show = new PHSlideshow(el.querySelector('.phv-stage'), list, {
    interval: +el.querySelector('#pvspeed').value, start: shuffle ? 0 : i, shuffle,
    onChange: (n, p) => {
      $('#pvt').textContent = p.title; $('#pvd').textContent = p.taken_at ? fmtDay(p.taken_at) : '';
      $('#pvc').textContent = `${n + 1} / ${list.length}`;
    }
  });
  if (play) PH.show.start(); else PH.show.go(i);
  pvDraw(); pvPoke();
  el.querySelector('[data-ph="toggle"]').focus({ preventScroll: true });
}
function pvDraw() {
  const b = $('#pvplay'); if (!b || !PH.show) return;
  b.innerHTML = PH.show.playing ? PAUSE : PLAY;
  b.setAttribute('aria-label', PH.show.playing ? 'Pause slideshow' : 'Play slideshow');
}
function closeViewer() {
  if (!PH.show) return;
  PH.show.destroy(); PH.show = null;
  PH.el.hidden = true; document.body.classList.remove('phv-open');
  const f = $('.phthumb[data-i]'); if (f) f.focus({ preventScroll: true });
}

/* ---------- Picking music for a slideshow ---------- */
async function trackList(key) { return ((await api(`/api/albums/${enc(key)}`)).track_list || []).map(remember); }
async function shuffleAll() {
  const albums = (await api('/api/albums?sort=az')).albums || [];
  const pick = PHSlideshow.shuffle(albums).slice(0, 12);
  const lists = await Promise.all(pick.map(a => trackList(a.key).catch(() => [])));
  return PHSlideshow.shuffle([].concat(...lists));
}
async function musicSheet(then) {
  const albums = (await api('/api/albums?sort=az').catch(() => ({ albums: [] }))).albums || [];
  const M = C.M, cur = M && M.queue[M.i];
  const opts = (cur ? `<button class="cand best" data-nav data-mus="keep">Keep playing “${esc(cur.title)}”</button>` : '')
    + (albums.length ? '<button class="cand" data-nav data-mus="shuffle">Shuffle all music</button>' : '')
    + '<button class="cand" data-nav data-mus="none">No music</button>';
  sheetEl.querySelector('#sheetBox').innerHTML = `${sheetHead('Slideshow', 'Music to play')}
    <div class="cands" style="margin:12px 0 18px">${opts}</div>
    ${albums.length ? `<p class="note">Or an album:</p><div class="phalbums">${albums.map(a => `<button class="cand" data-nav data-mus="album" data-key="${esc(a.key)}">${esc(join(a.title, a.artist))}</button>`).join('')}</div>`
      : '<p class="note">No music in the library yet: add a music folder on the Library page.</p>'}`;
  S.sheet = { mode: 'music' }; sheetEl.hidden = false;
  const first = sheetEl.querySelector('[data-mus]'); if (first) first.focus();
  musicSheet.then = then;
}
async function chooseMusic(btn) {
  const how = btn.dataset.mus;
  closeSheet();
  try {
    if (how === 'shuffle') { const q = await shuffleAll(); if (q.length) playTrack(q, 0); }
    else if (how === 'album') { const q = await trackList(btn.dataset.key); if (q.length) playTrack(q, 0); }
  } catch (e) { toast('Couldn’t start the music: ' + e.message); }
  if (musicSheet.then) { const t = musicSheet.then; musicSheet.then = null; t(); }
}

/* ---------- Visualiser ---------- */
const VZ = { el: null, viz: null };
function vizEl() {
  if (VZ.el) return VZ.el;
  const el = document.getElementById('vz') || document.createElement('div');
  if (el.id === 'vz') { VZ.el = el; return el; }
  el.id = 'vz'; el.className = 'vz'; el.hidden = true; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Music visualiser');
  el.innerHTML = `<canvas></canvas><div class="vz-ui"><div class="vz-t"><b id="vzt"></b><span id="vza"></span></div>
    <div class="vz-bar"><button class="phv-btn" data-vz="prev" aria-label="Previous track">${ico('prev')}</button>
    <button class="phv-btn big" data-vz="toggle" id="vzplay" aria-label="Pause">${PAUSE}</button>
    <button class="phv-btn" data-vz="next" aria-label="Next track">${ico('next')}</button>
    <button class="phv-btn" data-vz="mode" aria-label="Change style">${ico('viz')}</button>
    <button class="phv-btn" data-vz="close" aria-label="Close">${ico('close')}</button></div></div>`;
  document.body.appendChild(el);
  el.addEventListener('mousemove', () => { el.classList.remove('idle'); clearTimeout(VZ.t); VZ.t = setTimeout(() => el.classList.add('idle'), 3000); });
  VZ.el = el; return el;
}
function vizInfo() {
  const M = C.M;
  if (!VZ.el || VZ.el.hidden || !M) return;
  const t = M.queue[M.i] || {};
  $('#vzt').textContent = t.title || ''; $('#vza').textContent = join(t.artist, t.album);
  $('#vzplay').innerHTML = A.paused ? PLAY : PAUSE;
  if (VZ.viz && VZ.art !== (t.art && t.art.poster)) { VZ.art = t.art && t.art.poster; VZ.viz.setArt(VZ.art); }
}
function openViz() {
  if (!C.M) { toast('Play some music first'); return; }
  const el = vizEl(); el.hidden = false; document.body.classList.add('phv-open');
  if (!VZ.viz) VZ.viz = PHViz.attach(el.querySelector('canvas'), A, {});
  VZ.art = null; vizInfo(); VZ.viz.start();
  el.querySelector('[data-vz="toggle"]').focus({ preventScroll: true });
}
function closeViz() { if (!VZ.el || VZ.el.hidden) return; VZ.viz.stop(); VZ.el.hidden = true; document.body.classList.remove('phv-open'); }
function skipTrack(d) {
  const M = C.M;
  if (!M) return;
  const n = M.i + d;
  if (n >= 0 && n < M.queue.length) playTrack(M.queue, n);
}
['play', 'pause', 'loadedmetadata'].forEach(ev => A.addEventListener(ev, vizInfo));
// A visualiser button on the mini player.
(function () {
  const b = document.createElement('button');
  b.className = 'pbtn'; b.id = 'mviz'; b.setAttribute('data-nav', ''); b.setAttribute('aria-label', 'Visualiser');
  b.innerHTML = ico('viz'); b.dataset.vz = 'open';
  $('#mplay').insertAdjacentElement('beforebegin', b);
})();

/* ---------- Events ---------- */
document.addEventListener('click', e => {
  const t = e.target.closest && e.target.closest('[data-ph],[data-mus],[data-vz]');
  if (!t) return;
  e.stopPropagation();
  const ph = t.dataset.ph, vz = t.dataset.vz;
  if (t.dataset.mus) { chooseMusic(t); return; }
  if (ph === 'open') openViewer(PH.list, +t.dataset.i, false);
  else if (ph === 'slideshow') openViewer(PH.list, 0, true);
  else if (ph === 'slideshow-music') musicSheet(() => openViewer(PH.list, 0, true));
  else if (ph === 'slideshow-all' || ph === 'slideshow-all-music') {
    allPhotos().then(list => {
      PH.list = list;
      if (ph === 'slideshow-all') openViewer(list, 0, true); else musicSheet(() => openViewer(list, 0, true));
    }).catch(err => toast('Couldn’t load the photos: ' + err.message));
  }
  else if (ph === 'music') { const was = PH.show && PH.show.playing; if (was) PH.show.pause(); musicSheet(() => { if (was && PH.show) PH.show.start(); pvDraw(); }); }
  else if (ph === 'close') closeViewer();
  else if (ph === 'prev') PH.show.prev();
  else if (ph === 'next') PH.show.next();
  else if (ph === 'toggle') { PH.show.toggle(); pvDraw(); pvPoke(); }
  else if (vz === 'open') openViz();
  else if (vz === 'close') closeViz();
  else if (vz === 'toggle') { if (A.paused) A.play(); else A.pause(); }
  else if (vz === 'prev') skipTrack(-1);
  else if (vz === 'next') skipTrack(1);
  else if (vz === 'mode') toast('Style: ' + VZ.viz.nextMode());
}, true);

// While the viewer or visualiser is open it owns the keyboard (and the remote).
window.addEventListener('keydown', e => {
  if (S.sheet) return;  // the music picker is on top
  const tag = (e.target.tagName || '').toLowerCase();
  if (PH.show && !PH.el.hidden) {
    pvPoke();
    if (tag === 'select' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) return;
    const k = e.key;
    if (k === 'Escape' || k === 'Backspace') closeViewer();
    else if (k === 'ArrowLeft') PH.show.prev();
    else if (k === 'ArrowRight') PH.show.next();
    else if ((k === ' ' || k === 'Enter') && tag !== 'button' && tag !== 'select' && tag !== 'input') { PH.show.toggle(); pvDraw(); }
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  } else if (VZ.el && !VZ.el.hidden) {
    const k = e.key;
    if (k === 'Escape' || k === 'Backspace') closeViz();
    else if (k === 'ArrowLeft') skipTrack(-1);
    else if (k === 'ArrowRight') skipTrack(1);
    else if (k === 'v' || k === 'V') toast('Style: ' + VZ.viz.nextMode());
    else if (k === ' ' && tag !== 'button') { if (A.paused) A.play(); else A.pause(); }
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }
}, true);

// app.js routed before this file loaded: open a photos link it couldn't know.
if (/^#\/(photo|music|artist|falbum)/.test(location.hash)) route();
})();

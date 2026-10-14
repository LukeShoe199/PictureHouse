/* PictureHouse TV: Music & audiobooks, Photos, slideshows (with music) and
   the music visualiser. Loaded after tv.js and uses its helpers (api, esc,
   focus, go, PAGES, ACTIVATORS, KEY_HANDLERS, lazyGrid...). The slideshow
   and visualiser engines are shared with the web console (static/shared/).
   webOS 5 = Chrome 68: ES2017 only (no ?. or ??). */
'use strict';

var AU = $('#audio');
var MP = null;  // music: { queue, i, albumKey, seekable, offset, lastSave, failed, levels }
var LONG_TRACK = 20 * 60;  // seconds: audiobook chapters / long mixes resume and seek by 30 s
var REPEAT = (function () { try { return localStorage.getItem('repeat') || 'off'; } catch (e) { return 'off'; } })();
function store(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
function stored(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }

var SVG = {
  prev: '<path d="M18 6L9 12l9 6zM6 6v12"/>', next: '<path d="M6 6l9 6-9 6zM18 6v12"/>',
  play: '<path d="M8 5v14l11-7z"/>', pause: '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>',
  back30: '<path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/>',
  fwd30: '<path d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/>',
  repeat: '<path d="M7 7h10v3l4-4-4-4v3H5v6h2zM17 17H7v-3l-4 4 4 4v-3h12v-6h-2z"/>',
  shuffle: '<path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/>',
  inorder: '<path d="M3 6h12v2H3zM3 11h12v2H3zM3 16h12v2H3zM17 8l4 4-4 4z"/>',
  style: '<path d="M3 10h2v4H3zM7 6h2v12H7zM11 9h2v6h-2zM15 4h2v16h-2zM19 11h2v2h-2z"/>'
};
function icon(n, label) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + SVG[n] + '</svg>' + (label ? '<span>' + label + '</span>' : ''); }

function isLong(t) { return t && t.duration > LONG_TRACK; }
function trackSub(t) { return [t.artist, t.album].filter(Boolean).join(' · '); }
// Folder albums ("f:" + key, from /api/music) and tag albums (/api/albums).
function albumOf(key) {
  return key.indexOf('f:') === 0 ? api('/api/music/albums/' + enc(key.slice(2))) : api('/api/albums/' + enc(key));
}

/* ---------- Music: Artists / Albums / Songs ---------- */
function albumCard(al) {
  return '<button class="f card sq" data-k="a' + esc(al.key) + '" data-album="f:' + esc(al.key) + '">' +
    '<div class="art">' + img(al.art && al.art.poster, al.title, 240) + '</div>' +
    '<div class="cap"><b>' + esc(al.title) + '</b><span>' + esc([al.artist, al.year].filter(Boolean).join(' · ')) + '</span></div></button>';
}
function artistCard(a) {
  return '<button class="f card sq" data-k="r' + esc(a.key) + '" data-artist="' + esc(a.key) + '">' +
    '<div class="art">' + img(a.art && a.art.poster, a.name, 240) + '</div>' +
    '<div class="cap"><b>' + esc(a.name) + '</b><span>' + (a.albums ? a.albums + (a.albums === 1 ? ' album' : ' albums') : a.tracks + ' tracks') +
    '</span></div></button>';
}
var SONGS = [];
function songRow(t, i) {
  SONGS[i] = t;
  var on = MP && MP.queue[MP.i] && MP.queue[MP.i].id === t.id;
  return '<button class="f trk' + (on ? ' on' : '') + '" data-k="t' + t.id + '" data-act="song" data-i="' + i + '">' +
    '<span class="tt">' + esc(t.title) + '</span><span class="sub">' + esc(trackSub(t)) + '</span>' +
    '<span class="d">' + esc(clock(t.duration)) + '</span></button>';
}
var VIEWS_MUSIC = [['artists', 'Artists'], ['albums', 'Albums'], ['songs', 'Songs']];
PAGES.music = function (view) {
  view = view || stored('musicView', 'artists');
  var p = cur(); p.arg = view;
  var chips = '<div class="btns chips">' + VIEWS_MUSIC.map(function (v) {
    return '<button class="f chip' + (v[0] === view ? ' cur' : '') + '" data-k="view-' + v[0] + '" data-view="' + v[0] + '">' + v[1] + '</button>';
  }).join('') + '</div>';
  var none = '<p class="empty">No music yet. Add a music folder on the PC running PictureHouse.</p>';
  var body = view === 'songs' ? (SONGS = [], lazyGrid('list tracks', apiPage('/api/music/songs', 'items'), songRow, none))
    : view === 'albums' ? lazyGrid('grid', apiPage('/api/music/albums?sort=az', 'albums'), albumCard, none)
    : lazyGrid('grid', apiPage('/api/music/artists', 'artists'), artistCard, none);
  return body.then(function (html) { return '<h1>Music &amp; audiobooks</h1>' + chips + html; });
};
PAGES.artist = function (key) {
  return api('/api/music/artists/' + enc(key)).then(function (a) {
    ALBUMS['artist:' + key] = { track_list: a.tracks };
    return '<h1>' + esc(a.name) + '</h1><div class="facts"><span>' + a.albums.length + (a.albums.length === 1 ? ' album' : ' albums') + '</span></div>' +
      (a.albums.length ? '<div class="grid">' + a.albums.map(albumCard).join('') + '</div>' : '') +
      (a.tracks.length ? '<h2>Other tracks</h2><div class="tracks">' + a.tracks.map(function (t, i) {
        return '<button class="f trk" data-k="t' + t.id + '" data-act="artist-play" data-i="' + i + '"><span class="n">' + (i + 1) +
          '</span><span class="tt">' + esc(t.title) + '</span><span class="d">' + esc(clock(t.duration)) + '</span></button>';
      }).join('') + '</div>' : '');
  });
};
var ALBUMS = {};
PAGES.album = function (key) {
  return albumOf(key).then(function (al) {
    ALBUMS[key] = al;
    var tracks = al.track_list || [];
    var total = tracks.reduce(function (s, t) { return s + (t.duration || 0); }, 0);
    var book = tracks.some(isLong);
    var resumeAt = -1;
    tracks.forEach(function (t, i) { if (resumeAt < 0 && t.progress && !t.progress.completed && t.progress.position > 30) resumeAt = i; });
    var playingHere = MP && MP.albumKey === key;
    return '<div class="detail-bg" style="background-image:url(\'' + esc(sized(al.art && al.art.poster, 640)) + '\')"></div>' +
      '<div class="detail"><div class="poster sq"><div class="art">' + img(al.art && al.art.poster, al.title, 480) + '</div></div><div class="info">' +
      '<div class="eyebrow">' + (book ? 'Audiobook' : 'Album') + '</div><h1>' + esc(al.title) + '</h1>' +
      '<div class="facts"><span>' + esc(al.artist || '') + '</span><span>' + esc(al.year || '') + '</span><span>' +
      tracks.length + (tracks.length === 1 ? ' track' : ' tracks') + '</span><span>' + esc(mins(total)) + '</span></div>' +
      '<div class="btns">' +
      (resumeAt >= 0 ? '<button class="f btn primary" data-k="resume" data-act="album-play" data-i="' + resumeAt + '">Resume ' +
        esc(tracks[resumeAt].title) + ' at ' + clock(tracks[resumeAt].progress.position) + '</button>' : '') +
      '<button class="f btn' + (resumeAt >= 0 ? '' : ' primary') + '" data-k="play" data-act="album-play" data-i="0" data-fresh="1">' +
        (resumeAt >= 0 ? 'Play from the start' : 'Play') + '</button>' +
      (book ? '' : '<button class="f btn" data-k="shuffle" data-act="album-shuffle">Shuffle</button>') +
      (playingHere ? '<button class="f btn" data-k="np" data-act="nowplaying">Now playing</button>' : '') +
      '</div><div class="tracks">' + tracks.map(function (t, i) {
        var pct = t.progress && !t.progress.completed && t.duration ? Math.min(100, t.progress.position / t.duration * 100) : 0;
        var on = MP && MP.queue[MP.i] && MP.queue[MP.i].id === t.id;
        return '<button class="f trk' + (on ? ' on' : '') + '" data-k="t' + t.id + '" data-act="album-play" data-i="' + i + '">' +
          '<span class="n">' + (t.track_no || i + 1) + '</span><span class="tt">' + esc(t.title) +
          (t.progress && t.progress.completed ? ' <span class="muted">✓</span>' : '') + '</span>' +
          '<span class="d">' + esc(clock(t.duration)) + '</span>' +
          (pct ? '<span class="tprog"><i style="width:' + pct.toFixed(1) + '%"></i></span>' : '') + '</button>';
      }).join('') + '</div></div></div>';
  });
};

/* ---------- Music: playback ---------- */
function playQueue(queue, i, albumKey, fresh) {
  queue = queue.filter(Boolean);
  if (!queue.length) return;
  MP = { queue: queue, i: Math.min(i, queue.length - 1), albumKey: albumKey || null, seekable: true, offset: 0, lastSave: 0, fresh: !!fresh };
  startTrack();
}
var LEVELS = {};  // track id -> { fps, bands, data: Uint8Array } (server-analysed, for the visualisers)
function loadLevels(t) {
  if (!t || LEVELS[t.id] || isLong(t)) return;
  LEVELS[t.id] = 'loading';
  api('/api/items/' + t.id + '/levels').then(function (r) {
    var bin = atob(r.data), a = new Uint8Array(bin.length), nb = r.bands, frames = Math.floor(bin.length / nb);
    for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    var lo = new Float32Array(nb), hi = new Float32Array(nb);
    for (var b = 0; b < nb; b++) {  // each band's quiet (8th percentile) and loud (97th) points
      var col = [];
      for (var f = 0; f < frames; f += 4) col.push(a[f * nb + b]);
      col.sort(function (x, y) { return x - y; });
      lo[b] = col[Math.floor(col.length * 0.08)] || 0;
      hi[b] = Math.max(lo[b] + 12, col[Math.floor(col.length * 0.97)] || 255);
    }
    LEVELS[t.id] = { fps: r.fps, bands: nb, data: a, lo: lo, hi: hi, out: new Uint8Array(nb) };
  }).catch(function () { LEVELS[t.id] = null; });
}
function levelsNow() {
  var t = MP && MP.queue[MP.i], L = t && LEVELS[t.id];
  if (!L || !L.data) return null;
  var pos = apos() * L.fps, f = Math.floor(pos), frac = pos - f, nb = L.bands, off = f * nb;
  if (off < 0 || off + 2 * nb > L.data.length) return null;
  for (var b = 0; b < nb; b++) {
    var v = L.data[off + b] * (1 - frac) + L.data[off + nb + b] * frac;
    var n = Math.max(0, Math.min(1, (v - L.lo[b]) / (L.hi[b] - L.lo[b])));
    L.out[b] = Math.round(Math.pow(n, 1.3) * 255);  // a little contrast: quiet stays low, hits stand out
  }
  return L.out;
}
function startTrack() {
  var t = MP.queue[MP.i], m = MP;
  if (!t) return;
  m.failed = false; m.offset = 0;
  var at = !m.fresh && t.progress && !t.progress.completed && t.progress.position > 30 && isLong(t) ? t.progress.position : 0;
  m.fresh = false;
  // Keep the cache small: only this track and the next.
  var keep = {}; keep[t.id] = 1; var nx = m.queue[m.i + 1]; if (nx) keep[nx.id] = 1;
  Object.keys(LEVELS).forEach(function (id) { if (!keep[id]) delete LEVELS[id]; });
  loadLevels(t);
  api('/api/items/' + t.id + '/play?client=tv').then(function (d) {
    if (MP !== m) return;
    m.seekable = d.seekable; m.url = d.url;
    if (d.seekable) { AU.src = d.url; m.seekTo = at; }
    else { m.offset = at; AU.src = d.url + (d.url.indexOf('?') < 0 ? '?' : '&') + 'start=' + Math.floor(at); }
    return AU.play();
  }).then(function () { if (MP === m) loadLevels(m.queue[m.i + 1]); })  // the next one, while this plays
    .catch(function (e) { if (MP === m) toast('Couldn’t play “' + t.title + '”: ' + e.message); });
  musicUi();
}
function apos() { return MP ? MP.offset + (AU.currentTime || 0) : 0; }
function adur() { var t = MP && MP.queue[MP.i]; return (t && t.duration) || AU.duration || 0; }
function saveTrack(final) {
  var t = MP && MP.queue[MP.i]; if (!t || apos() < 5) return Promise.resolve();
  var at = apos(), done = adur() && at > adur() * 0.95;
  MP.lastSave = Date.now();
  return api('/api/items/' + t.id + '/progress', { method: 'PUT', body: { position: at, completed: !!done }, keepalive: !!final })
    .then(function () { t.progress = { position: at, completed: !!done }; }).catch(function () { /* next save */ });
}
function seekMusic(s) {
  if (!MP) return;
  var to = Math.max(0, Math.min(apos() + s, adur() - 2));
  if (MP.seekable) AU.currentTime = to;
  else { MP.offset = to; AU.src = MP.url + (MP.url.indexOf('?') < 0 ? '?' : '&') + 'start=' + Math.floor(to); AU.play(); }
}
function skip(d) {
  if (!MP) return;
  if (d < 0 && apos() > 5) { seekMusic(-apos()); return; }  // back: restart the track first
  var n = MP.i + d;
  if (n >= MP.queue.length && REPEAT === 'all') n = 0;
  if (n < 0 && REPEAT === 'all') n = MP.queue.length - 1;
  if (n < 0 || n >= MP.queue.length) return;
  saveTrack(false); MP.i = n; MP.fresh = true; startTrack();
}
function musicToggle() { if (!MP) return; if (AU.paused) AU.play(); else AU.pause(); }
AU.addEventListener('pause', function () { if (!$('#np').hidden) npPoke(); });  // paused: keep the controls up
function cycleRepeat() {
  REPEAT = REPEAT === 'off' ? 'all' : REPEAT === 'all' ? 'one' : 'off';
  store('repeat', REPEAT);
  toast(REPEAT === 'off' ? 'Repeat off' : REPEAT === 'all' ? 'Repeat all' : 'Repeat this track');
  musicUi();
}
window.musicPause = function () { if (MP && !AU.paused) AU.pause(); };
function stopMusic() {
  if (!MP) return;
  saveTrack(true); MP = null; AU.pause(); AU.removeAttribute('src'); AU.load();
  closeNp(); musicUi();
}
AU.addEventListener('loadedmetadata', function () { if (MP && MP.seekTo) { AU.currentTime = MP.seekTo; MP.seekTo = 0; } });
AU.addEventListener('timeupdate', function () {
  if (!MP) return;
  if (!$('#np').hidden) drawNp();
  if (isLong(MP.queue[MP.i]) && Date.now() - MP.lastSave > 15000) saveTrack(false);
});
AU.addEventListener('play', musicUi);
AU.addEventListener('pause', function () { musicUi(); if (MP) saveTrack(false); });
AU.addEventListener('ended', function () {
  if (!MP) return;
  saveTrack(true);
  if (REPEAT === 'one') { MP.fresh = true; startTrack(); return; }
  if (MP.i + 1 < MP.queue.length) { MP.i++; MP.fresh = true; startTrack(); }
  else if (REPEAT === 'all') { MP.i = 0; MP.fresh = true; startTrack(); }
  else stopMusic();
});
AU.addEventListener('error', function () {
  if (!MP || !AU.getAttribute('src') || MP.failed) return;
  MP.failed = true;
  toast('Couldn’t play “' + MP.queue[MP.i].title + '”');
  if (MP.i + 1 < MP.queue.length) setTimeout(function () { skip(1); }, 1500);
});
function musicUi() {
  var pill = $('#nowpill'), t = MP && MP.queue[MP.i];
  pill.hidden = !t;
  if (t) { $('#nowpillt').textContent = t.title; pill.classList.toggle('paused', AU.paused); }
  $$('.trk.on').forEach(function (b) { b.classList.remove('on'); });
  if (t) $$('.trk[data-k="t' + t.id + '"]').forEach(function (b) { b.classList.add('on'); });
  if (!$('#np').hidden) npInfo();
  if (SS) viewerCtl();
  mini();
}

/* ---------- A small visualiser in the corner while music plays ---------- */
var miniT = null;
function mini() {
  var el = $('#miniviz'), show = !!(MP && !AU.paused && $('#np').hidden && $('#player').hidden);
  el.hidden = !show;
  if (show && !miniT) {
    var bars = $$('i', el), t = 0;
    miniT = setInterval(function () {  // 12 fps of five bars: next to nothing for the TV
      var lv = levelsNow(); t += 0.35;
      bars.forEach(function (b, n) {
        var v = lv ? lv[Math.min(lv.length - 1, n * 3)] / 255 : 0.35 + 0.3 * Math.sin(t + n * 1.3);
        b.style.transform = 'scaleY(' + Math.max(0.12, v).toFixed(2) + ')';
      });
    }, 83);
  } else if (!show && miniT) { clearInterval(miniT); miniT = null; }
}

/* ---------- Now playing: visualiser + clear controls ---------- */
var VIZ = null, npHideT = null, npAt = 2;
function npButtons() {
  var t = MP && MP.queue[MP.i], long = isLong(t);
  var b = [['prev', 'prev', 'Previous']];
  if (long) b.push(['back30', 'back30', '30 s']);
  b.push(['toggle', AU.paused ? 'play' : 'pause', AU.paused ? 'Play' : 'Pause']);
  if (long) b.push(['fwd30', 'fwd30', '30 s']);
  b.push(['next', 'next', 'Next'], ['repeat', 'repeat', REPEAT === 'off' ? 'Repeat' : REPEAT === 'all' ? 'Repeat all' : 'Repeat one'],
    ['style', 'style', 'Style']);
  return b;
}
function npInfo() {
  var t = MP && MP.queue[MP.i]; if (!t) return;
  $('#npt').textContent = t.title; $('#npa').textContent = trackSub(t);
  var bs = npButtons(), keep = $('#npctl .on') && $('#npctl .on').dataset.np;
  $('#npctl').innerHTML = bs.map(function (b) {
    return '<button class="cbtn' + (b[0] === 'toggle' ? ' big' : '') + (b[0] === 'repeat' && REPEAT !== 'off' ? ' lit' : '') +
      '" data-np="' + b[0] + '">' + icon(b[1], b[2]) + '</button>';
  }).join('');
  var btns = $$('#npctl .cbtn'), at = keep ? btns.map(function (x) { return x.dataset.np; }).indexOf(keep) : -1;
  npAt = at >= 0 ? at : btns.map(function (x) { return x.dataset.np; }).indexOf('toggle');
  btns[npAt].classList.add('on');
  if (VIZ) VIZ.setArt(sized(t.art && t.art.poster, 480));
  drawNp();
}
function npPress(what) {
  if (what === 'prev') skip(-1); else if (what === 'next') skip(1);
  else if (what === 'toggle') musicToggle();
  else if (what === 'back30') seekMusic(-30); else if (what === 'fwd30') seekMusic(30);
  else if (what === 'repeat') cycleRepeat();
  else if (what === 'style' && VIZ) toast('Style: ' + VIZ.nextMode());
}
function drawNp() {
  var d = adur();
  $('#npcur').textContent = clock(apos()); $('#nptot').textContent = clock(d);
  $('#npbar').style.width = d ? (apos() / d * 100).toFixed(2) + '%' : '0';
}
function npPoke() {  // returns true if the controls were hidden (that press only shows them)
  var np = $('#np'), was = np.classList.contains('calm');
  np.classList.remove('calm'); clearTimeout(npHideT);
  npHideT = setTimeout(function () { if (!AU.paused) np.classList.add('calm'); }, 5000);
  return was;
}
function openNp() {
  if (!MP) return;
  var np = $('#np'); np.hidden = false; document.body.classList.add('overlay'); $('#top').classList.add('hide');
  if (focused) focused.classList.remove('on');
  // On the TV the levels come from the server (webOS plays audio in hardware,
  // where Web Audio hears nothing); drawn at half resolution, 30 fps.
  if (!VIZ) VIZ = PHViz.attach($('#npviz'), AU, { levels: levelsNow, scale: 0.5, fps: 30 });
  npInfo(); VIZ.start(); npPoke(); mini();
}
function closeNp() {
  var np = $('#np'); if (np.hidden) return;
  np.hidden = true; if (VIZ) VIZ.stop();
  document.body.classList.remove('overlay'); $('#top').classList.remove('hide');
  if (focused) focus(focused, { noScroll: true });
  mini();
}

/* ---------- Photos ---------- */
function photoAlbumCard(a) {
  var when = (a.to || '').slice(0, 4);
  return '<button class="f card land" data-k="p' + esc(a.key) + '" data-photoalbum="' + esc(a.key) + '">' +
    '<div class="art">' + img(a.art && a.art.poster, a.title, 480) + '</div>' +
    '<div class="cap"><b>' + esc(a.title) + '</b><span>' + a.photos + (a.photos === 1 ? ' photo' : ' photos') + (when ? ' · ' + when : '') + '</span></div></button>';
}
var PHOTOS = {};
var ssButtons = '<div class="btns"><button class="f btn primary" data-k="ss" data-act="slideshow">Slideshow</button>' +
  '<button class="f btn" data-k="ssm" data-act="slideshow-music">Slideshow with music</button></div>';
PAGES.photos = function () {
  return api('/api/photos').then(function (r) {
    var n = r.albums.reduce(function (s, a) { return s + a.photos; }, 0);
    if (!n) return '<h1>Photos</h1><p class="empty">No photos yet. On the PC, add a folder of pictures as a “Photos” library.</p>';
    return lazyGrid('grid wide', listPage(r.albums), photoAlbumCard).then(function (g) {
      return '<h1>Photos</h1><div class="facts"><span>' + r.albums.length + ' albums</span><span>' + n + ' photos</span></div>' +
        ssButtons.replace('>Slideshow<', '>Slideshow of everything<') + g;
    });
  });
};
function asSlides(list) {  // album photos -> slideshow records with the TV-sized URL
  return list.map(function (p) { return { id: p.id, title: p.title, taken_at: p.taken_at, thumb: p.thumb, tv: p.tv || p.screen, screen: p.screen }; });
}
function allPhotos() {
  return api('/api/photos/all').then(function (r) {
    return r.rows.map(function (x) {
      var u = function (size) { return '/api/items/' + x[0] + '/photo?size=' + size + '&v=' + x[1]; };
      return { id: x[0], title: x[2], taken_at: x[3], thumb: u('thumb'), tv: u('tv'), screen: u('screen') };
    });
  });
}
PAGES.photoalbum = function (key) {
  return api('/api/photos/albums/' + enc(key)).then(function (al) {
    PHOTOS[key] = asSlides(al.photos);
    return lazyGrid('pgrid', listPage(al.photos), function (p, i) {
      return '<button class="f pthumb" data-k="ph' + p.id + '" data-photo="' + i + '">' + img(p.thumb, p.title) + '</button>';
    }).then(function (g) {
      return '<h1>' + esc(al.title) + '</h1><div class="facts"><span>' + al.photos.length + ' photos</span>' +
        (al.path !== al.title ? '<span>' + esc(al.path) + '</span>' : '') + '</div>' + ssButtons + g;
    });
  });
};
PAGES.musicpick = function (target) {
  var t = MP && MP.queue[MP.i];
  return lazyGrid('grid', apiPage('/api/music/albums?sort=az', 'albums'), function (al) {
    return albumCard(al).replace('data-album="f:', 'data-act="pick" data-how="album" data-key="f:');
  }, '').then(function (g) {
    return '<h1>Music for the slideshow</h1><div class="btns">' +
      (t ? '<button class="f btn primary" data-k="keep" data-act="pick" data-how="keep">Keep playing “' + esc(t.title) + '”</button>' : '') +
      (g ? '<button class="f btn' + (t ? '' : ' primary') + '" data-k="shuf" data-act="pick" data-how="shuffle">Shuffle all music</button>' : '') +
      '<button class="f btn" data-k="none" data-act="pick" data-how="none">No music</button></div>' +
      (g ? '<h2>Or an album</h2>' + g : '');
  });
};

/* Viewer / slideshow, with a control bar: photo and (when playing) music controls. */
var SS = null, ctlT = null, ctlAt = 0, ssShuffle = stored('ssShuffle', '0') === '1';
function viewerCtl() {
  var b = [['pprev', 'prev', 'Photo'], ['ptoggle', SS && SS.playing ? 'pause' : 'play', SS && SS.playing ? 'Pause' : 'Slideshow'],
    ['pnext', 'next', 'Photo'], ['pshuffle', ssShuffle ? 'shuffle' : 'inorder', ssShuffle ? 'Shuffle' : 'In order']];
  if (MP) {
    b.push(['mprev', 'prev', 'Track'], ['mtoggle', AU.paused ? 'play' : 'pause', AU.paused ? 'Music' : 'Music'],
      ['mnext', 'next', 'Track'], ['mrepeat', 'repeat', REPEAT === 'off' ? 'Repeat' : REPEAT === 'all' ? 'Repeat all' : 'Repeat one']);
  }
  var keep = $('#pctl .on') && $('#pctl .on').dataset.pv;
  $('#pctl').innerHTML = b.map(function (x, n) {
    return (n === 4 ? '<span class="sep"></span>' : '') + '<button class="cbtn' +
      ((x[0] === 'pshuffle' && ssShuffle) || (x[0] === 'mrepeat' && REPEAT !== 'off') ? ' lit' : '') + '" data-pv="' + x[0] + '">' + icon(x[1], x[2]) + '</button>';
  }).join('');
  var btns = $$('#pctl .cbtn'), ids = btns.map(function (x) { return x.dataset.pv; });
  ctlAt = keep && ids.indexOf(keep) >= 0 ? ids.indexOf(keep) : Math.min(ctlAt, btns.length - 1);
  btns[ctlAt].classList.add('on');
  var t = MP && MP.queue[MP.i];
  $('#pvmusic').textContent = t ? '♪ ' + t.title + (t.artist ? ' · ' + t.artist : '') : '';
}
function ctlShown() { return !$('#pinfo').classList.contains('hide'); }
function showCtl(on) {
  var el = $('#pinfo'); clearTimeout(ctlT);
  el.classList.toggle('hide', !on);
  if (on) { viewerCtl(); ctlT = setTimeout(function () { el.classList.add('hide'); }, 6000); }
}
function pvPress(what) {
  if (!SS) return;
  if (what === 'pprev') SS.prev(); else if (what === 'pnext') SS.next();
  else if (what === 'ptoggle') SS.toggle();
  else if (what === 'pshuffle') { ssShuffle = !ssShuffle; store('ssShuffle', ssShuffle ? '1' : '0'); SS.setShuffle(ssShuffle); }
  else if (what === 'mprev') skip(-1); else if (what === 'mnext') skip(1);
  else if (what === 'mtoggle') musicToggle(); else if (what === 'mrepeat') cycleRepeat();
  showCtl(true);
}
function openViewer(list, i, play) {
  if (!list.length) return;
  var pv = $('#pview'); pv.hidden = false; document.body.classList.add('overlay'); $('#top').classList.add('hide');
  if (focused) focused.classList.remove('on');
  ctlAt = 1;
  // TV settings: smaller pictures, no full-screen blur, no zoom, shorter fades:
  // smooth on a 2020 TV's CPU and GPU, and the music never stutters.
  SS = new PHSlideshow($('#pstage'), list, {
    interval: 7000, start: play && ssShuffle ? 0 : i, shuffle: play && ssShuffle,
    size: 'tv', fill: 'dim', kenBurns: false, fade: 800,
    onChange: function (n, p, total) {
      $('#pvtitle').textContent = p.title;
      var d = p.taken_at ? new Date(p.taken_at) : null;
      $('#pvsub').textContent = (d ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) + ' · ' : '') + (n + 1) + ' of ' + total;
    }
  });
  if (play) SS.start(); else SS.go(i);
  showCtl(true); mini();
}
function closeViewer() {
  if (!SS) return;
  SS.destroy(); SS = null; clearTimeout(ctlT);
  $('#pview').hidden = true; document.body.classList.remove('overlay'); $('#top').classList.remove('hide');
  if (focused) focus(focused);
  mini();
}
function shuffleAll() {
  return api('/api/music/songs?limit=1').then(function (r) {
    var n = r.total, picks = [];
    for (var k = 0; k < Math.min(8, Math.ceil(n / 25)); k++) picks.push(Math.floor(Math.random() * Math.max(1, n - 25)));
    return Promise.all(picks.map(function (o) { return api('/api/music/songs?offset=' + o + '&limit=25'); }));
  }).then(function (pages) {
    var all = []; pages.forEach(function (p) { all = all.concat(p.items); });
    var seen = {};
    return PHSlideshow.shuffle(all.filter(function (t) { if (seen[t.id] || isLong(t)) return false; seen[t.id] = 1; return true; }));
  });
}

/* ---------- Buttons ---------- */
ACTIVATORS.push(function (el) {
  var d = el.dataset, p = cur();
  if (d.view) { store('musicView', d.view); p.focusKey = 'view-' + d.view; p.loaded = 0; p.arg = d.view; render(); return true; }
  if (d.artist) { go('artist', d.artist); return true; }
  if (d.album) { go('album', d.album); return true; }
  if (d.photoalbum) { go('photoalbum', d.photoalbum); return true; }
  if (d.photo != null) { openViewer(PHOTOS[p.arg] || [], +d.photo, false); return true; }
  if (d.kind === 'track' && d.item) {  // e.g. an audiobook in Continue watching: resume it in its album
    var id = +d.item;
    var start = function (q, key) {
      var i = 0; q.forEach(function (t, n) { if (t.id === id) i = n; });
      playQueue(q, i, key); openNp();
    };
    if (d.albumkey) albumOf(d.albumkey).then(function (al) { start(al.track_list || [], d.albumkey); }).catch(function () { start([ITEMS[id]]); });
    else api('/api/items/' + id).then(function (t) { start([t]); });
    return true;
  }
  if (d.act === 'nowplaying') { openNp(); return true; }
  if (d.act === 'song') { playQueue(SONGS.slice(+d.i, +d.i + 200), 0, null, true); openNp(); return true; }
  if (d.act === 'artist-play') { playQueue((ALBUMS['artist:' + p.arg] || {}).track_list || [], +d.i, null, true); openNp(); return true; }
  if (d.act === 'album-play' || d.act === 'album-shuffle') {
    var al = ALBUMS[p.arg]; if (!al) return true;
    var q = al.track_list || [];
    if (d.act === 'album-shuffle') playQueue(PHSlideshow.shuffle(q), 0, p.arg, true);
    else playQueue(q, +d.i, p.arg, !!d.fresh);
    openNp();
    return true;
  }
  var slides = function () { return p.name === 'photos' ? allPhotos() : Promise.resolve(PHOTOS[p.arg] || []); };
  if (d.act === 'slideshow') { slides().then(function (l) { openViewer(l, 0, true); }).catch(function (e) { toast(e.message); }); return true; }
  if (d.act === 'slideshow-music') { go('musicpick', p.name === 'photos' ? '*' : p.arg); return true; }
  if (d.act === 'pick') {
    var target = p.arg; stack.pop();  // back to the photos page underneath
    var list = target === '*' ? allPhotos() : Promise.resolve(PHOTOS[target] || []);
    var run = function () { render(); list.then(function (l) { openViewer(l, 0, true); }); };
    if (d.how === 'shuffle') shuffleAll().then(function (qq) { if (qq.length) playQueue(qq, 0, null, true); run(); }).catch(run);
    else if (d.how === 'album') albumOf(d.key).then(function (a) { playQueue(a.track_list || [], 0, d.key, true); run(); }).catch(run);
    else { if (d.how === 'none') window.musicPause(); else if (MP && AU.paused) AU.play(); run(); }
    return true;
  }
  return false;
});
// Pointer (Magic Remote) on the control bars.
document.addEventListener('click', function (e) {
  var b = e.target.closest && e.target.closest('[data-np],[data-pv]');
  if (!b) return;
  e.stopPropagation();
  if (b.dataset.np) npPress(b.dataset.np); else pvPress(b.dataset.pv);
}, true);

/* ---------- Remote ---------- */
function moveIn(sel, at, d) {
  var btns = $$(sel); if (!btns.length) return at;
  btns[at] && btns[at].classList.remove('on');
  at = Math.max(0, Math.min(btns.length - 1, at + d));
  btns[at].classList.add('on'); return at;
}
KEY_HANDLERS.push(function (k) {
  if (SS) {  // photo viewer
    var shown = ctlShown();
    if (k === KEY.BACK || k === KEY.ESC || k === KEY.BACKSPACE) { if (shown) showCtl(false); else closeViewer(); }
    else if (k === KEY.STOP) closeViewer();
    else if (shown && (k === KEY.LEFT || k === KEY.RIGHT)) { ctlAt = moveIn('#pctl .cbtn', ctlAt, k === KEY.RIGHT ? 1 : -1); showCtl(true); }
    else if (shown && k === KEY.ENTER) { var b = $$('#pctl .cbtn')[ctlAt]; if (b) pvPress(b.dataset.pv); }
    else if (k === KEY.LEFT) SS.prev();
    else if (k === KEY.RIGHT) SS.next();
    else if (k === KEY.PLAYPAUSE || k === KEY.PLAY || k === KEY.PAUSE) { if (MP) musicToggle(); else SS.toggle(); showCtl(true); }
    else if (k === KEY.FF) skip(1); else if (k === KEY.RW) skip(-1);
    else showCtl(true);  // OK, up, down: show the controls
    return true;
  }
  if (!$('#np').hidden) {  // now playing
    var woke = npPoke();
    if (k === KEY.BACK || k === KEY.ESC || k === KEY.BACKSPACE) closeNp();
    else if (woke && (k === KEY.LEFT || k === KEY.RIGHT || k === KEY.ENTER || k === KEY.UP || k === KEY.DOWN)) { /* just show the controls */ }
    else if (k === KEY.LEFT || k === KEY.RIGHT) npAt = moveIn('#npctl .cbtn', npAt, k === KEY.RIGHT ? 1 : -1);
    else if (k === KEY.ENTER) { var nb = $$('#npctl .cbtn')[npAt]; if (nb) npPress(nb.dataset.np); }
    else if (k === KEY.PLAYPAUSE) musicToggle();
    else if (k === KEY.PLAY) AU.play();
    else if (k === KEY.PAUSE) AU.pause();
    else if (k === KEY.STOP) stopMusic();
    else if (k === KEY.RW || k === KEY.PGDN) skip(-1);
    else if (k === KEY.FF || k === KEY.PGUP) skip(1);
    else if (k === KEY.UP || k === KEY.DOWN) toast('Style: ' + (VIZ ? VIZ.nextMode() : ''));
    return true;
  }
  // Browsing with music playing: the media keys control it.
  if (MP) {
    if (k === KEY.PLAYPAUSE || k === KEY.PLAY || k === KEY.PAUSE) { if (k === KEY.PAUSE) AU.pause(); else if (k === KEY.PLAY) AU.play(); else musicToggle(); return true; }
    if (k === KEY.FF) { skip(1); return true; }
    if (k === KEY.RW) { skip(-1); return true; }
    if (k === KEY.STOP) { stopMusic(); return true; }
  }
  return false;
});

/* ---------- Home: music and photos rows ---------- */
var homeBase = PAGES.home;
PAGES.home = function () {
  return Promise.all([homeBase(), api('/api/music/albums?sort=added&limit=20').catch(function () { return { albums: [] }; }),
    api('/api/photos').catch(function () { return { albums: [] }; })]).then(function (r) {
    var html = r[0];
    var music = r[1].albums, photos = r[2].albums.slice(0, 20);
    if (music.length) html += '<h2>Music &amp; audiobooks</h2><div class="row">' + music.map(albumCard).join('') + '</div>';
    if (photos.length) html += '<h2>Photos</h2><div class="row">' + photos.map(photoAlbumCard).join('') + '</div>';
    return html;
  });
};
window.addEventListener('pagehide', function () { if (MP) saveTrack(true); });
// tv.js drew Home before this file added the music and photo rows: draw it again.
if (cur() && cur().name === 'home') render();

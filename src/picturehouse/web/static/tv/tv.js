/* PictureHouse for the TV (opened by the LG webOS app, also fine in any
   browser at /tv/). Remote control: arrows move, OK selects, Back goes back.
   webOS 5 runs Chrome 68: ES2017 only (no ?. or ??, no replaceAll,
   no Promise.allSettled, no flatMap). */
'use strict';

/* ---------- Small helpers ---------- */
var $ = function (s, el) { return (el || document).querySelector(s); };
var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function enc(s) { return encodeURIComponent(s); }
function clock(t) {
  t = Math.max(0, Math.floor(t || 0));
  var h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function mins(t) { if (!t) return ''; var m = Math.round(t / 60); return m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m + ' min'; }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function toast(msg) {
  var t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3500);
}

var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 461, ESC: 27, BACKSPACE: 8,
  PLAY: 415, PAUSE: 19, PLAYPAUSE: 10252, STOP: 413, FF: 417, RW: 412, PGUP: 33, PGDN: 34 };

function api(path, opts) {
  opts = opts || {};
  var init = { method: opts.method || 'GET', headers: { Accept: 'application/json' }, keepalive: !!opts.keepalive };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  return fetch(path, init).then(function (r) {
    if (r.status === 204) return null;
    return r.text().then(function (text) {
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { /* not JSON */ }
      if (!r.ok) {
        var err = new Error(data && typeof data.detail === 'string' ? data.detail : r.status + ' ' + r.statusText);
        err.status = r.status; throw err;
      }
      return data;
    });
  });
}

/* ---------- Artwork ---------- */
function hue(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h % 360; }
function genArt(title) {
  var a = hue(title), b = (a + 40) % 360;
  return '<div class="gen" style="background:linear-gradient(160deg,hsl(' + a + ',45%,28%),hsl(' + b + ',55%,12%))">' + esc(title) + '</div>';
}
// Images: asked for at the size they're shown (?w=, resized and cached by the
// server) and only loaded when about to come on screen. A 2020 TV has little
// memory: every full-size decoded poster costs megabytes.
function sized(url, w) { return url && w ? url + (url.indexOf('?') < 0 ? '?' : '&') + 'w=' + w : url; }
function img(url, title, w) {
  if (!url) return genArt(title);
  return '<img data-src="' + esc(sized(url, w)) + '" alt="" data-title="' + esc(title) + '">';
}
var IO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(function (entries) {
  entries.forEach(function (en) {
    if (!en.isIntersecting) return;
    var el = en.target; IO.unobserve(el);
    if (el.dataset.src) { el.src = el.dataset.src; el.removeAttribute('data-src'); }
  });
}, { rootMargin: '400px 600px' }) : null;
function lazyImages(root) {
  $$('img[data-src]', root).forEach(function (el) {
    if (IO) IO.observe(el); else { el.src = el.dataset.src; el.removeAttribute('data-src'); }
  });
}
document.addEventListener('error', function (e) {
  var el = e.target;
  if (el && el.tagName === 'IMG' && el.dataset.title != null) {
    var d = document.createElement('div'); d.innerHTML = genArt(el.dataset.title);
    el.parentNode.replaceChild(d.firstChild, el);
  }
}, true);

/* ---------- Focus and spatial navigation ---------- */
var focused = null;
function focusables() {
  if (!$('#player').hidden || document.body.classList.contains('overlay')) return [];
  return $$('.f').filter(function (el) {
    if (el.disabled || el.offsetParent === null) return false;
    return !(el.closest('.top') && $('#top').classList.contains('hide'));
  });
}
function focus(el, opts) {
  if (!el) return;
  if (focused) focused.classList.remove('on');
  focused = el; el.classList.add('on');
  // Text boxes only take the keyboard on OK (focusing one pops up the TV keyboard).
  if (document.activeElement && document.activeElement.tagName === 'INPUT' && document.activeElement !== el) document.activeElement.blur();
  if (!(opts && opts.noScroll)) reveal(el);
  var p = cur(); if (p && el.dataset.k) p.focusKey = el.dataset.k;
  var g = el.parentElement;
  if (g && g.dataset.lazy && !g._done) {
    var i = Array.prototype.indexOf.call(g.children, el);
    if (i >= g.children.length - cols(g) * 3) loadMore(g);
  }
}

/* ---------- Lazy grids: a page of cards at a time ---------- */
// source(offset, limit) -> Promise<{items, total}>; make(item, index) -> card HTML.
var LAZY = {}, lazyN = 0, PAGE = 42;
function lazyGrid(cls, source, make, emptyHtml) {
  var id = 'lz' + (++lazyN), p = cur();
  var want = Math.max(PAGE, (p && p.loaded) || 0);  // coming back: as many as before
  return source(0, want).then(function (r) {
    if (!r.items.length) return emptyHtml || '<p class="empty">Nothing here yet.</p>';
    LAZY[id] = { source: source, make: make, n: r.items.length, total: r.total, busy: false };
    if (p) p.loaded = r.items.length;
    return '<div class="' + cls + '" data-lazy="' + id + '">' + r.items.map(function (x, i) { return make(x, i); }).join('') + '</div>';
  });
}
function loadMore(grid) {
  var st = LAZY[grid.dataset.lazy];
  if (!st || st.busy) return;
  if (st.n >= st.total) { grid._done = true; return; }
  st.busy = true;
  st.source(st.n, PAGE).then(function (r) {
    st.busy = false;
    if (!document.body.contains(grid)) return;
    var start = st.n;
    grid.insertAdjacentHTML('beforeend', r.items.map(function (x, i) { return st.make(x, start + i); }).join(''));
    st.n += r.items.length;
    var p = cur(); if (p) p.loaded = st.n;
    if (!r.items.length || st.n >= st.total) grid._done = true;
    grid._cols = null;
    lazyImages(grid);
  }).catch(function () { st.busy = false; });
}
function apiPage(url, key) {  // a server list with offset/limit/total
  return function (offset, limit) {
    return api(url + (url.indexOf('?') < 0 ? '?' : '&') + 'offset=' + offset + '&limit=' + limit).then(function (r) {
      return { items: r[key], total: r.total };
    });
  };
}
function listPage(list) {  // an in-memory list (e.g. an album's photos), drawn a page at a time
  return function (offset, limit) { return Promise.resolve({ items: list.slice(offset, offset + limit), total: list.length }); };
}
function reveal(el) {
  var row = el.closest('.row');
  if (row) {
    var r = el.getBoundingClientRect(), rr = row.getBoundingClientRect(), pad = rr.width * 0.06;
    if (r.right > rr.right - pad) row.scrollLeft += r.right - rr.right + pad;
    else if (r.left < rr.left + pad) row.scrollLeft -= rr.left + pad - r.left;
  }
  var sc = el.closest('.scroller');
  if (sc) {
    var er = el.getBoundingClientRect(), sr = sc.getBoundingClientRect();
    var topPad = sr.height * 0.18, botPad = sr.height * 0.12;
    if (er.top < sr.top + topPad) sc.scrollTop -= sr.top + topPad - er.top;
    else if (er.bottom > sr.bottom - botPad) sc.scrollTop += er.bottom - sr.bottom + botPad;
    if (sc.scrollTop < 40) sc.scrollTop = 0;
  }
}
// Most moves are inside a row, grid or list: answer those from the DOM
// structure (constant time). Only moves between sections fall back to the
// geometric search, which measures every focusable on the page.
var HORIZ = { row: 1, tabs: 1, btns: 1, seasons: 1, ctl: 1 };
var GRID = { grid: 1, pgrid: 1 };
var VERT = { tracks: 1, eps: 1, list: 1 };
function kind(el) {
  var c = el.parentElement; if (!c) return null;
  for (var i = 0; i < c.classList.length; i++) {
    var n = c.classList[i];
    if (HORIZ[n]) return 'h'; if (GRID[n]) return 'g'; if (VERT[n]) return 'v';
  }
  return null;
}
function sib(el, d) {
  var x = d > 0 ? el.nextElementSibling : el.previousElementSibling;
  while (x && !(x.classList.contains('f') && x.offsetParent !== null)) x = d > 0 ? x.nextElementSibling : x.previousElementSibling;
  return x;
}
function cols(grid) {
  if (grid._cols) return grid._cols;
  var kids = grid.children, top = kids.length ? kids[0].offsetTop : 0, n = 0;
  while (n < kids.length && kids[n].offsetTop === top) n++;
  return (grid._cols = Math.max(1, n));
}
function fastMove(dir) {
  var el = focused, k = el && kind(el);
  if (!k) return undefined;
  var side = dir === 'left' || dir === 'right', d = dir === 'right' || dir === 'down' ? 1 : -1;
  if (k === 'h') return side ? (sib(el, d) || null) : undefined;  // end of a row: stay
  if (k === 'v') return side ? undefined : (sib(el, d) || undefined);
  var grid = el.parentElement, kids = grid.children, i = Array.prototype.indexOf.call(kids, el), c = cols(grid);
  if (side) return kids[i + d] || (d < 0 ? undefined : null);
  var t = kids[i + d * c];
  if (t) return t;
  if (d > 0 && grid.dataset.lazy && !grid._done) { loadMore(grid); return null; }  // more on the way
  if (d > 0 && i + c - (i % c) < kids.length) return kids[kids.length - 1];  // short last row
  return undefined;
}
function move(dir) {
  var fast = fastMove(dir);
  if (fast !== undefined) { if (fast) focus(fast); return; }
  var all = focusables();
  if (!focused || all.indexOf(focused) < 0) { focus(all[0]); return; }
  var a = focused.getBoundingClientRect();
  var ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  var best = null, bestScore = Infinity;
  all.forEach(function (el) {
    if (el === focused) return;
    var b = el.getBoundingClientRect(), bx = b.left + b.width / 2, by = b.top + b.height / 2;
    var main, cross;
    // Sideways only within the same band: Right at the end of a row stays put
    // rather than jumping up into the menu.
    if ((dir === 'left' || dir === 'right') && (b.bottom <= a.top + 4 || b.top >= a.bottom - 4)) return;
    if (dir === 'left') { if (b.right > a.left + 2) return; main = a.left - b.right; cross = Math.abs(by - ay); }
    else if (dir === 'right') { if (b.left < a.right - 2) return; main = b.left - a.right; cross = Math.abs(by - ay); }
    else if (dir === 'up') { if (b.bottom > a.top + 2) return; main = a.top - b.bottom; cross = Math.abs(bx - ax); }
    else { if (b.top < a.bottom - 2) return; main = b.top - a.bottom; cross = Math.abs(bx - ax); }
    // Prefer the same row/column: sideways distance counts far more than distance ahead.
    var score = Math.max(main, 0) + cross * (dir === 'left' || dir === 'right' ? 4 : 1.2);
    if (score < bestScore) { bestScore = score; best = el; }
  });
  if (best) focus(best);
}
// The Magic Remote's pointer: hovering focuses, clicking selects.
document.addEventListener('mouseover', function (e) {
  var el = e.target.closest && e.target.closest('.f');
  if (el && el !== focused) focus(el, { noScroll: true });
});
document.addEventListener('click', function (e) {
  var el = e.target.closest && e.target.closest('.f');
  if (el) { focus(el, { noScroll: true }); activate(el); }
});

document.addEventListener('scroll', function (e) {
  var sc = e.target;
  if (!sc.classList || !sc.classList.contains('scroller')) return;
  if (sc.scrollTop + sc.clientHeight > sc.scrollHeight - sc.clientHeight) $$('[data-lazy]', sc).forEach(loadMore);
}, true);

/* ---------- Pages ---------- */
var stack = [];
function cur() { return stack[stack.length - 1]; }
function go(name, arg, replaceAll) {
  if (replaceAll) stack = [];
  stack.push({ name: name, arg: arg, focusKey: null, scroll: 0 });
  render();
}
function back() {
  if (stack.length > 1) {
    stack.pop(); render(); return;
  }
  if (cur().name !== 'home') { go('home', null, true); return; }
  // Home + Back: leave the app (webOS), or do nothing in a browser.
  if (window.PalmSystem && window.PalmSystem.platformBack) window.PalmSystem.platformBack();
}
var renderToken = 0;
function render() {
  var p = cur(), token = ++renderToken;
  if (IO) IO.disconnect();
  LAZY = {};
  $$('.tab').forEach(function (t) {
    var root = stack[0] ? stack[0].name : 'home';
    t.classList.toggle('cur', t.dataset.go === root);
  });
  var old = $('.page .scroller');
  if (stack.length > 1 && stack[stack.length - 2] && old) stack[stack.length - 2].scroll = old.scrollTop;
  $('#app').innerHTML = '<div class="page"><div class="scroller"><div class="inner"><p class="empty"><span class="spinner"></span>Loading…</p></div></div></div>';
  PAGES[p.name](p.arg).then(function (html) {
    if (token !== renderToken) return;
    $('#app').innerHTML = '<div class="page"><div class="scroller"><div class="inner">' + html + '</div></div></div>';
    var sc = $('.page .scroller'); sc.scrollTop = p.scroll || 0;
    var k = p.focusKey && $('.page [data-k="' + p.focusKey.replace(/"/g, '\\"') + '"]');
    lazyImages($('#app'));
    focus(k || $('.page .f') || $('.tab.cur'));
    if (PAGES[p.name].after) PAGES[p.name].after(p.arg);
  }).catch(function (e) {
    if (token !== renderToken) return;
    $('#app').innerHTML = '<div class="page"><div class="inner"><h1>Something went wrong</h1><p class="empty">' + esc(e.message) +
      '</p><div class="btns"><button class="f btn primary" data-act="retry">Try again</button></div></div></div>';
    focus($('.page .f'));
  });
}

/* Cards */
function card(it, i, all, row) {
  row = row || '';
  // Mixed rows (continue / recent) are all landscape: films show their backdrop.
  var wideRow = row === 'Continue watching' || row === 'Recently added';
  var land = wideRow || it.kind === 'episode' || it.kind === 'home_video';
  var url = it.art ? (!land ? it.art.poster : it.kind === 'film' ? it.art.backdrop : it.art.thumb) : null;
  var sub = it.kind === 'episode' ? epCode(it) + (it.show ? ' · ' + it.show : '') : (it.year || '');
  var pct = it.progress && !it.progress.completed && it.duration ? Math.min(100, it.progress.position / it.duration * 100) : 0;
  return '<button class="f card' + (land ? ' land' : '') + (it.kind === 'track' ? ' sq' : '') + '" data-k="' + row + 'i' + it.id +
    '" data-item="' + it.id + '" data-kind="' + esc(it.kind) + '"' + (it.album_key ? ' data-albumkey="' + esc(it.album_key) + '"' : '') + '>' +
    '<div class="art">' + img(url, it.title, land ? 480 : 240) + (it.quality === '4K' ? '<span class="badge">4K</span>' : '') +
    (pct ? '<div class="prog"><i style="width:' + pct.toFixed(1) + '%"></i></div>' : '') + '</div>' +
    '<div class="cap"><b>' + esc(it.title) + '</b><span>' + esc(sub) + '</span></div></button>';
}
function showCard(sh) {
  return '<button class="f card" data-k="s' + esc(sh.key) + '" data-show="' + esc(sh.key) + '">' +
    '<div class="art">' + img(sh.art && sh.art.poster, sh.title, 240) + '</div>' +
    '<div class="cap"><b>' + esc(sh.title) + '</b><span>' + sh.seasons + (sh.seasons === 1 ? ' season' : ' seasons') + '</span></div></button>';
}
function epCode(it) {
  if (it.season == null && it.episode == null) return '';
  return 'S' + (it.season == null ? '?' : it.season) + ' E' + (it.episode == null ? '?' : it.episode) + (it.episode_end ? '–' + it.episode_end : '');
}
function rowOf(title, items, make) {
  return row(title, items.map(function (x, i) { return make(x, i, items, title); }));
}
function row(title, cards) { return cards.length ? '<h2>' + esc(title) + '</h2><div class="row">' + cards.join('') + '</div>' : ''; }

var PAGES = {
  home: function () {
    return Promise.all([api('/api/continue?audiobooks=1'), api('/api/recent?limit=20'), api('/api/films?sort=added&limit=24'),
      api('/api/shows?sort=added&limit=24'), api('/api/home-videos?sort=added&limit=24')]).then(function (r) {
      var cont = r[0].items, recent = r[1].items, films = r[2].items, shows = r[3].shows, vids = r[4].items;
      var contVideo = cont.filter(function (x) { return x.kind !== 'track'; });  // audiobooks: row only
      var hero = contVideo[0] || recent[0] || films[0];
      var heroHtml = '';
      if (hero) {
        var bg = hero.kind === 'episode' ? hero.art.thumb : hero.art.backdrop;
        heroHtml = '<div class="hero"><div class="hero-bg" style="background-image:url(\'' + esc(bg) + '\')"></div><div class="hero-text">' +
          '<div class="eyebrow">' + (contVideo[0] === hero ? 'Continue watching' : 'Just added') + '</div>' +
          '<h1>' + esc(hero.kind === 'episode' ? hero.show : hero.title) + '</h1>' +
          '<p>' + esc(hero.kind === 'episode' ? epCode(hero) + ' · ' + hero.title : (hero.overview || '')) + '</p>' +
          '<div class="btns"><button class="f btn primary" data-k="hero" data-play="' + hero.id + '">' + (contVideo[0] === hero ? 'Resume' : 'Play') + '</button>' +
          '<button class="f btn" data-k="hero-more" data-item="' + hero.id + '">More info</button></div></div></div>';
      }
      if (!films.length && !shows.length && !vids.length) {
        return heroHtml + '<p class="empty">Nothing here yet. Add folders on the PC running PictureHouse, then scan them.</p>';
      }
      return heroHtml + rowOf('Continue watching', cont, card) + rowOf('Recently added', recent, card) +
        rowOf('Films', films.slice(0, 24), card) + row('TV series', shows.slice(0, 24).map(showCard)) +
        rowOf('Home videos', vids.slice(0, 24), card);
    });
  },
  films: function () {
    return lazyGrid('grid', apiPage('/api/films?sort=az', 'items'), card, '<p class="empty">No films yet.</p>')
      .then(function (g) { return '<h1>Films</h1>' + g; });
  },
  shows: function () {
    return lazyGrid('grid', apiPage('/api/shows?sort=az', 'shows'), showCard, '<p class="empty">No series yet.</p>')
      .then(function (g) { return '<h1>TV series</h1>' + g; });
  },
  videos: function () {
    return lazyGrid('grid wide', apiPage('/api/home-videos?sort=added', 'items'), card, '<p class="empty">No home videos yet.</p>')
      .then(function (g) { return '<h1>Home videos</h1>' + g; });
  },
  search: function (q) {
    q = q || '';
    var box = '<input class="f search-box" data-k="q" id="q" type="search" placeholder="Search films, series, episodes" value="' + esc(q) + '">';
    if (!q.trim()) return Promise.resolve('<h1>Search</h1>' + box + '<div id="results"></div>');
    return api('/api/search?q=' + enc(q)).then(function (r) {
      var html = rowOf('Films', r.films, card) + row('Series', r.shows.map(showCard)) +
        rowOf('Episodes', r.episodes, card) + rowOf('Home videos', r.home_videos, card);
      return '<h1>Search</h1>' + box + '<div id="results">' + (html || '<p class="empty">Nothing matches “' + esc(q) + '”.</p>') + '</div>';
    });
  },
  item: function (id) {
    return api('/api/items/' + enc(id)).then(function (it) {
      ITEMS[it.id] = it;
      var isEp = it.kind === 'episode', isVid = it.kind === 'home_video';
      var resume = it.progress && !it.progress.completed && it.progress.position > 30;
      var facts = [isEp ? epCode(it) : it.year, mins(it.duration), it.quality, it.hdr ? 'HDR' : '', it.certification,
        it.rating ? '★ ' + Number(it.rating).toFixed(1) : ''].filter(Boolean);
      var bg = sized(isEp || isVid ? it.art.thumb : it.art.backdrop, 1280);
      return '<div class="detail-bg" style="background-image:url(\'' + esc(bg) + '\')"></div><div class="detail">' +
        (isEp || isVid ? '' : '<div class="poster"><div class="art">' + img(it.art.poster, it.title, 480) + '</div></div>') +
        '<div class="info">' + (isEp ? '<div class="eyebrow">' + esc(it.show) + '</div>' : '') +
        '<h1>' + esc(it.title) + '</h1><div class="facts">' + facts.map(function (f) { return '<span>' + esc(f) + '</span>'; }).join('') + '</div>' +
        (it.genres ? '<div class="facts">' + esc(it.genres) + '</div>' : '') +
        (it.overview ? '<p class="overview">' + esc(it.overview) + '</p>' : '') +
        '<div class="btns">' +
        '<button class="f btn primary" data-k="play" data-play="' + it.id + '">' + (resume ? 'Resume from ' + clock(it.progress.position) : 'Play') + '</button>' +
        (resume ? '<button class="f btn" data-k="restart" data-play="' + it.id + '" data-restart="1">Play from start</button>' : '') +
        (isEp && it.show_key ? '<button class="f btn" data-k="series" data-show="' + esc(it.show_key) + '">All episodes</button>' : '') +
        '</div></div></div>';
    });
  },
  settings: function () {
    return Promise.all([api('/api/hello'), api('/api/themes').catch(function () { return null; })]).then(function (r) {
      var h = r[0], inApp = /[?&]app=1(&|$)/.test(location.search) || !!window.PalmSystem;
      return '<h1>Settings</h1>' + themesHTML(r[1]) +
        '<h2>Connection</h2>' +
        '<div class="facts"><span>Connected to <b style="color:var(--fg)">' + esc(h.name) + '</b></span></div>' +
        '<div class="facts"><span>Address: ' + esc(location.host) + '</span><span>PictureHouse ' + esc(h.version) + '</span></div>' +
        '<div class="btns" style="margin-top:1.5rem">' +
        (inApp ? '<button class="f btn primary" data-k="change" data-act="change-server">Change server</button>' : '') +
        '<button class="f btn" data-k="reload" data-act="reload">Reload</button></div>' +
        '<p class="overview muted" style="font-size:1.15rem;max-width:60rem">' +
        (inApp ? 'Change server if the PC running PictureHouse gets a new address, or to use a different PictureHouse. '
               : 'You’re using PictureHouse in a web browser: to change server, change the address you opened. ') +
        'Folders, matching and playback settings are changed on the PC (or on a phone with the admin PIN).</p>';
    });
  },
  show: function (key) {
    return api('/api/shows/' + enc(key)).then(function (sh) {
      SHOWS[key] = sh;
      var p = cur(), seasons = sh.season_list || [];
      if (p.season == null) {
        var nu = sh.next_up, idx = 0;
        if (nu) seasons.forEach(function (s, i) { if (s.season === nu.season) idx = i; });
        p.season = idx;
      }
      var s = seasons[p.season] || { episodes: [] };
      var nu2 = sh.next_up;
      return '<div class="detail-bg" style="background-image:url(\'' + esc(sh.art && sh.art.backdrop) + '\')"></div><div class="detail" style="padding-top:1rem"><div class="info" style="max-width:none;width:100%">' +
        '<h1>' + esc(sh.title) + '</h1><div class="facts"><span>' + esc(sh.year || '') + '</span><span>' + sh.episodes + ' episodes</span>' +
        (sh.certification ? '<span>' + esc(sh.certification) + '</span>' : '') + '</div>' +
        (sh.overview ? '<p class="overview" style="max-height:6rem">' + esc(sh.overview) + '</p>' : '') +
        (nu2 ? '<div class="btns"><button class="f btn primary" data-k="nextup" data-play="' + nu2.id + '">' +
          (nu2.progress && !nu2.progress.completed ? 'Resume ' : 'Play ') + esc(epCode(nu2)) + '</button></div>' : '') +
        '<div class="seasons">' + seasons.map(function (x, i) {
          return '<button class="f season' + (i === p.season ? ' cur' : '') + '" data-k="season' + i + '" data-season="' + i + '">' +
            (x.season === 0 ? 'Specials' : x.season == null || x.season < 0 ? 'Other' : 'Season ' + x.season) + '</button>';
        }).join('') + '</div>' +
        '<div class="eps">' + s.episodes.map(function (e) {
          var pct = e.progress && !e.progress.completed && e.duration ? Math.min(100, e.progress.position / e.duration * 100) : 0;
          return '<button class="f ep" data-k="e' + e.id + '" data-item="' + e.id + '"><div class="art">' + img(e.art.thumb, e.title, 320) +
            (pct ? '<div class="prog"><i style="width:' + pct.toFixed(1) + '%"></i></div>' : '') + '</div>' +
            '<div><div class="t">' + esc(epCode(e)) + ' · ' + esc(e.title) + (e.progress && e.progress.completed ? ' <span class="muted">✓</span>' : '') + '</div>' +
            '<div class="o">' + esc(e.overview || mins(e.duration)) + '</div></div></button>';
        }).join('') + '</div></div></div>';
    });
  }
};
var ITEMS = {}, SHOWS = {};

/* Themes (docs/themes.md): this TV can use its own, kept in its storage like
   the console's per-device choice; otherwise it follows the server default. */
function tvTheme() { try { return localStorage.getItem('ph.theme'); } catch (e) { return null; } }
function themesHTML(t) {
  if (!t || !t.themes) return '';
  var mine = tvTheme(), known = t.themes.some(function (x) { return x.id === mine; });
  var active = known ? mine : null;
  var def = t.themes.filter(function (x) { return x.id === t.default; })[0];
  var btn = function (id, label) {
    return '<button class="f season' + (id === active ? ' cur' : '') + '" data-k="theme-' + esc(id || '') +
      '" data-theme="' + esc(id || '') + '">' + esc(label) + '</button>';
  };
  return '<h2>Appearance</h2><div class="seasons">' +
    btn(null, 'Default' + (def ? ' (' + def.name + ')' : '')) +
    t.themes.map(function (x) { return btn(x.id, x.name); }).join('') + '</div>';
}
function setTvTheme(id) {
  try { if (id) localStorage.setItem('ph.theme', id); else localStorage.removeItem('ph.theme'); } catch (e) { /* no storage */ }
  var link = $('#themecss');
  if (link) link.href = '/theme.css?target=tv' + (id ? '&id=' + enc(id) : '&d=' + Date.now());
  var p = cur(); p.focusKey = 'theme-' + (id || ''); p.scroll = $('.page .scroller').scrollTop;
  render();
}

// Search: type with the TV's on-screen keyboard; results follow.
var searchT = null;
document.addEventListener('input', function (e) {
  if (e.target.id !== 'q') return;
  clearTimeout(searchT);
  var q = e.target.value;
  searchT = setTimeout(function () {
    var p = cur(); if (!p || p.name !== 'search') return;
    p.arg = q;
    PAGES.search(q).then(function (html) {
      var tmp = document.createElement('div'); tmp.innerHTML = html;
      $('#results').innerHTML = $('#results', tmp).innerHTML;
    }).catch(function () { /* keep typing */ });
  }, 450);
});

// media.js (music, photos) adds its own: each returns true when it handled el / the key.
var ACTIVATORS = [], KEY_HANDLERS = [];
function activate(el) {
  for (var a = 0; a < ACTIVATORS.length; a++) if (ACTIVATORS[a](el)) return;
  if (el.dataset.go) { go(el.dataset.go, null, true); return; }
  if (el.dataset.act === 'retry') { render(); return; }
  if (el.dataset.act === 'reload') { location.reload(); return; }
  if (el.dataset.act === 'change-server') { changeServer(); return; }
  if (el.dataset.theme != null) { setTvTheme(el.dataset.theme || null); return; }
  if (el.dataset.play) { play(+el.dataset.play, !!el.dataset.restart); return; }
  if (el.dataset.item) { go('item', el.dataset.item); return; }
  if (el.dataset.show) { go('show', el.dataset.show); return; }
  if (el.dataset.season != null) {
    var p = cur(); p.season = +el.dataset.season; p.focusKey = 'season' + p.season;
    p.scroll = $('.page .scroller').scrollTop; render(); return;
  }
  if (el.id === 'q') el.focus();
}

/* Back to the TV app's launcher, which then shows its server chooser. The
   launcher page is the previous history entry; window.name tells it why. */
function changeServer() {
  window.name = 'ph:choose';
  history.back();
  setTimeout(function () {  // still here: no launcher behind us
    window.name = '';
    toast('Close PictureHouse, then hold the red button while it starts to choose a server.');
  }, 2500);
}

/* ---------- Player ---------- */
var V = $('#video'), P = null;
var PLAYER_ID = 'tv-' + Math.random().toString(36).slice(2, 8);
function pmsg(text, err) {
  var m = $('#pmsg');
  if (!text) { m.hidden = true; return; }
  m.innerHTML = text; m.className = 'osd-msg' + (err ? ' err' : ''); m.hidden = false;
}
function osd(show) {
  var o = $('#osd');
  clearTimeout(osd.t);
  o.classList.toggle('hide', !show);
  if (show && P && !V.paused) osd.t = setTimeout(function () { o.classList.add('hide'); }, 4000);
}
function play(id, fromStart) {
  var load = ITEMS[id] ? Promise.resolve(ITEMS[id]) : api('/api/items/' + id);
  load.then(function (it) {
    ITEMS[it.id] = it;
    var start = !fromStart && it.progress && !it.progress.completed && it.progress.position > 30 ? it.progress.position : 0;
    P = { it: it, start: start, mode: null, sid: null, lastSave: 0, ended: false, upNext: null };
    if (window.musicPause) window.musicPause();  // media.js: a film and music don't mix
    $('#player').hidden = false; $('#top').classList.add('hide');
    if (focused) focused.classList.remove('on');
    $('#ptitle').textContent = it.kind === 'episode' ? it.show : it.title;
    $('#vnext').hidden = !(it.kind === 'episode' && it.show_key);
    $('#psub').textContent = it.kind === 'episode' ? epCode(it) + ' · ' + it.title : [it.year, it.quality].filter(Boolean).join(' · ');
    pmsg('<span class="spinner"></span>Starting…'); osd(true);
    return api('/api/items/' + it.id + '/play?client=tv').then(function (d) {
      P.d = d;
      return d.mode === 'transcode' ? startHls(start) : startDirect(start);
    });
  }).catch(function (e) { playerError(e.message); });
}
function startPlaying() {
  V.play().catch(function (err) {
    // Autoplay refused (some browsers): wait for OK instead of spinning forever.
    if (P && err && err.name === 'NotAllowedError') pmsg('Press OK to play');
    /* anything else is reported by the error event */
  });
}
function startDirect(at) {
  P.mode = 'direct';
  V.src = P.d.url; P.seekTo = at;
  startPlaying();
}
function startHls(at) {
  if (!P.d.hls_url) { playerError('this file can’t be converted for the TV'); return Promise.resolve(); }
  P.mode = 'hls';
  pmsg('<span class="spinner"></span>Converting for the TV…');
  var url = P.d.hls_url + '?c=' + PLAYER_ID;
  // Fetch the playlist ourselves first: it says "server busy" clearly, and gives the session to close later.
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        throw new Error(j.detail || r.status + ' ' + r.statusText);
      });
    }
    P.sid = r.headers.get('X-HLS-Session');
    V.src = url; P.seekTo = at;
    startPlaying();
  }).catch(function (e) { playerError(e.message); });
}
function playerError(msg) {
  pmsg('<b>Can’t play this</b><br>' + esc(msg) + '<br><span class="muted">Press Back to return.</span>', true);
  osd(true);
}
function pos() { return V.currentTime || 0; }
function total() { return (P && P.it.duration) || V.duration || 0; }
function saveProgress(final) {
  if (!P || !P.it || pos() < 5) return Promise.resolve();
  var done = P.ended || (total() && pos() > total() * 0.92);
  var it = P.it, at = pos();
  P.lastSave = Date.now();
  return api('/api/items/' + it.id + '/progress', { method: 'PUT', body: { position: at, completed: !!done }, keepalive: !!final })
    .then(function () { it.progress = { position: at, completed: !!done }; }).catch(function () { /* next save retries */ });
}
function stopPlayer() {
  if (!P) return;
  var saved = saveProgress(true);
  clearTimeout(P.upNextT);
  if (P.sid) api('/api/hls/' + P.sid, { method: 'DELETE', keepalive: true }).catch(function () {});
  var wasId = P.it.id; P = null;
  V.pause(); V.removeAttribute('src'); V.load();
  $('#player').hidden = true; $('#top').classList.remove('hide');
  delete ITEMS[wasId];
  saved.then(render);  // after the save lands, so "Resume" and progress bars are current
}
function seekBy(s) {
  if (!P || !V.duration) return;
  var t = Math.max(0, Math.min(pos() + s, (total() || V.duration) - 2));
  V.currentTime = t; drawBar(); osd(true);
}
function drawBar() {
  var t = total();
  $('#pcur').textContent = clock(pos()); $('#ptot').textContent = clock(t);
  $('#pbar').style.width = t ? (pos() / t * 100).toFixed(2) + '%' : '0';
  try {
    var b = V.buffered; if (b.length && t) $('#pbuf').style.width = (b.end(b.length - 1) / t * 100).toFixed(2) + '%';
  } catch (e) { /* no buffer info */ }
}
V.addEventListener('loadedmetadata', function () {
  if (P && P.seekTo) { V.currentTime = P.seekTo; P.seekTo = 0; }
});
V.addEventListener('playing', function () { pmsg(null); osd(true); });
// The on-screen buttons (also clickable with the Magic Remote's pointer).
var PAUSE_SVG = '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
var PLAY_SVG = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
function vIcons() { $('#vtoggle').innerHTML = (V.paused ? PLAY_SVG : PAUSE_SVG) + '<span>OK</span>'; }
V.addEventListener('play', vIcons);
V.addEventListener('pause', vIcons);
function nextEpisode() {
  if (!P || !P.it.show_key) return;
  var it = P.it;
  api('/api/shows/' + enc(it.show_key)).then(function (sh) {
    var eps = [];
    (sh.season_list || []).forEach(function (x) { eps = eps.concat(x.episodes); });
    var i = -1; eps.forEach(function (e, n) { if (e.id === it.id) i = n; });
    var next = i >= 0 ? eps[i + 1] : null;
    if (!next) { toast('That was the last episode'); return; }
    stopPlayer(); play(next.id, true);
  });
}
document.addEventListener('click', function (e) {
  var b = e.target.closest && e.target.closest('[data-v]');
  if (!b || !P) return;
  e.stopPropagation();
  var v = b.dataset.v;
  if (v === 'back') seekBy(-10); else if (v === 'fwd') seekBy(30);
  else if (v === 'toggle') { if (V.paused) V.play(); else V.pause(); osd(true); }
  else if (v === 'next') nextEpisode(); else if (v === 'stop') stopPlayer();
}, true);
V.addEventListener('waiting', function () { if (P) pmsg('<span class="spinner"></span>Loading…'); });
V.addEventListener('pause', function () { if (P && !P.ended) { osd(true); saveProgress(false); } });
V.addEventListener('timeupdate', function () {
  if (!P) return; drawBar();
  if (Date.now() - P.lastSave > 10000) saveProgress(false);
});
V.addEventListener('error', function () {
  if (!P || !V.getAttribute('src')) return;
  // The TV couldn't play the original after all: convert it instead, from the same point.
  if (P.mode === 'direct' && P.d.hls_url) {
    var at = pos() || P.start; toast('Converting this one for the TV…');
    startHls(at); return;
  }
  var e = V.error;
  playerError(e && e.message ? e.message : 'the TV couldn’t decode this file (error ' + (e ? e.code : '?') + ')');
});
V.addEventListener('ended', function () {
  if (!P) return;
  P.ended = true; saveProgress(true);
  if (P.it.kind === 'episode' && P.it.show_key) upNext(); else setTimeout(stopPlayer, 1500);
});
function upNext() {
  var it = P.it;
  api('/api/shows/' + enc(it.show_key)).then(function (sh) {
    var eps = [];
    (sh.season_list || []).forEach(function (s) { eps = eps.concat(s.episodes); });
    var i = -1; eps.forEach(function (e, n) { if (e.id === it.id) i = n; });
    var next = i >= 0 ? eps[i + 1] : null;
    if (!P) return;
    if (!next) { stopPlayer(); return; }
    var left = 10; P.upNext = next;
    var tick = function () {
      if (!P || P.upNext !== next) return;
      pmsg('<b>Up next: ' + esc(epCode(next)) + ' · ' + esc(next.title) + '</b><br>Starting in ' + left + ' s · OK: play now · Back: stop');
      if (left-- <= 0) { var id = next.id; stopPlayer(); play(id, true); return; }
      P.upNextT = setTimeout(tick, 1000);
    };
    osd(true); tick();
  }).catch(function () { stopPlayer(); });
}
function playerKey(k) {
  if (P && P.upNext && (k === KEY.ENTER || k === KEY.PLAY)) { var id = P.upNext.id; stopPlayer(); play(id, true); return; }
  switch (k) {
    case KEY.BACK: case KEY.ESC: case KEY.BACKSPACE: case KEY.STOP: stopPlayer(); return;
    case KEY.ENTER: case KEY.PLAYPAUSE: if (V.paused) V.play(); else V.pause(); osd(true); return;
    case KEY.PLAY: V.play(); return;
    case KEY.PAUSE: V.pause(); return;
    case KEY.LEFT: seekBy(-10); return;
    case KEY.RIGHT: seekBy(30); return;
    case KEY.RW: seekBy(-60); return;
    case KEY.FF: seekBy(60); return;
    case KEY.PGUP: nextEpisode(); return;  // channel up: next episode
    case KEY.UP: case KEY.DOWN: osd(true); return;
  }
}

/* ---------- Keys ---------- */
document.addEventListener('keydown', function (e) {
  var k = e.keyCode;
  if (!$('#player').hidden) { e.preventDefault(); playerKey(k); return; }
  for (var h = 0; h < KEY_HANDLERS.length; h++) if (KEY_HANDLERS[h](k, e)) { e.preventDefault(); return; }
  var typing = document.activeElement && document.activeElement.id === 'q';
  if (typing && (k === KEY.BACKSPACE || (k >= 48 && k <= 90) || k === 32)) return;  // let the text box have it
  if (k === KEY.LEFT) { e.preventDefault(); move('left'); }
  else if (k === KEY.RIGHT) { e.preventDefault(); move('right'); }
  else if (k === KEY.UP) { e.preventDefault(); move('up'); }
  else if (k === KEY.DOWN) { e.preventDefault(); if (typing) document.activeElement.blur(); move('down'); }
  else if (k === KEY.ENTER) { if (focused && !typing) { e.preventDefault(); activate(focused); } }
  else if (k === KEY.BACK || k === KEY.ESC || (k === KEY.BACKSPACE && !typing)) { e.preventDefault(); back(); }
  else if (k === KEY.PGDN) { e.preventDefault(); var sc = $('.page .scroller'); if (sc) sc.scrollTop += sc.clientHeight * 0.8; }
  else if (k === KEY.PGUP) { e.preventDefault(); var sc2 = $('.page .scroller'); if (sc2) sc2.scrollTop -= sc2.clientHeight * 0.8; }
});
window.addEventListener('pagehide', function () { if (P) saveProgress(true); });
// webOS: the app was sent to the background (Home button): pause and save.
document.addEventListener('visibilitychange', function () { if (document.hidden && P) { V.pause(); saveProgress(true); } });

function tick() { var d = new Date(); $('#clock').textContent = d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0'); }
tick(); setInterval(tick, 15000);
go('home', null, true);

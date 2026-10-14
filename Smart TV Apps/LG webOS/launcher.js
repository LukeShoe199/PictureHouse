/* PictureHouse TV app launcher (webOS 5 = Chrome 68: ES2017, no ?. / ??).
 *
 * 1. Try the server used last time.
 * 2. Otherwise look for PictureHouse on the TV's own network: ask each
 *    address for /api/hello on port 9595 (a web app can't use DLNA discovery).
 * 3. Otherwise let the user type the address.
 * Then open http://server:9595/tv/, the big-screen interface served by
 * PictureHouse itself. Hold the red button while the app starts to pick a
 * different server, or use Settings → Change server in the TV interface: it
 * comes back here (history.back, with window.name = 'ph:choose', which
 * survives the navigation) and the chooser opens.
 */
'use strict';

var PORT = 9595;
var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 461, RED: 403 };
var body = document.getElementById('body');
var choosing = false;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function saved() { try { return localStorage.getItem('server'); } catch (e) { return null; } }
function save(url) { try { localStorage.setItem('server', url); } catch (e) { /* private mode */ } }

/* Is there a PictureHouse server at this base URL? Resolves {url, name} or null. */
function hello(base, ms) {
  var ctl = typeof AbortController === 'function' ? new AbortController() : null;
  var timer = setTimeout(function () { if (ctl) ctl.abort(); }, ms || 1500);
  return fetch(base + '/api/hello', { cache: 'no-store', signal: ctl ? ctl.signal : undefined })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) { return j && j.app === 'picturehouse' ? { url: base, name: j.name || 'PictureHouse' } : null; })
    .catch(function () { return null; })
    .then(function (x) { clearTimeout(timer); return x; });
}

function open(server, pause) {
  save(server.url);
  body.innerHTML = '<p><span class="spinner"></span>Opening ' + esc(server.name) + '…</p>' +
    (pause ? '<p>Press the red button to choose another server.</p>' : '');
  // A moment to press Red first (start() listens for it); none after a manual pick.
  // A real navigation (not replace): Settings → Change server comes back here.
  setTimeout(function () { if (!choosing) location.href = server.url + '/tv/?app=1'; }, pause ? 1500 : 0);
}

/* The TV's own address, to know which network to search. */
function lunaIp() {
  return new Promise(function (resolve) {
    if (typeof window.PalmServiceBridge !== 'function') { resolve(null); return; }
    var done = false;
    var bridge = new window.PalmServiceBridge();
    bridge.onservicecallback = function (msg) {
      if (done) return; done = true;
      try {
        var r = JSON.parse(msg);
        var net = (r.wired && r.wired.state === 'connected' && r.wired) || (r.wifi && r.wifi.state === 'connected' && r.wifi) || {};
        resolve(net.ipAddress || null);
      } catch (e) { resolve(null); }
    };
    bridge.call('luna://com.palm.connectionmanager/getStatus', '{}');
    setTimeout(function () { if (!done) { done = true; resolve(null); } }, 2500);
  });
}
function webrtcIp() {
  // Chrome 68 still reports the real local address in ICE candidates.
  return new Promise(function (resolve) {
    if (typeof RTCPeerConnection !== 'function') { resolve(null); return; }
    var pc = new RTCPeerConnection({ iceServers: [] }), done = false;
    var finish = function (ip) { if (!done) { done = true; try { pc.close(); } catch (e) { /* closed */ } resolve(ip); } };
    pc.createDataChannel('x');
    pc.onicecandidate = function (e) {
      var m = e.candidate && /(\d+\.\d+\.\d+\.\d+)/.exec(e.candidate.candidate);
      if (m && !/^(0|127)\./.test(m[1])) finish(m[1]);
    };
    pc.createOffer().then(function (o) { return pc.setLocalDescription(o); }).catch(function () { finish(null); });
    setTimeout(function () { finish(null); }, 2500);
  });
}

function scan(prefixes, skip) {
  var hosts = [];
  prefixes.forEach(function (p) { for (var i = 1; i < 255; i++) if (p + '.' + i !== skip) hosts.push(p + '.' + i); });
  var found = [], next = 0, total = hosts.length, doneCount = 0;
  var status = function () {
    if (choosing) return;
    body.innerHTML = '<p><span class="spinner"></span>Looking for your PictureHouse server…</p>' +
      '<p>Checked ' + doneCount + ' of ' + total + ' addresses on ' + esc(prefixes.join('.x, ')) + '.x</p>';
  };
  status();
  return new Promise(function (resolve) {
    var running = 0;
    var pump = function () {
      while (running < 48 && next < hosts.length) {
        var host = hosts[next++]; running++;
        hello('http://' + host + ':' + PORT, 1500).then(function (s) {
          running--; doneCount++;
          if (s) found.push(s);
          if (doneCount % 16 === 0) status();
          if (doneCount === total) resolve(found); else pump();
        });
      }
    };
    pump();
  });
}

function discover() {
  return Promise.all([lunaIp(), webrtcIp()]).then(function (ips) {
    var ip = ips[0] || ips[1];
    if (ip) return scan([ip.split('.').slice(0, 3).join('.')], ip);
    // The TV's own address is unknown: try common home networks one at a
    // time, stopping at the first that has a server.
    var guesses = ['192.168.1', '192.168.0', '192.168.4', '192.168.2', '10.0.0', '10.0.1'];
    var next = function (i) {
      if (i >= guesses.length || choosing) return Promise.resolve([]);
      return scan([guesses[i]], null).then(function (found) { return found.length ? found : next(i + 1); });
    };
    return next(0);
  });
}

/* ---------- Choosing a server by hand ---------- */
function chooser(found, note, current) {
  choosing = true;
  var last = saved();
  var html = (note ? '<p class="err">' + esc(note) + '</p>' : '') + '<div class="list">';
  if (current) {
    html += '<button class="f" data-act="keep">Keep using ' + esc(current.name) + '<small>' +
      esc(current.url.replace(/^http:\/\//, '')) + '</small></button>';
  }
  found.forEach(function (s, i) {
    html += '<button class="f" data-i="' + i + '">' + esc(s.name) + '<small>' + esc(s.url.replace(/^http:\/\//, '')) + '</small></button>';
  });
  html += '<input class="f" id="addr" type="text" placeholder="Type the address, e.g. 192.168.1.40" value="' +
    esc(last ? last.replace(/^http:\/\//, '').replace(':' + PORT, '') : '') + '">' +
    '<button class="f" data-act="connect">Connect to this address</button>' +
    '<button class="f" data-act="rescan">Search the network again</button></div>' +
    '<p>The address is shown in PictureHouse on your PC: Library → Phones &amp; tablets.</p>';
  body.innerHTML = html;
  var items = Array.prototype.slice.call(document.querySelectorAll('.f'));
  var at = 0;
  var hl = function (n) {
    items.forEach(function (el) { el.classList.remove('on'); });
    at = Math.max(0, Math.min(items.length - 1, n)); items[at].classList.add('on');
    if (document.activeElement && document.activeElement !== items[at]) document.activeElement.blur();
  };
  hl(0);
  var pick = function (el) {
    if (el.dataset.i != null) { choosing = false; open(found[+el.dataset.i]); return; }
    if (el.id === 'addr') { el.focus(); return; }
    if (el.dataset.act === 'rescan') { choosing = false; start(true); return; }
    if (el.dataset.act === 'keep') { choosing = false; open(current); return; }
    var v = document.getElementById('addr').value.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    if (!v) { hl(items.indexOf(document.getElementById('addr'))); return; }
    var base = 'http://' + (v.indexOf(':') >= 0 ? v : v + ':' + PORT);
    body.querySelector('.list').insertAdjacentHTML('beforebegin', '<p id="trying"><span class="spinner"></span>Trying ' + esc(base) + '…</p>');
    hello(base, 4000).then(function (s) {
      var t = document.getElementById('trying'); if (t) t.parentNode.removeChild(t);
      if (s) { choosing = false; open(s); } else chooser(found, 'No PictureHouse server answered at ' + base.replace('http://', '') + '.');
    });
  };
  document.onkeydown = function (e) {
    var typing = document.activeElement && document.activeElement.id === 'addr';
    if (e.keyCode === KEY.DOWN) { e.preventDefault(); hl(at + 1); }
    else if (e.keyCode === KEY.UP) { e.preventDefault(); hl(at - 1); }
    else if (e.keyCode === KEY.ENTER) { e.preventDefault(); if (typing) { hl(at + 1); } else pick(items[at]); }
    else if (e.keyCode === KEY.BACK && !typing) {
      e.preventDefault();
      if (current) { choosing = false; open(current); }  // back to where you were
      else if (window.PalmSystem) window.PalmSystem.platformBack();
    }
  };
  items.forEach(function (el, i) {
    el.onmouseover = function () { hl(i); };
    el.onclick = function () { hl(i); pick(el); };
  });
}

function start(skipSaved) {
  document.onkeydown = function (e) { if (e.keyCode === KEY.RED) { choosing = true; chooser([], ''); } };
  var last = skipSaved ? null : saved();
  var first = last ? hello(last, 3000) : Promise.resolve(null);
  first.then(function (s) {
    if (choosing) return null;
    if (s) { open(s, true); return null; }
    return discover().then(function (found) {
      if (choosing) return;
      if (found.length === 1) open(found[0], true);
      else chooser(found, found.length ? '' : (last ? 'Couldn’t reach ' + last.replace('http://', '') + '. ' : '') +
        'No PictureHouse server was found on this network. Is it running on your PC?');
    });
  });
}
function changeServer() {
  // Sent here by Settings → Change server in the TV interface.
  choosing = true;
  var last = saved();
  body.innerHTML = '<p><span class="spinner"></span>One moment…</p>';
  (last ? hello(last, 3000) : Promise.resolve(null)).then(function (cur) {
    chooser([], 'Type the address of the PC running PictureHouse, or search the network.', cur);
  });
}

function boot() {
  if (window.name === 'ph:choose') { window.name = ''; changeServer(); return; }
  start(false);
}
// Coming back from the TV interface may restore this page rather than reload it.
window.addEventListener('pageshow', function (e) { if (e.persisted) boot(); });
boot();

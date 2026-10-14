/* PictureHouse photo slideshow engine, shared by the web console and the TV
   app. Plain ES2017 (the LG TV runs Chrome 68): no modules, no ?. or ??.

     var show = new PHSlideshow(containerEl, photos, { interval: 6000, onChange: fn });
     show.start();  show.pause();  show.next();  show.prev();  show.go(i);
     show.setShuffle(true);  show.destroy();

   photos: [{ screen: url, thumb: url, title, taken_at }] (opts.size picks
   another URL field, e.g. "tv"). Two layers cross-fade; each picture is
   shown whole ("contain") over a blurred, enlarged copy of itself.

   Built to stay smooth on a 2020 TV playing music at the same time:
   - a picture is fully decoded (img.decode) before it is faded in, so the
     fade never stutters on a half-decoded image;
   - only the next picture is preloaded, and the one faded out is released,
     so memory stays flat however long the slideshow runs;
   - opts.fill = 'dim' backs it with the small thumbnail, darkened, instead
     of a full-screen blur; opts.kenBurns = false turns off the slow zoom;
     opts.fade sets the cross-fade length (ms). */
(function () {
  'use strict';
  var CSS = '' +
    '.phs{position:absolute;top:0;left:0;right:0;bottom:0;overflow:hidden;background:#000}' +
    '.phs-layer{position:absolute;top:0;left:0;right:0;bottom:0;opacity:0;transition:opacity var(--phs-fade,1.2s) ease;will-change:opacity}' +
    '.phs-layer.on{opacity:1}' +
    '.phs-fill{position:absolute;top:-6%;left:-6%;right:-6%;bottom:-6%;background-size:cover;background-position:center;' +
      'filter:blur(40px) brightness(.45);transform:scale(1.1)}' +
    '.phs.dim .phs-fill{filter:brightness(.3);top:0;left:0;right:0;bottom:0;transform:none}' +
    '.phs-img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:contain}' +
    '.phs-layer.zoom .phs-img{animation:phs-kb var(--phs-dur,7s) linear forwards}' +
    '@keyframes phs-kb{from{transform:scale(1)}to{transform:scale(1.07)}}';

  function injectCss() {
    if (document.getElementById('phs-css')) return;
    var s = document.createElement('style'); s.id = 'phs-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function shuffle(list) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  function PHSlideshow(el, photos, opts) {
    injectCss();
    this.el = el; this.opts = opts || {};
    this.original = (photos || []).slice();
    this.photos = this.opts.shuffle ? shuffle(this.original) : this.original.slice();
    this.interval = this.opts.interval || 6000;
    this.i = -1; this.playing = false; this.timer = null; this.ahead = null; this.seq = 0;
    el.classList.add('phs');
    if (this.opts.fill === 'dim') el.classList.add('dim');
    el.style.setProperty('--phs-fade', (this.opts.fade || 1200) + 'ms');
    el.innerHTML = '';
    this.layers = [0, 1].map(function () {
      var l = document.createElement('div'); l.className = 'phs-layer';
      l.innerHTML = '<div class="phs-fill"></div><img class="phs-img" alt="">';
      el.appendChild(l); return l;
    });
    this.front = 0;
  }
  PHSlideshow.prototype.url = function (p) { return (this.opts.size && p[this.opts.size]) || p.screen; };
  PHSlideshow.prototype.preloadNext = function () {
    var p = this.photos[(this.i + 1) % this.photos.length];
    if (!p) return;
    var im = new Image(); im.src = this.url(p);
    if (im.decode) im.decode().catch(function () {});
    this.ahead = im;  // only one held: the one before is let go
  };
  PHSlideshow.prototype.go = function (i) {
    var n = this.photos.length; if (!n) return;
    i = ((i % n) + n) % n;
    var self = this, p = this.photos[i], seq = ++this.seq;
    this.i = i;
    if (this.opts.onChange) this.opts.onChange(i, p, n);
    clearTimeout(this.timer);
    var back = this.layers[1 - this.front], frontEl = this.layers[this.front];
    var img = back.querySelector('.phs-img');
    var show = function () {
      if (seq !== self.seq) return;  // a newer go() won the race
      var fill = self.opts.fill === 'dim' && p.thumb ? p.thumb : self.url(p);
      back.querySelector('.phs-fill').style.backgroundImage = 'url("' + fill + '")';
      back.style.setProperty('--phs-dur', (self.interval / 1000 + 1.5) + 's');
      back.classList.remove('zoom'); void back.offsetWidth;  // restart the zoom
      if (self.playing && self.opts.kenBurns !== false) back.classList.add('zoom');
      back.classList.add('on'); frontEl.classList.remove('on');
      self.front = 1 - self.front;
      // Release the picture that just faded out once the fade is over.
      setTimeout(function () {
        if (!frontEl.classList.contains('on')) {
          frontEl.querySelector('.phs-img').removeAttribute('src');
          frontEl.querySelector('.phs-fill').style.backgroundImage = '';
        }
      }, (self.opts.fade || 1200) + 200);
      self.preloadNext();
      self.schedule();
    };
    var shown = false, once = function () { if (!shown) { shown = true; show(); } };
    img.onload = null; img.onerror = null;
    img.src = this.url(p);
    // Fade in once decoded; never wait on decode() forever (it can stall,
    // e.g. while the page isn't being drawn): show it after 2 s regardless.
    if (img.decode) img.decode().then(once, once); else { img.onload = once; img.onerror = once; if (img.complete) once(); }
    setTimeout(once, 2000);
  };
  PHSlideshow.prototype.schedule = function () {
    clearTimeout(this.timer);
    var self = this;
    if (this.playing) this.timer = setTimeout(function () { self.go(self.i + 1); }, this.interval);
  };
  PHSlideshow.prototype.start = function () {
    this.playing = true;
    if (this.i < 0) this.go(this.opts.start || 0);
    else { this.schedule(); if (this.opts.kenBurns !== false) this.layers[this.front].classList.add('zoom'); }
  };
  PHSlideshow.prototype.pause = function () {
    this.playing = false; clearTimeout(this.timer);
    this.layers.forEach(function (l) { l.classList.remove('zoom'); });
  };
  PHSlideshow.prototype.toggle = function () { if (this.playing) this.pause(); else this.start(); return this.playing; };
  PHSlideshow.prototype.next = function () { this.go(this.i + 1); };
  PHSlideshow.prototype.prev = function () { this.go(this.i - 1); };
  /* Shuffle on/off while running: the current picture stays put. */
  PHSlideshow.prototype.setShuffle = function (on) {
    var cur = this.photos[this.i];
    this.photos = on ? shuffle(this.original) : this.original.slice();
    var at = this.photos.indexOf(cur);
    if (on && at > 0) { this.photos.splice(at, 1); this.photos.unshift(cur); at = 0; }
    this.i = Math.max(0, at);
    this.preloadNext();
    return on;
  };
  PHSlideshow.prototype.destroy = function () {
    this.pause(); this.seq++; this.ahead = null;
    this.el.innerHTML = ''; this.el.classList.remove('phs', 'dim');
  };
  PHSlideshow.shuffle = shuffle;
  window.PHSlideshow = PHSlideshow;
})();

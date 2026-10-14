/* PictureHouse music visualiser, shared by the web console and the TV app.
   Plain ES2017 (the LG TV runs Chrome 68): no modules, no ?. or ??.

     var viz = PHViz.attach(canvas, audioEl, { art: url });
     // TVs: levels precomputed by the server, drawn at reduced resolution:
     var viz = PHViz.attach(canvas, audioEl, { levels: fn, scale: 0.5, fps: 30 });
     viz.start();  viz.mode('radial' | 'bars' | 'wave');  viz.nextMode();  viz.stop();

   Browsers: the Web Audio API analyses the page's own <audio> element (wired
   once and reused; it still plays through the speakers as before).
   TVs: opts.levels() returns 0-255 band levels for "now" (webOS plays audio
   in hardware, where Web Audio hears only silence); the element is then
   never wired to Web Audio at all.

   Drawing is kept cheap for a weak TV CPU: the background is an opaque fill
   (no full-screen alpha trails), the glow gradient and the round album art
   are prepared once and reused, colours are precomputed, and bars rise fast
   and fall gently so movement reads clearly even at 30 fps. */
(function () {
  'use strict';
  var MODES = ['radial', 'bars', 'wave'];
  var BARS = 64;

  function graph(audio) {
    if (audio._phGraph) return audio._phGraph;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      var ctx = new AC();
      var src = ctx.createMediaElementSource(audio);
      var an = ctx.createAnalyser();
      an.fftSize = 512; an.smoothingTimeConstant = 0.75;
      src.connect(an); an.connect(ctx.destination);
      audio._phGraph = { ctx: ctx, analyser: an };
      return audio._phGraph;
    } catch (e) {
      return null;
    }
  }

  function rgba(f, a) {  // amber (low) -> sky blue (high)
    var r = Math.round(242 + (127 - 242) * f), g = Math.round(179 + (178 - 179) * f), b = Math.round(61 + (229 - 61) * f);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }
  var COLORS = [], DIM = [];
  for (var c = 0; c < BARS; c++) { COLORS.push(rgba(c / BARS, 0.95)); DIM.push(rgba(c / BARS, 0.2)); }

  function Viz(canvas, audio, opts) {
    this.c = canvas; this.audio = audio; this.opts = opts || {};
    this.g = this.opts.levels ? null : graph(audio);
    this.scale = this.opts.scale || 1;
    this.frameMs = 1000 / (this.opts.fps || 60);
    this.lastDraw = 0;
    this.modeName = this.opts.mode || 'radial';
    this.raf = 0; this.t = 0; this.bass = 0;
    this.raw = new Uint8Array(this.g ? this.g.analyser.frequencyBinCount : 256);
    this.wave = new Uint8Array(this.g ? this.g.analyser.fftSize : 512);
    this.bars = new Float32Array(BARS);  // 0..1, displayed (with fall-off)
    this.target = new Float32Array(BARS); this.pts = new Float32Array(96);  // reused every frame
    this.art = null; this.artDisc = null; this.glow = null; this.w = 0; this.h = 0;
    this.setArt(this.opts.art);
  }
  Viz.prototype.setArt = function (url) {
    if (url === this.artUrl) return;
    this.artUrl = url; this.art = null; this.artDisc = null;
    if (!url) return;
    var im = new Image(), self = this;
    im.onload = function () { if (self.artUrl === url) { self.art = im; self.artDisc = null; } };
    im.src = url;
  };
  Viz.prototype.mode = function (m) { if (MODES.indexOf(m) >= 0) this.modeName = m; return this.modeName; };
  Viz.prototype.nextMode = function () { return this.mode(MODES[(MODES.indexOf(this.modeName) + 1) % MODES.length]); };
  Viz.prototype.start = function () {
    var self = this;
    if (this.g && this.g.ctx.state === 'suspended') this.g.ctx.resume();
    cancelAnimationFrame(this.raf);
    var loop = function (t) {
      if (!t || t - self.lastDraw >= self.frameMs - 2) { self.lastDraw = t || 0; self.draw(); }
      self.raf = requestAnimationFrame(loop);
    };
    loop();
  };
  Viz.prototype.stop = function () { cancelAnimationFrame(this.raf); this.raf = 0; };

  /* Fill this.bars (0..1) from the source, with a fast rise and slow fall. */
  Viz.prototype.sample = function () {
    var target = this.target, i, playing = !this.audio.paused;
    var lv = this.opts.levels && playing ? this.opts.levels() : null;
    if (lv && lv.length) {
      var m = lv.length;
      for (i = 0; i < BARS; i++) {  // spread the bands over the bars
        var pos = (i / (BARS - 1)) * (m - 1), k = Math.floor(pos), f = pos - k;
        target[i] = (lv[k] * (1 - f) + lv[Math.min(m - 1, k + 1)] * f) / 255;
      }
    } else if (this.g && playing) {
      this.g.analyser.getByteFrequencyData(this.raw);
      this.g.analyser.getByteTimeDomainData(this.wave);
      var used = Math.floor(this.raw.length * 0.7);
      for (i = 0; i < BARS; i++) {  // log-ish spacing: more bars for the low end
        var a = Math.floor(Math.pow(i / BARS, 1.6) * used), b = Math.max(a + 1, Math.floor(Math.pow((i + 1) / BARS, 1.6) * used));
        var s = 0; for (var j = a; j < b; j++) s += this.raw[j];
        target[i] = s / (b - a) / 255;
      }
    } else {  // idle (paused, or no data yet): a slow, quiet swell
      this.t += 0.03;
      for (i = 0; i < BARS; i++) target[i] = 0.12 + 0.08 * Math.sin(this.t + i * 0.2);
    }
    for (i = 0; i < BARS; i++) {
      var v = target[i], cur = this.bars[i];
      this.bars[i] = v > cur ? cur + (v - cur) * 0.7 : cur * 0.86 + v * 0.14;  // rise fast, fall gently
    }
    var bass = (this.bars[0] + this.bars[1] + this.bars[2] + this.bars[3]) / 4;
    this.bass = this.bass * 0.7 + bass * 0.3;
  };

  Viz.prototype.prepare = function (w, h) {
    // Size-dependent pieces, made once per size: the glow and the round art.
    var x = this.c.getContext('2d');
    this.glow = x.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.55);
    this.glow.addColorStop(0, 'rgba(242,179,61,0.28)');
    this.glow.addColorStop(1, 'rgba(242,179,61,0)');
    this.artDisc = null;
  };
  Viz.prototype.disc = function (r) {
    if (this.artDisc && this.artDisc.width === Math.round(r * 2)) return this.artDisc;
    var d = document.createElement('canvas'); d.width = d.height = Math.max(2, Math.round(r * 2));
    var x = d.getContext('2d');
    x.beginPath(); x.arc(r, r, r, 0, Math.PI * 2); x.closePath(); x.clip();
    if (this.art) x.drawImage(this.art, 0, 0, d.width, d.height);
    else { x.fillStyle = '#19212c'; x.fillRect(0, 0, d.width, d.height); }
    if (this.art) this.artDisc = d;
    return d;
  };

  Viz.prototype.draw = function () {
    var c = this.c, dpr = (window.devicePixelRatio || 1) * this.scale;
    var w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr) || w !== this.w || h !== this.h) {
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); this.w = w; this.h = h;
      this.prepare(w, h);
    }
    var x = c.getContext('2d');
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.sample();
    x.globalAlpha = 1;
    x.fillStyle = '#0a0d11'; x.fillRect(0, 0, w, h);
    x.globalAlpha = 0.25 + this.bass * 0.75; x.fillStyle = this.glow; x.fillRect(0, 0, w, h);
    x.globalAlpha = 1;
    if (this.modeName === 'bars') this.drawBars(x, w, h);
    else if (this.modeName === 'wave') this.drawWave(x, w, h);
    else this.drawRadial(x, w, h);
  };

  Viz.prototype.drawRadial = function (x, w, h) {
    var cx = w / 2, cy = h / 2, r = Math.min(w, h) * 0.19 * (1 + this.bass * 0.18), reach = Math.min(w, h) * 0.24;
    x.lineCap = 'round';
    x.lineWidth = Math.max(3, (Math.PI * 2 * r / BARS) * 0.5);
    for (var i = 0; i < BARS * 2; i++) {  // mirrored: bass at the top and bottom
      var k = i < BARS ? i : BARS * 2 - 1 - i, v = this.bars[k];
      var a = (i / (BARS * 2)) * Math.PI * 2 - Math.PI / 2, len = 6 + v * reach;
      var ca = Math.cos(a), sa = Math.sin(a);
      x.strokeStyle = COLORS[k];
      x.beginPath();
      x.moveTo(cx + ca * (r + 8), cy + sa * (r + 8));
      x.lineTo(cx + ca * (r + 8 + len), cy + sa * (r + 8 + len));
      x.stroke();
    }
    x.drawImage(this.disc(r), cx - r, cy - r, r * 2, r * 2);
    x.strokeStyle = 'rgba(242,179,61,' + (0.35 + this.bass * 0.65).toFixed(2) + ')';
    x.lineWidth = 3; x.beginPath(); x.arc(cx, cy, r + 2, 0, Math.PI * 2); x.stroke();
  };

  Viz.prototype.drawBars = function (x, w, h) {
    var gap = w * 0.005, bw = (w * 0.86 - gap * (BARS - 1)) / BARS, left = w * 0.07, floor = h * 0.8;
    for (var i = 0; i < BARS; i++) {
      var bh = Math.max(4, this.bars[i] * h * 0.62), px = left + i * (bw + gap);
      x.fillStyle = COLORS[i]; x.fillRect(px, floor - bh, bw, bh);
      x.fillStyle = DIM[i]; x.fillRect(px, floor + 4, bw, bh * 0.25);  // reflection
    }
  };

  Viz.prototype.drawWave = function (x, w, h) {
    var n = 96, mid = h / 2, amp = h * 0.3, i, useWave = !!(this.g && !this.audio.paused);
    var pts = this.pts;
    for (i = 0; i < n; i++) {
      if (useWave) pts[i] = (this.wave[Math.floor(i / n * this.wave.length)] - 128) / 128;
      else {  // from the bars: a moving wave whose height follows the music
        var v = this.bars[Math.floor(i / n * BARS)];
        pts[i] = v * Math.sin(this.t * 3 + i * 0.35) * (0.6 + this.bass);
      }
    }
    this.t += 0.05;
    for (var pass = 0; pass < 2; pass++) {
      x.lineWidth = pass ? 3 : 10;
      x.strokeStyle = pass ? 'rgba(242,179,61,0.95)' : 'rgba(127,178,229,0.25)';
      x.beginPath();
      for (i = 0; i < n; i++) {
        var px = (i / (n - 1)) * w, py = mid + pts[i] * amp;
        if (i) x.lineTo(px, py); else x.moveTo(px, py);
      }
      x.stroke();
    }
  };

  window.PHViz = {
    modes: MODES,
    attach: function (canvas, audio, opts) { return new Viz(canvas, audio, opts); },
    supported: function () { return !!(window.AudioContext || window.webkitAudioContext); }
  };
})();

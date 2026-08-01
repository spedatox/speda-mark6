/* ═══════════════════════════════════════════════════════════════════════════
   LIQUID GLASS — real refraction on the SPEDA mark.

   The technique is the standard one (Apple's iOS 26 Liquid Glass, as recreated
   for the web): a `feDisplacementMap` warps the backdrop, driven by a
   displacement map whose RED channel encodes horizontal displacement and GREEN
   encodes vertical, with 128 as neutral. Feeding that through `backdrop-filter`
   bends whatever is behind the element instead of merely blurring it — which is
   the entire difference between glass and frosted plastic.

   The map is generated here at runtime rather than shipped as a PNG, because it
   has to match the logo's silhouette exactly: Path2D accepts the SVG path data
   directly, canvas blurs it, and a Sobel pass over the blurred alpha gives the
   signed edge normals. Displacement therefore peaks along the mark's edges and
   is neutral across its interior — which is how real glass behaves, since a
   flat pane only bends light where its surface turns.

   Chromium only supports SVG filters inside backdrop-filter. Everywhere else
   the CSS fallback (@supports in site.css) keeps a plain frosted blur, which
   degrades honestly rather than breaking.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  var glass = document.querySelector('[data-glass]');
  if (!glass) return;

  var srcPath = document.querySelector('[data-glass-path] path');
  if (!srcPath || typeof Path2D === 'undefined') return;

  // Chromium is the only engine that accepts an SVG filter as a backdrop-filter.
  if (!CSS.supports('backdrop-filter', 'url(#x)') &&
      !CSS.supports('-webkit-backdrop-filter', 'url(#x)')) return;

  var S = 512;                 // map resolution
  var EDGE = 26;               // blur radius: how far the refraction band reaches
  var GAIN = 19;               // how hard the gradient drives displacement

  function buildDisplacementMap(d) {
    var c = document.createElement('canvas');
    c.width = c.height = S;
    var ctx = c.getContext('2d', { willReadFrequently: true });

    // The mark, filled and blurred. The blur is what turns a hard silhouette
    // into a ramp, and the ramp is what the Sobel pass differentiates into
    // surface normals.
    ctx.filter = 'blur(' + EDGE + 'px)';
    ctx.fillStyle = '#fff';
    ctx.save();
    ctx.scale(S / 100, S / 100);          // the paths are authored on a 100x100 viewBox
    ctx.fill(new Path2D(d));
    ctx.restore();

    var src = ctx.getImageData(0, 0, S, S).data;
    var out = ctx.createImageData(S, S);
    var o = out.data;

    var at = function (x, y) {
      if (x < 0) x = 0; else if (x >= S) x = S - 1;
      if (y < 0) y = 0; else if (y >= S) y = S - 1;
      return src[(y * S + x) * 4 + 3];     // alpha carries the ramp
    };

    for (var y = 0; y < S; y++) {
      for (var x = 0; x < S; x++) {
        // Sobel — signed gradient, so the displacement points along the
        // surface normal instead of smearing uniformly in one direction.
        var gx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) -
                 (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
        var gy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) -
                 (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));

        var i = (y * S + x) * 4;
        o[i]     = Math.max(0, Math.min(255, 128 + (gx / 1020) * 127 * GAIN));
        o[i + 1] = Math.max(0, Math.min(255, 128 + (gy / 1020) * 127 * GAIN));
        o[i + 2] = 128;
        o[i + 3] = 255;
      }
    }

    ctx.putImageData(out, 0, 0);
    return c.toDataURL('image/png');
  }

  var mapUrl;
  try {
    mapUrl = buildDisplacementMap(srcPath.getAttribute('d'));
  } catch (e) {
    return;                     // leave the CSS fallback in place
  }

  var NS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.cssText = 'position:absolute;width:0;height:0;pointer-events:none';

  var filter = document.createElementNS(NS, 'filter');
  filter.setAttribute('id', 'speda-liquid-glass');
  filter.setAttribute('color-interpolation-filters', 'sRGB');
  // Operate across the whole element — refraction reaches past the edges.
  filter.setAttribute('x', '-20%');
  filter.setAttribute('y', '-20%');
  filter.setAttribute('width', '140%');
  filter.setAttribute('height', '140%');

  var feImage = document.createElementNS(NS, 'feImage');
  feImage.setAttribute('href', mapUrl);
  feImage.setAttribute('preserveAspectRatio', 'none');
  feImage.setAttribute('result', 'map');

  var feDisp = document.createElementNS(NS, 'feDisplacementMap');
  feDisp.setAttribute('in', 'SourceGraphic');
  feDisp.setAttribute('in2', 'map');
  feDisp.setAttribute('scale', '74');
  feDisp.setAttribute('xChannelSelector', 'R');
  feDisp.setAttribute('yChannelSelector', 'G');

  filter.appendChild(feImage);
  filter.appendChild(feDisp);
  svg.appendChild(filter);
  document.body.appendChild(svg);

  // Refract first, then a light blur and a lift in saturation — the blur alone
  // would flatten the warp it is meant to reveal, so it stays small.
  var value = 'url(#speda-liquid-glass) blur(1.5px) saturate(1.5) brightness(1.05)';
  glass.style.backdropFilter = value;
  glass.style.webkitBackdropFilter = value;
  glass.setAttribute('data-glass-active', '');
})();

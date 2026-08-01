/* ═══════════════════════════════════════════════════════════════════════════
   HERO MOTION — scroll-driven parallax on the field behind the glass.

   The motion is applied to `.mono-field`, which is a SIBLING of the mark, and
   never to the mark or any of its ancestors. That is not a stylistic choice:
   a transform forms a backdrop root, and a backdrop root leaves the mark's
   backdrop-filter with nothing to sample — which silently reduces the liquid
   glass to a flat grey shape. Moving the field instead keeps the refraction
   intact and is also the better effect, since what visibly changes is the
   colour bending THROUGH the glass rather than the glass itself sliding about.

   Driven by scroll position only. No pointer tracking.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  var field = document.querySelector('[data-field]');
  if (!field) return;

  var hero = field.closest('.hero') || field.parentElement;

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var current = 0, target = 0, ticking = false;

  function progress() {
    var rect = hero.getBoundingClientRect();
    var travel = rect.height || window.innerHeight;
    return Math.max(0, Math.min(1, -rect.top / travel));
  }

  function apply(p) {
    // Drift and swell. The scale is what makes the refracted colour visibly
    // move through the mark's edges as the page scrolls.
    var x = -34 * p;
    var y = 46 * p;
    var s = 1 + 0.24 * p;
    var r = 26 * p;
    field.style.transform =
      'translate3d(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px,0) rotate(' +
      r.toFixed(1) + 'deg) scale(' + s.toFixed(3) + ')';
  }

  function frame() {
    var delta = target - current;
    if (Math.abs(delta) < 0.0004) {
      current = target;
      apply(current);
      ticking = false;
      return;
    }
    current += delta * 0.12;
    apply(current);
    requestAnimationFrame(frame);
  }

  function onScroll() {
    target = progress();
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(frame);
    }
  }

  apply(0);
  onScroll();
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
})();

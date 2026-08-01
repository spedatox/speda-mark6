/* ═══════════════════════════════════════════════════════════════════════════
   THE SPEDA MARK IN GLASS — real 3D, real refraction.

   Why WebGL and not CSS: the target is an extruded solid with bevelled edges
   that light refracts THROUGH. CSS can fake a frosted pane (backdrop-filter)
   or fake depth (stacked layers), but it cannot do both at once — a transform
   forms a backdrop root and kills the backdrop sampling. So the only way to
   get a genuinely three-dimensional glass object is to render one.

   The material is `MeshPhysicalMaterial` with transmission: light passes
   through the body, bends by the index of refraction, and is tinted by
   absorption over distance (attenuationColor / attenuationDistance) rather
   than by painting the surface a colour. That distinction is what separates
   glass from coloured plastic — a thick part of the mark absorbs more and
   goes deeper blue, a thin part stays almost clear.

   Bevelled edges are not cosmetic either: they are where the specular
   highlights live, and they are most of why a moulded glass object reads as
   glass rather than as a flat extrusion.
   ═══════════════════════════════════════════════════════════════════════════ */

import * as THREE from 'three';
import { SVGLoader } from 'three/addons/loaders/SVGLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const mount = document.querySelector('[data-glass3d]');
const pathEl = document.querySelector('[data-glass-path] path');
if (mount && pathEl) init(mount, pathEl.getAttribute('d'));

function init(canvas, d) {
  const parent = canvas.parentElement;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,                       // the page background shows through
    powerPreference: 'high-performance'
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 2000);
  camera.position.set(0, 0, 205);

  // Transmission needs somewhere for the light to come FROM. Without an
  // environment the glass samples black and renders as a dark blob — which is
  // the single most common way this effect gets shipped broken.
  // Blur is kept low so the environment retains distinct bright/dark regions.
  // A heavily blurred probe averages to flat grey, and flat grey is exactly
  // what makes transmissive glass read as milky plastic — there is nothing
  // sharp left for the bevels to catch.
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.02).texture;

  // A lit backdrop INSIDE the scene. This is the piece that is easy to miss:
  // transmission samples the 3D scene, not the DOM, so a glow painted behind
  // the canvas in CSS is never refracted — the glass just renders as a flat
  // pastel solid. Putting the colour on a plane in the scene is what gives the
  // body something to bend, and it is the difference between a glass object
  // and a shape filled with light blue.
  const grad = document.createElement('canvas');
  grad.width = grad.height = 1024;
  const gx = grad.getContext('2d');
  // Left transparent on purpose: an opaque base would render as a coloured
  // plate filling the hero. Only the blobs are drawn, so the plane fades to
  // nothing at its edges and reads as a glow behind the mark.
  // Pastel and multi-hue rather than saturated blue. The body is near-clear,
  // so whatever is on this plane IS the colour seen through the mark — three
  // saturated blues behind it is what previously made the object read as one
  // solid blue lump. Spread hues give the thin-film sheen something to work
  // against, which is where the holographic shift comes from.
  // Every blob must fall entirely inside the texture. A gradient that runs past
  // the edge is cut off square, and because this plane sits behind the mark
  // that straight cut shows through as a rectangle floating in the hero — which
  // instantly reads as a card behind the logo rather than open space.
  const blobs = [
    [370, 340, 290, 'rgba(120,215,245,0.60)'],   // cyan
    [700, 400, 260, 'rgba(196,150,250,0.50)'],   // violet
    [520, 720, 275, 'rgba(110,175,255,0.52)'],   // deep blue
    [760, 700, 200, 'rgba(255,170,205,0.30)'],   // rose, for the warm shift
    [300, 660, 200, 'rgba(150,245,215,0.28)']    // mint, for the cool shift
  ];
  gx.globalCompositeOperation = 'lighter';
  for (const [x, y, r, col] of blobs) {
    const g = gx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, col);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    gx.fillStyle = g;
    gx.fillRect(0, 0, 1024, 1024);
  }
  const gradTex = new THREE.CanvasTexture(grad);
  gradTex.colorSpace = THREE.SRGBColorSpace;
  const backdrop = new THREE.Mesh(
    new THREE.PlaneGeometry(430, 430),
    new THREE.MeshBasicMaterial({ map: gradTex, transparent: true, opacity: 0.9, depthWrite: false })
  );
  backdrop.position.z = -120;
  scene.add(backdrop);

  // Two hard lights purely for the bevel specular — the sharp catches along
  // the edges that sell the material.
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(-1, 1.4, 1.1);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xbfe6ff, 1.5);
  rim.position.set(1.3, -0.6, -0.9);
  scene.add(rim);
  // A low warm fill so faces turned away from both lights settle to a soft
  // grey rather than crushing to black. Bevels reading as hard black wedges is
  // the other half of why the object looked moulded rather than transparent.
  const fill = new THREE.DirectionalLight(0xffe9d8, 0.75);
  fill.position.set(0.2, -1.1, 0.9);
  scene.add(fill);

  // ── Geometry: the SVG path, extruded and bevelled ────────────────────────
  const svgText =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">` +
    `<path fill="#000" fill-rule="evenodd" d="${d}"/></svg>`;

  const paths = new SVGLoader().parse(svgText).paths;
  const shapes = [];
  for (const p of paths) shapes.push(...SVGLoader.createShapes(p));

  const geometry = new THREE.ExtrudeGeometry(shapes, {
    depth: 17,
    bevelEnabled: true,
    // Fat, many-segment bevels: this is where every specular highlight lives,
    // and it is most of why moulded glass reads as glass instead of as a slab.
    bevelThickness: 3.2,
    bevelSize: 2.6,
    bevelOffset: 0,
    bevelSegments: 14,
    curveSegments: 32
  });

  // SVG's Y axis points down; flip it, then centre the mark on the origin so
  // it rotates about itself instead of swinging around a corner.
  geometry.scale(1, -1, 1);
  geometry.computeBoundingBox();
  const bb = geometry.boundingBox;
  geometry.translate(
    -(bb.max.x + bb.min.x) / 2,
    -(bb.max.y + bb.min.y) / 2,
    -(bb.max.z + bb.min.z) / 2
  );
  geometry.computeVertexNormals();

  const material = new THREE.MeshPhysicalMaterial({
    color: 0xffffff,
    metalness: 0,
    // A touch of roughness, not a mirror. Perfectly polished glass reads as
    // hard candy; real moulded glass scatters just enough to look soft.
    roughness: 0.04,
    transmission: 1,
    // Thickness drives how hard the body bends what is behind it. Too low and
    // the mark is a clear pane with nothing happening inside it; this is high
    // enough that the backdrop visibly warps through the strokes.
    thickness: 26,
    ior: 1.46,

    // Absorption, not surface paint — but only just enough to be perceptible.
    // The previous value (a saturated blue over a short distance) meant every
    // thick section went opaque teal, which is the whole reason the object
    // read as moulded acrylic. Near-white over a long distance keeps the body
    // essentially clear and lets the backdrop supply the colour instead.
    attenuationColor: new THREE.Color(0xa9d8f5),
    attenuationDistance: 85,

    // Thin-film interference — the actual physics behind the pastel rainbow
    // sheen on the reference. This is a real optical term in the material, not
    // a colour ramp painted on: hue shifts with viewing angle and film
    // thickness, so it moves as the mark rotates. Without it, glass this clear
    // just looks like a clear plastic blank.
    iridescence: 0.85,
    iridescenceIOR: 1.33,
    iridescenceThicknessRange: [140, 520],

    clearcoat: 1,
    clearcoatRoughness: 0.05,
    // Held back deliberately. Pushed higher, environment reflection overwhelms
    // transmission and the mark goes chalky white — bright, but opaque, which
    // is the opposite of the goal. Glass has to be seen THROUGH.
    envMapIntensity: 1.7,
    specularIntensity: 1
  });

  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  // Frame the mark to the canvas.
  const size = new THREE.Vector3();
  geometry.boundingBox.getSize(size);
  const fit = Math.max(size.x, size.y);

  function resize() {
    const w = parent.clientWidth;
    const h = parent.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Keep the mark a constant fraction of the shorter side at any viewport.
    const vFov = (camera.fov * Math.PI) / 180;
    // 1.24 leaves the mark at roughly 62% of the frame's shorter side. Tighter
    // than this and the bevel highlights touch the canvas edge, which reads as
    // a cropping mistake rather than as scale.
    const dist = (fit / 1.24) / Math.tan(vFov / 2);
    camera.position.z = dist / Math.min(1, camera.aspect / 1);
    camera.updateProjectionMatrix();
  }

  // ── Motion: scroll only ──────────────────────────────────────────────────
  const REST = { y: -0.42, x: 0.16 };
  const END = { y: 0.52, x: -0.14 };
  const hero = parent.closest('.hero') || parent;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  let current = 0, target = 0;

  function progress() {
    const r = hero.getBoundingClientRect();
    return Math.max(0, Math.min(1, -r.top / (r.height || innerHeight)));
  }

  function render() {
    mesh.rotation.y = REST.y + (END.y - REST.y) * current;
    mesh.rotation.x = REST.x + (END.x - REST.x) * current;
    renderer.render(scene, camera);
  }

  let running = true;
  function loop() {
    if (!running) return;
    current += (target - current) * 0.09;
    render();
    requestAnimationFrame(loop);
  }

  addEventListener('scroll', () => { target = progress(); }, { passive: true });
  addEventListener('resize', () => { resize(); render(); }, { passive: true });

  resize();
  target = reduced ? 0 : progress();
  current = target;
  render();
  if (!reduced) requestAnimationFrame(loop);

  document.addEventListener('visibilitychange', () => {
    running = !document.hidden;
    if (running && !reduced) requestAnimationFrame(loop);
  });

  canvas.setAttribute('data-glass3d-ready', '');
  // Expose for render capture during development.
  window.__speda = { renderer, scene, camera, mesh, material, render, resize };
}

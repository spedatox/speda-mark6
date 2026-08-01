import { useEffect, useRef } from 'react'
import * as THREE from 'three'

/**
 * The orb — a real 3D assembly, rebuilt from the reference.
 *
 * The previous version was a single 2D fragment shader drawing signed distance
 * fields in screen space. That approach cannot produce this object: a geodesic
 * core seen in perspective, shells that occlude each other, ring segments that
 * foreshorten as they turn away. Those read as depth precisely because they ARE
 * depth, and faking them flat is what made the old orb look like a diagram.
 *
 * Layers, outside in — each one is in the reference and each one does a job:
 *
 *   particles   a thin dust shell, so the object sits IN something
 *   membrane    two crumpled low-poly shells, the jagged silhouette
 *   rings       segmented data bands — the spectrum lives here
 *   teeth       a dense fine-tick annulus, read as texture not as detail
 *   core        the geodesic wireframe sphere with lit vertices
 *
 * ── On sharpness ───────────────────────────────────────────────────────────
 * The old orb pixelated for an arithmetic reason worth recording, because it is
 * easy to reintroduce: it was drawn into a backing store the size of its CSS box
 * (400×400 at dpr 1), while its stroke half-widths were ~0.0011 in a space where
 * one unit spanned 200px — sub-pixel lines, which alias no matter how good the
 * antialiasing is. Nothing here is authored in normalized units; the buffer is
 * supersampled to at least 2× the CSS size, and MSAA runs on top. Both matter:
 * MSAA alone does not help geometry finer than the sample grid.
 *
 * ── On glow ────────────────────────────────────────────────────────────────
 * No postprocessing bloom. UnrealBloomPass does not survive a transparent
 * canvas, and this orb is composited over the app's gradient — the previous
 * build already lost a day to alpha artefacts for the same reason. Glow is
 * built instead: additive blending everywhere, plus a soft halo sprite behind
 * the core, so overlapping elements accumulate into bright centres the way an
 * emissive display actually does.
 */

export type OrbState = 'idle' | 'thinking' | 'speaking' | 'listening'

/** Angular resolution of the audio-driven deformation. Also the number of
 *  segments in the outer ring, so one band owns exactly one segment. */
const BANDS = 48

interface Props {
  state: OrbState
  /** The AGENT's output level, 0..1. */
  amplitude: () => number
  /** Fills `out` with the agent's spectrum, 0..1 per band. */
  spectrum?: (out: Float32Array) => void
  /** The OWNER's mic level, 0..1. The orb answers to both voices — being heard
   *  has to look different from being talked at, or the mic is a lie. */
  inputLevel?: () => number
  /** Whoever is speaking. Only used to re-read the theme accent when the agent
   *  changes — the colour itself still comes from CSS, never from here. */
  agentId?: string
  size?: number
}

/* ── Noise ────────────────────────────────────────────────────────────────────
 * Ashima/Gustavson simplex, the standard GLSL implementation. Used to crumple
 * the membrane and to breathe the core. Evaluated in the vertex shader from
 * POSITION alone, so both ends of a wireframe edge always agree on where they
 * moved to — displacing per-vertex from anything else tears the mesh open. */
const SIMPLEX = `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(
      i.z+vec4(0.0,i1.z,i2.z,1.0))
    + i.y+vec4(0.0,i1.y,i2.y,1.0))
    + i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`

/** Shared by every custom material, so one frame's uniform write moves the
 *  whole assembly and the layers can never disagree about the beat. */
interface OrbUniforms {
  uTime: { value: number }
  uAmp: { value: number }
  uSpeak: { value: number }
  uThink: { value: number }
  uListen: { value: number }
  uAccent: { value: THREE.Color }
}

/* ── Displacement, shared by membrane and core ─────────────────────────────
 * Two octaves: a slow large-scale fold that gives the silhouette its lobes, and
 * a faster fine one that keeps the surface from looking inflated. `uListen` is
 * a separate term rather than more amplitude — when the OWNER speaks the shell
 * draws IN and tightens, which is a visibly different gesture from the outward
 * bloom of the agent speaking. One orb, two voices, told apart at a glance. */
const DISPLACE = `
float displace(vec3 p, float t, float amp, float speak, float think, float listen) {
  vec3 n = normalize(p);
  float slow = snoise(n * 1.6 + vec3(0.0, 0.0, t * 0.16));
  float fine = snoise(n * 4.3 - vec3(t * 0.22, 0.0, 0.0));
  float idle = slow * 0.055 + fine * 0.018;
  float live = (slow * 0.16 + fine * 0.09) * amp * speak;
  float draw = -(0.06 + 0.05 * abs(fine)) * listen;
  return idle * (1.0 + think * 0.5) + live + draw;
}`

/** Round, soft-edged dot. Points render as squares without one, and a field of
 *  tiny squares is exactly the "pixelated" read this rebuild exists to remove. */
function dotTexture(): THREE.Texture {
  const s = 64
  const c = document.createElement('canvas')
  c.width = c.height = s
  const ctx = c.getContext('2d')!
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.35, 'rgba(255,255,255,0.55)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, s, s)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

/** Read the theme accent, so the orb matches whichever agent is speaking
 *  without the colour ever being hardcoded here. */
function accentOf(el: HTMLElement): THREE.Color {
  const raw = getComputedStyle(el).getPropertyValue('--hb-accent-rgb').trim()
  const p = raw.split(',').map(s => parseFloat(s))
  if (p.length !== 3 || p.some(Number.isNaN)) return new THREE.Color(0.28, 0.72, 0.82)
  return new THREE.Color(p[0] / 255, p[1] / 255, p[2] / 255)
}

export default function VoiceOrb({
  state, amplitude, spectrum, inputLevel, agentId, size = 400,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  // Held in a ref and uploaded every frame rather than captured at init: read
  // once, the orb stays SPEDA's cyan no matter which agent is actually talking.
  const accentRef = useRef<THREE.Color>(new THREE.Color(0.21, 0.67, 0.79))
  useEffect(() => {
    if (mountRef.current) accentRef.current = accentOf(mountRef.current)
  }, [agentId])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return

    /* ── Renderer ─────────────────────────────────────────────────────────
     * alpha:true — the orb composites over the app's gradient.
     * antialias:true — MSAA on the geometry edges.
     * Supersampling on TOP of MSAA, never instead of it: the wireframe is
     * finer than the MSAA sample grid, so the extra resolution is what
     * actually resolves it. See the note at the top of this file. */
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, premultipliedAlpha: false })
    } catch {
      // No WebGL. The mode still works — it just has no orb.
      return
    }
    renderer.setClearColor(0x000000, 0)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    mount.appendChild(renderer.domElement)
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'

    const scene = new THREE.Scene()
    // A narrow FOV keeps the perspective honest — wide-angle on an object this
    // small reads as a fisheye distortion rather than as depth.
    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100)
    // Distance is DERIVED, not chosen by eye. The visible half-height at the
    // origin is tan(fov/2) * distance, so the camera has to stand far enough
    // back to contain the outermost thing in the scene — the membrane at 1.52
    // displacing to ~2.35, and the particle shell at ~2.4. At the 4.15 this
    // first shipped with, half-height was 1.19 and most of the orb was outside
    // the frustum. Change OUTER_EXTENT if a layer is ever pushed further out.
    const OUTER_EXTENT = 2.55
    camera.position.set(0, 0.55, OUTER_EXTENT / Math.tan((32 / 2) * Math.PI / 180))
    camera.lookAt(0, 0, 0)

    const uniforms: OrbUniforms = {
      uTime: { value: 0 },
      uAmp: { value: 0 },
      uSpeak: { value: 0 },
      uThink: { value: 0 },
      uListen: { value: 0 },
      uAccent: { value: accentRef.current.clone() },
    }

    const disposables: { dispose: () => void }[] = []
    const track = <T extends { dispose: () => void }>(x: T): T => { disposables.push(x); return x }

    const root = new THREE.Group()
    // Tilted so the rings are seen at an angle. Face-on they collapse into flat
    // circles and every trace of the third dimension goes with them — and a
    // SMALL tilt is the same as none: at the 0.34 rad this first shipped with,
    // a ring foreshortened by cos(0.34) = 0.94, a 6% squash that reads as a
    // perfect circle. It has to be big enough to see.
    // A SMALL fixed tilt — just enough that the segments show a little of their
    // thickness. The reference is close to face-on, and a hard tilt would make
    // the dials read as tipped turntables rather than as instrument faces.
    root.rotation.x = -0.16
    root.rotation.z = 0.05
    scene.add(root)

    /* ── Core: geodesic wireframe sphere ─────────────────────────────────── */
    const CORE_R = 0.62
    const coreGeo = track(new THREE.IcosahedronGeometry(CORE_R, 3))
    const coreMat = track(new THREE.ShaderMaterial({
      uniforms: uniforms as unknown as Record<string, THREE.IUniform>,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      vertexShader: `
        uniform float uTime, uAmp, uSpeak, uThink, uListen;
        varying float vRim;
        ${SIMPLEX}
        ${DISPLACE}
        void main() {
          vec3 n = normalize(position);
          vec3 p = position + n * displace(position, uTime, uAmp, uSpeak, uThink, uListen) * 0.85;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          // Facing away from the camera = dimmer. This is what makes a
          // wireframe sphere read as a sphere and not as a flat net: without
          // it the far side is exactly as bright as the near one.
          vec3 wn = normalize(normalMatrix * n);
          vRim = clamp(dot(wn, vec3(0.0, 0.0, 1.0)), 0.0, 1.0);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uAccent; uniform float uAmp, uListen;
        varying float vRim;
        void main() {
          // Far side kept faintly visible rather than culled — the reference's
          // core is translucent, and you can see its back through it.
          float f = mix(0.16, 1.0, vRim);
          vec3 col = mix(uAccent, vec3(1.0), 0.34 + uAmp * 0.22) * f;
          gl_FragColor = vec4(col * (0.52 + uAmp * 0.55 + uListen * 0.30), f * 0.85);
        }`,
    }))
    const coreWire = new THREE.LineSegments(track(new THREE.WireframeGeometry(coreGeo)), coreMat)
    root.add(coreWire)

    // Lit vertices. In the reference only some of them are bright, so which
    // ones glow is driven by a per-point random seed against the beat — all of
    // them lighting at once reads as a mesh, a scatter reads as activity.
    const corePos = coreGeo.getAttribute('position')
    const seeds = new Float32Array(corePos.count)
    for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random()
    const dotGeo = track(new THREE.BufferGeometry())
    dotGeo.setAttribute('position', corePos.clone())
    dotGeo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1))
    const dotTex = track(dotTexture())
    const dotMat = track(new THREE.ShaderMaterial({
      uniforms: { ...uniforms, uMap: { value: dotTex }, uScale: { value: 1 } } as unknown as Record<string, THREE.IUniform>,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      vertexShader: `
        uniform float uTime, uAmp, uSpeak, uThink, uListen, uScale;
        attribute float aSeed;
        varying float vGlow;
        ${SIMPLEX}
        ${DISPLACE}
        void main() {
          vec3 n = normalize(position);
          vec3 p = position + n * displace(position, uTime, uAmp, uSpeak, uThink, uListen) * 0.85;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          float beat = sin(uTime * 1.7 + aSeed * 43.0) * 0.5 + 0.5;
          vGlow = pow(beat, 3.0) * (0.35 + uAmp * 0.9) + 0.06;
          gl_Position = projectionMatrix * mv;
          // Base size is in WORLD UNITS, converted to device pixels by uScale
          // (which carries the projection term) and divided by view depth so a
          // point does not swell as it rotates toward the camera. Writing these
          // as if they were pixels and ALSO multiplying by uScale is how this
          // first shipped, and it produced 500px points that saturated the
          // whole canvas to white. The clamp is the guard against ever doing
          // that silently again.
          gl_PointSize = clamp((0.030 + vGlow * 0.040) * uScale / -mv.z, 1.0, 48.0);
        }`,
      fragmentShader: `
        uniform vec3 uAccent; uniform sampler2D uMap;
        varying float vGlow;
        void main() {
          float a = texture2D(uMap, gl_PointCoord).a;
          vec3 col = mix(uAccent, vec3(1.0), 0.55);
          gl_FragColor = vec4(col * vGlow * 2.4, a * vGlow * 1.6);
        }`,
    }))
    const coreDots = new THREE.Points(dotGeo, dotMat)
    root.add(coreDots)

    /* ── Ring segments ────────────────────────────────────────────────────
     * The spectrum's home. One band owns one segment, so a formant moving up
     * shows as a lit arc travelling around the ring — a ring that only breathes
     * in and out is a progress spinner, and this is the difference. */
    interface Ring {
      /** Each ring gets its OWN group, tilted differently and spun on its own
       *  axis. Sharing one plane is what made these read as a flat dashed
       *  circle: coplanar rings turning together are indistinguishable from a
       *  2D wheel however fast they go. */
      group: THREE.Group
      mesh: THREE.InstancedMesh
      count: number
      radius: number
      /** Wheel spin: the ring turns about its OWN normal, in its own plane,
       *  like a dial. The two rings take opposite signs.
       *
       *  This is deliberately the only rotation a ring gets. Anything that
       *  changes the ring's PLANE — precession, or a continuous rotation on an
       *  ancestor — turns the dials into a tumbling globe, which is not what
       *  this is. The rings are instrumentation; they read as instrumentation
       *  because they stay flat-on and only turn. */
      spin: number
      /** Which spectrum bands this ring reads, as a fraction of the range.
       *  The inner ring takes the bottom end, where speech energy actually is. */
      from: number
      to: number
      base: number
      /** Per-segment static variation, so the ring is not a uniform grey band
       *  at rest. The reference's segments differ in brightness even when
       *  nothing is happening — that unevenness is most of its character. */
      seeds: Float32Array
    }
    const rings: Ring[] = []
    const dummy = new THREE.Object3D()

    const makeRing = (
      count: number, radius: number, w: number, h: number, thick: number,
      spin: number, from: number, to: number, base: number,
    ): Ring => {
      const geo = track(new THREE.BoxGeometry(w, h, thick))
      const mat = track(new THREE.MeshBasicMaterial({
        transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.95,
      }))
      const mesh = new THREE.InstancedMesh(geo, mat, count)
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
      // Allocate the colour buffer up front; setColorAt on an unallocated
      // instanceColor throws on first call in recent three versions.
      mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3)
      const group = new THREE.Group()
      group.add(mesh)
      root.add(group)
      const seeds = new Float32Array(count)
      for (let i = 0; i < count; i++) seeds[i] = Math.random()
      const ring: Ring = { group, mesh, count, radius, spin, from, to, base, seeds }
      rings.push(ring)
      return ring
    }

    // Outer band: coarse, widely spaced blocks — the reference's most legible
    // element, and the one carrying the top of the spectrum. Turns clockwise…
    makeRing(BANDS, 1.26, 0.105, 0.125, 0.032, 0.30, 0.32, 1.0, 0.30)
    // …and the inner band counter-clockwise, a little faster. Opposed dials on
    // a shared axis: the contrast between the two is the motion you read.
    makeRing(36, 1.00, 0.090, 0.082, 0.026, -0.42, 0.0, 0.42, 0.26)

    /* ── Teeth: the dense fine annulus ────────────────────────────────────
     * Read as texture, not as detail. Deliberately static and dim — it is the
     * still surface the moving rings are measured against, and animating it
     * would turn the middle of the frame into noise. */
    const TEETH = 168
    const teethGeo = track(new THREE.BoxGeometry(0.012, 0.085, 0.010))
    const teethMat = track(new THREE.MeshBasicMaterial({
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.5,
    }))
    const teeth = new THREE.InstancedMesh(teethGeo, teethMat, TEETH)
    teeth.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(TEETH * 3), 3)
    for (let i = 0; i < TEETH; i++) {
      const a = (i / TEETH) * Math.PI * 2
      dummy.position.set(Math.cos(a) * 0.855, Math.sin(a) * 0.855, 0)
      dummy.rotation.set(0, 0, a + Math.PI / 2)
      dummy.scale.set(1, 0.55 + 0.45 * ((i * 7) % 5) / 4, 1)
      dummy.updateMatrix()
      teeth.setMatrixAt(i, dummy.matrix)
    }
    teeth.instanceMatrix.needsUpdate = true
    root.add(teeth)

    /* ── Membrane: the crumpled outer shells ──────────────────────────────
     * Two layers at slightly different radii and noise phases. One shell reads
     * as a balloon; two overlapping ones produce the reference's folded, faceted
     * silhouette where the layers cross. Low subdivision on purpose — the
     * facets ARE the look, and smoothing them out loses it. */
    const membraneMat = (radius: number, phase: number, edge: boolean) => track(new THREE.ShaderMaterial({
      uniforms: { ...uniforms, uPhase: { value: phase }, uR: { value: radius } } as unknown as Record<string, THREE.IUniform>,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
      vertexShader: `
        uniform float uTime, uAmp, uSpeak, uThink, uListen, uPhase, uR;
        varying float vRim;
        varying float vFold;
        ${SIMPLEX}
        ${DISPLACE}

        void main() {
          vec3 n = normalize(position);
          // Fine crumple on top of the broad fold, so a dense mesh has
          // something to crease along instead of reading as a smooth ball.
          float d = displace(position + vec3(uPhase), uTime + uPhase, uAmp, uSpeak, uThink, uListen) * 2.6
                  + snoise(n * 7.5 + vec3(uPhase)) * 0.055;
          vec3 p = position + n * d;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);

          // Shading uses the SPHERE's normal, which cannot see the crumple —
          // so the fold amount is handed to the fragment stage separately and
          // brightens the creases directly. The correct fix is a displaced
          // surface normal, but that needs finite differences: three
          // displace() calls per vertex, which inlines simplex noise nine
          // times, spills registers, and drops this from 60fps to 1.6. Fold
          // brightness costs nothing and buys most of the same read.
          vec3 wn = normalize(normalMatrix * n);
          vRim = pow(1.0 - abs(dot(wn, normalize(-mv.xyz))), 1.7);
          vFold = d / uR;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uAccent; uniform float uAmp, uListen;
        varying float vRim;
        varying float vFold;
        void main() {
          float e = ${edge ? '1.0' : '0.34'};
          // Outward folds catch light, inward ones fall away — the crease term
          // is what makes the crumple visible at all, since the fresnel above
          // is computed from a normal that knows nothing about it.
          float crease = clamp(vFold * 2.2, -1.0, 1.0);
          float lit = vRim * 0.80 + max(0.0, crease) * 0.62 + 0.06;
          float i = lit * (0.80 + uAmp * 0.85 + uListen * 0.40) * e;
          // Edges push toward white at their brightest. Pure accent × a scalar
          // never leaves the hue, so a shell that is "brighter teal" still
          // reads as tinted fog — the white point is what makes it read as lit.
          vec3 c = mix(uAccent, vec3(1.0), clamp(i * ${edge ? '0.40' : '0.15'}, 0.0, 0.5));
          // Per-edge intensity is tuned AGAINST the subdivision level, but NOT
          // in inverse proportion to it. The naive correction — divide by the
          // density increase — is wrong here: the fresnel term concentrates
          // light at the silhouette, where the extra edges land on pixels that
          // were already lit, so they add far less than their count suggests.
          // Dividing by 16 made the shell darker than the sparse version it
          // replaced. These are set by eye against the reference instead.
          gl_FragColor = vec4(c * i * ${edge ? '2.7' : '1.2'}, i * ${edge ? '0.88' : '0.36'});
        }`,
    }))

    const shells: THREE.Object3D[] = []
    const addShell = (radius: number, detail: number, phase: number) => {
      const geo = track(new THREE.IcosahedronGeometry(radius, detail))
      // Faces carry the volume, edges carry the fold. Both, because either
      // alone is half the object: faces only is fog, edges only is a diagram.
      const faces = new THREE.Mesh(geo, membraneMat(radius, phase, false))
      const wire = new THREE.LineSegments(track(new THREE.WireframeGeometry(geo)), membraneMat(radius, phase, true))
      root.add(faces); root.add(wire)
      shells.push(faces, wire)
    }
    // Subdivision level is the mesh DENSITY, and it goes up fast: an
    // icosahedron has 20·4^detail faces, so 2 = 320 and 4 = 5120. At 2 the
    // triangles projected to ~20px each and the shell read as a sparse cage
    // rather than as a surface. Note the density is paid for twice — once in
    // the faces, once in the wireframe's 3 edges per face — so this is the
    // knob to turn back first if the orb ever costs too much.
    addShell(1.52, 4, 0.0)
    addShell(1.40, 4, 11.7)

    /* ── Particles ────────────────────────────────────────────────────────
     * A dust shell so the orb sits IN something rather than floating on a flat
     * background. Distributed by cube-root of a uniform sample, which is what
     * puts them EVENLY through the shell — a naive radius picks crowds them all
     * at the centre. */
    const PARTICLES = 1400
    const pPos = new Float32Array(PARTICLES * 3)
    const pSeed = new Float32Array(PARTICLES)
    for (let i = 0; i < PARTICLES; i++) {
      const u = Math.random() * 2 - 1
      const th = Math.random() * Math.PI * 2
      const r = 1.45 + Math.cbrt(Math.random()) * 0.75
      const s = Math.sqrt(1 - u * u)
      pPos[i * 3] = Math.cos(th) * s * r
      pPos[i * 3 + 1] = Math.sin(th) * s * r
      pPos[i * 3 + 2] = u * r
      pSeed[i] = Math.random()
    }
    const partGeo = track(new THREE.BufferGeometry())
    partGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3))
    partGeo.setAttribute('aSeed', new THREE.BufferAttribute(pSeed, 1))
    const partMat = track(new THREE.ShaderMaterial({
      uniforms: { ...uniforms, uMap: { value: dotTex }, uScale: { value: 1 } } as unknown as Record<string, THREE.IUniform>,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
      vertexShader: `
        uniform float uTime, uAmp, uSpeak, uListen, uScale;
        attribute float aSeed;
        varying float vG;
        void main() {
          vec3 p = position;
          // Drift along the position's own axis so the cloud stirs without any
          // of it escaping the shell.
          p += normalize(p) * sin(uTime * 0.5 + aSeed * 30.0) * (0.05 + uAmp * 0.16 * uSpeak);
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          float tw = sin(uTime * 2.1 + aSeed * 61.0) * 0.5 + 0.5;
          vG = (0.22 + pow(tw, 4.0) * 0.75) * (0.70 + uAmp * 0.9 + uListen * 0.5);
          gl_Position = projectionMatrix * mv;
          // World units, same contract as the core dots above.
          gl_PointSize = clamp((0.013 + tw * 0.016) * uScale / -mv.z, 1.0, 24.0);
        }`,
      fragmentShader: `
        uniform vec3 uAccent; uniform sampler2D uMap;
        varying float vG;
        void main() {
          float a = texture2D(uMap, gl_PointCoord).a;
          gl_FragColor = vec4(mix(uAccent, vec3(1.0), 0.22) * vG * 2.6, a * vG);
        }`,
    }))
    const particles = new THREE.Points(partGeo, partMat)
    root.add(particles)

    /* ── Halo behind the core ─────────────────────────────────────────────
     * Stands in for the bloom this cannot afford to postprocess. Billboarded,
     * so it stays a pool of light behind the core from any angle. */
    const haloMat = track(new THREE.SpriteMaterial({
      map: dotTex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.5,
    }))
    const halo = new THREE.Sprite(haloMat)
    halo.scale.setScalar(2.4)
    root.add(halo)

    /* ── Sizing ───────────────────────────────────────────────────────────
     * Read the ratio EVERY frame, never once at mount: page zoom changes it,
     * and a backing store pinned to the value captured at mount is half of why
     * the old orb went blocky. The other half is the 2× floor — at dpr 1 a
     * 400px canvas gives the wireframe nothing to land on. */
    const applySize = () => {
      // Measured from the DOM, never from the `size` prop — that is deliberate.
      // The box is CSS-animated when a reply starts, so its real size changes
      // every frame during the transition, and reading the prop would rebuild
      // the whole scene (it is an effect dependency) instead of just resizing.
      const w = mount.clientWidth
      const h = mount.clientHeight
      if (!w || !h) return
      const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 2), 3)
      renderer.setPixelRatio(dpr)
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      // World-units → device-pixels for gl_PointSize. Two terms, both required:
      // half the BUFFER height (so dots keep their apparent size when
      // supersampling changes), and the projection's vertical scale
      // (elements[5] = 1/tan(fov/2)), without which the conversion is wrong by
      // whatever the field of view happens to be.
      const scale = h * dpr * 0.5 * camera.projectionMatrix.elements[5]
      ;(dotMat.uniforms.uScale as THREE.IUniform).value = scale
      ;(partMat.uniforms.uScale as THREE.IUniform).value = scale
    }
    applySize()
    const ro = new ResizeObserver(applySize)
    ro.observe(mount)

    /* ── Loop ─────────────────────────────────────────────────────────────── */
    const bands = new Float32Array(BANDS)
    const smooth = new Float32Array(BANDS)
    const col = new THREE.Color()
    // Hoisted: allocating a Color per segment per frame is ~4000 objects a
    // second handed straight to the collector.
    const WHITE = new THREE.Color(1, 1, 1)
    let amp = 0, speak = 0, think = 0, listen = 0
    let raf = 0
    const t0 = performance.now()

    const frame = () => {
      raf = requestAnimationFrame(frame)
      applySize()
      const t = (performance.now() - t0) / 1000
      const st = stateRef.current

      // Attack fast, release slow — consonants have to register, but nothing
      // may flicker in the gaps between words.
      const agentAmp = st === 'speaking' ? amplitude() : 0
      const ownAmp = inputLevel ? inputLevel() : 0
      const target = Math.max(agentAmp, ownAmp)
      amp += (target - amp) * (target > amp ? 0.4 : 0.07)

      // Eased mode weights. Snapping between states is the single most
      // artificial-looking thing an orb can do.
      speak += ((st === 'speaking' ? 1 : 0) - speak) * 0.06
      think += ((st === 'thinking' ? 1 : 0) - think) * 0.05
      listen += ((st === 'listening' ? 1 : 0) - listen) * 0.07

      if (spectrum && st === 'speaking') spectrum(bands)
      else bands.fill(0)
      for (let i = 0; i < BANDS; i++) {
        smooth[i] += (bands[i] - smooth[i]) * (bands[i] > smooth[i] ? 0.5 : 0.1)
      }

      uniforms.uTime.value = t
      uniforms.uAmp.value = amp
      uniforms.uSpeak.value = speak
      uniforms.uThink.value = think
      uniforms.uListen.value = listen
      uniforms.uAccent.value.copy(accentRef.current)

      // Rings: per-segment length and brightness from its own band. The ORBIT
      // is the group's job (below) — doing it here as well would fight the
      // tilt and flatten the ring back into its own plane.
      for (const ring of rings) {
        // Wheel spin, and nothing else. Rotation about the ring's own normal
        // leaves its plane alone, which is exactly the point — the dials stay
        // flat-on to the viewer and simply turn.
        ring.group.rotation.z = t * ring.spin
        for (let i = 0; i < ring.count; i++) {
          const frac = i / ring.count
          const bi = Math.min(
            BANDS - 1,
            Math.floor((ring.from + (ring.to - ring.from) * frac) * BANDS),
          )
          const v = smooth[bi]
          const a = frac * Math.PI * 2
          dummy.position.set(Math.cos(a) * ring.radius, Math.sin(a) * ring.radius, 0)
          dummy.rotation.set(0, 0, a + Math.PI / 2)
          // Grows outward along the ring's radius, not uniformly — a segment
          // that scales in every direction reads as a blob, one that extends
          // radially reads as a bar on a meter.
          dummy.scale.set(1, 1 + v * 2.6, 1)
          dummy.updateMatrix()
          ring.mesh.setMatrixAt(i, dummy.matrix)

          // Static seed + a slow travelling wave, so the band is uneven at rest
          // and alive before a word is spoken. Uniform segments are why this
          // first read as a dashed circle rather than as instrumentation.
          const seed = ring.seeds[i]
          const idle = 0.45 + 0.55 * seed
          const wave = 0.5 + 0.5 * Math.sin(t * 0.9 + frac * Math.PI * 6 + seed * 4.0)
          const lit = ring.base * (idle + wave * 0.5) + v * 2.2 + listen * 0.18
          col.copy(accentRef.current).multiplyScalar(lit)
          // Hot segments push toward white — a monochrome additive render with
          // no white point looks like flat tinted fog however bright it gets.
          col.lerp(WHITE, Math.min(0.55, v * 0.6 + wave * 0.10))
          ring.mesh.setColorAt(i, col)
        }
        ring.mesh.instanceMatrix.needsUpdate = true
        if (ring.mesh.instanceColor) ring.mesh.instanceColor.needsUpdate = true
      }

      col.copy(accentRef.current).multiplyScalar(0.34 + amp * 0.26)
      for (let i = 0; i < TEETH; i++) teeth.setColorAt(i, col)
      if (teeth.instanceColor) teeth.instanceColor.needsUpdate = true

      haloMat.color.copy(accentRef.current)
      haloMat.opacity = 0.16 + amp * 0.26 + listen * 0.10
      halo.scale.setScalar(2.1 + amp * 0.55)

      // NOTE: root does NOT rotate. It carries a fixed tilt and nothing else.
      // A continuous rotation here turns every child's plane — including the
      // ring dials — which is the other half of what made them tumble like a
      // globe. The assembly holds still; the parts inside it move.
      //
      // The CORE is the exception, and legitimately so: it is a sphere, and a
      // sphere is the one thing here that should rotate in three dimensions.
      coreWire.rotation.y = t * 0.34
      coreWire.rotation.x = t * 0.13
      coreDots.rotation.copy(coreWire.rotation)
      particles.rotation.y = -t * 0.06
      // Faces and their wireframe share a rotation — they are one shell, and
      // letting them drift apart would double-image the whole silhouette.
      shells[0].rotation.y = shells[1].rotation.y = t * 0.11
      shells[2].rotation.y = shells[3].rotation.y = -t * 0.17
      shells[2].rotation.x = shells[3].rotation.x = t * 0.07

      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(frame)

    // A lost context on wake-from-sleep would otherwise leave a dead rectangle.
    const onLost = (e: Event) => { e.preventDefault(); cancelAnimationFrame(raf) }
    renderer.domElement.addEventListener('webglcontextlost', onLost)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.domElement.removeEventListener('webglcontextlost', onLost)
      for (const d of disposables) d.dispose()
      teethGeo.dispose(); teethMat.dispose()
      // forceContextLoss before dispose: WebGL contexts are a small, capped
      // pool, and leaving and re-entering voice mode repeatedly would exhaust
      // it and silently stop rendering.
      renderer.forceContextLoss()
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
    }
    // `size` is NOT a dependency: it only feeds the wrapper's CSS box, and the
    // renderer follows that box on its own. Listing it would rebuild every
    // geometry, shader and texture each time the orb grows or shrinks.
  }, [amplitude, spectrum, inputLevel])

  return (
    <div
      ref={mountRef}
      style={{
        width: size, height: size, flexShrink: 0, position: 'relative',
        // Animated rather than snapped: the orb changes size when a reply
        // starts and ends, and a jump there reads as a glitch. applySize()
        // re-measures every frame, so the render follows the CSS.
        transition: 'width 0.5s cubic-bezier(0.4, 0, 0.2, 1), height 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    />
  )
}

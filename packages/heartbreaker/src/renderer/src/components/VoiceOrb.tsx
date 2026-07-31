import { useEffect, useRef } from 'react'

/**
 * The orb — a single WebGL2 fragment shader over one full-screen triangle.
 *
 * Everything is a signed distance field evaluated in polar space and added
 * together as light. That is what buys the holographic look: overlapping
 * elements accumulate into brighter cores with soft falloff, the way a real
 * emissive display blooms, instead of compositing as flat stacked shapes.
 *
 * The outer band deforms from the SPECTRUM, not from loudness. A ring that only
 * breathes in and out reads as a progress spinner; lobes that move against each
 * other because they track different frequency bands read as a voice. The inner
 * rings and the lattice stay near-rigid so there is a stable core for the
 * moving rim to be measured against — all of it moving at once reads as noise.
 *
 * No dependency: three.js for one quad would be a megabyte to do less.
 */

export type OrbState = 'idle' | 'thinking' | 'speaking'

/** Angular resolution of the audio-driven deformation. */
const BANDS = 24

interface Props {
  state: OrbState
  /** Live loudness, 0..1. */
  amplitude: () => number
  /** Fills `out` with the spectrum, 0..1 per band. Optional: without it the orb
   *  still lives, driven by loudness and its own motion. */
  spectrum?: (out: Float32Array) => void
  /** Whoever is speaking. Only used to re-read the theme accent when the agent
   *  changes — the colour itself still comes from CSS, never from here. */
  agentId?: string
  size?: number
}

const VERT = `#version 300 es
void main() {
  // Full-screen triangle — no buffers, positions derived from gl_VertexID.
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`

const FRAG = `#version 300 es
precision highp float;

uniform vec2  u_res;
uniform float u_time;
uniform float u_amp;        // smoothed loudness 0..1
uniform float u_speak;      // 0..1 — how "live" the orb is
uniform float u_think;      // 0..1 — working, but not speaking
uniform vec3  u_accent;
uniform float u_bands[${BANDS}];

out vec4 fragColor;

const float TAU = 6.28318530718;

/* A glowing line is a HARD line plus a SHORT halo — never one soft gradient.
 * A 1/d falloff has no crisp core at all, so every stroke becomes a cloud and
 * the whole orb reads as out of focus rather than lit. These two are kept
 * separate deliberately: stroke() carries the shape, halo() carries the light.
 * (No backticks anywhere in this shader — it lives in a template literal.) */

// Hard-edged stroke, antialiased to exactly one pixel via screen-space
// derivatives — so it stays razor sharp at any canvas size or DPR.
float stroke(float d, float halfW) {
  float aa = fwidth(d);
  return 1.0 - smoothstep(halfW - aa, halfW + aa, abs(d));
}

// Emission around a stroke. Exponential and TIGHT: it has to die out within a
// few pixels, or it is just blur wearing a glow's name.
float halo(float d, float falloff) {
  return exp(-abs(d) / falloff);
}

// Soft-bodied band — a filled tube rather than a wire. This is what separates
// a volume from a diagram: the rim has to read as a surface catching light,
// not as an outline someone drew.
float tube(float d, float halfW) {
  float x = clamp(d / halfW, -1.0, 1.0);
  return pow(1.0 - x * x, 1.6);
}

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// Spectrum sampled continuously around the ring. Linear interpolation between
// neighbouring bands, wrapping at the seam, so the rim is a smooth membrane and
// not ${BANDS} visible facets.
float bandAt(float a) {
  float x = fract(a / TAU) * float(${BANDS});
  int i0 = int(x);
  int i1 = (i0 + 1) % ${BANDS};
  float f = smoothstep(0.0, 1.0, fract(x));
  float b0 = u_bands[i0];
  float b1 = u_bands[i1];
  return mix(b0, b1, f);
}

// Radial displacement of the outer band. Idle motion is always present so the
// orb is alive before a word is spoken; the audio term rides on top of it.
float rim(float a, float t) {
  float base =
      sin(a * 3.0  + t * 0.7) * 0.34
    + sin(a * 5.0  - t * 0.9) * 0.22
    + sin(a * 9.0  + t * 1.3) * 0.12;
  float audio = (bandAt(a) - 0.28) * 1.9;
  return base * (0.012 + u_think * 0.010)
       + audio * 0.075 * u_speak;
}

// Flower of life: circle outlines on a triangular lattice. Looped over a fixed
// neighbourhood rather than tiled, so the pattern has a real centre and a real
// edge instead of repeating forever.
float lattice(vec2 p, float t) {
  float acc = 0.0;
  float R = 0.060;
  float h = R * 0.8660254;   // triangular row spacing
  for (int j = -3; j <= 3; j++) {
    for (int i = -3; i <= 3; i++) {
      vec2 c = vec2((float(i) + (j % 2 == 0 ? 0.0 : 0.5)) * R, float(j) * h);
      float lc = length(c);
      if (lc > 0.185) continue;
      float d = length(p - c) - R;
      // Fade cells toward the rim of the disc so it dissolves rather than cuts.
      float fade = 1.0 - smoothstep(0.05, 0.185, lc);
      acc += (stroke(d, 0.0012) + halo(d, 0.006) * 0.35) * fade;
    }
  }
  // Never fully dark: this is the orb's core and a lattice that blinks out
  // leaves a hole in the middle of the frame.
  return acc * (0.70 + 0.30 * sin(t * 1.1));
}

void main() {
  vec2 uv = (gl_FragCoord.xy * 2.0 - u_res) / min(u_res.x, u_res.y);

  // Slight vertical squash — the orb is read at a shallow angle, like a dish
  // standing in front of the viewer rather than a flat disc pasted on screen.
  vec2 p = uv;
  p.y /= 0.94;

  float r = length(p);
  float a = atan(p.y, p.x);
  float t = u_time;

  float glow = 0.0;
  float core = 0.0;

  // Fixed key light, upper-left. A torus lit from everywhere is a flat annulus;
  // one direction is all it takes for the rim to acquire a near and a far side.
  vec2  L   = normalize(vec2(-0.45, 0.89));
  float lam = 0.52 + 0.48 * dot(normalize(p + 1e-5), L);

  // ── The rim — a soft-bodied torus, deformed by the voice ───────────────
  // Both the radius AND the thickness take the displacement, so loud passages
  // crumple the ring the way a struck membrane folds, instead of merely
  // scaling it up and down.
  float disp  = rim(a, t);
  float rimR  = 0.735 + disp;
  float halfW = 0.072 + disp * 0.55;

  float d    = r - rimR;
  float body = tube(d, halfW);

  // The lit surface of the ring. Kept low: the rim should read from its edges,
  // and a bright body just fogs the whole assembly.
  glow += body * (0.055 + u_amp * 0.10) * lam;

  // Silhouette edges, where a real torus turns away from the eye. These carry
  // the shape, so they are hard strokes with only a short halo on top.
  float eOut = abs(d) - halfW;
  core += stroke(eOut, 0.0016) * (1.9 + u_amp * 0.9) * (0.5 + 0.5 * lam);
  glow += halo(eOut, 0.010) * (0.20 + u_amp * 0.25) * lam;

  // A brighter crease just inside the rim — the reference's inner wall.
  float crease = d + halfW * 0.42;
  core += stroke(crease, 0.0013) * 0.85 * lam;
  glow += halo(crease, 0.007) * 0.10 * lam;

  // ── Interior — a dark concave dish, not a stack of rings ───────────────
  float inside = 1.0 - smoothstep(rimR - halfW * 1.05, rimR - halfW * 0.55, r);
  // Barely lifted. The cavity being genuinely dark is what makes the rim and
  // the lattice read as light sources instead of as parts of a bright disc.
  glow += inside * (0.008 + 0.018 * (1.0 - smoothstep(0.0, 0.62, r))) * (0.65 + 0.35 * lam);
  // One faint ring on that floor. One — a second turns this back into a target.
  float ring = r - 0.415;
  core += stroke(ring, 0.0011) * 0.55 * inside;
  glow += halo(ring, 0.005) * 0.05 * inside;

  // ── Centre lattice ─────────────────────────────────────────────────────
  // Counter-rotates slowly so the middle never looks frozen.
  float ca = t * 0.13;
  mat2 rot = mat2(cos(ca), -sin(ca), sin(ca), cos(ca));
  core += lattice(rot * p, t) * (0.85 + u_amp * 0.75);

  // Disc the lattice sits on, and the soft pool of light under it
  float disc = r - 0.205;
  core += stroke(disc, 0.0014) * 1.3;
  glow += halo(disc, 0.008) * 0.14;
  glow += (1.0 - smoothstep(0.0, 0.26, r)) * (0.030 + u_amp * 0.18);

  // ── Assemble ───────────────────────────────────────────────────────────
  // stroke() returns 0..1, so the shape term needs a much higher weight than
  // the old unbounded 1/d falloff did.
  float energy = core * 0.62 + glow;

  // Hot centres push toward white; the falloff stays accent-coloured. This is
  // what stops a monochrome additive render from looking like flat tinted fog.
  vec3 col = u_accent * energy;
  col += vec3(1.0) * pow(clamp(core, 0.0, 1.0), 2.0) * 0.42;

  // Volumetric haze. Small on purpose — this was fogging the whole orb and
  // flattening the contrast that makes the rim look like a solid object.
  col += u_accent * exp(-r * 3.2) * (0.014 + u_amp * 0.045);

  // Vignette. Written ascending and inverted: GLSL smoothstep is UNDEFINED
  // when edge0 >= edge1, and the descending form left a visible square edge
  // where the corners never reached zero.
  col *= 1.0 - smoothstep(0.62, 1.06, r);
  col = col / (col + vec3(0.55));             // filmic rolloff, no clipping
  col = pow(col, vec3(0.4545));               // to sRGB

  // No dither. The orb is drawn on a TRANSPARENT canvas and alpha is derived
  // from brightness, so any per-pixel noise in the colour becomes per-pixel
  // noise in the alpha — and the page gradient read through that random mask
  // as a band of black speckle around the rim. Whatever a dither buys in
  // gradient smoothness is not worth punching holes in the compositing.
  float alpha = clamp(max(col.r, max(col.g, col.b)) * 1.35, 0.0, 1.0);
  fragColor = vec4(col, alpha);
}`

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type)
  if (!sh) return null
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.error('[VoiceOrb] shader compile failed', {
      lost: gl.isContextLost(),
      head: JSON.stringify(src.slice(0, 40)),
      log: gl.getShaderInfoLog(sh),
    })
    gl.deleteShader(sh)
    return null
  }
  return sh
}

/** Read the theme accent as linear-ish 0..1 RGB, so the orb matches whichever
 *  agent is speaking without the colour being hardcoded here. */
function accentOf(el: HTMLElement): [number, number, number] {
  const raw = getComputedStyle(el).getPropertyValue('--hb-accent-rgb').trim()
  const parts = raw.split(',').map(s => parseFloat(s))
  if (parts.length !== 3 || parts.some(n => Number.isNaN(n))) return [0.28, 0.72, 0.82]
  return [parts[0] / 255, parts[1] / 255, parts[2] / 255]
}

export default function VoiceOrb({ state, amplitude, spectrum, agentId, size = 300 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  // Live accent, re-read whenever the speaking agent changes. Held in a ref and
  // uploaded every frame rather than captured once at init — read once, the orb
  // stayed SPEDA's cyan no matter which agent was actually talking.
  const accentRef = useRef<[number, number, number]>([0.21, 0.67, 0.79])
  useEffect(() => {
    if (canvasRef.current) accentRef.current = accentOf(canvasRef.current)
  }, [agentId])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const gl = canvas.getContext('webgl2', {
      alpha: true, premultipliedAlpha: false, antialias: false,
    })
    if (!gl) {
      console.warn('[VoiceOrb] WebGL2 unavailable — orb disabled')
      return
    }

    const vs = compile(gl, gl.VERTEX_SHADER, VERT)
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG)
    if (!vs || !fs) return
    const prog = gl.createProgram()!
    gl.attachShader(prog, vs)
    gl.attachShader(prog, fs)
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error('[VoiceOrb] link:', gl.getProgramInfoLog(prog))
      return
    }
    gl.useProgram(prog)

    const uRes   = gl.getUniformLocation(prog, 'u_res')
    const uTime  = gl.getUniformLocation(prog, 'u_time')
    const uAmp   = gl.getUniformLocation(prog, 'u_amp')
    const uSpeak = gl.getUniformLocation(prog, 'u_speak')
    const uThink = gl.getUniformLocation(prog, 'u_think')
    const uAcc   = gl.getUniformLocation(prog, 'u_accent')
    const uBands = gl.getUniformLocation(prog, 'u_bands')

    accentRef.current = accentOf(canvas)

    // Read the device pixel ratio EVERY frame, never once at mount. Page zoom
    // changes it, and a backing store pinned to the ratio captured at mount is
    // exactly why a procedural, resolution-independent orb still turned into
    // visible pixels when the window was zoomed in.
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 3)
      const w = Math.round(canvas.clientWidth * dpr)
      const h = Math.round(canvas.clientHeight * dpr)
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
        gl.viewport(0, 0, w, h)
      }
      gl.uniform2f(uRes, canvas.width, canvas.height)
    }

    const bands = new Float32Array(BANDS)
    const smooth = new Float32Array(BANDS)
    let amp = 0
    let speak = 0
    let think = 0
    let raf = 0
    const t0 = performance.now()

    const frame = () => {
      resize()
      const t = (performance.now() - t0) / 1000
      const st = stateRef.current

      // Attack fast, release slow: consonants have to register, but the ring
      // must not flicker in every gap between words.
      const targetAmp = st === 'speaking' ? amplitude() : 0
      amp += (targetAmp - amp) * (targetAmp > amp ? 0.40 : 0.07)

      // Mode weights are eased too — snapping between states is the single
      // most artificial-looking thing an orb can do.
      speak += ((st === 'speaking' ? 1 : 0) - speak) * 0.06
      think += ((st === 'thinking' ? 1 : 0) - think) * 0.05

      if (spectrum && st === 'speaking') spectrum(bands)
      else bands.fill(0)
      for (let i = 0; i < BANDS; i++) {
        smooth[i] += (bands[i] - smooth[i]) * (bands[i] > smooth[i] ? 0.5 : 0.10)
      }

      gl.uniform3fv(uAcc, accentRef.current)
      gl.uniform1f(uTime, t)
      gl.uniform1f(uAmp, amp)
      gl.uniform1f(uSpeak, speak)
      gl.uniform1f(uThink, think)
      gl.uniform1fv(uBands, smooth)

      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    // A lost context on wake-from-sleep would otherwise leave a dead black square.
    const onLost = (e: Event) => { e.preventDefault(); cancelAnimationFrame(raf) }
    canvas.addEventListener('webglcontextlost', onLost)

    return () => {
      cancelAnimationFrame(raf)
      canvas.removeEventListener('webglcontextlost', onLost)
      gl.deleteProgram(prog)
      gl.deleteShader(vs)
      gl.deleteShader(fs)
      // Deliberately NOT loseContext(): a canvas hands back the SAME context
      // object on every getContext, and a lost one stays lost. Any remount that
      // reuses this element — StrictMode's double-effect, leaving and re-entering
      // the mode — would then get a dead context whose shaders fail to compile
      // with an empty info log. The context is released with the element.
    }
  }, [amplitude, spectrum])

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size, display: 'block', flexShrink: 0 }}
    />
  )
}

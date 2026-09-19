import { useEffect, useRef } from "react";
import * as THREE from "three";

/**
 * The hero background. Replaces the stock looping mp4 with a generated thermal
 * field: domain-warped FBM ridges advected left-to-right, coloured on an
 * ink → maroon → copper → bone ramp. Reads as profiler heat rather than stock
 * footage, is ~4 KB of shader instead of 700 KB of video, and is tied to the
 * palette so it can never drift away from the rest of the page.
 *
 * Behaviour: pauses when the tab is hidden, renders a single frame under
 * `prefers-reduced-motion`, clamps DPR to 1.5, and degrades to the CSS gradient
 * painted behind the canvas if WebGL is unavailable.
 */

const VERT = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

const FRAG = /* glsl */ `
  precision highp float;
  varying vec2 vUv;
  uniform float uTime;
  uniform vec2  uResolution;

  // --- value noise + fbm ------------------------------------------------
  vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
  }

  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(dot(hash2(i + vec2(0.0, 0.0)), f - vec2(0.0, 0.0)),
                   dot(hash2(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0)), u.x),
               mix(dot(hash2(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0)),
                   dot(hash2(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0)), u.x), u.y);
  }

  float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 5; i++) {
      v += a * noise(p);
      p = p * 2.02 + vec2(1.7, 9.2);
      a *= 0.5;
    }
    return v;
  }

  // ridged fbm: filaments rather than clouds — the "hot path" through the field
  float ridges(vec2 p) {
    float v = 0.0;
    float a = 0.55;
    for (int i = 0; i < 4; i++) {
      v += a * (1.0 - abs(noise(p)) * 2.0);
      p = p * 2.05 + vec2(4.3, 1.9);
      a *= 0.52;
    }
    return v;
  }

  void main() {
    // aspect-corrected, centred coordinates
    vec2 uv = vUv;
    vec2 p = (uv - 0.5) * vec2(uResolution.x / max(uResolution.y, 1.0), 1.0);

    float t = uTime * 0.035;

    // domain warp — two octaves of offset so the filaments curl instead of slide
    vec2 q = vec2(fbm(p * 1.6 + vec2(t, t * 0.35)),
                  fbm(p * 1.6 + vec2(5.2, 1.3) - vec2(t * 0.7, 0.0)));
    vec2 r = vec2(fbm(p * 2.4 + 3.4 * q + vec2(1.7 - t * 1.2, 9.2)),
                  fbm(p * 2.4 + 3.4 * q + vec2(8.3, 2.8 + t * 0.5)));

    float field = ridges(p * 2.1 + 2.2 * r - vec2(t * 2.0, 0.0));
    field = smoothstep(-0.15, 1.25, field);

    // heat concentrates toward the middle band and fades at the edges, so the
    // headline sits on quiet ground
    float band = exp(-pow((uv.y - 0.42) * 2.35, 2.0));
    // biased right of centre: the headline column sits on the left
    float sides = smoothstep(0.08, 0.52, uv.x) * smoothstep(1.06, 0.70, uv.x);
    float heat = field * mix(0.35, 1.0, band) * mix(0.55, 1.0, sides);

    // ink → maroon → copper → bone
    vec3 ink    = vec3(0.043, 0.035, 0.031);
    vec3 maroon = vec3(0.216, 0.078, 0.055);
    vec3 copper = vec3(0.851, 0.400, 0.184);
    vec3 bone   = vec3(0.957, 0.941, 0.925);

    vec3 col = ink;
    col = mix(col, maroon, smoothstep(0.08, 0.52, heat));
    col = mix(col, copper, smoothstep(0.46, 0.86, heat));
    col = mix(col, bone,   smoothstep(0.88, 1.0, heat) * 0.55);

    // faint measurement scanline drifting upward — instrument, not TV static
    float scan = sin((uv.y + uTime * 0.012) * uResolution.y * 0.55) * 0.5 + 0.5;
    col += vec3(0.012, 0.008, 0.006) * scan;

    // dither so the wide dark gradients don't band on 8-bit displays
    col += (fract(sin(dot(uv * uResolution, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;

    gl_FragColor = vec4(col, 1.0);
  }
`;

export default function HeatField({ className = "" }: { className?: string }) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: "low-power" });
    } catch {
      return; // no WebGL — the CSS gradient behind the canvas stands in
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    const uniforms = {
      uTime: { value: 0 },
      uResolution: { value: new THREE.Vector2(1, 1) },
    };
    const material = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms,
      depthTest: false,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
    mesh.frustumCulled = false;
    scene.add(mesh);

    const resize = () => {
      const w = host.clientWidth || 1;
      const h = host.clientHeight || 1;
      renderer.setSize(w, h, false);
      uniforms.uResolution.value.set(w, h);
    };
    resize();

    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(host);
    window.addEventListener("resize", resize);

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    let raf = 0;
    let running = true;
    const start = performance.now();

    const frame = (now: number) => {
      if (!running) return;
      uniforms.uTime.value = (now - start) / 1000;
      renderer.render(scene, camera);
      raf = requestAnimationFrame(frame);
    };

    if (reduced) {
      uniforms.uTime.value = 12; // a settled, still frame
      renderer.render(scene, camera);
    } else {
      raf = requestAnimationFrame(frame);
    }

    // don't burn a GPU on a hidden tab
    const onVisibility = () => {
      if (reduced) return;
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", resize);
      observer?.disconnect();
      mesh.geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      aria-hidden
      className={className}
      style={{
        // stands in until the first frame, and permanently if WebGL is missing
        background:
          "radial-gradient(ellipse 70% 55% at 50% 42%, #3a1509 0%, #1a0d08 45%, #0b0908 100%)",
      }}
    />
  );
}

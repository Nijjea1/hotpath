import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import type { Frame } from "../../data/hotpath";

/* ---------------- icicle-style flame graph: width = ms per decode step ---------------- */

const W = 520;
const ROW = 34;
const GAP = 3;
const X0 = 0;
const X1 = 520;

const TONE: Record<Frame["tone"], { fill: string; text: string }> = {
  hot: { fill: "#ef4444", text: "#fff5f5" },
  warm: { fill: "#f59e0b", text: "#1c1204" },
  cool: { fill: "#3f3f46", text: "#e5e5e5" },
  gone: { fill: "transparent", text: "#737373" },
};

type Placed = { f: Frame; x: number; w: number; depth: number };

function layout(root: Frame, scaleMs: number) {
  const out: Placed[] = [];
  const px = (X1 - X0) / scaleMs;
  const walk = (f: Frame, x: number, depth: number) => {
    out.push({ f, x, w: f.ms * px, depth });
    let cx = x;
    for (const c of f.children ?? []) {
      walk(c, cx, depth + 1);
      cx += c.ms * px;
    }
  };
  walk(root, X0, 0);
  return out;
}

// `scaleMs` is shared by before and after, so the after graph is visibly narrower.
export default function FlameGraph({ root, scaleMs }: { root: Frame; scaleMs: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const placed = layout(root, scaleMs);
  const depth = Math.max(...placed.map((p) => p.depth)) + 1;
  const H = depth * (ROW + GAP);

  return (
    <div ref={ref} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={`Flame graph of ${root.name}, ${root.ms} ms per step`}>
        {/* the full before-width, as a ghost, so the shrink is visible */}
        <rect x={X0} y={0} width={X1 - X0} height={ROW} rx={5} fill="none" stroke="rgba(255,255,255,0.12)" strokeDasharray="4 4" />
        {placed.map((p, i) => {
          const t = TONE[p.f.tone];
          const y = p.depth * (ROW + GAP);
          // monospace at 11px ≈ 6.7 units per char; only print what fits inside the bar
          const fit = Math.floor((p.w - 14) / 6.7);
          const full = `${p.f.name} · ${p.f.ms} ms`;
          const label = full.length <= fit ? full : p.f.name.length <= fit ? p.f.name : fit >= 4 ? `${p.f.name.slice(0, fit - 1)}…` : "";
          return (
            <motion.g key={`${p.f.name}-${i}`} initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : { opacity: 0 }} transition={{ duration: 0.4, delay: 0.1 + p.depth * 0.18 }}>
              <motion.rect
                x={p.x + 1}
                y={y}
                height={ROW}
                rx={5}
                fill={t.fill}
                fillOpacity={p.f.tone === "cool" ? 1 : 0.9}
                initial={{ width: 0 }}
                animate={inView ? { width: Math.max(0, p.w - 2) } : { width: 0 }}
                transition={{ duration: 0.6, delay: 0.1 + p.depth * 0.18, ease: "easeOut" }}
              />
              {label && (
                <text x={p.x + 8} y={y + ROW / 2 + 4} fontSize={11} fill={t.text} fontFamily="'JetBrains Mono', monospace">
                  {label}
                </text>
              )}
            </motion.g>
          );
        })}
      </svg>
    </div>
  );
}

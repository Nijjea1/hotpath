import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { EXPERIMENTS, NOISE_PCT, RUN_LENGTH, THROUGHPUT_STEPS } from "../../data/hotpath";

/* ---------------- best verified tokens/sec over the run ---------------- */

const W = 1000;
const H = 400;
const X0 = 70;
const X1 = 968;
const Y0 = 26;
const Y1 = 340;
const YMIN = 900;
const YMAX = 1500;
const xs = (n: number) => X0 + (n / RUN_LENGTH) * (X1 - X0);
const ys = (v: number) => Y1 - ((v - YMIN) / (YMAX - YMIN)) * (Y1 - Y0);

// step line: flat until the next accepted change, then a jump
function stepPath(scale = 1) {
  const pts: string[] = [];
  THROUGHPUT_STEPS.forEach((s, i) => {
    const v = s.tokPerSec * scale;
    if (i > 0) pts.push(`${xs(s.at).toFixed(1)} ${ys(THROUGHPUT_STEPS[i - 1].tokPerSec * scale).toFixed(1)}`);
    pts.push(`${xs(s.at).toFixed(1)} ${ys(v).toFixed(1)}`);
  });
  const last = THROUGHPUT_STEPS[THROUGHPUT_STEPS.length - 1];
  pts.push(`${xs(RUN_LENGTH).toFixed(1)} ${ys(last.tokPerSec * scale).toFixed(1)}`);
  return pts;
}

export default function ThroughputChart() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const line = "M " + stepPath().join(" L ");
  const area = line + ` L ${xs(RUN_LENGTH)} ${Y1} L ${xs(0)} ${Y1} Z`;
  const hi = stepPath(1 + NOISE_PCT / 100);
  const lo = stepPath(1 - NOISE_PCT / 100).reverse();
  const band = `M ${hi.join(" L ")} L ${lo.join(" L ")} Z`;
  const last = THROUGHPUT_STEPS[THROUGHPUT_STEPS.length - 1];
  // Measured but not kept. Candidates that failed correctness were never benchmarked, so they have no point.
  const rejected = EXPERIMENTS.filter((e) => !e.shipped && e.status !== "accepted" && e.tokPerSec !== null);

  return (
    <div ref={ref} className="w-full">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto min-w-[640px]" role="img" aria-label="Best verified tokens per second over the run, stepping up at each accepted change">
          <defs>
            <linearGradient id="tp-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#6fb588" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#6fb588" stopOpacity={0} />
            </linearGradient>
            <clipPath id="tp-reveal">
              <motion.rect x={X0} y={0} height={H} initial={{ width: 0 }} animate={inView ? { width: X1 - X0 } : { width: 0 }} transition={{ duration: 1.6, ease: "easeInOut" }} />
            </clipPath>
          </defs>

          {[900, 1000, 1100, 1200, 1300, 1400, 1500].map((v) => (
            <g key={v}>
              <line x1={X0} x2={X1} y1={ys(v)} y2={ys(v)} stroke="rgba(255,255,255,0.06)" strokeWidth={1} />
              <text x={X0 - 10} y={ys(v) + 4} textAnchor="end" fontSize={12} fill="#78706a" fontFamily="'Public Sans', sans-serif">{v}</text>
            </g>
          ))}
          {[0, 10, 20, 30, 40, 50].map((n) => (
            <text key={n} x={xs(n)} y={Y1 + 24} textAnchor="middle" fontSize={12} fill="#8a817a" fontFamily="'Public Sans', sans-serif">{n}</text>
          ))}

          <g clipPath="url(#tp-reveal)">
            <path d={band} fill="#6fb588" fillOpacity={0.1} />
            <path d={area} fill="url(#tp-fill)" />
            <path d={line} fill="none" stroke="#6fb588" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
          </g>

          {/* rejected candidates: measured, but never counted */}
          {rejected.map((e, i) => (
            <motion.g key={e.id} initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : { opacity: 0 }} transition={{ delay: 1.2 + i * 0.08, duration: 0.4 }}>
              <circle cx={xs(e.n)} cy={ys(e.tokPerSec!)} r={5} fill="none" stroke="#9c938a" strokeWidth={1.6} />
              <text x={xs(e.n) > (X0 + X1) / 2 ? xs(e.n) - 9 : xs(e.n) + 9} y={ys(e.tokPerSec!) + 16} textAnchor={xs(e.n) > (X0 + X1) / 2 ? "end" : "start"} fontSize={11.5} fill="#a39a92" fontFamily="'Public Sans', sans-serif">{e.idea} · {e.status === "not_selected" ? "not selected" : "within noise"}</text>
            </motion.g>
          ))}

          {/* accepted step labels */}
          {THROUGHPUT_STEPS.slice(1).map((s, i) => (
            <motion.g key={s.label} initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : { opacity: 0 }} transition={{ delay: 0.5 + i * 0.3, duration: 0.4 }}>
              <circle cx={xs(s.at)} cy={ys(s.tokPerSec)} r={4} fill="#6fb588" />
              <text x={xs(s.at) + 8} y={ys(s.tokPerSec) - 9} fontSize={12} fill="#cde7d4" fontFamily="'Public Sans', sans-serif">{s.label}</text>
            </motion.g>
          ))}
          <motion.text x={xs(RUN_LENGTH) - 8} y={ys(last.tokPerSec) - 12} textAnchor="end" fontSize={15} fontWeight={600} fill="#96cba8" fontFamily="'Public Sans', sans-serif" initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : { opacity: 0 }} transition={{ delay: 1.6, duration: 0.4 }}>{last.tokPerSec} tok/s</motion.text>

          <text x={(X0 + X1) / 2} y={H - 4} textAnchor="middle" fontSize={13} fill="#9c938a" fontFamily="'Public Sans', sans-serif">experiments</text>
          <text x={18} y={(Y0 + Y1) / 2} textAnchor="middle" fontSize={13} fill="#9c938a" fontFamily="'Public Sans', sans-serif" transform={`rotate(-90 18 ${(Y0 + Y1) / 2})`}>tokens / sec</text>
        </svg>
      </div>

      {/* legend */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mt-4 pl-12 text-sm">
        <span className="inline-flex items-center gap-2 text-neutral-300"><span className="w-3 h-3 rounded-sm bg-emerald-400" />best verified</span>
        <span className="inline-flex items-center gap-2 text-neutral-300"><span className="w-3 h-3 rounded-sm bg-emerald-400/20" />noise band ±{NOISE_PCT}%</span>
        <span className="inline-flex items-center gap-2 text-neutral-300"><span className="w-3 h-3 rounded-full border border-neutral-400" />measured, not kept</span>
      </div>
    </div>
  );
}

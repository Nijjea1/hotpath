import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { MOVES } from "../../data/hotpath";

const VERDICTS = [
  { label: "accepted", tone: "good" },
  { label: "rejected_speed", tone: "warn" },
  { label: "rejected_correctness", tone: "bad" },
  { label: "patch_failed", tone: "bad" },
  { label: "locked_file", tone: "bad" },
  { label: "not_selected", tone: "warn" },
  { label: "timeout", tone: "bad" },
  { label: "error", tone: "bad" },
] as const;

const ACTION_PATH = "M 648 198 C 588 116, 542 116, 482 198";
const OBS_PATH = "M 482 286 C 542 372, 588 372, 648 286";

function Node({
  x, y, w, h, accent = "#d9662f", children, delay = 0, inView,
}: { x: number; y: number; w: number; h: number; accent?: string; children: React.ReactNode; delay?: number; inView: boolean }) {
  return (
    <motion.g
      initial={{ opacity: 0, y: 14 }}
      animate={inView ? { opacity: 1, y: 0 } : { opacity: 0, y: 14 }}
      transition={{ duration: 0.5, delay, ease: "easeOut" }}
    >
      <rect x={x} y={y} width={w} height={h} rx={14} fill="#141110" stroke={accent} strokeOpacity={0.5} strokeWidth={1.4} />
      <rect x={x} y={y} width={4} height={h} rx={2} fill={accent} />
      {children}
    </motion.g>
  );
}

function Pulse({ path, color, dur, begin }: { path: string; color: string; dur: number; begin: number }) {
  return (
    <circle r={4.5} fill={color}>
      <animateMotion dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite" path={path} />
      <animate attributeName="opacity" values="0;1;1;0" dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite" />
    </circle>
  );
}

export default function LoopDiagram() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <div ref={ref} className="w-full">
      <svg viewBox="0 0 1000 480" className="w-full h-auto" role="img" aria-label="The Hotpath loop: models propose patches, the harness returns verdicts">
        <defs>
          <marker id="fd-arrow" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill="#6e665f" />
          </marker>
        </defs>

        {/* input -> compiler */}
        <motion.path d="M 150 116 C 175 150, 215 168, 252 175" fill="none" stroke="#585049" strokeWidth={1.6} markerEnd="url(#fd-arrow)" strokeDasharray="5 5" initial={{ pathLength: 0 }} animate={inView ? { pathLength: 1 } : {}} transition={{ duration: 0.6, delay: 0.2 }} />
        {/* compiler -> optimized program */}
        <motion.path d="M 340 318 L 340 392" fill="none" stroke="#6fb588" strokeWidth={1.8} markerEnd="url(#fd-arrow)" initial={{ pathLength: 0 }} animate={inView ? { pathLength: 1 } : {}} transition={{ duration: 0.5, delay: 1.0 }} />

        {/* the loop arrows */}
        <path d={ACTION_PATH} fill="none" stroke="#d9662f" strokeOpacity={0.6} strokeWidth={1.8} markerEnd="url(#fd-arrow)" />
        <path d={OBS_PATH} fill="none" stroke="#e0b341" strokeOpacity={0.6} strokeWidth={1.8} markerEnd="url(#fd-arrow)" />
        <text x={565} y={120} textAnchor="middle" fontSize={13} fill="#f0a585" fontFamily="'Public Sans', sans-serif">action · &lt;patch&gt;</text>
        <text x={565} y={372} textAnchor="middle" fontSize={13} fill="#ecd089" fontFamily="'Public Sans', sans-serif">observation · verdict + reason</text>

        {/* traveling pulses */}
        {inView && (
          <>
            <Pulse path={ACTION_PATH} color="#e8845a" dur={2.4} begin={0} />
            <Pulse path={ACTION_PATH} color="#e8845a" dur={2.4} begin={1.2} />
            <Pulse path={OBS_PATH} color="#e0b341" dur={2.4} begin={0.6} />
            <Pulse path={OBS_PATH} color="#e0b341" dur={2.4} begin={1.8} />
          </>
        )}

        {/* Input Program */}
        <Node x={70} y={66} w={130} h={56} accent="#9c938a" delay={0} inView={inView}>
          <text x={135} y={90} textAnchor="middle" fontSize={14} fontWeight={600} fill="#e4ded7" fontFamily="'Public Sans', sans-serif">Your repo</text>
          <text x={135} y={108} textAnchor="middle" fontSize={11} fill="#8a817a" fontFamily="'Public Sans', sans-serif">config.yaml</text>
        </Node>

        {/* Compiler & Runtime */}
        <Node x={200} y={168} w={284} h={150} accent="#e0b341" delay={0.15} inView={inView}>
          <text x={342} y={196} textAnchor="middle" fontSize={16} fontWeight={600} fill="#f4f0ec" fontFamily="'Public Sans', sans-serif">Harness</text>
          <text x={342} y={214} textAnchor="middle" fontSize={11} fill="#9c938a" fontFamily="'Public Sans', sans-serif">sandboxed · models cannot edit it</text>
          {["worktree", "locked", "tests", "bench"].map((s, i) => (
            <g key={s}>
              <rect x={216 + i * 64} y={232} width={58} height={26} rx={6} fill="rgba(245,158,11,0.12)" stroke="rgba(245,158,11,0.3)" />
              <text x={216 + i * 64 + 29} y={249} textAnchor="middle" fontSize={10.5} fill="#ecd089" fontFamily="'Public Sans', sans-serif">{s}</text>
            </g>
          ))}
          <text x={342} y={290} textAnchor="middle" fontSize={11} fill="#78706a" fontFamily="'Public Sans', sans-serif">verdict + reason → history</text>
        </Node>

        {/* LLM Agent */}
        <Node x={650} y={168} w={224} h={150} accent="#d9662f" delay={0.3} inView={inView}>
          <text x={762} y={206} textAnchor="middle" fontSize={18} fontWeight={600} fill="#f4f0ec" fontFamily="'Public Sans', sans-serif">Planner + workers</text>
          <text x={762} y={228} textAnchor="middle" fontSize={11} fill="#9c938a" fontFamily="'Public Sans', sans-serif">big model plans</text>
          <text x={762} y={246} textAnchor="middle" fontSize={11} fill="#9c938a" fontFamily="'Public Sans', sans-serif">fast model explores</text>
          <g>
            <rect x={690} y={262} width={144} height={28} rx={7} fill="rgba(59,130,246,0.12)" stroke="rgba(59,130,246,0.3)" />
            <text x={762} y={280} textAnchor="middle" fontSize={11} fill="#f0a585" fontFamily="'Public Sans', sans-serif">proposes a patch</text>
          </g>
        </Node>

        {/* Optimized Program */}
        <Node x={262} y={394} w={156} h={56} accent="#6fb588" delay={1.1} inView={inView}>
          <text x={340} y={418} textAnchor="middle" fontSize={14} fontWeight={600} fill="#e4ded7" fontFamily="'Public Sans', sans-serif">Verified stack</text>
          <text x={340} y={436} textAnchor="middle" fontSize={11} fill="#96cba8" fontFamily="'Public Sans', sans-serif">correct + faster</text>
        </Node>
      </svg>

      {/* chips: transformations + feedback */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-8">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-neutral-500 mb-3">optimization moves · {MOVES.length} seeds + free-form</p>
          <div className="flex flex-wrap gap-2">
            {MOVES.map((t, i) => (
              <motion.span
                key={t}
                className="text-sm text-blue-200 bg-blue-500/10 border border-blue-500/25 rounded-lg px-3 py-1.5"
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.4, delay: i * 0.05 }}
              >
                {t}
              </motion.span>
            ))}
          </div>
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-neutral-500 mb-3">verdict statuses</p>
          <div className="flex flex-wrap gap-2">
            {VERDICTS.map((f, i) => (
              <motion.span
                key={f.label}
                className={`text-sm rounded-lg px-3 py-1.5 border ${f.tone === "good" ? "text-emerald-200 bg-emerald-500/10 border-emerald-500/25" : f.tone === "warn" ? "text-amber-200 bg-amber-500/10 border-amber-500/25" : "text-red-200 bg-red-500/10 border-red-500/25"}`}
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.4, delay: i * 0.05 }}
              >
                {f.label}
              </motion.span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

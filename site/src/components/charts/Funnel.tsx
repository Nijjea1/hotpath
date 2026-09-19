import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { FUNNEL } from "../../data/hotpath";

const W = 1000;
const ROW = 58;
const GAP = 14;
const LABEL_W = 250;
const X0 = LABEL_W;
const X1 = 960;
const H = FUNNEL.length * (ROW + GAP);
const max = FUNNEL[0].count;

// shade: grey for "still a proposal", emerald for what shipped
const fillFor = (i: number) => (i === FUNNEL.length - 1 ? "#34d399" : `rgba(255,255,255,${0.32 - i * 0.05})`);

export default function Funnel() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <div ref={ref} className="w-full">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto min-w-[560px]" role="img" aria-label="Funnel from ideas proposed to changes shipped">
          {FUNNEL.map((s, i) => {
            const y = i * (ROW + GAP);
            const w = (s.count / max) * (X1 - X0);
            const dropped = i > 0 ? FUNNEL[i - 1].count - s.count : 0;
            return (
              <g key={s.stage}>
                <text x={0} y={y + ROW / 2 + 5} fontSize={15} fill="#d4d4d4" fontFamily="'Inter', sans-serif">{s.stage}</text>
                <motion.rect
                  x={X0}
                  y={y}
                  height={ROW}
                  rx={8}
                  fill={fillFor(i)}
                  initial={{ width: 0 }}
                  animate={inView ? { width: w } : { width: 0 }}
                  transition={{ duration: 0.7, delay: 0.15 + i * 0.18, ease: "easeOut" }}
                />
                <motion.text
                  x={X0 + w + 12}
                  y={y + ROW / 2 + 6}
                  fontSize={18}
                  fontWeight={600}
                  fill={i === FUNNEL.length - 1 ? "#6ee7b7" : "#f5f5f5"}
                  fontFamily="'Inter', sans-serif"
                  initial={{ opacity: 0 }}
                  animate={inView ? { opacity: 1 } : { opacity: 0 }}
                  transition={{ delay: 0.6 + i * 0.18, duration: 0.4 }}
                >
                  {s.count}
                  {dropped > 0 && <tspan fontSize={13} fontWeight={400} fill="#f87171">{`  −${dropped}`}</tspan>}
                </motion.text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

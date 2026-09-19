import type * as React from "react";
import { useEffect, useRef, useState } from "react";
import {
  motion,
  useMotionValue,
  useTransform,
  animate,
  useInView as useInViewFM,
} from "framer-motion";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  REPO_URL,
  README_URL,
  HARNESS_URL,
  BENCHMARK_URL,
  DEMO_URL,
  ILLUSTRATIVE,
  LOOP,
  EXPERIMENTS,
  TRUST_ID,
  OUTCOMES,
  TOTALS,
  THESIS_CARDS,
  THROUGHPUT_STEPS,
  BASELINE_TOK_S,
  FLAME_BEFORE,
  FLAME_AFTER,
  FLAME_NOTES,
  CONFIG_YAML,
  STATUS_LABEL,
} from "../data/hotpath";
import type { Experiment } from "../data/hotpath";
import LoopDiagram from "../components/charts/LoopDiagram";
import ThroughputChart from "../components/charts/ThroughputChart";
import FlameGraph from "../components/charts/FlameGraph";
import Funnel from "../components/charts/Funnel";
import HeatField from "../components/HeatField";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Hotpath — an AI agent that makes your code faster and proves every change is correct" },
      {
        name: "description",
        content:
          "AI can write optimizations. It can't tell you if they work. Hotpath profiles your code, proposes optimizations, tests and benchmarks each one, and keeps only the changes that are both correct and measurably faster.",
      },
    ],
  }),
  component: Index,
});

/* ------------------------------------------------------------------ icons */

function LogoMark({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden>
      <rect width="40" height="40" rx="10" fill="#141110" stroke="rgba(244,240,236,0.14)" />
      <path d="M7 29 H14 V22 H21 V15 H28" stroke="#d9662f" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="28" cy="15" r="3.2" fill="#d9662f" />
      <path d="M21 22 L33 29" stroke="#f4f0ec" strokeWidth="1.6" strokeLinecap="round" strokeDasharray="2 2.4" />
      <circle cx="33" cy="29" r="2" fill="#c4544a" />
    </svg>
  );
}

function Icon({ path, size = 20, stroke = "currentColor", fill = "none", width = 1.6 }: { path: React.ReactNode; size?: number; stroke?: string; fill?: string; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={stroke} strokeWidth={width} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {path}
    </svg>
  );
}

const ICONS = {
  terminal: <><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></>,
  copy: <><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
  check: <polyline points="20 6 9 17 4 12" />,
  x: <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>,
  arrowUpRight: <><line x1="7" y1="17" x2="17" y2="7" /><polyline points="7 7 17 7 17 17" /></>,
  github: <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />,
  shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
};

/* ------------------------------------------------------------------ page */

const navItems = [
  { label: "Loop", href: "#loop", active: true },
  { label: "Trust", href: "#trust", active: false },
  { label: "Results", href: "#results", active: false },
  { label: "Quickstart", href: "#quickstart", active: false },
];

// Hold-to-scroll recording aid: with `?record` in the URL, holding ArrowDown scrolls
// the page at a constant, buttery-smooth speed (requestAnimationFrame), so the whole
// landing can be screen-recorded in one take. Opt-in only, so normal visitors keep
// native keyboard scrolling. Tune the speed with `?record=150` (pixels per second).
function HoldToScroll() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (!params.has("record")) return;
    const speed = Number(params.get("record")) || 200; // px per second

    let scrolling = false;
    let raf = 0;
    let last = 0;
    const step = (now: number) => {
      if (!scrolling) return;
      window.scrollBy(0, (speed * (now - last)) / 1000); // move ∝ elapsed time
      last = now;
      raf = requestAnimationFrame(step);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "ArrowDown") return;
      e.preventDefault(); // no native step-scroll layered on top
      if (!scrolling) {
        scrolling = true;
        last = performance.now();
        raf = requestAnimationFrame(step);
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") scrolling = false;
    };

    // exact per-frame motion — override any CSS smooth-scrolling
    const prevBehavior = document.documentElement.style.scrollBehavior;
    document.documentElement.style.scrollBehavior = "auto";
    window.addEventListener("keydown", onKeyDown, { passive: false });
    window.addEventListener("keyup", onKeyUp);
    return () => {
      scrolling = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      document.documentElement.style.scrollBehavior = prevBehavior;
    };
  }, []);
  return null;
}

function Index() {
  const [menuOpen, setMenuOpen] = useState(false);
  // Reveal the hero exactly once, after fonts are ready — avoids the SSR-paint /
  // font-swap "double animation" glitch and keeps the entrance fast.
  const [show, setShow] = useState(false);
  useEffect(() => {
    let done = false;
    const reveal = () => {
      if (done) return;
      done = true;
      setShow(true);
    };
    const fallback = setTimeout(reveal, 1500);
    const fonts = (document as unknown as { fonts?: { ready?: Promise<unknown> } }).fonts;
    if (fonts?.ready) fonts.ready.then(reveal);
    else reveal();
    return () => clearTimeout(fallback);
  }, []);

  return (
    <div className="relative w-full bg-black overflow-hidden">
      <HoldToScroll />
      {/* Hero */}
      <div className={`relative ${show ? "hero-ready" : ""}`}>
        {/* generated thermal field behind the entire hero — see components/HeatField */}
        <HeatField className="absolute inset-0 w-full h-full z-0 pointer-events-none" />
        <div
          className="absolute inset-0 z-0 pointer-events-none"
          style={{
            background:
              "radial-gradient(ellipse 80% 58% at 50% 24%, rgba(11,9,8,0.58), transparent 82%), linear-gradient(to bottom, rgba(11,9,8,0.5), rgba(11,9,8,0.14) 32%, rgba(11,9,8,0.55) 86%, #0b0908)",
          }}
        />
        {/* Header */}
        <header className="relative z-10 flex items-center px-[20px] pt-6">
          <a href="#top" className="shrink-0 flex items-center gap-3 anim-rise" style={{ animationDelay: "60ms" }}>
            <LogoMark size={44} />
            <span className="text-[19px] font-medium tracking-tight text-neutral-100">Hotpath</span>
          </a>
          <nav className="hidden md:flex items-center gap-[30px] ml-[64px]">
            {navItems.map((item, i) => (
              <a
                key={item.label}
                href={item.href}
                className={`text-[15px] text-neutral-100 ${item.active ? "opacity-100" : "opacity-50"} hover:opacity-100 transition-opacity anim-rise`}
                style={{ animationDelay: `${120 + i * 40}ms` }}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="ml-auto hidden md:flex items-center gap-5 anim-pop" style={{ animationDelay: "220ms" }}>
            <Link to="/docs" className="text-[15px] text-neutral-100 opacity-50 hover:opacity-100 transition-opacity">
              Docs
            </Link>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 bg-accent text-[#0b0908] rounded-md py-[11px] px-[18px] text-[15px] font-medium hover:bg-accent-soft transition-colors"
            >
              <Icon path={ICONS.github} size={17} /> View on GitHub
            </a>
          </div>
          <button
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
            className="ml-auto md:hidden flex flex-col gap-1.5 p-2 anim-pop"
            style={{ animationDelay: "600ms" }}
          >
            <span className="block w-6 h-0.5 bg-neutral-100" />
            <span className="block w-6 h-0.5 bg-neutral-100" />
            <span className="block w-6 h-0.5 bg-neutral-100" />
          </button>
        </header>

        {menuOpen && (
          <div className="fixed inset-0 z-[100] bg-black md:hidden flex flex-col p-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <LogoMark size={40} />
                <span className="text-lg font-medium text-neutral-100">Hotpath</span>
              </div>
              <button aria-label="Close menu" onClick={() => setMenuOpen(false)} className="p-2 text-neutral-100 text-3xl leading-none">×</button>
            </div>
            <nav className="flex flex-col gap-6 mt-12">
              {navItems.map((item) => (
                <a key={item.label} href={item.href} onClick={() => setMenuOpen(false)} className="text-3xl text-neutral-100 font-medium">
                  {item.label}
                </a>
              ))}
              <Link to="/docs" className="text-3xl text-neutral-100 font-medium">Docs</Link>
            </nav>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="mt-auto bg-accent text-[#0b0908] rounded-md py-4 text-center text-base font-medium">
              View on GitHub
            </a>
          </div>
        )}

        <div id="top" />

        {/* window — translucent so the heat field shows through */}
        <div className="relative z-10 mt-[10px] mx-[20px] rounded-2xl overflow-hidden anim-fade border border-white/10" style={{ backgroundColor: "rgba(11,9,8,0.5)" }}>
          <div className="relative">
            <div className="relative overflow-hidden m-4 mb-0 border border-white/10 rounded-2xl flex flex-col text-left pt-[56px] sm:pt-[72px] px-6 sm:px-10 pb-0">
              <div className="absolute inset-0 bg-grid z-0 opacity-25" />
              {/* quiet ground under the copy, which now sits on the left */}
              <div className="absolute inset-0 z-0 pointer-events-none" style={{ background: "radial-gradient(ellipse 80% 78% at 26% 34%, rgba(11,9,8,0.8), transparent 72%)" }} />

              <div className="relative z-10 w-full max-w-[1124px] mx-auto grid grid-cols-1 lg:grid-cols-12">
                <div className="lg:col-span-9 flex flex-col items-start">
                  <motion.div
                    className="eyebrow-rule mb-7"
                    initial={{ opacity: 0, y: 10 }}
                    animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 10 }}
                    transition={{ duration: 0.5, delay: 0.05, ease: "easeOut" }}
                  >
                    <span className="font-instrument text-[10px] sm:text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-400">
                      Performance agent · Hack the North 2026
                    </span>
                  </motion.div>

                  <WordsReveal
                    as="h1"
                    className="text-[40px] leading-[44px] sm:text-[58px] sm:leading-[62px] lg:text-[84px] lg:leading-[86px] font-semibold text-neutral-100 max-w-[900px] mb-[14px] sm:mb-[18px] block"
                    text="AI can write optimizations."
                    active={show}
                    step={0.05}
                    duration={0.7}
                    delay={0.18}
                  />
                  <WordsReveal
                    as="p"
                    className="font-display text-[22px] sm:text-[28px] lg:text-[32px] font-medium text-accent mb-[22px] sm:mb-[26px] block"
                    text="It can't tell you if they work."
                    active={show}
                    step={0.07}
                    duration={0.6}
                    delay={0.6}
                  />
                  <WordsReveal
                    as="p"
                    className="text-[15px] sm:text-[17px] lg:text-[18px] opacity-60 text-neutral-100 max-w-[52ch] leading-relaxed mb-[26px] sm:mb-[32px] block"
                    text="Hotpath is an AI agent that makes your code faster and proves every change is correct. It profiles, proposes, tests and benchmarks — and keeps only the changes that are both correct and measurably faster."
                    active={show}
                    step={0.018}
                    duration={0.6}
                    delay={0.8}
                  />

                  {/* command bar */}
                  <motion.div
                    className="relative w-[572px] max-w-full h-12 mb-[34px]"
                    initial={{ opacity: 0, y: 12 }}
                    animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 12 }}
                    transition={{ duration: 0.55, delay: 0.95, ease: "easeOut" }}
                  >
                    <div className="absolute inset-0 bg-neutral-900/80 outline outline-[1.30px] outline-white/10 rounded-md flex items-center pl-4 pr-1.5 gap-3">
                      <span className="text-accent-soft shrink-0"><Icon path={ICONS.terminal} size={18} /></span>
                      <CommandBar text="hotpath run configs/demo_repo.yaml" startDelay={1100} speed={38} />
                      <CopyButton value="hotpath run configs/demo_repo.yaml" />
                    </div>
                  </motion.div>
                </div>
              </div>

              <HeroDashboard show={show} />
            </div>
          </div>
        </div>

        <div className="absolute bottom-0 left-0 w-full h-[300px] bg-gradient-to-t from-black via-black/90 to-transparent pointer-events-none z-50" />
      </div>

      {/* Thesis */}
      <section id="thesis" className="px-[20px] pt-[120px] pb-[120px]">
        <ThesisHeader />
        <ThesisCards />
      </section>

      {/* Stats */}
      <StatsSection />

      {/* The loop */}
      <MechanismSection />
      <FrameworkSection />

      {/* Rejections → reason (pills) */}
      <section className="bg-black pb-24">
        <div className="max-w-7xl mx-auto px-5 flex flex-col gap-6">
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
            <WordsReveal as="h2" className="text-3xl lg:text-4xl text-neutral-100 leading-tight block" text="Every rejection is recorded with its reason." step={0.06} />
            <p className="text-neutral-500 text-base shrink-0">
              harness verdicts: <span className="text-emerald-400 font-medium">{TOTALS.shipped} kept</span> · {TOTALS.proposed - TOTALS.shipped} rejected
            </p>
          </div>
          <div className="flex flex-col gap-3 lg:gap-4">
            <div className="flex flex-col lg:flex-row w-full gap-3 lg:gap-4">
              {PILLS.slice(0, 3).map((e, i) => (
                <PillReveal key={e.id} delay={0.3 + i * 0.1}>
                  <VerdictPillCard exp={e} />
                </PillReveal>
              ))}
            </div>
            <div className="flex flex-col lg:flex-row w-full gap-3 lg:gap-4">
              {PILLS.slice(3, 6).map((e, i) => (
                <PillReveal key={e.id} delay={0.4 + i * 0.1}>
                  <VerdictPillCard exp={e} />
                </PillReveal>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* The trust moment */}
      <TrustSection />

      {/* Funnel + results */}
      <FunnelSection />
      <ResultsSection />

      {/* Quickstart */}
      <QuickstartSection />

      {/* Footer */}
      <motion.footer className="bg-black border-t-2 border-neutral-100/20" initial="hidden" whileInView="visible" viewport={{ once: true, margin: "-80px" }}>
        <div className="max-w-7xl mx-auto px-2 py-16 flex flex-col gap-24">
          <div className="grid grid-cols-1 md:grid-cols-12 gap-10 items-start">
            <div className="md:col-span-4 flex items-center gap-4">
              <motion.div initial={{ scale: 0, opacity: 0 }} whileInView={{ scale: 1, opacity: 1 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}>
                <LogoMark size={48} />
              </motion.div>
              <span className="text-3xl font-medium text-neutral-100" aria-label="Hotpath">
                {"Hotpath".split("").map((ch, i) => (
                  <motion.span key={i} className="inline-block" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.4, delay: 0.3 + i * 0.06, ease: "easeOut" }}>
                    {ch}
                  </motion.span>
                ))}
              </span>
            </div>
            <motion.nav className="md:col-span-4 flex flex-col gap-4" initial="hidden" whileInView="visible" viewport={{ once: true, margin: "-80px" }} transition={{ staggerChildren: 0.12, delayChildren: 0.9 }}>
              {[
                { l: "View on GitHub", h: REPO_URL },
                { l: "Read the README", h: README_URL },
                { l: "The demo script", h: DEMO_URL },
              ].map((item) => (
                <motion.a key={item.l} href={item.h} target="_blank" rel="noreferrer" className="text-base font-medium text-neutral-100 cursor-pointer hover:opacity-70 transition-opacity inline-flex items-center gap-1.5" variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} transition={{ duration: 0.5, ease: "easeOut" }}>
                  {item.l} <Icon path={ICONS.arrowUpRight} size={15} />
                </motion.a>
              ))}
            </motion.nav>
            <motion.nav className="md:col-span-4 flex flex-col gap-4" initial="hidden" whileInView="visible" viewport={{ once: true, margin: "-80px" }} transition={{ staggerChildren: 0.12, delayChildren: 1.5 }}>
              {["hotpath run configs/demo_repo.yaml", "hotpath serve configs/demo_repo.yaml", "hotpath ablate configs/demo_repo.yaml"].map((l) => (
                <motion.span key={l} className="text-sm font-mono text-neutral-400" variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} transition={{ duration: 0.5, ease: "easeOut" }}>
                  $ {l}
                </motion.span>
              ))}
            </motion.nav>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-12 gap-10 items-end">
            <div className="md:col-span-5">
              <motion.p className="text-sm font-medium text-neutral-100" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.5, delay: 2.0, ease: "easeOut" }}>
                Hotpath · Hack the North 2026 · MIT
              </motion.p>
              <motion.p className="text-sm font-normal text-neutral-100 opacity-70 mt-2" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.5, delay: 2.15, ease: "easeOut" }}>
                Built by Avneet Nijjer · co-authored by Pranoy Mukherjee
              </motion.p>
            </div>
            <div className="md:col-span-7">
              <p className="text-sm font-normal text-neutral-100 opacity-70 leading-5 max-w-[866px]">
                <WordsReveal
                  text={`Hotpath never accepts a change because a model says it is faster. Correctness is the exit code of a locked test command; speed is a bootstrap-CI decision against a machine-measured noise floor.${ILLUSTRATIVE ? " Run numbers on this page are illustrative, from the design run, and will be replaced with the measured H100 result." : ""} Planner: OpenAI API · workers: an OpenAI-compatible endpoint · traces: Sentry.`}
                  step={0.02}
                  delay={2.3}
                  duration={0.4}
                />
              </p>
            </div>
          </div>
        </div>
      </motion.footer>

      <GiantWordmark />
    </div>
  );
}

/* ------------------------------------------------------------ giant wordmark */

function GiantWordmark() {
  return (
    <div className="relative w-full bg-black overflow-hidden select-none" aria-label="Hotpath">
      <svg viewBox="0 0 1000 150" className="block w-full h-auto" preserveAspectRatio="xMidYMax meet" role="img" aria-label="Hotpath">
        <motion.text
          x={500}
          y={132}
          textAnchor="middle"
          textLength={984}
          lengthAdjust="spacingAndGlyphs"
          fontFamily="'Archivo', 'Public Sans', sans-serif"
          fontWeight={700}
          fontSize={170}
          fill="#d9662f"
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        >
          HOTPATH
        </motion.text>
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------ hero dashboard */

const TRUST = EXPERIMENTS.find((e) => e.id === TRUST_ID)!;
const ACCEPTED = EXPERIMENTS.filter((e) => e.status === "accepted");
const BEST = ACCEPTED[ACCEPTED.length - 1];

function HeroDashboard({ show }: { show: boolean }) {
  return (
    <div
      className="w-full max-w-[1124px] h-auto md:h-[465px] mx-auto bg-black rounded-[20px] outline outline-[1.4px] outline-neutral-100/10 flex flex-col md:flex-row overflow-hidden relative z-10 anim-rise text-left"
      style={{ animationDelay: "150ms" }}
    >
      {/* sidebar: the six steps */}
      <aside className="w-full md:w-60 shrink-0 md:h-full relative bg-black border-b md:border-b-0 md:border-r border-white/10">
        <motion.div
          className="flex items-center gap-5 px-5 py-5"
          initial={{ opacity: 0, y: 20 }}
          animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
          transition={{ duration: 0.36, delay: 0.06, ease: "easeOut" }}
        >
          <span className="text-sm font-medium text-neutral-100">Loop</span>
          <span className="text-sm font-medium text-neutral-100 opacity-30">Tree</span>
        </motion.div>
        <div className="flex flex-col gap-1 px-3 pb-4 border-t border-white/10 pt-3">
          {LOOP.map((l, i) => {
            const on = l.id === "verify";
            return (
              <motion.div
                key={l.id}
                initial={{ opacity: 0, y: 16 }}
                animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 16 }}
                transition={{ duration: 0.6, delay: 0.18 + i * 0.12, ease: "easeOut" }}
              >
                <div className={`flex items-center gap-3 px-3 py-2 rounded-xl ${on ? "bg-white/[0.06] outline outline-1 outline-white/10" : ""}`}>
                  <span className={`w-7 h-7 rounded-md flex items-center justify-center font-display font-semibold text-xs shrink-0 ${on ? "bg-blue-500 text-white" : "bg-white/5 text-neutral-400 border border-white/10"}`}>
                    {l.n}
                  </span>
                  <span className={`text-sm leading-tight ${on ? "text-neutral-100" : "text-neutral-400"}`}>{l.name}</span>
                  {l.kind === "ai" && <span className="ml-auto text-[10px] uppercase tracking-wider text-neutral-600">ai</span>}
                </div>
              </motion.div>
            );
          })}
        </div>
      </aside>

      {/* right grid */}
      <div className="flex-1 p-4 sm:p-5 flex flex-wrap gap-3 sm:gap-4 content-start">
        <motion.div
          className="w-full lg:flex-1 lg:min-w-[320px] h-[200px] sm:h-[212px]"
          initial={{ opacity: 0, y: 20 }}
          animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
          transition={{ duration: 0.36, delay: 0.18, ease: "easeOut" }}
        >
          <PatchScan active={show} />
        </motion.div>

        {/* experiment log mini */}
        <motion.div
          className="w-full sm:w-[260px] lg:w-[244px] h-[200px] sm:h-[212px] rounded-2xl bg-neutral-950 border border-white/10 p-4 flex flex-col"
          initial={{ opacity: 0, y: 20 }}
          animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
          transition={{ duration: 0.36, delay: 0.26, ease: "easeOut" }}
        >
          <span className="text-xs text-neutral-500 mb-3 uppercase tracking-wider">verdicts · {TOTALS.proposed} proposed</span>
          <div className="flex flex-col gap-1.5">
            {OUTCOMES.map((o) => (
              <div key={o.label} className="flex items-center justify-between">
                <span className="text-xs text-neutral-300 truncate">{o.label}</span>
                <span className={`text-sm font-medium tabular-nums ${o.status === "accepted" ? "text-emerald-400" : o.status === "pruned" ? "text-neutral-400" : "text-red-400"}`}>
                  {o.count}
                </span>
              </div>
            ))}
          </div>
        </motion.div>

        {/* best verified count-up */}
        <motion.div
          className="w-full sm:w-[180px] lg:w-[180px] h-[140px] sm:h-[140px] rounded-2xl bg-[#D0C9B9] p-4 flex flex-col justify-between text-[#171312]"
          initial={{ opacity: 0, y: 20 }}
          animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
          transition={{ duration: 0.36, delay: 0.34, ease: "easeOut" }}
        >
          <span className="text-xs opacity-50 uppercase tracking-wider">verified · decode</span>
          <div>
            <span className="text-4xl font-medium tabular-nums">
              <CountUpInView end={Math.round(BEST.speedup! * 100)} format={(n) => (n / 100).toFixed(2)} duration={1400} active={show} />×
            </span>
            <p className="text-xs opacity-60 mt-1 leading-tight">tokens match the original exactly</p>
          </div>
        </motion.div>

        {/* accepted chain mini bars */}
        <motion.div
          className="w-full sm:flex-1 sm:min-w-[200px] h-[140px] sm:h-[140px] rounded-2xl bg-neutral-950 border border-white/10 p-4 flex flex-col"
          initial={{ opacity: 0, y: 20 }}
          animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
          transition={{ duration: 0.36, delay: 0.42, ease: "easeOut" }}
        >
          <span className="text-xs text-neutral-500 mb-2 uppercase tracking-wider">accepted chain · tokens/sec</span>
          <MiniBars active={show} />
        </motion.div>
      </div>
    </div>
  );
}

function PatchScan({ active }: { active: boolean }) {
  const gates = ["apply", "locked", "tests", "bench"];
  const failAt = 2; // the tests gate
  const [phase, setPhase] = useState(0); // 0 idle, 1..3 scanning, 4 rejected
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const seq = [600, 700, 700, 700, 2600];
    let i = 0;
    let timer: ReturnType<typeof setTimeout>;
    const step = () => {
      if (cancelled) return;
      setPhase(i);
      const wait = seq[Math.min(i, seq.length - 1)];
      i = i >= 4 ? 0 : i + 1;
      timer = setTimeout(step, wait);
    };
    step();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active]);

  const rejected = phase === 4;
  return (
    <div className="relative w-full h-full rounded-2xl bg-neutral-950 border border-white/10 overflow-hidden p-4 flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-neutral-500 uppercase tracking-wider">{TRUST.id} · {TRUST.speedup}× faster</span>
        <span className="text-xs text-neutral-600 uppercase tracking-wider">model.py</span>
      </div>
      <pre className="text-[11px] sm:text-xs font-mono text-neutral-400 leading-relaxed overflow-hidden flex-1">{TRUST.diff}</pre>
      {/* gate ticks */}
      <div className="flex items-center gap-2 mt-2">
        {gates.map((g, idx) => {
          const reached = phase > idx && idx < failAt;
          const failedHere = rejected && idx === failAt;
          const skipped = rejected && idx > failAt;
          return (
            <div
              key={g}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-[11px] font-display font-semibold transition-colors duration-200 ${
                failedHere
                  ? "bg-red-500/20 text-red-300 outline outline-1 outline-red-500/40"
                  : reached
                  ? "bg-emerald-500/15 text-emerald-300"
                  : phase > idx && !skipped
                  ? "bg-blue-500/20 text-blue-300"
                  : "bg-white/5 text-neutral-600"
              }`}
            >
              {reached && <Icon path={ICONS.check} size={11} />}
              {failedHere && <Icon path={ICONS.x} size={11} />}
              {g}
            </div>
          );
        })}
      </div>
      {active && phase >= 1 && phase <= 3 && (
        <motion.div
          key={phase}
          className="absolute top-0 bottom-0 w-px bg-blue-400/80 shadow-[0_0_12px_2px_rgba(217,102,47,0.65)]"
          initial={{ left: "0%" }}
          animate={{ left: "100%" }}
          transition={{ duration: 0.7, ease: "linear" }}
        />
      )}
      {rejected && (
        <motion.div
          className="absolute top-3 right-3 bg-red-500/90 text-white text-[11px] font-bold tracking-wide px-2.5 py-1 rounded-md rotate-[-6deg]"
          initial={{ opacity: 0, scale: 0.6 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ type: "spring", stiffness: 400, damping: 14 }}
        >
          REJECTED · OUTPUT CHANGED
        </motion.div>
      )}
    </div>
  );
}

function MiniBars({ active }: { active: boolean }) {
  const items = THROUGHPUT_STEPS;
  const max = items[items.length - 1].tokPerSec;
  return (
    <div className="flex-1 flex items-end justify-between gap-1.5">
      {items.map((b, i) => (
        <div key={b.label} className="flex-1 flex flex-col items-center justify-end h-full gap-1">
          <motion.div
            className={`w-full rounded-sm ${i === 0 ? "bg-white/25" : "bg-emerald-500/80"}`}
            initial={{ height: 0 }}
            animate={active ? { height: `${(b.tokPerSec / max) * 100}%` } : { height: 0 }}
            transition={{ duration: 0.7, delay: 0.2 + i * 0.08, ease: "easeOut" }}
          />
          <span className="text-[8px] text-neutral-600 truncate w-full text-center">{b.label}</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ thesis */

function ThesisHeader() {
  return (
    <div className="flex flex-col md:flex-row items-start justify-between mb-[80px] gap-8 max-w-7xl mx-auto w-full">
      <motion.div
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-100px" }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className="flex flex-col gap-8 w-full md:max-w-[760px]"
      >
        <span className="text-xs font-semibold uppercase tracking-[0.25em] text-neutral-500">§ 01 — the problem</span>
        <WordsReveal
          as="h2"
          className="text-4xl leading-tight text-neutral-100 font-normal"
          text="Performance work is profile, guess, rewrite, re-test, re-benchmark — dozens of times. AI speeds up the guessing. Hotpath closes the loop."
        />
        <div className="flex items-center gap-4">
          <a href="#loop" className="bg-white text-black rounded-xl px-5 py-4 text-[15px] font-medium hover:bg-neutral-200 transition-colors">
            See the loop
          </a>
          <a href="#trust" className="text-neutral-400 hover:text-neutral-100 transition-colors text-[15px]">Or skip to the rejected 1.8× →</a>
        </div>
      </motion.div>
      <motion.p
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-100px" }}
        transition={{ duration: 0.8, ease: "easeOut", delay: 0.15 }}
        className="hidden md:block text-xl text-neutral-500 text-right shrink-0 max-w-[220px]"
      >
        no change is accepted without evidence
      </motion.p>
    </div>
  );
}

function ThesisCards() {
  const cardAnim = (delay: number) => ({
    initial: { opacity: 0, y: 50 },
    whileInView: { opacity: 1, y: 0 },
    viewport: { once: true, margin: "-80px" },
    transition: { duration: 0.7, delay, ease: "easeOut" as const },
  });
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-[30px] max-w-7xl mx-auto">
      {/* fast but wrong */}
      <motion.div
        {...cardAnim(0.1)}
        className="relative h-[520px] rounded-3xl overflow-hidden bg-neutral-950 border border-white/5 flex flex-col pt-12 px-7"
        style={{ backgroundImage: "radial-gradient(ellipse at 50% -10%, rgba(196,84,74,0.12), transparent 60%)" }}
      >
        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-red-400/80">{THESIS_CARDS[0].eyebrow}</span>
        <WordsReveal as="h3" className="mt-4 text-4xl text-neutral-100 leading-tight" text={THESIS_CARDS[0].title} delay={0.2} />
        <WordsReveal as="p" className="mt-5 text-base opacity-50 text-neutral-100 max-w-[340px]" text={THESIS_CARDS[0].body} delay={0.4} step={0.02} duration={0.5} />
        <div className="mt-auto mb-10">
          <div className="text-6xl font-medium text-red-400 tabular-nums"><CountUpInView end={TOTALS.brokeCorrectness} duration={900} />/{TOTALS.proposed}</div>
          <p className="text-sm text-neutral-500 mt-2">{THESIS_CARDS[0].statLabel}</p>
        </div>
      </motion.div>

      {/* within noise */}
      <motion.div
        {...cardAnim(0.3)}
        className="relative h-[520px] rounded-3xl overflow-hidden bg-neutral-900 border border-white/5 flex flex-col pt-12 px-7"
        style={{ backgroundImage: "radial-gradient(ellipse at 50% -10%, rgba(224,179,65,0.10), transparent 60%)" }}
      >
        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-400/80">{THESIS_CARDS[1].eyebrow}</span>
        <WordsReveal as="h3" className="mt-4 text-4xl text-neutral-100 leading-tight" text={THESIS_CARDS[1].title} delay={0.4} />
        <WordsReveal as="p" className="mt-5 text-base opacity-50 text-neutral-100 max-w-[340px]" text={THESIS_CARDS[1].body} delay={0.6} step={0.02} duration={0.5} />
        <div className="mt-auto mb-10">
          <div className="text-6xl font-medium text-amber-400 tabular-nums"><CountUpInView end={TOTALS.withinNoise} duration={1100} />/{TOTALS.proposed}</div>
          <p className="text-sm text-neutral-500 mt-2">{THESIS_CARDS[1].statLabel}</p>
        </div>
      </motion.div>

      {/* the hotpath rule — measured candidates */}
      <motion.div {...cardAnim(0.5)} className="relative h-[520px] rounded-3xl overflow-hidden bg-[#D0C9B9] flex flex-col">
        <div className="p-7 pb-0">
          <span className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-800/80">The Hotpath rule</span>
          <WordsReveal as="h3" className="mt-3 text-[30px] leading-tight text-neutral-900 font-normal [font-family:'Archivo',sans-serif]" text="Correct and faster than the noise. Nothing else ships." delay={0.6} step={0.06} />
        </div>
        <MeasuredCardChart />
        <div className="absolute bottom-0 left-0 w-full h-[78px] flex items-end pb-5 px-7 gap-2">
          <span className="text-4xl text-neutral-900 leading-none font-medium tabular-nums">
            <CountUp end={TOTALS.shipped} duration={1600} active={true} />/{TOTALS.proposed}
          </span>
          <span className="text-sm text-neutral-900/70 leading-none pb-1">shipped · 0 unverified</span>
        </div>
      </motion.div>
    </div>
  );
}

// every measured candidate, in run order: red = fast but rejected, green = kept
function MeasuredCardChart() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInViewFM(ref, { once: true, margin: "-80px" });
  const bars = EXPERIMENTS.filter((e) => e.tokPerSec !== null);
  const max = Math.max(...bars.map((b) => b.tokPerSec!));
  return (
    <motion.div ref={ref} className="absolute bottom-[86px] left-0 w-full h-[220px] px-7 flex items-end justify-between gap-2 overflow-hidden" initial="hidden" animate={inView ? "visible" : "hidden"} transition={{ staggerChildren: 0.08, delayChildren: 0.7 }}>
      {bars.map((b) => (
        <div key={b.id} className="relative flex-1 h-full flex items-end justify-center">
          <motion.div
            className={`w-2/3 rounded-t-sm ${b.status === "accepted" ? "bg-emerald-700" : "bg-red-500/40 border-t border-red-600/50"}`}
            variants={{ hidden: { height: 0 }, visible: { height: `${((b.tokPerSec! - BASELINE_TOK_S * 0.8) / (max - BASELINE_TOK_S * 0.8)) * 100}%` } }}
            transition={{ duration: 0.6, ease: "easeOut" }}
            style={{ maxHeight: "100%" }}
          />
          <span className="absolute left-0 right-0 text-[8px] text-neutral-900/50 text-center truncate -bottom-4">{b.tokPerSec}</span>
        </div>
      ))}
    </motion.div>
  );
}

/* ------------------------------------------------------------ stats */

function StatsSection() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInViewFM(ref, { once: true, margin: "-100px" });
  return (
    <section ref={ref} className="bg-black py-24">
      <div className="max-w-7xl mx-auto px-5 flex flex-col md:flex-row items-center justify-between gap-12">
        <motion.div className="flex flex-col items-center text-center gap-3" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.7, ease: "easeOut" }}>
          <span className="text-6xl text-emerald-400 font-medium tabular-nums"><CountNumber to={0} start={inView} /></span>
          <p className="text-2xl text-neutral-100 opacity-40 max-w-[250px]">changes kept on a model's word alone</p>
        </motion.div>
        <motion.div className="flex flex-col items-center text-center gap-3" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.7, ease: "easeOut", delay: 0.2 }}>
          <span className="text-6xl text-neutral-100 font-medium tabular-nums"><CountNumber to={Math.round(((TOTALS.proposed - TOTALS.shipped) / TOTALS.proposed) * 100)} start={inView} />%</span>
          <p className="text-2xl text-neutral-100 opacity-40 max-w-[260px]">of AI-proposed optimizations rejected</p>
        </motion.div>

        <motion.div className="relative bg-neutral-900 rounded-3xl p-10 w-full max-w-[520px] overflow-hidden border border-white/5" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.7, ease: "easeOut", delay: 0.4 }}>
          <p className="text-3xl text-white leading-snug">
            Across {TOTALS.proposed} experiments on the transformer, Hotpath kept {TOTALS.shipped} changes worth{" "}
            <span className="relative inline-block align-baseline px-2 py-1">
              <motion.span aria-hidden className="absolute inset-0 bg-emerald-400 rounded-sm origin-left" initial={{ scaleX: 0 }} animate={inView ? { scaleX: 1 } : { scaleX: 0 }} transition={{ duration: 0.91, delay: 1.55, ease: "linear" }} style={{ transformOrigin: "left center" }} />
              <span className="relative font-medium text-emerald-400">{BEST.speedup}× decode</span>
              <motion.span aria-hidden className="absolute inset-0 px-2 py-1 font-medium text-stone-950 whitespace-nowrap" initial={{ clipPath: "inset(0 100% 0 0)" }} animate={inView ? { clipPath: "inset(0 0% 0 0)" } : { clipPath: "inset(0 100% 0 0)" }} transition={{ duration: 0.91, delay: 1.55, ease: "linear" }}>
                {BEST.speedup}× decode
              </motion.span>
            </span>{" "}
            — every one correct and faster than the noise.
          </p>
          <div className="mt-6 flex items-center gap-2 text-sm text-neutral-500">
            <span className="w-2 h-2 rounded-full bg-emerald-400" /> {ILLUSTRATIVE ? "illustrative run · replaced by the measured H100 result" : "measured run"}
          </div>
        </motion.div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ mechanism: the six steps */

function MechanismSection() {
  return (
    <section id="loop" className="bg-black">
      <div className="max-w-7xl mx-auto px-5 py-24 flex flex-col gap-16">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-start">
          <div className="flex flex-col gap-8">
            <span className="text-xs font-semibold uppercase tracking-[0.25em] text-neutral-500">§ 02 — the loop</span>
            <WordsReveal as="h2" className="text-5xl lg:text-6xl leading-tight text-white" text="Six steps. The AI may be wrong. The harness can't be talked into it." step={0.07} duration={0.6} />
            <WordsReveal as="p" className="text-2xl opacity-60 text-neutral-100 max-w-[520px]" text="Like a scientist: form a hypothesis, run the experiment, keep the change only if the data supports it. Two steps are models. Four are plain, deterministic code the models cannot edit." step={0.03} delay={0.3} duration={0.5} />
            <motion.div className="flex gap-3" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.6, delay: 0.8, ease: "easeOut" }}>
              <a href={HARNESS_URL} target="_blank" rel="noreferrer" className="bg-white text-black px-7 py-4 rounded-xl font-medium hover:bg-neutral-200 transition-colors">Read harness.py</a>
              <a href={BENCHMARK_URL} target="_blank" rel="noreferrer" className="bg-white/10 text-white px-7 py-4 rounded-xl font-medium hover:bg-white/20 transition-colors">The noise rule</a>
            </motion.div>
          </div>

          {/* harness code window */}
          <motion.div className="rounded-3xl border border-white/10 overflow-hidden flex flex-col" style={{ backgroundColor: "#141110" }} initial={{ opacity: 0, y: 40 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.7, delay: 0.2, ease: "easeOut" }}>
            <div className="flex items-center px-5 py-4">
              <span className="text-xs font-mono text-neutral-500">hotpath/harness.py · simplified</span>
            </div>
            <div className="mx-[20px] mb-[20px] relative rounded-2xl overflow-hidden border border-white/10">
              <div className="absolute inset-0 bg-grid opacity-40" />
              <div className="absolute inset-0" style={{ background: "radial-gradient(ellipse at 70% 0%, rgba(217,102,47,0.10), transparent 60%)" }} />
              <div className="relative p-6">
                <Typewriter
                  className="text-[13px] sm:text-sm opacity-80 text-neutral-200 leading-relaxed whitespace-pre-wrap font-mono"
                  delay={0.6}
                  speed={9}
                  text={`async def run_experiment(self, exp, parent_bench, ...):
    wt = self.ws.create_worktree(exp.parent_commit, exp.id)
    # 1. apply edits (locked paths checked before anything is written)
    try:
        self.ws.apply_edits(wt, exp.edits, cfg.editable, cfg.locked)
    except LockedFileError as e:
        return exp.set_status(locked_file, f"blocked: {e}")
    except PatchError as e:
        return exp.set_status(patch_failed, str(e))
    # 2. correctness gate: a plain exit code
    exp.correctness = await self.run_tests(wt)
    if not exp.correctness.passed:
        return exp.set_status(rejected_correctness, "tests failed")
    # 3. speed gate: parent re-benchmarked next to the candidate
    exp.comparison = compare(parent_bench, exp.benchmark, ...)
    if not exp.comparison.significant:
        return exp.set_status(rejected_speed, exp.comparison.reason)
    # 4. keep it
    exp.commit = self.ws.commit(wt, exp.id, ...)
    exp.set_status(accepted)`}
                />
              </div>
            </div>
          </motion.div>
        </div>

        {/* six step cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {LOOP.map((l, i) => (
            <StepCard key={l.id} step={l} index={i} />
          ))}
        </div>
      </div>
    </section>
  );
}

function StepCard({ step, index }: { step: (typeof LOOP)[number]; index: number }) {
  const isGate = step.id === "verify";
  return (
    <motion.div
      className={`relative rounded-2xl p-6 flex flex-col gap-4 border ${isGate ? "bg-blue-500/[0.07] border-blue-500/30" : "bg-neutral-950 border-white/10"}`}
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.6, delay: index * 0.1, ease: "easeOut" }}
    >
      <div className="flex items-center justify-between">
        <span className={`font-display font-semibold text-sm px-2.5 py-1 rounded-md ${isGate ? "bg-blue-500 text-white" : "bg-white/10 text-neutral-300"}`}>{step.n}</span>
        {isGate ? (
          <span className="text-[11px] font-display uppercase tracking-wider text-blue-300">the gate</span>
        ) : (
          <span className="text-[11px] font-display uppercase tracking-wider text-neutral-500">{step.kind === "ai" ? "ai · may be wrong" : "harness"}</span>
        )}
      </div>
      <div>
        <h3 className="text-lg font-medium text-neutral-100">{step.name}</h3>
        <p className="text-sm italic text-neutral-500 mt-1">{step.question}</p>
      </div>
      <p className="text-sm text-neutral-400 leading-relaxed flex-1">{step.detail}</p>
      <pre className="text-[10.5px] font-mono text-neutral-500 leading-relaxed bg-black/40 rounded-lg p-3 overflow-x-auto border border-white/5">{step.code}</pre>
      <span className="text-[11px] text-neutral-600 font-mono">{step.tool}</span>
    </motion.div>
  );
}

/* ------------------------------------------------------------ verdict pills */

const PILLS: Experiment[] = ["exp_0004", "exp_0029", "exp_0017", "exp_0006", "exp_0026", "exp_0011"].map(
  (id) => EXPERIMENTS.find((e) => e.id === id)!,
);

function VerdictPillCard({ exp }: { exp: Experiment }) {
  const good = exp.status === "accepted";
  const verdict = good
    ? `kept · ${exp.short}`
    : `${STATUS_LABEL[exp.status]} · ${exp.short}`;
  return (
    <div
      className={`h-20 w-full grow flex items-center gap-4 px-7 rounded-2xl cursor-default hover:scale-[1.02] transition-transform min-w-0 border ${good ? "bg-emerald-500/10 border-emerald-500/30" : "bg-[#171312] border-white/10"}`}
    >
      <div className={`size-9 rounded-lg flex items-center justify-center shrink-0 ${good ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
        <Icon path={good ? ICONS.check : ICONS.shield} size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-lg font-medium truncate text-neutral-100">{exp.idea}</div>
        <div className={`text-xs font-medium truncate ${good ? "text-emerald-400" : "text-neutral-500"}`}>{verdict}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ the trust moment */

function TrustSection() {
  return (
    <section id="trust" className="bg-black">
      <div className="max-w-7xl mx-auto px-5 py-24 flex flex-col gap-16 relative">
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-end gap-8">
          <div className="flex flex-col gap-6 max-w-2xl">
            <span className="text-xs font-semibold uppercase tracking-[0.25em] text-neutral-500">§ 03 — the trust moment</span>
            <WordsReveal as="h2" className="text-5xl lg:text-6xl text-neutral-100 leading-tight block" text="The agent found a 1.8× speedup. Hotpath threw it out." step={0.06} duration={0.6} />
            <WordsReveal as="p" className="text-xl lg:text-2xl opacity-60 text-neutral-100 leading-8 block" text="The planner proposed int8 weights. The benchmark said 1.8× faster. The locked test said the model now writes different text. So it was rejected, and the reason was saved." step={0.025} delay={0.2} duration={0.5} />
          </div>
          <motion.a href={HARNESS_URL} target="_blank" rel="noreferrer" initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.6, delay: 0.5, ease: "easeOut" }} className="inline-flex shrink-0 items-center gap-2 bg-white text-black px-7 py-4 rounded-xl font-medium text-lg hover:bg-neutral-200 transition-colors">
            See the harness <Icon path={ICONS.arrowUpRight} size={18} />
          </motion.a>
        </div>

        <div className="flex flex-col lg:flex-row gap-10">
          {/* left: the rejected number */}
          <motion.div className="w-full lg:w-[38%] shrink-0 flex flex-col gap-6" initial={{ opacity: 0, y: 40 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.7, ease: "easeOut" }}>
            <div className="rounded-3xl bg-neutral-900 border border-white/5 p-8 flex flex-col gap-6">
              <div className="flex items-baseline gap-3">
                <span className="text-7xl font-medium text-red-400 tabular-nums line-through decoration-2"><CountUpInView end={18} format={(n) => (n / 10).toFixed(1)} duration={1600} />×</span>
                <span className="text-sm text-neutral-400 leading-tight">measured speedup<br />never counted</span>
              </div>
              <div className="grid grid-cols-2 gap-4 border-t border-white/10 pt-6">
                <Stat big="17" label="token where output diverged" />
                <Stat big="0.41" label="max |Δlogit| · tolerance 1e-3" />
              </div>
              <p className="text-sm text-neutral-500 leading-relaxed">
                <span className="italic text-neutral-400">Correctness comes first, speed second.</span> Tokens must match the original under greedy decoding, and logits must stay within tolerance. A fused kernel that reorders floating-point math passes; a change that alters what the model says does not, however fast it is.
              </p>
            </div>
          </motion.div>

          {/* right: experiment replay */}
          <motion.div className="w-full lg:w-[62%]" initial={{ opacity: 0, y: 40 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.7, delay: 0.15, ease: "easeOut" }}>
            <ExperimentReplay />
          </motion.div>
        </div>
      </div>
    </section>
  );
}

function Stat({ big, label }: { big: string; label: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-3xl font-medium text-neutral-100 tabular-nums">{big}</span>
      <span className="text-sm text-neutral-500 mt-1">{label}</span>
    </div>
  );
}

// Click any experiment: its verdict, the planner's reasoning, the harness reason, the diff.
function ExperimentReplay() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInViewFM(ref, { once: true, margin: "-80px" });
  const trace = EXPERIMENTS;
  const [active, setActive] = useState(trace.findIndex((e) => e.id === TRUST_ID));
  const [playing, setPlaying] = useState(false);
  const max = Math.max(...trace.map((t) => t.tokPerSec ?? 0));

  useEffect(() => {
    if (!inView || !playing) return;
    const t = setTimeout(() => setActive((a) => (a + 1) % trace.length), 2600);
    return () => clearTimeout(t);
  }, [inView, playing, active, trace.length]);

  const cur = trace[active];
  const good = cur.status === "accepted";
  return (
    <div ref={ref} className="rounded-3xl border border-white/10 overflow-hidden flex flex-col" style={{ backgroundColor: "#141110" }}>
      <div className="flex justify-between items-center px-5 py-4">
        <span className="hidden sm:inline text-xs font-mono text-neutral-500">.hotpath/hotpath.db · {cur.id}</span>
        <button onClick={() => setPlaying((p) => !p)} className="text-xs font-medium text-neutral-400 hover:text-neutral-100 transition-colors bg-white/5 rounded-md px-2.5 py-1">
          {playing ? "❚❚ pause" : "▶ play"}
        </button>
      </div>

      <div className="px-6 pb-5">
        {/* verdict + speed */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div className="flex items-baseline gap-3 min-w-0">
            <span className={`text-4xl font-medium tabular-nums ${good ? "text-neutral-100" : "text-neutral-500"}`}>{cur.speedup ? `${cur.speedup.toFixed(2)}×` : "—"}</span>
            <span className="text-xs text-neutral-500 truncate">{cur.idea}</span>
          </div>
          <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md outline outline-1 ${good ? "bg-emerald-500/15 text-emerald-300 outline-emerald-500/30" : "bg-red-500/15 text-red-300 outline-red-500/30"}`}>
            <Icon path={good ? ICONS.check : ICONS.x} size={13} /> {STATUS_LABEL[cur.status]}
          </span>
        </div>

        {/* reason + reasoning */}
        <div className="rounded-xl bg-black/50 border border-white/10 p-4 mb-3">
          <span className="text-[11px] text-neutral-500 uppercase tracking-wider">harness · reason</span>
          <p className={`text-[13px] font-mono mt-1.5 leading-relaxed ${good ? "text-emerald-300" : "text-red-300"}`}>{cur.reason}</p>
          <span className="block text-[11px] text-neutral-500 uppercase tracking-wider mt-3">planner · reasoning</span>
          <p className="text-[13px] text-neutral-400 mt-1.5 leading-relaxed italic">{cur.rationale}</p>
        </div>

        {/* diff pane */}
        <div className="rounded-xl bg-black/50 border border-white/10 p-4 mb-4 min-h-[120px]">
          <span className="text-[11px] text-neutral-500 uppercase tracking-wider">diff</span>
          <pre className="text-[12px] sm:text-[13px] font-mono leading-relaxed whitespace-pre-wrap mt-2">
            {cur.diff.split("\n").map((ln, i) => (
              <div key={i} className={ln.startsWith("+") ? "text-emerald-300" : ln.startsWith("-") ? "text-red-300" : "text-neutral-400"}>{ln}</div>
            ))}
          </pre>
        </div>

        {/* timeline scrubber */}
        <div className="flex items-end justify-between gap-1.5 h-16">
          {trace.map((t, i) => {
            const h = t.tokPerSec ? (t.tokPerSec / max) * 100 : 12;
            const isActive = i === active;
            const ok = t.status === "accepted";
            return (
              <button
                key={t.id}
                onClick={() => { setActive(i); setPlaying(false); }}
                className="flex-1 flex flex-col items-center justify-end h-full group"
                aria-label={`${t.id}: ${t.idea}, ${STATUS_LABEL[t.status]}`}
              >
                <motion.div
                  className={`w-full rounded-t-sm transition-colors ${isActive ? "bg-neutral-100" : ok ? "bg-emerald-400" : "bg-red-400/50 group-hover:bg-red-400/70"}`}
                  initial={{ height: 0 }}
                  animate={inView ? { height: `${h}%` } : { height: 0 }}
                  transition={{ duration: 0.6, delay: 0.1 + i * 0.05, ease: "easeOut" }}
                />
                <span className={`text-[9px] font-display mt-1 ${isActive ? "text-neutral-300" : "text-neutral-600"}`}>{t.n}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ funnel */

function FunnelSection() {
  return (
    <section className="bg-black max-w-7xl mx-auto px-5 py-20">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-end mb-12">
        <div className="flex flex-col gap-6">
          <Eyebrow>Why you need a verifier</Eyebrow>
          <WordsReveal as="h2" className="text-4xl lg:text-5xl leading-tight text-white" text="Most AI-proposed optimizations fail. That's the point." step={0.04} />
        </div>
        <p className="text-lg text-neutral-400 leading-8">Out of {TOTALS.proposed} ideas, {TOTALS.shipped} shipped. A high rejection rate isn't the agent failing — it's the evidence that verification was necessary. If the model is usually wrong, correctness can't be something you trust. It has to be something you <span className="text-neutral-100">measure</span>.</p>
      </div>
      <div className="flex items-center justify-between mb-5">
        <Eyebrow>From idea to shipped change</Eyebrow>
        <FigTag>{ILLUSTRATIVE ? "illustrative run" : "measured run"} · {TOTALS.proposed} proposals</FigTag>
      </div>
      <ChartPanel>
        <Funnel />
      </ChartPanel>
      <div className="grid grid-cols-3 gap-4 mt-6">
        <StatTile value={<><CountUpInView end={Math.round((TOTALS.shipped / TOTALS.proposed) * 1000)} format={(n) => (n / 10).toFixed(1)} duration={1200} />%</>} label="shipped — correct and faster" color="text-emerald-400" />
        <StatTile value={<><CountUpInView end={Math.round((TOTALS.brokeCorrectness / TOTALS.proposed) * 1000)} format={(n) => (n / 10).toFixed(1)} duration={1200} />%</>} label="broke correctness" color="text-red-400" delay={0.08} />
        <StatTile value={<><CountUpInView end={Math.round((TOTALS.withinNoise / TOTALS.proposed) * 1000)} format={(n) => (n / 10).toFixed(1)} duration={1200} />%</>} label="within measurement noise" color="text-amber-400" delay={0.16} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ results */

function ResultsSection() {
  const first = THROUGHPUT_STEPS[0];
  const last = THROUGHPUT_STEPS[THROUGHPUT_STEPS.length - 1];
  return (
    <section id="results" className="bg-black max-w-7xl mx-auto px-5 py-20 scroll-mt-20">
      <div className="flex flex-col gap-6 mb-12 max-w-3xl">
        <Eyebrow>Results</Eyebrow>
        <WordsReveal as="h2" className="text-4xl lg:text-5xl leading-tight text-white" text={`${first.tokPerSec} → ${last.tokPerSec} tokens/sec. Every step verified.`} step={0.04} />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-12">
        <StatTile value={<><CountUpInView end={Math.round(BEST.speedup! * 100)} format={(n) => (n / 100).toFixed(2)} duration={1400} />×</>} label="decode throughput vs baseline" color="text-emerald-400" delay={0} />
        <StatTile value={<CountUpInView end={last.tokPerSec} duration={1400} />} label="tokens/sec, best verified" color="text-neutral-100" delay={0.08} />
        <StatTile value={ACCEPTED.length} label="changes in the accepted chain" color="text-neutral-100" delay={0.16} />
        <StatTile value={0} label="changed outputs shipped" color="text-blue-300" delay={0.24} />
      </div>
      <div className="flex items-center justify-between mb-5">
        <Eyebrow>Throughput over the run</Eyebrow>
        <FigTag>{ILLUSTRATIVE ? "illustrative" : "measured"} · steps are accepted changes</FigTag>
      </div>
      <ChartPanel>
        <ThroughputChart />
      </ChartPanel>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
        <ChartPanel>
          <div className="flex items-center justify-between mb-6">
            <Eyebrow>Before · baseline profile</Eyebrow>
            <FigTag>{FLAME_BEFORE.ms} ms / step</FigTag>
          </div>
          <FlameGraph root={FLAME_BEFORE} scaleMs={FLAME_BEFORE.ms} />
        </ChartPanel>
        <ChartPanel>
          <div className="flex items-center justify-between mb-6">
            <Eyebrow>After · head profile</Eyebrow>
            <FigTag>{FLAME_AFTER.ms} ms / step · {(FLAME_AFTER.ms / FLAME_BEFORE.ms).toFixed(2)}×</FigTag>
          </div>
          <FlameGraph root={FLAME_AFTER} scaleMs={FLAME_BEFORE.ms} />
        </ChartPanel>
      </div>
      <motion.div
        className="rounded-3xl border border-white/10 bg-[#141110] p-8 flex flex-col justify-center gap-5 mt-6"
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-80px" }}
        transition={{ duration: 0.7, ease: "easeOut" }}
      >
        <WordsReveal as="h3" className="text-3xl text-white leading-tight" text="The widest bars shrank." step={0.05} />
        <p className="text-neutral-400 leading-relaxed">Fix the slowest thing and something else becomes the slowest, so Hotpath re-profiles after every accepted change. The profile shows where time went; the benchmark is the authority on how much faster it is.</p>
        <div className="flex flex-wrap gap-2 pt-1">
          {FLAME_NOTES.map((t) => (
            <span key={t.name} className="text-sm text-emerald-200 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-3 py-1.5 tabular-nums">{t.name} {t.before} → {t.after} · {t.how}</span>
          ))}
        </div>
      </motion.div>
    </section>
  );
}

/* ------------------------------------------------------------ the loop diagram */

function FrameworkSection() {
  return (
    <section className="bg-black max-w-7xl mx-auto px-5 pb-20">
      <div className="flex flex-col gap-6 mb-12 max-w-3xl">
        <Eyebrow>The architecture</Eyebrow>
        <WordsReveal as="h2" className="text-4xl lg:text-5xl leading-tight text-white" text="Big model plans. Fast model explores. The harness decides." step={0.04} />
        <WordsReveal as="p" className="text-xl opacity-60 text-neutral-100 leading-8 max-w-[640px]" text="A strong planner reads profiles and history and chooses what to try. Cheap, fast workers write many candidate patches. The harness applies each one in its own worktree, runs the locked tests and the benchmark, and returns a structured verdict." step={0.02} delay={0.2} duration={0.5} />
      </div>
      <div className="flex items-center justify-between mb-5">
        <Eyebrow>The loop</Eyebrow>
        <FigTag>action / verdict</FigTag>
      </div>
      <ChartPanel>
        <LoopDiagram />
      </ChartPanel>
    </section>
  );
}

/* ------------------------------------------------------------ quickstart */

function QuickstartSection() {
  return (
    <section id="quickstart" className="bg-black scroll-mt-20">
      <div className="max-w-7xl mx-auto px-5 py-24">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-start">
          <div className="flex flex-col gap-8">
            <span className="text-xs font-semibold uppercase tracking-[0.25em] text-neutral-500">§ 04 — quickstart</span>
            <WordsReveal as="h2" className="text-5xl lg:text-6xl leading-tight text-white" text="Any repo with a test and a benchmark." step={0.07} duration={0.6} />
            <WordsReveal as="p" className="text-2xl opacity-60 text-neutral-100 max-w-[520px]" text="One small config file: the command that proves it's correct, the command that times it, what the agent may edit, and what it must never touch." step={0.03} delay={0.3} duration={0.5} />
            <motion.div className="flex gap-3" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }} transition={{ duration: 0.6, delay: 0.8, ease: "easeOut" }}>
              <Link to="/docs" className="bg-white text-black px-7 py-4 rounded-xl font-medium hover:bg-neutral-200 transition-colors">Read the docs</Link>
              <a href={README_URL} target="_blank" rel="noreferrer" className="bg-white/10 text-white px-7 py-4 rounded-xl font-medium hover:bg-white/20 transition-colors">README</a>
            </motion.div>
          </div>

          <motion.div className="rounded-3xl border border-white/10 overflow-hidden flex flex-col" style={{ backgroundColor: "#141110" }} initial={{ opacity: 0, y: 40 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.7, delay: 0.2, ease: "easeOut" }}>
            <div className="flex items-center justify-between px-5 py-4">
              <span className="text-xs font-mono text-neutral-500">configs/dryft_h100.yaml</span>
              <CopyButton value={CONFIG_YAML} />
            </div>
            <div className="mx-[20px] mb-[20px] relative rounded-2xl overflow-hidden border border-white/10">
              <div className="absolute inset-0 bg-grid opacity-40" />
              <div className="absolute inset-0" style={{ background: "radial-gradient(ellipse at 70% 0%, rgba(217,102,47,0.10), transparent 60%)" }} />
              <div className="relative p-6">
                <Typewriter className="text-[13px] sm:text-sm opacity-80 text-neutral-200 leading-relaxed whitespace-pre-wrap font-mono" delay={0.4} speed={9} text={CONFIG_YAML} />
              </div>
            </div>
            <p className="px-5 pb-5 text-sm text-neutral-500 leading-relaxed">
              <span className="text-neutral-300">locked</span> is enforced by the harness, not the prompt: an agent rewarded for "tests pass and it's faster" will sometimes try to edit the test. That patch is rejected before it runs.
            </p>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ shared helpers */


function CountNumber({ to, duration = 1.5, start }: { to: number; duration?: number; start: boolean }) {
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) => Math.round(v).toString());
  useEffect(() => {
    if (!start) return;
    const controls = animate(mv, to, { duration, ease: "easeOut" });
    return () => controls.stop();
  }, [start, to, duration, mv]);
  return <motion.span>{rounded}</motion.span>;
}

function CommandBar({ text, startDelay = 0, speed = 60 }: { text: string; startDelay?: number; speed?: number }) {
  const [shown, setShown] = useState("");
  useEffect(() => {
    let cancelled = false;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const start = setTimeout(() => {
      const tick = () => {
        if (cancelled) return;
        i += 1;
        setShown(text.slice(0, i));
        if (i < text.length) timer = setTimeout(tick, speed);
      };
      tick();
    }, startDelay);
    return () => { cancelled = true; clearTimeout(start); if (timer) clearTimeout(timer); };
  }, [text, startDelay, speed]);
  return (
    <span className="flex-1 min-w-0 text-[13px] font-instrument text-neutral-100 truncate text-left">
      <span className="text-neutral-500">$ </span>
      {shown}
      <span className="inline-block w-[7px] h-[15px] -mb-0.5 bg-neutral-300 caret-blink ml-0.5" />
    </span>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        try { navigator.clipboard?.writeText(value); } catch { /* noop */ }
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
      className="shrink-0 inline-flex items-center gap-1.5 bg-white/10 hover:bg-white/20 text-neutral-100 text-sm font-medium rounded-[5px] h-9 px-3 transition-colors"
    >
      <Icon path={copied ? ICONS.check : ICONS.copy} size={15} /> {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Typewriter({ text, className, speed = 20, delay = 0 }: { text: string; className?: string; speed?: number; delay?: number }) {
  const ref = useRef<HTMLPreElement>(null);
  const inView = useInViewFM(ref, { once: true, margin: "-80px" });
  const [shown, setShown] = useState("");
  useEffect(() => {
    if (!inView) return;
    let i = 0;
    let raf = 0;
    const start = setTimeout(() => {
      const tick = () => {
        i += 1;
        setShown(text.slice(0, i));
        if (i < text.length) raf = window.setTimeout(tick, speed) as unknown as number;
      };
      tick();
    }, delay * 1000);
    return () => { clearTimeout(start); clearTimeout(raf); };
  }, [inView, text, speed, delay]);
  return (
    <pre ref={ref} className={className}>
      {shown}
      <span className="inline-block w-[0.5ch] -mb-0.5 bg-white/60 caret-blink" style={{ height: "1em" }} />
    </pre>
  );
}

function WordsReveal({
  text,
  className,
  as = "span",
  step = 0.06,
  delay = 0,
  duration = 0.7,
  active,
}: {
  text: string;
  className?: string;
  as?: "span" | "h1" | "h2" | "h3" | "p";
  step?: number;
  delay?: number;
  duration?: number;
  active?: boolean;
}) {
  const words = text.split(" ");
  const MotionTag = motion[as] as typeof motion.span;
  const triggerProps =
    active === undefined
      ? { whileInView: "visible" as const, viewport: { once: true, margin: "-80px" } }
      : { animate: active ? ("visible" as const) : ("hidden" as const) };
  return (
    <MotionTag className={className} initial="hidden" {...triggerProps} transition={{ staggerChildren: step, delayChildren: delay }}>
      {words.map((w, i) => (
        <motion.span key={i} style={{ display: "inline-block" }} variants={{ hidden: { opacity: 0, y: 18 }, visible: { opacity: 1, y: 0 } }} transition={{ duration, ease: "easeOut" }}>
          {w}
          {i < words.length - 1 ? " " : ""}
        </motion.span>
      ))}
    </MotionTag>
  );
}

function PillReveal({ delay, children }: { delay: number; children: React.ReactNode }) {
  return (
    <motion.div className="grow flex min-w-0 lg:basis-0" initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-100px" }} transition={{ duration: 0.5, delay, ease: "easeOut" }}>
      {children}
    </motion.div>
  );
}

function CountUp({ end, duration = 1500, active, format = (n: number) => n.toLocaleString("en-US") }: { end: number; duration?: number; active: boolean; format?: (n: number) => string }) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    const start = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(end * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active, end, duration]);
  return <>{format(val)}</>;
}

function CountUpInView({ end, duration = 1500, delay = 0, format, active }: { end: number; duration?: number; delay?: number; format?: (n: number) => string; active?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInViewFM(ref, { once: true, margin: "-100px" });
  const trigger = active === undefined ? inView : active;
  const [start, setStart] = useState(false);
  useEffect(() => {
    if (!trigger) return;
    const t = setTimeout(() => setStart(true), delay);
    return () => clearTimeout(t);
  }, [trigger, delay]);
  return <span ref={ref}><CountUp end={end} duration={duration} active={start} format={format} /></span>;
}

/* ------------------------------------------------------------ section primitives */

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <span className="text-xs font-semibold uppercase tracking-[0.25em] text-neutral-500">{children}</span>;
}

function StatTile({ value, label, color = "text-neutral-100", delay = 0 }: { value: React.ReactNode; label: string; color?: string; delay?: number }) {
  return (
    <motion.div
      className="flex flex-col rounded-2xl bg-neutral-950 border border-white/10 p-5"
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.5, delay, ease: "easeOut" }}
    >
      <span className={`text-4xl sm:text-5xl font-semibold tabular-nums ${color}`}>{value}</span>
      <span className="text-sm text-neutral-400 mt-2 leading-tight">{label}</span>
    </motion.div>
  );
}

function ChartPanel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.div
      className={`rounded-3xl border border-white/10 bg-[#141110] p-5 sm:p-8 ${className ?? ""}`}
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.7, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

function FigTag({ children }: { children: React.ReactNode }) {
  return <span className="text-[11px] uppercase tracking-[0.18em] text-neutral-600">{children}</span>;
}

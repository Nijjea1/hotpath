import type * as React from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { COMMANDS, CONFIG_YAML, LOOP, README_URL, REPO_URL, DEMO_URL, BENCHMARK_URL } from "../data/hotpath";

const ACCENT = "#d9662f";
const ISOLATION_URL = `${REPO_URL}/blob/main/docs/ISOLATION.md`;

export const Route = createFileRoute("/docs")({
  head: () => ({
    meta: [
      { title: "Docs — Hotpath" },
      {
        name: "description",
        content:
          "How to install and run Hotpath: point it at a repo with a test command and a benchmark command, and it keeps only the optimizations that are correct and faster than the noise.",
      },
    ],
  }),
  component: Docs,
});

/* ---------------------------------------------------------------- primitives */

function LogoMark({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden>
      <rect width="40" height="40" rx="10" fill="#141110" stroke="rgba(244,240,236,0.14)" />
      <path d="M7 29 H14 V22 H21 V15 H28" stroke="#d9662f" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="28" cy="15" r="3.2" fill="#d9662f" />
      <path d="M21 22 L33 29" stroke="#f5f5f5" strokeWidth="1.6" strokeLinecap="round" strokeDasharray="2 2.4" />
      <circle cx="33" cy="29" r="2" fill="#c4544a" />
    </svg>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="rounded-xl border border-white/10 bg-[#0B0A0B] p-4 font-mono text-[13px] leading-relaxed text-neutral-200 overflow-x-auto whitespace-pre-wrap">
      <code>{children}</code>
    </pre>
  );
}

function Section({
  id,
  eyebrow,
  title,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-white/10 pt-12 mt-12 first:border-t-0 first:mt-0 first:pt-0">
      <div className="text-[12px] uppercase tracking-[0.2em] mb-3" style={{ color: ACCENT }}>
        {eyebrow}
      </div>
      <h2 className="font-display text-3xl sm:text-4xl text-neutral-100 mb-5">{title}</h2>
      <div className="text-[15px] leading-relaxed text-neutral-300 space-y-4 max-w-[760px]">
        {children}
      </div>
    </section>
  );
}

const Mono = ({ children }: { children: React.ReactNode }) => <span className="font-mono text-neutral-200">{children}</span>;

const TOC = [
  ["install", "Install"],
  ["quickstart", "Quickstart"],
  ["how", "How the loop works"],
  ["config", "The config file"],
  ["cli", "CLI reference"],
];

/* ---------------------------------------------------------------------- page */

function Docs() {
  return (
    <div className="min-h-screen bg-black text-neutral-100">
      {/* header */}
      <header className="sticky top-0 z-20 backdrop-blur-md bg-black/70 border-b border-white/10">
        <div className="max-w-[1000px] mx-auto flex items-center px-5 py-4">
          <Link to="/" className="flex items-center gap-3 shrink-0">
            <LogoMark size={36} />
            <span className="text-[18px] font-medium tracking-tight text-neutral-100">Hotpath</span>
          </Link>
          <span className="ml-3 text-neutral-500 text-[15px]">/ docs</span>
          <nav className="ml-auto flex items-center gap-6 text-[15px]">
            <Link to="/" className="text-neutral-400 hover:text-neutral-100 transition-colors">
              Home
            </Link>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="bg-white text-black rounded-lg py-2 px-4 text-[14px] font-medium hover:bg-neutral-200 transition-colors"
            >
              GitHub
            </a>
          </nav>
        </div>
      </header>

      <main className="max-w-[1000px] mx-auto px-5 pb-28">
        {/* hero */}
        <div className="pt-16 pb-4">
          <h1 className="font-display text-5xl sm:text-6xl text-neutral-100 leading-[1.05]">
            Documentation
          </h1>
          <p className="mt-5 text-lg text-neutral-400 max-w-[680px] leading-relaxed">
            Hotpath makes code faster and proves every change is correct. Install it, run the
            offline demo on the bundled slow repo, then point it at your own code with one config
            file.
          </p>
          <div className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
            {TOC.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="text-neutral-400 hover:text-neutral-100 transition-colors">
                {label}
              </a>
            ))}
          </div>
        </div>

        {/* install */}
        <Section id="install" eyebrow="Get started" title="Install">
          <p>Hotpath is a Python package (Python 3.11+). Clone the repo and install it in editable mode:</p>
          <Code>{`git clone ${REPO_URL}.git
cd hotpath
pip install -e ".[dev]"`}</Code>
          <p>
            The demo configs run with an offline mock provider, so no API key is needed to see the
            full loop. For real models, set <Mono>OPENAI_API_KEY</Mono> and use{" "}
            <Mono>--provider openai</Mono>. Tracing to Sentry is optional via <Mono>SENTRY_DSN</Mono>.
          </p>
        </Section>

        {/* quickstart */}
        <Section id="quickstart" eyebrow="Five minutes" title="Quickstart">
          <p>Three commands, in order:</p>
          <Code>{`# 1. run the full loop on the deliberately slow demo repo (offline, no keys)
hotpath run configs/demo_repo.yaml

# 2. open the dashboard: experiment tree, throughput chart, diffs, reasons
hotpath serve configs/demo_repo.yaml      # http://127.0.0.1:8765

# 3. re-measure each accepted change and drop any that isn't pulling its weight
hotpath ablate configs/demo_repo.yaml`}</Code>
          <p>
            The mock provider replaces the <span className="text-neutral-100">model</span>, not the
            verification: every recorded patch still goes through a real worktree, the locked
            tests, and the benchmark — and several are deliberately wrong, so you see each reject
            path.
          </p>
        </Section>

        {/* how it works */}
        <Section id="how" eyebrow="The mechanism" title="How the loop works">
          <p>
            Two steps are models, allowed to be creative and wrong. Four are plain harness code the
            models cannot edit. A patch that fails a gate is rejected and logged with the reason.
          </p>
          <div className="space-y-4">
            {LOOP.map((l) => (
              <div key={l.id} className="rounded-2xl border border-white/10 bg-[#141110] p-5">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-[15px] font-semibold" style={{ color: ACCENT }}>
                    {l.n}
                  </span>
                  <span className="font-display text-xl text-neutral-100">{l.name}</span>
                  <span className="ml-auto text-[12px] uppercase tracking-wider text-neutral-500">{l.kind === "ai" ? "ai" : "harness"}</span>
                </div>
                <p className="mt-1 text-neutral-400 text-[14px] italic">{l.question}</p>
                <p className="mt-2 text-neutral-300 text-[14px] leading-relaxed">{l.detail}</p>
              </div>
            ))}
          </div>
          <p>
            <span style={{ color: ACCENT }}>The speed gate is statistical.</span> The untouched code
            is benchmarked repeatedly to measure the noise floor; the threshold is{" "}
            <Mono>max(min_speedup, 1 + noise_multiplier × noise)</Mono>, and the bootstrap 95%
            interval of the median ratio must exclude 1.0. See{" "}
            <a className="underline decoration-white/30 hover:decoration-white" href={BENCHMARK_URL} target="_blank" rel="noreferrer">
              benchmark.py
            </a>
            .
          </p>
        </Section>

        {/* config */}
        <Section id="config" eyebrow="Your own code" title="The config file">
          <p>Any project with a way to check it is correct and a way to time it works through one file:</p>
          <Code>{CONFIG_YAML}</Code>
          <ul className="space-y-2 list-none pl-0">
            {[
              ["test_cmd", "exit 0 means correct. The only definition of correctness Hotpath uses."],
              ["bench_cmd", "prints the samples and the metric (seconds, or tokens/sec — higher is better)."],
              ["profile_cmd", "prints the hotspots the planner reads (cProfile or torch.profiler)."],
              ["editable", "globs the agent may change."],
              ["locked", "globs it must never touch — enforced in the harness before a byte is written."],
            ].map(([k, v]) => (
              <li key={k} className="flex gap-3">
                <span className="font-mono text-[14px] shrink-0" style={{ color: ACCENT }}>{k}</span>
                <span className="text-neutral-300 text-[14px]">{v}</span>
              </li>
            ))}
          </ul>
          <p>
            Execution uses Docker by default; the bundled demo configs opt into a local backend as
            a trusted smoke test. For arbitrary repositories, read the{" "}
            <a className="underline decoration-white/30 hover:decoration-white" href={ISOLATION_URL} target="_blank" rel="noreferrer">
              isolation guide
            </a>
            .
          </p>
        </Section>

        {/* cli reference */}
        <Section id="cli" eyebrow="Reference" title="CLI reference">
          <div className="space-y-4">
            {COMMANDS.map(([cmd, desc]) => (
              <div key={cmd} className="rounded-xl border border-white/10 bg-[#141110] p-4">
                <div className="font-mono text-[14px] text-neutral-100">{cmd}</div>
                <div className="mt-1.5 text-neutral-400 text-[14px] leading-relaxed">{desc}</div>
              </div>
            ))}
          </div>
          <p className="text-neutral-400 text-[14px]">
            Useful <span className="font-mono">run</span> flags: <span className="font-mono">--iterations N</span>,{" "}
            <span className="font-mono">--beam N</span>, <span className="font-mono">--provider {"{mock,openai}"}</span>,{" "}
            <span className="font-mono">--export DIR</span>, <span className="font-mono">--resume RUN_ID</span>,{" "}
            <span className="font-mono">--autocommit</span>. Dryft configs: <span className="font-mono">configs/dryft_local.yaml</span> (trusted host) and{" "}
            <span className="font-mono">configs/dryft_h100.yaml</span> (Docker).
          </p>
        </Section>

        {/* footer */}
        <footer className="border-t border-white/10 mt-16 pt-8 flex flex-wrap items-center gap-x-6 gap-y-3 text-[14px] text-neutral-400">
          <Link to="/" className="hover:text-neutral-100 transition-colors">← Home</Link>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">GitHub</a>
          <a href={README_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">README</a>
          <a href={DEMO_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">Demo script</a>
          <a href={ISOLATION_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">Isolation</a>
        </footer>
      </main>
    </div>
  );
}

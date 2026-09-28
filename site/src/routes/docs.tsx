import type * as React from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { CONFIG_YAML, LOOP, README_URL, REPO_URL, BENCHMARK_URL, LEDGER_URL } from "../data/hotpath";
import { CLI_COMMANDS } from "../data/commands";
import { COMMAND_GUIDE, CONFIG_REFERENCE, ENV_VARS, FAQ, PLATFORMS, TROUBLESHOOTING, WORKFLOWS } from "../data/guide";

const ACCENT = "#d9662f";
const ISOLATION_URL = `${REPO_URL}/blob/main/docs/ISOLATION.md`;
const GO_URL = `${REPO_URL}/blob/main/docs/GO.md`;
const PR_URL = `${REPO_URL}/blob/main/docs/PULL_REQUESTS.md`;
const PLATFORMS_URL = `${REPO_URL}/blob/main/docs/PLATFORMS.md`;
const SECURITY_URL = `${REPO_URL}/blob/main/SECURITY.md`;
const CONTRIBUTING_URL = `${REPO_URL}/blob/main/CONTRIBUTING.md`;
const ISSUES_URL = `${REPO_URL}/issues`;

export const Route = createFileRoute("/docs")({
  head: () => ({
    meta: [
      { title: "Docs — Hotpath" },
      {
        name: "description",
        content:
          "Install Hotpath, run it on any repository, and read every command, config key, and verdict. Hotpath keeps only the optimizations that are correct and faster than the measured noise.",
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
    <pre className="rounded-xl border border-white/10 bg-[#0B0A0B] p-4 font-mono text-[13px] leading-relaxed text-neutral-200 overflow-x-auto whitespace-pre-wrap break-words">
      <code>{children}</code>
    </pre>
  );
}

function Section({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-white/10 pt-12 mt-12 first:border-t-0 first:mt-0 first:pt-0">
      <div className="text-[12px] uppercase tracking-[0.2em] mb-3" style={{ color: ACCENT }}>
        {eyebrow}
      </div>
      <h2 className="font-display text-3xl sm:text-4xl text-neutral-100 mb-5">{title}</h2>
      <div className="text-[15px] leading-relaxed text-neutral-300 space-y-4">{children}</div>
    </section>
  );
}

function Card({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <div id={id} className="rounded-2xl border border-white/10 bg-[#141110] p-5 scroll-mt-24">
      {children}
    </div>
  );
}

function A({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a className="underline decoration-white/30 hover:decoration-white" href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

const Mono = ({ children }: { children: React.ReactNode }) => <span className="font-mono text-[0.92em] text-neutral-200">{children}</span>;

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full text-left text-[13.5px]">
        <thead className="bg-white/[0.04] text-neutral-400">
          <tr>{head.map((h) => <th key={h} className="px-4 py-2.5 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-white/10 align-top">
              {r.map((c, j) => <td key={j} className={`px-4 py-2.5 ${j === 0 ? "font-mono text-neutral-100" : "text-neutral-300"}`}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TOC: [string, string][] = [
  ["install", "Install"],
  ["workflows", "Workflows"],
  ["how", "How it decides"],
  ["cli", "CLI reference"],
  ["config", "Configuration"],
  ["keys", "Models & keys"],
  ["isolation", "Isolation & security"],
  ["platforms", "Platforms & GPUs"],
  ["dashboard", "The dashboard"],
  ["troubleshooting", "Troubleshooting"],
  ["faq", "FAQ"],
];

/* ---------------------------------------------------------------------- page */

function Docs() {
  return (
    <div className="min-h-screen bg-black text-neutral-100">
      <header className="sticky top-0 z-20 backdrop-blur-md bg-black/70 border-b border-white/10">
        <div className="max-w-[1200px] mx-auto flex items-center px-5 py-4">
          <Link to="/" className="flex items-center gap-3 shrink-0">
            <LogoMark size={36} />
            <span className="text-[18px] font-medium tracking-tight text-neutral-100">Hotpath</span>
          </Link>
          <span className="ml-3 text-neutral-500 text-[15px]">/ docs</span>
          <nav className="ml-auto flex items-center gap-6 text-[15px]">
            <Link to="/" className="text-neutral-400 hover:text-neutral-100 transition-colors">Home</Link>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="bg-white text-black rounded-lg py-2 px-4 text-[14px] font-medium hover:bg-neutral-200 transition-colors">
              GitHub
            </a>
          </nav>
        </div>
      </header>

      <div className="max-w-[1200px] mx-auto px-5 lg:grid lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-12">
        {/* contents */}
        <aside className="hidden lg:block">
          <nav className="sticky top-24 pt-16 space-y-2 text-[14px]">
            <div className="text-[11px] uppercase tracking-[0.2em] text-neutral-500 mb-3">On this page</div>
            {TOC.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="block text-neutral-400 hover:text-neutral-100 transition-colors">{label}</a>
            ))}
          </nav>
        </aside>

        <main className="pb-28 min-w-0 max-w-[860px]">
          <div className="pt-16 pb-4">
            <h1 className="font-display text-5xl sm:text-6xl text-neutral-100 leading-[1.05]">Documentation</h1>
            <p className="mt-5 text-lg text-neutral-400 max-w-[680px] leading-relaxed">
              Hotpath makes code faster and proves every change is correct. Install it, point it at a repository, and it
              comes back with a draft pull request of verified changes — or with the reason nothing was proved.
            </p>
            <div className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-[14px] lg:hidden">
              {TOC.map(([id, label]) => (
                <a key={id} href={`#${id}`} className="text-neutral-400 hover:text-neutral-100 transition-colors">{label}</a>
              ))}
            </div>
          </div>

          <Section id="install" eyebrow="Get started" title="Install">
            <p>Hotpath is a Python CLI. You need Python 3.11+ and Git; Docker and the GitHub <Mono>gh</Mono> CLI are optional.</p>
            <Code>{`pipx install git+${REPO_URL}
hotpath doctor          # Git, Docker, API keys, and the active accelerator`}</Code>
            <p>
              <Mono>uv tool install git+{REPO_URL}</Mono> works too, as does cloning the repo and running{" "}
              <Mono>hotpath.cmd</Mono> (Windows) or <Mono>./hotpath.sh</Mono>, which create a venv for you. Hotpath is not on
              PyPI yet, and the <Mono>hotpath</Mono> name there belongs to an unrelated project — don&apos;t{" "}
              <Mono>pip install hotpath</Mono>.
            </p>
            <Table
              head={["You need", "For", "Without it"]}
              rows={[
                ["Python 3.11+, Git", "everything", "Hotpath will not start"],
                ["OPENAI_API_KEY", "real planner and worker models", "use --provider mock (recorded patches, fully offline)"],
                ["Docker", "running untrusted repositories in a locked-down container", "go asks before running code on your machine"],
                ["gh or GITHUB_TOKEN", "opening pull requests and reading CI", "Hotpath pushes the branch and prints a pre-filled PR link"],
              ]}
            />
          </Section>

          <Section id="workflows" eyebrow="Recipes" title="Workflows">
            <div className="space-y-6">
              {WORKFLOWS.map((w) => (
                <Card key={w.id} id={`wf-${w.id}`}>
                  <h3 className="font-display text-2xl text-neutral-100">{w.title}</h3>
                  <p className="mt-1 text-neutral-400 text-[14px]">{w.when}</p>
                  <div className="mt-4"><Code>{w.code}</Code></div>
                  <ul className="mt-4 space-y-2 text-[14px] text-neutral-300 list-disc pl-5 marker:text-neutral-600">
                    {w.notes.map((n) => <li key={n}>{n}</li>)}
                  </ul>
                </Card>
              ))}
            </div>
            <p className="text-[14px] text-neutral-400">
              The full stage-by-stage behaviour of <Mono>go</Mono> is in <A href={GO_URL}>docs/GO.md</A>; publishing details in{" "}
              <A href={PR_URL}>docs/PULL_REQUESTS.md</A>.
            </p>
          </Section>

          <Section id="how" eyebrow="The mechanism" title="How it decides">
            <p>
              Two steps are models, allowed to be creative and wrong. The rest is harness code the models cannot edit. A
              patch that fails a gate is rejected and recorded with the reason.
            </p>
            <div className="space-y-3">
              {LOOP.map((l) => (
                <Card key={l.id}>
                  <div className="flex items-baseline gap-3">
                    <span className="font-mono text-[15px] font-semibold" style={{ color: ACCENT }}>{l.n}</span>
                    <span className="font-display text-xl text-neutral-100">{l.name}</span>
                    <span className="ml-auto text-[12px] uppercase tracking-wider text-neutral-500">{l.kind === "ai" ? "ai" : "harness"}</span>
                  </div>
                  <p className="mt-1 text-neutral-400 text-[14px] italic">{l.question}</p>
                  <p className="mt-2 text-neutral-300 text-[14px] leading-relaxed">{l.detail}</p>
                </Card>
              ))}
            </div>
            <h3 className="font-display text-2xl text-neutral-100 pt-4">The accept rule</h3>
            <ol className="space-y-2 list-decimal pl-5 marker:text-neutral-500">
              <li><span className="text-neutral-100">Correct.</span> The locked <Mono>test_cmd</Mono> exits 0. A failing candidate is never benchmarked.</li>
              <li><span className="text-neutral-100">Noise floor.</span> The untouched code is benchmarked <Mono>baseline_repeats</Mono> times; the variation between runs is the noise.</li>
              <li><span className="text-neutral-100">Threshold.</span> <Mono>max(min_speedup, 1 + noise_multiplier × noise)</Mono> — 3% on a quiet machine, higher on a noisy one.</li>
              <li><span className="text-neutral-100">Confidence.</span> 2,000 bootstrap resamples; the 95% interval of the median ratio must exclude 1.0.</li>
              <li><span className="text-neutral-100">Quiet machine.</span> Benchmarks take an exclusive lock, so nothing else runs while one is measured.</li>
            </ol>
            <Table
              head={["Verdict", "Meaning"]}
              rows={[
                ["accepted", "correct, and faster than the threshold with a confidence interval clear of 1.0"],
                ["rejected_correctness", "the locked tests failed; the benchmark never ran"],
                ["rejected_speed", "correct, but not measurably faster (or slower)"],
                ["patch_failed", "the edit could not be applied (search text not found, ambiguous, no-op)"],
                ["locked_file", "the patch touched a locked path; blocked before anything ran"],
                ["not_selected", "correct and faster, but a sibling was faster still"],
                ["timeout", "a test, benchmark or model call hit its hard limit"],
                ["error", "the benchmark crashed or printed no usable samples, or the harness hit an unexpected error"],
              ]}
            />
            <p className="text-[14px] text-neutral-400">
              Implementation: <A href={BENCHMARK_URL}>benchmark.py</A>. Recorded runs, including refusals:{" "}
              <A href={LEDGER_URL}>the evidence ledger</A>.
            </p>
          </Section>

          <Section id="cli" eyebrow="Reference" title="CLI reference">
            <p className="text-neutral-400 text-[14px]">
              Every command and option below is generated from Hotpath&apos;s own argument parser — the same text as{" "}
              <Mono>hotpath &lt;command&gt; --help</Mono>. Global flag: <Mono>-v</Mono> for debug logging and full tracebacks.
            </p>
            <div className="flex flex-wrap gap-2">
              {CLI_COMMANDS.map((c) => (
                <a key={c.name} href={`#cli-${c.name}`} className="font-mono text-[13px] rounded-lg border border-white/10 px-3 py-1.5 text-neutral-300 hover:text-neutral-100 hover:border-white/30">
                  {c.name}
                </a>
              ))}
            </div>
            <div className="space-y-4">
              {CLI_COMMANDS.map((c) => {
                const g = COMMAND_GUIDE[c.name];
                return (
                  <Card key={c.name} id={`cli-${c.name}`}>
                    <div className="font-mono text-[15px] text-neutral-100">{c.usage}</div>
                    <div className="mt-1.5 text-neutral-400 text-[14px] leading-relaxed">{c.summary}</div>
                    {g && <p className="mt-3 text-[14px] text-neutral-300"><span style={{ color: ACCENT }}>Use it when:</span> {g.when}</p>}
                    {g && <div className="mt-3"><Code>{g.example}</Code></div>}
                    <details className="mt-3 group">
                      <summary className="cursor-pointer text-[13px] text-neutral-400 hover:text-neutral-200 list-none">
                        <span className="group-open:hidden">Show all {c.args.length} options ▸</span>
                        <span className="hidden group-open:inline">Hide options ▾</span>
                      </summary>
                      <dl className="mt-3 grid gap-x-5 gap-y-2 sm:grid-cols-[minmax(0,260px)_1fr] text-[13px]">
                        {c.args.map((a) => (
                          <div key={a.flag} className="contents">
                            <dt className="font-mono break-words" style={{ color: ACCENT }}>{a.flag}</dt>
                            <dd className="text-neutral-300 mb-2 sm:mb-0">{a.help}</dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  </Card>
                );
              })}
            </div>
          </Section>

          <Section id="config" eyebrow="Your own code" title="Configuration">
            <p>
              <Mono>hotpath init</Mono> writes <Mono>.hotpath.yaml</Mono> for you, and <Mono>hotpath go</Mono> writes it into its
              setup commit. Every command then finds the nearest one. A config looks like this:
            </p>
            <Code>{CONFIG_YAML}</Code>
            {CONFIG_REFERENCE.map((g) => (
              <div key={g.group} className="space-y-2">
                <h3 className="font-mono text-[15px] pt-2" style={{ color: ACCENT }}>{g.group}</h3>
                <Table head={["Key", "Default", "Meaning"]} rows={g.keys.map((k) => [k.key, <span className="font-mono text-[12.5px]">{k.default}</span>, k.meaning])} />
              </div>
            ))}
          </Section>

          <Section id="keys" eyebrow="Providers" title="Models & keys">
            <p>
              The pattern is &ldquo;big model plans, fast model explores&rdquo;: one planner call per iteration, many worker calls
              in parallel. Both use the OpenAI API by default; workers can point at any OpenAI-compatible endpoint.
            </p>
            <Code>{`# once per shell
export OPENAI_API_KEY=sk-...              # PowerShell: $env:OPENAI_API_KEY="sk-..."
# or once per machine: add OPENAI_API_KEY=... to ~/.hotpath/.env

# workers on Baseten (planner stays on OpenAI)
export BASETEN_API_KEY=...
hotpath init --worker baseten             # writes worker_base_url + worker_api_key_env for you`}</Code>
            <Table head={["Variable", "What it does"]} rows={ENV_VARS.map((e) => [e.name, e.meaning])} />
            <p className="text-[14px] text-neutral-400">
              Keys are read from the environment, then <Mono>./.env</Mono>, then <Mono>~/.hotpath/.env</Mono>. They never enter
              containers, commits, or model prompts. <Mono>hotpath doctor --verify-keys</Mono> checks each one against its endpoint.
            </p>
          </Section>

          <Section id="isolation" eyebrow="Trust" title="Isolation & security">
            <Table
              head={["Where code runs", "Use it for", "What it protects"]}
              rows={[
                ["execution.backend: docker", "any repository you did not write (the default)", "a fresh container per command: no network, read-only source, non-root, quotas, no host env, home, or Docker socket; fails closed"],
                ["execution.backend: local", "trusted code only (the bundled demos)", "nothing — it runs as you"],
                ["go --sandbox auto", "the default for go", "Docker when it is running; otherwise asks before running on the host in a per-target venv"],
              ]}
            />
            <ul className="space-y-2 list-disc pl-5 marker:text-neutral-600 text-[14px]">
              <li>Locked paths are enforced in code before a byte is written; the models are told about them, but the code is the guarantee.</li>
              <li>Correctness is relative to your locked tests: a passing suite is evidence against that contract, not a proof of equivalence.</li>
              <li>The dashboard only answers the local machine; put an authenticated reverse proxy in front for remote access.</li>
            </ul>
            <p className="text-[14px] text-neutral-400">
              Threat model: <A href={ISOLATION_URL}>docs/ISOLATION.md</A>. Reporting a vulnerability: <A href={SECURITY_URL}>SECURITY.md</A>.
            </p>
          </Section>

          <Section id="platforms" eyebrow="Hardware" title="Platforms & GPUs">
            <Table head={["Host / accelerator", "Local execution", "Docker execution"]} rows={PLATFORMS.map((p) => [p.host, p.local, p.docker])} />
            <p>
              The bundled PyTorch helpers pick CUDA/ROCm, Intel XPU, Apple MPS or CPU automatically; <Mono>HOTPATH_TORCH_DEVICE</Mono>{" "}
              forces one. Kernel edits are allowed when the kernel and its call site are editable, and must keep a correct
              PyTorch fallback. <Mono>hotpath doctor --require-gpu</Mono> fails unless an accelerator is usable. More in{" "}
              <A href={PLATFORMS_URL}>docs/PLATFORMS.md</A>.
            </p>
          </Section>

          <Section id="dashboard" eyebrow="Reading a run" title="The dashboard">
            <Code>{`hotpath serve                    # http://127.0.0.1:8765 (go opens it for you)`}</Code>
            <ul className="space-y-2 list-disc pl-5 marker:text-neutral-600 text-[14px]">
              <li><span className="text-neutral-100">Experiment tree</span> — every candidate, retries and beam branches included; click a node for its hypothesis, diff, test output and exact verdict.</li>
              <li><span className="text-neutral-100">Progress chart</span> — the raw metric or speedup, with the keep-threshold drawn as a band around the baseline.</li>
              <li><span className="text-neutral-100">Hotspot comparison</span> — baseline versus head profile, with bounds instead of a fabricated &ldquo;−100%&rdquo; when a function drops out of the top N.</li>
              <li><span className="text-neutral-100">Funnel</span> — proposed → applied → correct → accepted → shipped.</li>
              <li><span className="text-neutral-100">Create PR</span> — appears only when a finished run has something verified to ship.</li>
            </ul>
            <p className="text-[14px] text-neutral-400">Every number on it is read from the run&apos;s SQLite store, written by the harness.</p>
          </Section>

          <Section id="troubleshooting" eyebrow="When it stops" title="Troubleshooting">
            <p>Every stop names its stage and prints a <Mono>next:</Mono> line. The common ones:</p>
            <div className="space-y-3">
              {TROUBLESHOOTING.map((t) => (
                <Card key={t.symptom}>
                  <div className="font-mono text-[13px] text-red-300 break-words">{t.symptom}</div>
                  <p className="mt-2 text-[14px] text-neutral-300">{t.fix}</p>
                </Card>
              ))}
            </div>
            <p className="text-[14px] text-neutral-400">
              Still stuck? Open an <A href={ISSUES_URL}>issue</A> with <Mono>hotpath doctor --json</Mono> and the command output.
            </p>
          </Section>

          <Section id="faq" eyebrow="Questions" title="FAQ">
            <div className="space-y-5">
              {FAQ.map((f) => (
                <div key={f.q}>
                  <h3 className="text-neutral-100 text-[16px] font-medium">{f.q}</h3>
                  <p className="mt-1.5 text-[14.5px] text-neutral-300">{f.a}</p>
                </div>
              ))}
            </div>
          </Section>

          <footer className="border-t border-white/10 mt-16 pt-8 flex flex-wrap items-center gap-x-6 gap-y-3 text-[14px] text-neutral-400">
            <Link to="/" className="hover:text-neutral-100 transition-colors">← Home</Link>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">GitHub</a>
            <a href={README_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">README</a>
            <a href={GO_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">hotpath go</a>
            <a href={ISOLATION_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">Isolation</a>
            <a href={SECURITY_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">Security</a>
            <a href={CONTRIBUTING_URL} target="_blank" rel="noreferrer" className="hover:text-neutral-100 transition-colors">Contributing</a>
          </footer>
        </main>
      </div>
    </div>
  );
}

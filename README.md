# VibeWiki

**Evidence-based product reverse engineering for AI-built codebases — from source scan to runtime evidence.**

<p align="center">
  <img src="docs/assets/vibewiki-hero.png" alt="A software product represented as a local evidence graph connecting user flows, code, data, and tests" width="100%">
</p>

<p align="center">
  <img src="docs/assets/vibewiki-product-preview.jpg" alt="VibeWiki local product map preview with evidence graph and scan controls" width="72%">
</p>

> Live preview from the local `scan → build → serve` workflow.

> VibeWiki is an early-stage open-source concept and UI prototype for developers who need to understand what an AI-assisted codebase actually does.

When implementation moves faster than documentation, VibeWiki is designed to connect:

```text
product concept → user flow → route/screen → API endpoint → service/function
→ database entity → source code → test/commit
```

The important design rule is **facts first, interpretation second**. Claims should point to a file, line, route, schema, test, commit, or runtime trace. If the repository does not provide enough evidence, VibeWiki should say so instead of presenting a guess as truth.

## Current status

This repository is a **local-first end-to-end preview**, not production-ready software. It currently contains:

- A standalone dark developer-tool UI in [`viewer/index.html`](viewer/index.html).
- A generated hero illustration in [`docs/assets/vibewiki-hero.png`](docs/assets/vibewiki-hero.png).
- The product development plan in [`docs/product-development-plan.md`](docs/product-development-plan.md).
- An offline verification script in [`scripts/verify_preview.py`](scripts/verify_preview.py).
- An offline `scan → build → serve` pipeline that writes deterministic source
  facts, a content-addressed inventory of every non-ignored file, a SQLite
  graph, Markdown/Mermaid wiki, and a local viewer backed by the built artifact.

The semantic analyzer is intentionally deterministic: it covers Next.js App
Router, generic JavaScript/JSX/TypeScript/TSX repositories, common source
languages such as Python, Go, Rust, Java/Kotlin, Ruby/PHP, C/C++/C#, Swift,
Dart, shell and SQL, plus Prisma models, markup, configuration, documentation,
CommonJS/ESM module references, and test links. The extra language adapters are
conservative regex-based facts rather than full compiler ASTs; files whose
semantics are not recognized still remain visible as inventory evidence. LLM
reasoning, runtime exploration, and network access are not required.

## Preview the UI

Requirements: Python 3.11+ or another static HTTP server.

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory viewer
```

Then open <http://127.0.0.1:4173/>.

The prototype demonstrates:

- Product map and evidence-oriented graph navigation.
- Inspector panels for routes, flows, APIs, services, entities, tests, and commits.
- Search, graph zoom, node selection, and command-palette interactions.
- One-click ZIP export of the generated wiki, graph, evidence, and unknowns
  artifacts without bundling source files.
- Explicit confidence, unknowns, and local-runtime status.
- A local-first visual language that does not require a hosted backend.

Runtime observation is available as an explicit, safe baseline. Start a local
application, then run:

```bash
uv run vibewiki observe http://127.0.0.1:3000 --repository /path/to/repo
```

The observer follows same-origin document routes with bounded `GET` requests,
never submits forms, and refuses remote hosts unless `--allow-network` is
passed explicitly. The viewer's **Observe runtime** button uses the same
loopback-only default. Results are written to `.vibewiki/runtime.json`, shown
through `/api/runtime`, and included in the source-free export. HTTP mode does
not execute JavaScript, so browser-only behavior remains unknown there. For a
browser-backed local probe, install the optional adapter and Chromium once:

```bash
uv sync --extra runtime
uv run playwright install chromium
uv run vibewiki observe http://127.0.0.1:3000 \
  --repository /path/to/repo --mode browser --screenshots
```

Browser mode runs headless with a fresh context, follows same-origin routes,
blocks cross-origin and non-GET requests, and never submits forms or performs
authentication. Observed routes and API requests are joined to matching graph
nodes by path/method; selected nodes show runtime status and console errors in
the inspector. The viewer exposes the same choice from **Observe runtime**.

## Product direction

The current end-to-end local flow is:

```bash
uv run vibewiki scan /path/to/next-app
uv run vibewiki build /path/to/next-app
uv run vibewiki serve /path/to/next-app --port 4173
```

Open `http://127.0.0.1:4173/` to inspect the generated graph, evidence and
unknowns. By default the server binds to loopback and does not contact
external services.
You can also use **Browse source** in the viewer to choose a local source
folder. Browse accepts common source, config, and documentation files
(including JavaScript/JSX, TypeScript/TSX, Python, Go, Rust, Java/Kotlin,
C-family, Swift/Dart, shell, SQL, markup, JSON/YAML/TOML and Markdown), plus
Prisma. It detects a supported package inside common monorepos and shows
skipped-file or size-limit errors before import. Selected supported files are
sent only to this loopback process, scanned locally, and the temporary imported
workspace is removed when the server exits. The CLI's default scan remains
strict for the original direct Next.js App Router contract; Browse uses the
generic local import profile.

The server remains offline when no LLM provider is configured. If a remote
provider is explicitly enabled, only the bounded retrieved context for the
current question is sent to that provider; source import and graph generation
remain local.

Every build also exposes `/api/files`, `/api/packages`, `/api/modules`,
`/api/symbols`, `/api/source`, `/api/history`, `/api/stale`, `/api/llm/status`,
and `/api/ask` for bounded local evidence inspection and optional grounded
discussion. Use `vibewiki history /path/to/repo <path-or-node>` for the same
scan history from the CLI.
Package, symbol, and call edges are deterministic; source evidence is served
by relative path and line range only. Traversal, symlinks, ignored paths, and
sensitive names are rejected.

### Compare product intent with the implementation

For a lightweight product contract, add `product.seed.yaml` at the repository
root. VibeWiki compares each expected route, API, test, file, function, module,
symbol, entity, or package with deterministic scan facts and exposes the result
in `.vibewiki/intent.json`, `/api/intent`, and the viewer's Unknowns view. Missing
expectations become explicit `intent_gap` findings rather than LLM guesses.
Start from [`docs/product.seed.example.yaml`](docs/product.seed.example.yaml).

Every scan records a bounded local history in `.vibewiki/history.json`. If a
source file changes after the last build, the server compares its current hash
with the built inventory and marks affected node/edge evidence as `stale`; it
does not pretend the old line reference is current.

The planned pipeline is:

```text
file discovery and static analysis
        ↓
deterministic facts and evidence
        ↓
local SQLite graph and claims
        ↓
Markdown/Mermaid product wiki and viewer
        ↓
optional bounded retrieval for local Q&A
```

The core remains useful without an LLM. Configure the optional discussion layer
with Ollama on the local machine:

```bash
export VIBEWIKI_LLM_PROVIDER=ollama
export VIBEWIKI_LLM_MODEL=qwen2.5:7b
export VIBEWIKI_LLM_BASE_URL=http://127.0.0.1:11434
# Or pass --llm-provider/--llm-model/--llm-base-url to `vibewiki serve`.
```

Or use a BYOK OpenAI-compatible endpoint from the server environment:

```bash
export VIBEWIKI_LLM_PROVIDER=openai-compatible
export VIBEWIKI_LLM_MODEL=your-model
export VIBEWIKI_LLM_API_KEY=your-key
export VIBEWIKI_LLM_BASE_URL=https://api.example.com
```

The default provider is `none`: VibeWiki returns deterministic evidence-only
results. When a model is enabled, retrieval sends only bounded graph neighbors
and cited source excerpts, never the whole repository. API keys stay server-side
and are not written to `.vibewiki` or returned by the status endpoint.

You can also click **LLM setup** in the viewer's Local runtime card. The form
updates the running server in memory; restarting the server clears that runtime
configuration unless environment variables or CLI flags are supplied again.

The Ask panel supports four grounded use cases: **Discuss** for general
questions, **Explain flow** for graph-connected execution paths, **Impact
analysis** for connected neighborhoods, and **Find unknowns** for gaps already
recorded by the analyzer. With provider `none`, each mode still returns a
deterministic evidence-only result, so the graph remains useful without a
model. Provider Markdown is normalized for readable headings, separators, and
escaped newlines; fenced code blocks are preserved.

Use **Export wiki** in the top bar or command palette to download a ZIP of the
current `.vibewiki` artifacts. The export includes Markdown/Mermaid wiki files,
graph JSON/SQLite, evidence manifests, and unknowns; it deliberately excludes
repository source files.

## Evidence model

A future claim should carry enough metadata to be inspected and challenged:

```json
{
  "claim": "Checkout creates an order",
  "status": "verified",
  "confidence": "medium",
  "evidence": [
    "app/checkout/page.tsx:42",
    "app/api/orders/route.ts:18",
    "tests/checkout.test.ts:11"
  ],
  "unknowns": ["Runtime payment provider was not observed"]
}
```

Planned evidence states include `verified`, `inferred`, `unknown`, and `stale`. Sensitive values should be redacted before indexing; source code should stay on the user's machine by default.

## Repository layout

```text
.
├── docs/
│   ├── assets/vibewiki-hero.png
│   ├── product.seed.example.yaml
│   └── product-development-plan.md
├── scripts/
│   └── verify_preview.py
├── viewer/
│   └── index.html
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
└── VERSION
```

## Verify the repository

Run the deterministic offline checks:

```bash
python3 scripts/verify_preview.py
```

The check validates required files, the PNG signature, the viewer's essential
UI hooks, README asset references, and obvious credential-assignment patterns.

The semantic pipeline is still intentionally narrower than a general-purpose
analyzer. It recognizes direct Next App Router routes specially and accepts
generic source/config/docs in local Browse imports. It records deterministic
facts for routes, API calls, functions/classes in supported language adapters,
Prisma models, imports, writes, calls, test links, and reverse module
dependencies. The separate inventory records non-ignored text and binary files
with path, type, size, and SHA-256 metadata without indexing secret content.
The viewer reads `.vibewiki/graph.json` through the loopback API; it does not
use the presentation fixture when running under `vibewiki serve`.

## Roadmap

1. Establish the product contract and a small fixture repository.
2. Add file discovery and deterministic Next.js/TypeScript facts.
3. Persist sources, nodes, edges, claims, and evidence in SQLite.
4. Generate Markdown/Mermaid wiki pages and replace demo data in the viewer.
5. Add bounded runtime observation evidence with safe read-only defaults. *(HTTP mode in 0.1.4-preview; browser mode and graph linkage in 0.1.6-preview)*
6. Package clean installs and CI gates for external users.
7. Add broader language/framework adapters behind fixture-backed gates.

See the detailed phase plan in [`docs/product-development-plan.md`](docs/product-development-plan.md).

## Privacy principles

- Local-first by default; scanning should not require a hosted service.
- No full-repository prompt by default; retrieve only relevant symbols/modules.
- No credential storage or proxying by VibeWiki.
- Redact secrets and sensitive values before writing evidence or claims.
- Separate deterministic facts from model-generated interpretation.

## Contributing

The project is intentionally early. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md), run the verification script, and keep changes honest about what is implemented versus planned. New product claims should include a source or be marked as an assumption.

## License

VibeWiki is released under the MIT License. See [`LICENSE`](LICENSE).

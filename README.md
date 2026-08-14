# VibeWiki

**Evidence-based product reverse engineering for AI-built codebases.**

<p align="center">
  <img src="docs/assets/vibewiki-hero.png" alt="A software product represented as a local evidence graph connecting user flows, code, data, and tests" width="100%">
</p>

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
- Explicit confidence, unknowns, and local-runtime status.
- A local-first visual language that does not require a hosted backend.

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
`/api/symbols`, `/api/source`, `/api/llm/status`, and `/api/ask` for bounded
local evidence inspection and optional grounded discussion.
Package, symbol, and call edges are deterministic; source evidence is served
by relative path and line range only. Traversal, symlinks, ignored paths, and
sensitive names are rejected.

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
5. Add product-seed intent comparison and explicit implementation gaps.
6. Add bounded local Q&A through an Ollama provider interface.
7. Add Git history and, after the MVP is stable, Playwright runtime evidence.

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

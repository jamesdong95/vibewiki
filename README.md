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
- An offline `scan → build → serve` pipeline that writes a deterministic
  TypeScript/TSX manifest, evidence-backed facts, a SQLite graph, Markdown/
  Mermaid wiki, and a local viewer backed by the built artifact.

The analyzer is intentionally narrow and deterministic: it supports the
documented Next.js App Router + TypeScript/TSX + Prisma + TypeScript test
surface. LLM reasoning, runtime exploration, and broader framework support are
not included.

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
unknowns. The server binds to loopback and does not contact external services.
You can also use **Browse source** in the viewer to choose a local source
folder; selected supported files are sent only to this loopback process,
scanned locally, and the temporary imported workspace is removed when the
server exits.

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

The core should remain useful without an LLM. Ollama/local models and cloud providers would be optional enhancement layers, never the only source of truth.

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

The implemented pipeline is deliberately narrower than a general-purpose
analyzer. It accepts only a direct `<repository>/app` App Router directory
containing at least one `.ts` or `.tsx` file, records TypeScript/TSX metadata
and SHA-256 hashes, then builds deterministic facts for routes, API calls,
functions, Prisma models, imports, writes, calls and direct test links. It
rejects nested/monorepo and Pages Router layouts, skips symlinks and special
files, and ignores build/cache and sensitive paths before reading them. The
viewer reads `.vibewiki/graph.json` through the loopback API; it does not use
the presentation fixture when running under `vibewiki serve`.

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

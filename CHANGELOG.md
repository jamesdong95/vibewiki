# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.10-preview] - 2026-08-25

### Added

- Bounded deterministic `/api/impact` traversal for upstream, downstream, or
  both directions, preserving source evidence on every returned edge.
- Reverse graph controls in the viewer inspector with depth/limit status and
  clear empty/error states.
- Regression coverage for Next.js route flow traversal, generic module symbols,
  and the live HTTP endpoint.

## [0.1.9-preview] - 2026-08-25

### Fixed

- Package the local viewer with the Python wheel so `vibewiki serve` works from
  a clean install without a source checkout.
- Add a CI smoke install that runs the packaged CLI against the fixture and
  checks the served viewer.
- Document the clean-install and end-to-end quickstart path.

## [0.1.8-preview] - 2026-08-25

### Added

- `vibewiki scan --generic` for non-Next repositories.
- Conservative route facts for Express/Fastify/Hono-style JavaScript,
  React Router JSX, Flask/FastAPI decorators, and Go `HandleFunc` calls.
- Generic `fetch`, `$fetch`, and Axios API-call facts with deterministic source
  evidence, while the existing Next.js golden output remains unchanged.

## [0.1.7-preview] - 2026-08-25

### Fixed

- Normalize common LLM Markdown glitches such as escaped newlines and inline
  headings/separators while preserving fenced code blocks.
- Render normalized provider output consistently in both the API response and
  the viewer Ask panel.

## [0.1.6-preview] - 2026-08-25

### Added

- Runtime route, network request, and browser console evidence is joined to
  matching static route/API graph nodes by path and method.
- Runtime graph links are persisted in `runtime.json` and included in exports.
- Selected route/API nodes now show linked runtime evidence and console errors
  directly in the viewer inspector.

## [0.1.5-preview] - 2026-08-25

### Added

- Optional Playwright browser observation mode with JavaScript execution,
  console error capture, network metadata, and local screenshots.
- Browser observation modal in the viewer with HTTP/Browser mode selection and
  explicit screenshot opt-in.
- Same-origin and GET-only browser request policy; authentication and side
  effects remain explicit unknowns.
- Runtime screenshots are included in source-free ZIP exports when captured.
- Optional `vibewiki[runtime]` packaging extra and synchronized `uv.lock`.

## [0.1.4-preview] - 2026-08-18

### Added

- Safe, read-only runtime observation through `vibewiki observe`, `/api/observe`,
  and `/api/runtime`.
- Viewer action **Observe runtime** with loopback-first GET-only behavior.
- `runtime.json` in `.vibewiki` and export archives, including route/network
  metadata and explicit unknowns for JavaScript side effects not executed.
- Release screenshot showing the live evidence graph and Scan history workspace.

## [0.1.3-preview] - 2026-08-18

### Added

- Local scan history with Git commit metadata and deterministic added/changed/removed paths.
- `vibewiki history`, `/api/history`, and a real Scan history viewer inspector.
- Post-build hash checks through `/api/stale`, stale node/edge/source evidence, and export snapshots.

## [0.1.2-preview] - 2026-08-17

### Added

- Optional `product.seed.yaml` intent contract with deterministic
  expected-vs-observed comparison, `/api/intent`, and Unknowns intent gaps.
- Exported `intent.json` artifact and a documented product-seed example.

## [0.1.1-preview] - 2026-08-17

### Added

- Downloadable ZIP export for generated wiki, graph, evidence, and unknowns
  artifacts through the viewer and `/api/export`.
- Live product preview screenshot in the README and release assets.

## [0.1.0-preview] - 2026-08-13

### Added

- Ask modes for grounded discussion, flow explanation, impact analysis, and
  unknowns investigation, each with a deterministic evidence-only fallback.
- Grounded `/api/ask` with evidence-only fallback and optional Ollama/OpenAI-compatible providers.
- Local bounded retrieval and source citations for the viewer's Ask control.
- Added `vibewiki serve --llm-provider/--llm-model/--llm-base-url` runtime configuration without accepting API keys on the command line.

- Initial public repository presentation for VibeWiki.
- Standalone product-intelligence UI prototype.
- Evidence-graph hero illustration for the README.
- Product development plan covering the local-first MVP.
- Offline repository verification script.

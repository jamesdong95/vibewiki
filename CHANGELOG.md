# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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

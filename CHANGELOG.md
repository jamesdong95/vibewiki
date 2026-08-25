# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.23-preview] - 2026-08-25

### Added

- Viewer stale-awareness monitor polls the local artifact state and surfaces a
  clear **Evidence is stale** banner when source files change after build.
- Banner includes changed paths and a one-click **Rescan now** action; it never
  mutates the graph until the user confirms the rescan.
- Regression coverage and live browser smoke for stale detection, rescan, and
  recovery to a current artifact.

## [0.1.22-preview] - 2026-08-25

### Added

- `vibewiki serve REPOSITORY` now auto-runs the local scan + build pipeline when
  no `.vibewiki/graph.json` exists, so a new user can open a repository with one
  command.
- Serve startup reports whether the artifact was auto-analyzed and preserves
  the same safe rollback behavior when first analysis fails.
- Regression coverage for first-run bootstrap, repeat serve, and unsupported
  repository errors.

## [0.1.21-preview] - 2026-08-25

### Added

- **Rescan workspace** thật trong viewer để scan + build lại source đang mở mà
  không cần Browse hoặc nhập lại path.
- Loopback-only `/api/rescan` với lock chống rescan đồng thời và response counts
  cho trạng thái UI.
- Artifact snapshot/rollback: graph `.vibewiki/` gần nhất được khôi phục nếu
  scan hoặc build thất bại; UI giữ graph hiện tại và hiển thị lỗi rõ ràng.
- Regression coverage cho rescan API, graph update, rollback, UI marker và
  browser smoke sau khi source file được thêm vào workspace đang chạy.

## [0.1.20-preview] - 2026-08-25

### Added

- **Use local path** fallback in the viewer for environments where a browser
  folder picker is unavailable.
- Loopback-only `/api/import-path` with the same source filtering, 10,000-file
  and 200 MB safety limits, temporary snapshot isolation, and artifact swap
  behavior as multipart Browse.
- Responsive topbar regression fix so Browse controls remain clickable in the
  real workspace viewport.

## [0.1.19-preview] - 2026-08-25

### Added

- CLI scan auto-detects direct Next.js App Router versus generic repositories;
  `--strict-next` preserves the legacy validation contract.
- `vibewiki analyze REPOSITORY` runs scan and build in one deterministic step.
- Generic route evidence for Angular Router route objects and NestJS controller
  decorators, with profile framework labels and regression fixtures.

## [0.1.18-preview] - 2026-08-25

### Added

- Project Profile package scope selector that focuses the graph, evidence, and
  inspector on a detected package without re-importing the repository.
- Monorepo scope regression coverage for deterministic package paths and the
  viewer's scope controls.

## [0.1.17-preview] - 2026-08-25

### Added

- Deterministic project profile in the artifact and `/api/profile`, covering
  scan mode, detected frameworks, source roots, package scope, language/byte
  counts, analyzer version, and local import limits.
- Real Project profile viewer opened from the project switcher, with a
  Browse-another-scope action replacing the previous prototype control.
- Profile API regression and live browser coverage for profile details,
  Source files inventory, responsive layout, and console errors.

## [0.1.16-preview] - 2026-08-25

### Added

- Reverse module edges for Python relative/absolute imports, Go local imports,
  Rust `mod`/`use`, Java/Kotlin package imports, and C/C++ local includes.
- Deterministic path/line evidence for the new language adapters while keeping
  unresolved external dependencies visible as inferred external modules.
- Multilanguage fixture coverage for Python, Go, Rust, Java, and C reverse graph
  relationships.

## [0.1.15-preview] - 2026-08-25

### Added

- Large repository Browse preflight that detects supported-file and byte-limit
  overflow before upload.
- Monorepo package picker for scanning a bounded `apps/*`, `packages/*`, or
  other nested source package while preserving the server-side safety limits.
- Clear package size/file counts, cancel flow, and current-artifact preservation
  when an oversized repository cannot be imported as a whole.

## [0.1.14-preview] - 2026-08-25

### Added

- Vue Router route-object facts from `createRouter` configurations in generic
  Browse imports.
- SvelteKit filesystem route facts for `src/routes/+page.svelte` and
  `+server` endpoints, including dynamic segments and API-call reverse edges.
- Permanent Vue Router and SvelteKit fixtures with deterministic route/path
  evidence and regression coverage.

## [0.1.13-preview] - 2026-08-25

### Added

- Next.js Pages Router facts for page files and API routes in generic Browse
  imports, including deterministic page/API path evidence and API-call links.
- A Source files inventory in the viewer with file filtering, stale status, and
  one-click source preview from the local artifact.

### Fixed

- Keep Source files navigation out of the crowded top bar so it remains
  clickable at the supported desktop viewport.

## [0.1.12-preview] - 2026-08-25

### Added

- Vite/React fixture-backed route facts for `createBrowserRouter`,
  `createHashRouter`, and `createMemoryRouter` route objects.
- Common `apiClient`, `client`, `httpClient`, and `request` wrapper call facts,
  with deterministic API-call to generic-route graph edges when method and path
  are literal.

## [0.1.11-preview] - 2026-08-25

### Added

- Real browser runtime acceptance fixture with client JavaScript, local route
  navigation, GET API traffic, and a captured console error.
- Playwright/Chromium CI job that verifies runtime route, network, and console
  records link to the static graph without persisting query secrets.

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

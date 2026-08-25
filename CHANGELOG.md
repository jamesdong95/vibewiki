# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.41-preview] - 2026-08-25

### Added

- Unknowns now render as a selectable review queue instead of exposing only
  the first finding.
- Open/All filters show the exact local review status for every finding, while
  selecting a row keeps its evidence and review actions in the inspector.
- Regression coverage includes a fixture with multiple intent gaps and a real
  browser smoke for selection, review, and filtering.

## [0.1.40-preview] - 2026-08-25

### Added

- Local `.vibewiki/reviews.json` overlay for human review status and bounded
  notes without changing deterministic graph or source evidence artifacts.
- Loopback `GET/POST /api/reviews` with atomic writes, `open`/`reviewed`
  transitions, validation limits, and review counts.
- Inspector actions to mark an Unknown or graph subject reviewed, save a note,
  and reopen it without reloading the viewer.
- Review state is included in source-free exports and covered by API, UI marker,
  and real browser smoke tests.

## [0.1.39-preview] - 2026-08-25

### Added

- Bounded graph index snapshots for deterministic node/edge change detection.
- `/api/changes` with file changes, graph changes, run IDs, counts, and a
  truncation safety flag.
- Scan history **Change review** UI separating source-file changes from graph
  nodes and edges, with current-node inspection links.
- Regression and browser smoke coverage for rescan-driven change review.

## [0.1.38-preview] - 2026-08-25

### Added

- Product intent setup directly in the viewer; no manual `product.seed.yaml`
  editing is required for the common flow workflow.
- Loopback `POST /api/intent` validates and atomically persists a canonical
  seed, rescans the workspace, and returns updated intent comparison counts.
- Regression coverage for intent writer round-tripping, API configuration,
  UI markers, and browser Unknowns refresh.

## [0.1.37-preview] - 2026-08-25

### Added

- Astro `src/pages/*.astro` route facts with `index` and `[slug]` dynamic
  segment normalization.
- Nuxt `pages/*.vue` route facts with deterministic source evidence.
- Profile labels for Astro pages and Nuxt pages, plus fixture coverage for
  both adapters.

## [0.1.36-preview] - 2026-08-25

### Added

- Generic Browse/import support for Astro, GraphQL/GQL, Protobuf, Terraform,
  HCL, PowerShell, batch, Perl, R, Objective-C/C++, F#/Visual Basic, Solidity,
  Clojure, assembly, Gradle, and common template files.
- Regression coverage proving discovery and local-path import preserve these
  formats inside bounded temporary workspaces.

## [0.1.35-preview] - 2026-08-25

### Fixed

- Generic scan no longer rejects a monorepo that has both a root `app/` and
  nested package routers such as `packages/web/app/`.
- Strict Next mode retains the original direct App Router contract and still
  rejects nested layouts when explicitly requested.
- Added regression coverage for mixed root/nested routes and collision-safe
  package-scoped route keys.

## [0.1.34-preview] - 2026-08-25

### Fixed

- Grounded AI answer normalization now breaks inline numbered step headings
  (`**Bước 1 — ...**`) into readable Markdown blocks before rendering.
- The backend and viewer apply the same deterministic rule, including when a
  provider returns escaped newlines or a single long response line.
- Added a regression test for the formatting shape reported by users.

## [0.1.33-preview] - 2026-08-25

### Fixed

- Viewer workspace summary now displays the complete graph edge count from
  facts, modules, packages, and symbols instead of only the smaller fact
  relation subset.
- The summary label now says `Graph edges` and identifies the combined static
  and package graph source, matching the interactive graph and inspector.

## [0.1.32-preview] - 2026-08-25

### Added

- Nested App Router and Pages Router route detection for monorepos scanned
  from the repository root, including `.js`, `.jsx`, `.ts`, and `.tsx` files.
- Package-scoped route semantic keys prevent same-path collisions between
  multiple frontend packages while keeping the user-facing route path clear.
- Regression coverage proving nested page and API handler facts retain exact
  path evidence.

### Changed

- Nested route/API facts now participate in the existing evidence graph and
  package-scoped page-to-handler links instead of remaining module-only.

## [0.1.31-preview] - 2026-08-25

### Added

- Workspace package-name resolution for local JavaScript/TypeScript monorepo
  imports such as `@demo/ui` and `@demo/ui/button`.
- Safe `package.json` entry-point and `exports` resolution with deterministic
  module/symbol evidence; package scripts are never executed.
- Regression coverage for package-name imports, subpath exports, and the
  existing path-alias and generic reverse graph behavior.

### Changed

- Reverse graph edges now connect local workspace package dependencies instead
  of labeling them as inferred external modules.

## [0.1.30-preview] - 2026-08-25

### Added

- TypeScript/JavaScript path-alias resolution from `tsconfig.json` and
  `jsconfig.json` for reverse module and symbol graphs.
- Nested package config support for aliases such as `@/*`, `@web/*`, and
  `@shared/*`, with deterministic evidence path/line metadata.
- Regression coverage for a monorepo-style `packages/web` alias and existing
  multilanguage reverse edges.

### Changed

- Generic repository analysis now links alias imports to local modules instead
  of leaving them as inferred external modules when the config provides a safe
  mapping.

## [0.1.29-preview] - 2026-08-25

### Added

- Workspace provenance in `/api/summary` and the viewer after Browse, local
  path, or public GitHub import.
- Safe source labels such as `GitHub · owner/repo@ref` and `local-path · folder`
  survive graph reloads without exposing absolute paths or credentials.
- Regression coverage for provenance on initial workspace and GitHub API swap.

### Changed

- README and product plan now make the source/origin boundary visible to users.

## [0.1.28-preview] - 2026-08-25

### Added

- **Import GitHub** in the viewer for explicit public HTTPS repository imports,
  with optional branch/tag selection and command-palette access.
- Loopback-only `/api/import-github` with bounded archive download, safe tar
  handling, supported-file filtering, secret/ignored path filtering, monorepo
  package selection, and temporary workspace cleanup.
- Clear GitHub URL/ref, network/HTTP, archive, file-limit, byte-limit, and
  unsupported-source errors while preserving the current artifact on failure.
- Regression coverage for archive security, package selection, limits, API
  behavior, UI markers, and live modal/status/console behavior.

### Changed

- README and product plan now document public GitHub import and the intentional
  private-repository/local-clone boundary.

## [0.1.27-preview] - 2026-08-25

### Changed

- Refreshed the README as the product-facing entry point for the verified
  local workflow: scan, reverse graph, evidence, grounded Q&A, runtime
  observation, Browse, rescan, and source-free export.
- Documented that the product screenshot is generated by the live local viewer
  and attached the real viewer screenshot and product hero to the GitHub
  release.
- Kept the release local-first and evidence-backed; no hosted backend or
  source upload is implied by the screenshots or documentation.

## [0.1.26-preview] - 2026-08-25

### Fixed

- Added a workspace lock around artifact reads and workspace swaps in the
  threaded local server.
- Browse/import, rescan, Ask, Observe, export, and graph APIs now see a
  consistent source root and artifact while another workspace is being loaded.
- Added a concurrency regression that blocks API readers until an imported
  workspace has a complete artifact.

## [0.1.25-preview] - 2026-08-25

### Changed

- Replaced the disabled Share placeholder with a working **Copy local link**
  action and clipboard fallback.
- Added the same action to the command palette for compact viewports; the UI
  explains that the copied URL works while the local server is running.
- Added static regression and live browser coverage for the action.

## [0.1.24-preview] - 2026-08-25

### Changed

- Grounded Q&A results now render four explicit sections: **Answer**,
  **Evidence**, **Confidence**, and **Unknowns**.
- Markdown answer content remains formatted while citations and uncertainty are
  separated into readable, evidence-first panels.
- Added regression and browser coverage for evidence-only Q&A formatting.

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

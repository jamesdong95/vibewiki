# VibeWiki Product Development Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Xây dựng VibeWiki thành một CLI/local web app local-first có thể phân tích repository Next.js/TypeScript, tạo facts và graph có bằng chứng, sinh product wiki có citation, rồi mở rộng sang Q&A local và so sánh product intent với implementation.

**Architecture:** Pipeline tách facts khỏi diễn giải: file discovery và static analysis tạo facts deterministic; SQLite lưu sources, evidence, nodes, edges và claims; wiki/viewer đọc từ knowledge base local. LLM là lớp tùy chọn phía trên retrieval có giới hạn, không được trở thành nguồn sự thật duy nhất. Runtime explorer bằng Playwright chỉ triển khai sau MVP.

**Tech Stack:** Python 3.11+, `uv`, CLI Python, SQLite, Tree-sitter TypeScript/TSX hoặc parser tương đương, Git CLI/library, Markdown/Mermaid, local HTTP server/API tối giản, HTML/CSS/JS viewer hiện có, Ollama adapter tùy chọn.

---

## 1. Định vị sản phẩm

### Tuyên bố giá trị

> VibeWiki giúp solo coder hiểu lại sản phẩm mà họ đã xây với AI bằng cách nối **product concept → user flow → route/screen → API → service/function → data entity → source code → test/commit**, kèm evidence và mức độ tin cậy.

VibeWiki phải trả lời được câu hỏi “điều gì thực sự tồn tại trong codebase?” tốt hơn chatbot tổng quát. Khi code không đủ bằng chứng, sản phẩm phải hiện rõ `Unknown` hoặc `Chưa đủ bằng chứng để kết luận`, không suy đoán như sự thật.

### Người dùng mục tiêu ban đầu

- Solo coder hoặc small team có một ứng dụng Next.js/TypeScript đã phát triển nhanh với AI.
- Người thừa kế một repository nhưng thiếu tài liệu đáng tin cậy.
- Developer cần tìm ảnh hưởng của một route, API, schema hoặc symbol trước khi sửa code.

### Ba use case ưu tiên

1. **Understand:** “Feature checkout/đăng ký/reset password thực sự đi qua những file nào?”
2. **Impact:** “Nếu thay đổi `users` schema hoặc function này, phần nào có thể bị ảnh hưởng?”
3. **Gap:** “Product intent nói flow này phải hoàn tất, nhưng implementation đã có đủ chưa?”

---

## 2. Baseline hiện tại và kế hoạch tự động tiếp theo

> Cập nhật 2026-08-18 từ worktree và live preview hiện tại. Mỗi phase chỉ được
> gọi hoàn thành khi có artifact, test và smoke evidence tương ứng.

### Đã triển khai

- CLI `scan → build → serve` với artifact `.vibewiki/` và SQLite graph.
- Browse/import local cho repository generic, monorepo/package lồng nhau, JS/JSX/TS/TSX,
  Python, Go, Rust, Java/Kotlin, C-family, Swift/Dart, shell, SQL, config/docs và Prisma.
- Route/module/package/symbol/API/test graph với evidence path/line và unknowns deterministic.
- Viewer đọc artifact thật, search/graph/inspector/source preview, Browse source và
  trạng thái lỗi import.
- LLM setup memory-only, Ollama/OpenAI-compatible BYOK, Ask grounded với Discuss,
  Flow, Impact và Unknowns modes; câu trả lời Markdown được render an toàn.
- Export thật qua nút/command palette và `/api/export`: ZIP wiki, Mermaid, graph,
  evidence manifests và unknowns, không chứa source files.
- Product intent seed tùy chọn qua `product.seed.yaml`, comparator deterministic,
  `.vibewiki/intent.json`, `/api/intent`, và Unknowns intent gaps trong viewer.
- Scan history local qua `.vibewiki/history.json`, `vibewiki history`,
  `/api/history`, `/api/stale`; changed/removed source được đánh dấu stale trên
  node, edge, source và export runtime.
- Bounded reverse graph traversal qua `/api/impact` và viewer inspector: upstream,
  downstream, both, depth/limit safeguards và edge evidence giữ nguyên.
- README, changelog, screenshot live preview, release metadata và draft PR đã có.
- Packaging baseline đã hoàn tất: wheel chứa viewer asset, clean-install
  quickstart và CI smoke gate chạy trên Python 3.11–3.13 ở Ubuntu/macOS.
- Runtime acceptance baseline đã hoàn tất: fixture browser local, Playwright /
  Chromium smoke, route/API/console graph linkage và CI runtime job.
- Generic adapter coverage có fixture Vite/React, Next.js Pages Router, Vue
  Router và SvelteKit; viewer có Source files inventory để đi từ indexed file
  đến bounded source preview.
- Browse large-repo preflight nhóm package monorepo, hiển thị file/byte counts,
  cho phép scan package nằm trong safety limit và giữ artifact cũ khi import
  thất bại.
- Reverse module graph có evidence cho Python, Go, Rust, Java/Kotlin và C/C++
  local imports; external dependencies vẫn được đánh dấu inferred.
- Project profile deterministic có API `/api/profile`, hiển thị scan mode,
  framework/language coverage, package scope và giới hạn import trong viewer;
  project switcher đã trở thành control Browse scope thật; selector package
  focus graph/evidence/inspector mà không cần import lại.
- CLI onboarding tự nhận diện direct Next App Router hoặc generic repository;
  `vibewiki analyze` chạy scan + build trong một bước, còn `--strict-next` giữ
  contract legacy cho CI và fixture golden.
- Generic adapter coverage đã thêm Angular Router route arrays và NestJS
  controller decorators với route evidence deterministic.
- Browse có fallback **Use local path** qua loopback API cho môi trường không
  cung cấp folder picker; snapshot vẫn đi qua ignore, secret và size limits.
- Viewer có **Rescan workspace** thật qua loopback API; rescan snapshot artifact
  hiện tại, cập nhật graph sau khi source đổi, và rollback `.vibewiki/` nếu scan
  hoặc build thất bại.
- `vibewiki serve REPOSITORY` tự bootstrap scan + build khi chưa có artifact,
  để người dùng mới có thể chạy một lệnh và mở được viewer.
- Viewer tự phát hiện stale source trong lúc đang mở, hiển thị path bị đổi và
  chỉ rescan khi người dùng bấm xác nhận.
- Placeholder Share đã được thay bằng Copy local link, có clipboard fallback và
  thông báo rõ link chỉ hoạt động khi local server còn chạy.
- Local server khóa workspace swap/read trong lúc Browse, rescan, Ask hoặc
  Observe để tránh graph và source root bị lệch khi request chạy đồng thời.
- Viewer có **Import GitHub** explicit action cho public HTTPS repository URL và
  branch/tag tùy chọn; archive bị giới hạn trước khi đọc, chỉ regular supported
  files được copy, secrets/ignored paths bị loại, và private/authenticated
  repositories vẫn dùng local clone/path để không đưa credential vào MVP.
- Workspace summary giữ provenance an toàn sau import/reload (`GitHub ·
  owner/repo@ref`, `local-path · folder`, hoặc `browser-folder`) để người dùng
  luôn biết graph hiện tại đến từ đâu mà không lộ absolute path.
- Reverse module/symbol graph resolve `paths` aliases từ `tsconfig.json` và
  `jsconfig.json`, kể cả config nằm trong package lồng nhau (`@/*`,
  `@shared/*`), với evidence line/path deterministic.
- Reverse module/symbol graph resolve local workspace package names và
  subpaths (`@demo/ui`, `@demo/ui/button`) qua `types`/`module`/`main` và
  `exports` an toàn, không thực thi package scripts.
- Nested App Router/Pages Router paths such as `packages/web/app/...` and
  `apps/frontend/pages/...` retain route facts, package-scoped semantic keys,
  and source evidence when the repository root is scanned directly.
- Viewer graph summary counts now use the combined artifact edge set (facts,
  modules, packages, and symbols), so the displayed total matches the graph
  users can inspect.
- Grounded answer normalization separates inline numbered step headings such as
  `**Bước 1 — ...**` before the viewer renders Markdown, keeping model output
  readable even when a provider returns one long line.
- Generic scanning now accepts repositories that contain both a root `app/`
  and nested package routers; strict Next mode keeps its legacy validation while
  package-scoped route keys prevent collisions.
- Browse/import generic registry now covers additional real-world source and
  infrastructure formats (Astro, GraphQL, Protobuf, Terraform/HCL, PowerShell,
  Perl, R, Solidity, Objective-C, F#, and related templates/scripts) with
  discovery and local-path regression coverage.

### Khoảng trống còn lại theo ưu tiên người dùng

- Adapter coverage cho các framework/language chưa có fixture chuyên biệt.
- GitHub private-repository OAuth, webhook sync và hosted multi-user workspace
  chưa nằm trong local-first MVP; public archive import là boundary chủ động.

### Thứ tự implementation tự động tiếp theo

1. **Adapter coverage:** mở rộng language/framework bằng fixture và evidence gates.
2. **User workflow:** thêm project profile/scan selection và tiếp tục bounded
   large-repo import khi nhu cầu người dùng thật chứng minh giới hạn hiện tại
   chưa đủ.

### Giả định để triển khai

- Repository mới sẽ được tạo tại `the repository root` hoặc một đường dẫn do người dùng chọn.
- Prototype sẽ được di chuyển/copy vào `viewer/` sau khi có repository; không phá hỏng bản preview hiện tại trong quá trình chuyển đổi.
- MVP chỉ cam kết nhóm stack: **Next.js App Router + TypeScript/TSX + một trong Prisma/Drizzle hoặc SQL migration phổ biến**. Các framework khác sẽ được báo `unsupported` thay vì phân tích mơ hồ.
- CLI và knowledge base chạy local; không cần tài khoản, billing, hosted backend hoặc API do nhà phát triển vận hành.

---

## 3. Phạm vi MVP

### MVP phải làm được

```text
vibewiki scan /path/to/next-app
vibewiki build /path/to/next-app
vibewiki serve /path/to/next-app
```

Sau khi chạy scan/build, VibeWiki tạo:

```text
/path/to/next-app/.vibewiki/
├── graph.db
├── manifest.json
├── claims.json
├── sources.json
├── intent.json
└── wiki/
    ├── index.md
    ├── routes.md
    ├── flows.md
    ├── data-model.md
    └── graph.mmd
```

MVP không có LLM vẫn phải cung cấp:

- File manifest và incremental scan theo content hash.
- Route map cho App Router; route handlers/API endpoints.
- Import/dependency graph.
- Symbol map cho function, class, component và exported symbol.
- Các quan hệ frontend call → API route khi có evidence tĩnh.
- Data model map cho schema/migration được adapter hỗ trợ.
- Test-to-code links khi test path/name/import cho phép xác định.
- SQLite graph có source line range, commit hiện tại, confidence và trạng thái evidence.
- Markdown wiki và Mermaid graph deterministic.
- Viewer localhost hiển thị dữ liệu thật thay cho mock data.
- Inspector có `Explanation`, `Evidence`, `Confidence`, `Unknowns`.

### Không làm trong MVP

- Hosted SaaS, account system, billing hoặc server trung tâm.
- Telemetry mặc định hoặc upload repository.
- Hỗ trợ đồng thời mọi framework/language.
- Embedding/vector database trước khi keyword/graph retrieval chứng minh là không đủ.
- Runtime crawler đầy đủ bằng Playwright.
- Phân tích thanh toán/auth thật nếu không có credential hoặc runtime evidence; không được đoán.
- Desktop app native.
- Plugin marketplace hoặc hệ thống extension phức tạp.

---

## 4. Nguyên tắc kỹ thuật và an toàn

1. **Facts first:** parser/static analysis/runtime observation tạo facts trước; LLM chỉ giải thích hoặc nhóm facts.
2. **Evidence required:** mỗi claim/edge xuất hiện trong UI phải có ít nhất một source hoặc được đánh dấu `unknown`.
3. **Confidence explicit:** dùng `verified`, `inferred`, `unknown`, `stale`; không dùng “high confidence” nếu không có tiêu chí rõ ràng.
4. **No whole-repository prompt:** Q&A chỉ lấy context từ symbol/module/route liên quan, có giới hạn số file và token.
5. **Privacy by default:** không gọi network trong `scan`/`build`; credential/value nhạy cảm phải thành `[REDACTED]`; không lưu secret content vào graph.
6. **Deterministic IDs:** source, node và edge có ID ổn định theo normalized path, qualified name và relation để scan lặp lại không tạo bản sao.
7. **Unknown over hallucination:** thiếu evidence phải hiện “Chưa đủ bằng chứng để kết luận.”
8. **Incremental:** chỉ parse lại file thay đổi và các node bị ảnh hưởng; lưu analyzer version để invalidation có thể kiểm soát.
9. **Human review:** các inferred claims và product intent gap phải có trạng thái review; con người có thể accept/reject/correct.
10. **Small supported surface:** mở rộng adapter chỉ sau khi fixture và acceptance tests của stack hiện tại ổn định.

---

## 5. Kiến trúc mục tiêu

### Pipeline

```text
Repository
  ↓
File discovery + ignore rules + content hashes
  ↓
Framework/language adapters
  ↓
Normalized facts + source ranges
  ↓
Evidence linker + confidence classifier
  ↓
SQLite graph / claims / scan runs
  ↓
Markdown + Mermaid exporter
  ↓
Local API + viewer
  ↓
Optional retrieval + local LLM / BYOK provider
```

### Cấu trúc repository đề xuất

```text
vibewiki/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── src/vibewiki/
│   ├── cli.py
│   ├── config.py
│   ├── scan.py
│   ├── build.py
│   ├── serve.py
│   ├── discovery/
│   │   ├── files.py
│   │   ├── ignore.py
│   │   └── hashing.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── nextjs.py
│   │   ├── typescript.py
│   │   ├── prisma.py
│   │   └── tests.py
│   ├── facts/
│   │   ├── models.py
│   │   ├── normalize.py
│   │   └── linking.py
│   ├── storage/
│   │   ├── schema.sql
│   │   ├── migrations.py
│   │   └── repository.py
│   ├── graph/
│   │   ├── ids.py
│   │   ├── traversal.py
│   │   └── mermaid.py
│   ├── evidence/
│   │   ├── redaction.py
│   │   ├── confidence.py
│   │   └── claims.py
│   ├── wiki/
│   │   ├── generator.py
│   │   └── templates.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── none.py
│   │   └── ollama.py
│   └── server/
│       ├── app.py
│       └── api.py
├── viewer/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── tests/
│   ├── fixtures/next-ts-demo/
│   ├── unit/
│   ├── integration/
│   └── golden/
└── docs/
    ├── product-spec.md
    ├── architecture.md
    ├── evidence-policy.md
    └── adapters/nextjs.md
```

### SQLite schema tối thiểu

Các bảng ban đầu:

- `scan_runs`: repository root, current commit, analyzer version, timestamps, status.
- `files`: normalized path, language, size, content hash, ignored/sensitive flag.
- `sources`: file ID, line start/end, symbol/route reference, commit SHA.
- `nodes`: stable ID, kind, label, qualified name, status.
- `edges`: source node, relation, target node, confidence, evidence source ID.
- `claims`: statement, claim type, status, confidence, generated/reviewed timestamps.
- `claim_evidence`: many-to-many claim/source mapping.
- `unknowns`: unresolved question, affected node/claim, reason, suggested human input.
- `product_seeds`: imported YAML metadata and expected flows.

### Evidence contract

```json
{
  "source": "app/api/orders/route.ts",
  "line_start": 18,
  "line_end": 42,
  "kind": "route_handler",
  "commit": "<sha-or-null>",
  "confidence": "verified",
  "redacted": false
}
```

MVP không cần lưu source code đầy đủ trong SQLite; chỉ lưu path, hash, line range, symbol metadata và excerpt đã redaction khi thực sự cần hiển thị.

---

## 6. Roadmap theo phase

Ước tính dưới đây dành cho một solo coder tập trung, không bao gồm thời gian chờ feedback hoặc thay đổi phạm vi. Mỗi phase có một gate rõ ràng; không bắt đầu phase sau nếu gate trước chưa đạt.

### Phase 0 — Product contract và fixture chuẩn

**Mục tiêu:** khóa định nghĩa “evidence-based understanding” trước khi xây analyzer.

**Kết quả:**

- `docs/product-spec.md` định nghĩa user jobs, output contract và MVP acceptance.
- `docs/evidence-policy.md` định nghĩa verified/inferred/unknown/stale.
- `tests/fixtures/next-ts-demo/` là app nhỏ có route, API, service, schema, test và một intentional gap.
- Bộ câu hỏi vàng: route đăng ký, reset password, checkout, schema `users`, route không có test, API không được frontend gọi.

**Gate:** một người mới có thể chạy fixture và hiểu expected graph trước khi parser được viết.

### Phase 1 — CLI và file discovery incremental

**Mục tiêu:** `vibewiki scan` đọc repository an toàn, deterministic và không gọi network.

**Kết quả:**

- `pyproject.toml`, package skeleton và entrypoint CLI.
- Ignore `.git`, `node_modules`, build output, cache và file nhạy cảm theo policy.
- Manifest file/language/size/hash lưu trong `.vibewiki/manifest.json` hoặc SQLite.
- Scan lặp lại không tạo duplicate; file không đổi được cache hit.
- Error rõ ràng cho path không tồn tại, permission denied và stack chưa hỗ trợ.

**Gate:** scan fixture hai lần cho cùng manifest và không tạo network request.

### Phase 2 — Next.js/TypeScript static analysis

**Mục tiêu:** tạo normalized facts từ nhóm stack đầu tiên.

**Facts cần thu thập:**

- App Router và Pages Router route path.
- Route handler/API method, request/response marker.
- Component/function/class/export và qualified name.
- Import/export và dependency edges nội bộ.
- `fetch`, axios hoặc client wrapper gọi API.
- Prisma schema/migration hoặc pattern SQL được hỗ trợ.
- Test file, test name và import/route reference.
- Auth middleware/guard marker; chỉ ghi nhận evidence, không suy luận security correctness.

**Gate:** golden fixture tạo đúng expected facts; unsupported pattern được gắn `unknown` thay vì bỏ qua âm thầm.

### Phase 3 — SQLite facts, evidence và graph

**Mục tiêu:** biến facts thành knowledge base có thể query và update incremental.

**Kết quả:**

- Schema/migrations và repository layer.
- Stable IDs cho node/edge/source.
- Evidence line ranges và commit metadata.
- Confidence classifier deterministic cho các relation tĩnh.
- Traversal API: neighbors, upstream/downstream, route-to-symbol và symbol-to-test.
- Stale marking khi file/hash/commit thay đổi.

**Gate:** query “route → API → function → data entity → test” trả về path và line evidence có thể kiểm tra lại.

### Phase 4 — Wiki Markdown và Mermaid không cần LLM

**Mục tiêu:** người dùng nhận được artifact hữu ích ngay cả khi không cài model.

**Kết quả:**

- `wiki/index.md`, `routes.md`, `flows.md`, `data-model.md`.
- `graph.mmd` biểu diễn node/edge theo loại và confidence.
- Mỗi trang có bảng `Evidence` và `Unknowns`.
- `vibewiki build` deterministic; build lại không tạo diff nếu input không đổi.
- Export có thể đọc offline bằng Markdown viewer thông thường.

**Gate:** một repository fixture tạo wiki mà mọi claim đều có citation hoặc trạng thái unknown.

### Phase 5 — Local server và viewer dùng dữ liệu thật

**Mục tiêu:** thay mock data trong prototype bằng `.vibewiki/graph.db` thực tế.

**Kết quả:**

- `vibewiki serve` bind mặc định `127.0.0.1`, không public network.
- API tối thiểu: `/api/summary`, `/api/nodes`, `/api/edges`, `/api/inspect/:id`, `/api/search`.
- `viewer/index.html`, `viewer/app.js`, `viewer/styles.css` tách khỏi prototype monolith khi UI ổn định.
- Dashboard hiển thị scan timestamp, commit, analyzer version, LLM mode và unknown count.
- Graph click/search/zoom mở inspector từ dữ liệu SQLite.
- Evidence links mở file/line theo format local rõ ràng; không gửi path ra ngoài.

**Gate MVP:** chạy `scan → build → serve`, mở UI localhost và trả lời được ít nhất 5 câu hỏi vàng bằng graph/wiki không cần LLM.

### Phase 6 — Product seed và intent gap

**Mục tiêu:** phân biệt implementation facts với product intent do người dùng cung cấp.

**Kết quả:**

- `product.seed.yaml` có product, audience, goals, flows, expected outcomes.
- Validator báo lỗi schema và chỉ chấp nhận field đã định nghĩa.
- Comparator map expected step với observed route/API/data/test.
- Trạng thái gap: `observed`, `partially_observed`, `not_observed`, `unknown`.
- UI hiển thị evidence của cả intent input và implementation; không gọi `not_observed` là “missing” nếu adapter chưa hỗ trợ.

**Gate:** intentional gap trong fixture được phát hiện với evidence frontend và thiếu backend/API, kèm confidence phù hợp.

### Phase 7 — Ollama/local LLM Q&A tùy chọn

**Mục tiêu:** thêm giải thích tự nhiên nhưng vẫn bị ràng buộc bởi graph/evidence.

**Implementation slice hiện tại:** đã bắt đầu với provider interface, fallback
evidence-only, adapter Ollama/OpenAI-compatible, retrieval bounded và API
`/api/ask`; UI Ask giữ conversation ngắn, cho chọn Discuss/Flow explainer/
Impact analyzer/Unknowns investigator và hiển thị citations. Model vẫn là
optional, API key chỉ được đọc ở server environment.

**Kết quả:**

- `providers/base.py` với interface `generate(prompt, context)`.
- `providers/none.py` giữ fallback không LLM.
- `providers/ollama.py` gọi Ollama local khi người dùng bật explicit; không bắt buộc cài Ollama.
- Retrieval theo route/symbol/claim/graph neighborhood; giới hạn context, không gửi toàn repository.
- Output contract bắt buộc:

```text
Answer
Evidence
Confidence
Unknowns
```

- Nếu model không thể trích dẫn source đã retrieve, câu trả lời bị đánh dấu `unknown` hoặc không được tạo.
- Log local chỉ lưu prompt metadata tối thiểu; không lưu secret.

**Gate:** Q&A fixture trả lời được câu hỏi route/function với citation chính xác; khi tắt Ollama, MVP vẫn hoạt động.

### Phase 8 — Git history và evidence staleness

**Mục tiêu:** giải thích “khi nào/commit nào thay đổi phần này?” và phát hiện tài liệu cũ.

**Kết quả:**

- Commit SHA, author/date và changed paths liên kết với sources/nodes.
- `vibewiki history <node-or-path>`.
- Stale evidence khi source thay đổi sau lần scan/build.
- Wiki hiển thị last observed commit, không suy luận business reason từ commit message nếu không đủ evidence.

**Implementation slice hiện tại:** `history.json` lưu tối đa 50 scan runs với
commit metadata và `added/changed/removed` paths; `/api/history` và CLI query
theo path/subject; `/api/stale` đối chiếu hash hiện tại sau build; viewer có
Scan history inspector; export kèm history và staleness snapshot.

**Gate:** fixture Git nhỏ chứng minh source đổi sẽ đánh dấu claim liên quan stale và re-scan cập nhật đúng node.

**Runtime baseline đã triển khai trong 0.1.4-preview:** `vibewiki observe`
và `/api/observe` chỉ thực hiện bounded same-origin `GET` trên loopback mặc
định; `/api/runtime` và `runtime.json` lưu route/network metadata, timestamp,
và unknown rõ ràng cho JavaScript/console/side effects chưa được chạy. Viewer
có nút Observe runtime và export chứa runtime artifact. Browser mode đã triển
khai trong 0.1.5-preview qua `vibewiki[runtime]`, chạy headless Chromium với
same-origin `GET`, chặn request khác origin/non-GET, thu console error, network
status và screenshot metadata; side effects/auth vẫn là unknown. Runtime
records được join deterministic vào route/API node theo path + method, persist
trong `runtime.json`, và hiển thị trong inspector.

### Phase 9 — Runtime explorer bằng Playwright

**Mục tiêu:** bổ sung observed behavior cho phần static analysis không thể chứng minh.

**Kết quả:**

- Lệnh explicit: `vibewiki observe http://localhost:3000`.
- Lưu route visited, network request, console error, screenshot metadata và timestamp local.
- Không tự đăng nhập hoặc gửi form có side effect nếu user chưa cấu hình flow/approval.
- Runtime evidence tách với static evidence; UI hiển thị nguồn và thời điểm quan sát.
- Payment/auth/external service không được coi là observed nếu thiếu credential/route execution.

**Gate:** demo app local có route transition và API request được liên kết với graph; lỗi runtime xuất hiện trong inspector.

**Đã đạt trong 0.1.6-preview:** runtime route/network/console records có
`graph_nodes` deterministic; `/api/nodes`, `/api/runtime` và `/api/inspect/*`
trả linked evidence; viewer inspector hiển thị status/error cho node được chọn.

### Phase 10 — Release hardening và open-source distribution

**Mục tiêu:** phát hành bản `0.1.14-preview` mà solo coder có thể cài và dùng mà không cần hạ tầng của maintainer.

**Kết quả:**

- README quickstart với `uv`/`pipx`, ví dụ fixture và troubleshooting.
- `CHANGELOG.md`, license, CONTRIBUTING và privacy/security policy.
- Version CLI hiển thị analyzer version và schema version.
- CI chạy unit/integration/golden tests trên macOS/Linux.
- Demo repository không chứa secret và có screenshot/GIF tùy chọn.
- Viewer có export ZIP thật cho wiki/graph/evidence mà không đóng gói source.
- Generic analyzer có fixture Vite/React, nhận diện route-object của React Router
  và nối các API wrapper literal vào generic route tương ứng.
- Generic analyzer có fixture Vue Router và SvelteKit, nhận diện route-object,
  filesystem route và `+server` endpoint với reverse API-call evidence.
- Build/serve error messages có exit code ổn định.
- Kiểm tra localhost binding và network-offline behavior.

**Đã bổ sung trong 0.1.5-preview:** workflow Verify chạy locked dependency,
Ruff, tests, viewer JavaScript syntax và preview checks trên Ubuntu/macOS với
Python 3.11–3.13.

**Gate release:** người dùng mới có thể cài, scan fixture, mở viewer, đọc wiki và xóa `.vibewiki/` mà không cần tài khoản/provider.

---

## 7. Kanban backlog và dependency routing

Dùng các card dưới đây làm backlog ban đầu. Một card chỉ được chuyển sang `Done` khi artifact và verification evidence tồn tại; self-report của worker không đủ để gọi hoàn thành.

| ID | Card | Lane đề xuất | Phụ thuộc | Done khi |
|---|---|---|---|---|
| VWK-001 | Product contract + evidence policy | analyst/writer | — | spec, policy và acceptance questions được review |
| VWK-002 | Next.js/TS golden fixture | backend/analyst | VWK-001 | fixture chạy và có expected graph/gaps |
| VWK-003 | Python package + CLI skeleton | backend | VWK-001 | `--help`, version, error codes hoạt động |
| VWK-004 | Safe file discovery + hashing | backend | VWK-002, VWK-003 | scan deterministic, cache hit lần hai |
| VWK-005 | Next.js route adapter | backend | VWK-004 | route facts có line evidence |
| VWK-006 | TypeScript symbol/import adapter | backend | VWK-004 | symbol/import graph pass golden tests |
| VWK-007 | Data schema/test adapters | backend | VWK-005, VWK-006 | Prisma/SQL/test links có status rõ |
| VWK-008 | SQLite schema + repository | backend | VWK-004 | insert/upsert/query/idempotency pass |
| VWK-009 | Evidence/confidence/stale model | analyst/backend | VWK-005–008 | mọi relation có evidence/status |
| VWK-010 | Graph traversal + Mermaid export | backend | VWK-008, VWK-009 | route-to-test traversal và `.mmd` pass |
| VWK-011 | Markdown wiki generator | backend/writer | VWK-010 | wiki offline, citation/unknown sections đầy đủ |
| VWK-012 | Local API/server | backend | VWK-008, VWK-011 | bind `127.0.0.1`, endpoints trả schema ổn định |
| VWK-013 | Viewer integration | frontend | VWK-012 | UI đọc graph thật, inspector có evidence |
| VWK-014 | MVP verification gate | QA/reviewer | VWK-002, VWK-011, VWK-013 | 5 golden questions có evidence |
| VWK-015 | Product seed + intent comparator | analyst/backend | VWK-014 | intentional gap được phát hiện |
| VWK-016 | Ollama provider + grounded Q&A | AI/backend | VWK-014, VWK-015 | local model optional, citation contract pass |
| VWK-017 | Git history/staleness | backend | VWK-014 | changed source đánh dấu stale đúng |
| VWK-018 | Playwright runtime observer | runtime | VWK-014 | route/network/error evidence tách biệt |
| VWK-019 | Packaging/docs/privacy release | release/QA | VWK-014 | clean install + fixture quickstart pass |

### Dependency graph chính

```text
VWK-001 → VWK-002 → VWK-004 → VWK-005/006/007 → VWK-008 → VWK-009
                                           ↓                 ↓
                                      VWK-010 → VWK-011 → VWK-012 → VWK-013 → VWK-014
                                                                                 ↓
                                                             VWK-015 → VWK-016
                                                                                 ├→ VWK-017
                                                                                 ├→ VWK-018
                                                                                 └→ VWK-019
VWK-003 có thể chạy song song sau VWK-001 và trước VWK-004.
```

Nếu dùng nhiều Hermes profiles, tạo toàn bộ task và links trước khi dispatch để tránh gateway chạy một task khi dependency graph chưa hoàn tất. Các lane frontend phải dùng profile/frontend workflow đã thống nhất; nếu lane đó không khả dụng, ghi rõ manual fallback, không báo cáo như thể lane chuyên dụng đã thực hiện.

---

## 8. Acceptance criteria theo cấp

### P0 — Privacy và deterministic behavior

- `scan`/`build` không gọi network khi provider/runtime không được bật.
- `.env`, private keys, token và credential value không xuất hiện trong claims/evidence excerpts.
- Cùng repository, cùng commit và cùng analyzer version tạo cùng node/edge IDs.
- Scan lại file không đổi không tạo duplicate hoặc thay đổi wiki không cần thiết.

### P0 — Evidence correctness

- Mỗi displayed claim có source path + line range hoặc trạng thái `unknown`.
- Click evidence mở đúng file/line format.
- `inferred` không bị hiển thị như `verified`.
- Source thay đổi làm evidence liên quan được cập nhật hoặc đánh dấu `stale`.

### P0 — MVP usability

- Người dùng mới chạy được quickstart trong tối đa vài lệnh.
- Không có LLM vẫn xem được graph, routes, wiki và evidence.
- Viewer không bị clipped ở viewport desktop 1280px và có layout usable ở mobile.
- Search theo route/symbol/file hoạt động; node selection cập nhật inspector.

### P1 — Grounded Q&A

- Q&A trả về đúng bốn phần `Answer`, `Evidence`, `Confidence`, `Unknowns`.
- Context được giới hạn bởi retrieval; không gửi toàn repo.
- Khi evidence không đủ, output nói rõ chưa đủ bằng chứng.
- Tắt Ollama không làm hỏng scan/build/serve.

### P1 — Runtime

- Runtime evidence có timestamp và URL/session context.
- Static và runtime observation không bị trộn thành một claim không phân biệt nguồn.
- Không tự động thực hiện destructive/external side effects.

---

## 9. Verification strategy

### Unit tests

- Ignore rules, path normalization, hashing và redaction.
- Stable ID generation.
- Route/symbol/import extraction.
- Confidence/stale transitions.
- Graph traversal và Mermaid escaping.
- Product seed validation/comparison.

### Golden/integration tests

- Scan `tests/fixtures/next-ts-demo` và compare JSON normalized facts.
- Build wiki và compare snapshots đã normalize timestamp/path machine-specific.
- Re-scan unchanged fixture và assert no duplicate rows.
- Modify one fixture file, rescan và assert affected evidence/node updates.
- Bấm Rescan workspace sau khi thêm source file và xác nhận graph cập nhật trên
  browser thật, không có console error.
- Start local server and test API response schemas.

### UI verification

- Browser smoke: load dashboard, search concept, click node, open inspector, zoom graph, open command palette.
- Check `document.body.scrollWidth <= window.innerWidth` ở desktop.
- Check no uncaught JavaScript exception.
- Check confidence/evidence/unknown states bằng fixtures có verified, inferred và unknown.
- Frontend implementation/QA nên đi qua Antigravity frontend profile khi khả dụng; nếu không, ghi rõ fallback và vẫn cần fresh browser evidence.

### Release checks

```text
uv run vibewiki --help
uv run vibewiki scan tests/fixtures/next-ts-demo
uv run vibewiki build tests/fixtures/next-ts-demo
uv run vibewiki serve tests/fixtures/next-ts-demo
```

Kiểm tra thủ công:

- Tắt mạng vẫn hoàn tất scan/build.
- Không có secret trong `.vibewiki/`.
- Xóa `.vibewiki/` rồi chạy lại tạo được kết quả sạch.
- Cài từ package trong môi trường sạch.

---

## 10. Rủi ro và cách giảm thiểu

| Rủi ro | Tác động | Giảm thiểu |
|---|---|---|
| TypeScript/Next.js có quá nhiều pattern | parser không ổn định | fixture-driven, adapter nhỏ, unsupported → unknown |
| Graph quá nhiều node | UI khó dùng, scan chậm | default filter theo feature/route, lazy traversal, pagination |
| LLM hallucination | mất niềm tin | facts first, citation bắt buộc, bounded retrieval, unknown state |
| Secret bị lưu vào index | rủi ro bảo mật | ignore/redact trước parse, test secret fixtures, privacy docs |
| Incremental invalidation sai | graph stale | content hash + analyzer version + explicit stale status |
| UI prototype lệch backend | phải làm lại frontend | chốt API schema trước, dùng fixture JSON contract |
| Local model không có hoặc quá yếu | Q&A không dùng được | no-LLM MVP, provider optional, structural answers trước |
| Runtime flow có side effects | rủi ro với app người dùng | explicit command/approval, read-only default, không tự đăng nhập |
| Scope creep sang mọi stack | trì hoãn phát hành | chỉ Next.js/TS trong MVP, adapter roadmap riêng |

---

## 11. Những quyết định cần chốt trước VWK-003

1. Tên package/CLI chính thức: giữ `vibewiki` hay đổi trước khi public.
2. Repository source chính thức: tạo mới `the repository root` hay dùng workspace khác.
3. Python web layer: FastAPI/Starlette hay static server + JSON export ở MVP.
4. Parser: Tree-sitter TypeScript/TSX hay TypeScript compiler service subprocess.
5. Data adapter MVP: Prisma trước, Drizzle trước, hay chỉ schema/migration generic.
6. License open source: MIT, Apache-2.0 hoặc lựa chọn khác.
7. Có cần hỗ trợ Pages Router trong MVP hay chỉ App Router.

Khuyến nghị mặc định: package Python + SQLite + Tree-sitter + FastAPI/Starlette tối giản; Next.js App Router trước; Prisma/schema migration adapter trước; license MIT hoặc Apache-2.0 sau khi kiểm tra dependency licenses.

---

## 12. Definition of Done cho MVP

MVP chỉ được gọi là hoàn thành khi tất cả điều kiện sau đúng trên một môi trường sạch:

- [ ] Cài được bằng documented command.
- [ ] Scan fixture Next.js/TS tạo `.vibewiki/graph.db`.
- [ ] Graph có route, API, symbol, data entity và test links.
- [ ] Mọi displayed claim có evidence hoặc unknown.
- [ ] Wiki Markdown/Mermaid mở được không cần server.
- [ ] Viewer localhost đọc dữ liệu thật và inspector hiển thị evidence/confidence/unknowns.
- [ ] Search, node selection và traversal hoạt động.
- [ ] Không có LLM vẫn hoàn tất toàn bộ luồng MVP.
- [ ] Offline/privacy checks pass.
- [ ] Có README, evidence policy, fixture, tests, changelog và release notes.
- [ ] Một verifier độc lập chạy lại các bước trên và cung cấp output mới.

---

## 13. Thứ tự hành động khuyến nghị

1. Chốt tên repository, license và 5 golden questions.
2. Viết product spec/evidence policy và tạo fixture nhỏ có intentional gap.
3. Tạo package skeleton và CLI `scan/build/serve` với help/version.
4. Làm file discovery + hashing trước khi viết parser.
5. Viết route/symbol/import adapter theo golden tests.
6. Thêm SQLite/evidence/graph persistence.
7. Sinh wiki/Mermaid không LLM.
8. Kết nối local API với prototype UI và thay mock data bằng fixture data.
9. Chạy MVP gate với verifier độc lập.
10. Chỉ sau gate mới thêm runtime observer và adapter coverage; product seed,
    Ollama và history đã có implementation slice và phải được giữ regression-tested.

**Nguyên tắc release:** không bắt đầu bằng Q&A. Nếu graph và evidence core chưa đáng tin, LLM chỉ làm sản phẩm trông thông minh hơn nhưng khó kiểm chứng hơn.

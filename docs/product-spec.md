# VibeWiki MVP product specification

## 1. Purpose and contract boundary

VibeWiki is a local-first evidence graph for understanding an existing
application. The MVP contract is deliberately independent of any parser,
programming language library, database implementation, or user interface. An
implementation is conformant when it produces the normalized facts, relations,
statuses, and command outcomes described here; the implementation may use any
static-analysis technique to do so.

The MVP answers what can be established from repository files. It does not
pretend that static evidence is a runtime observation, a product requirement,
or an exhaustive dependency analysis.

## 2. User jobs

### Understand

A developer wants to reconstruct a user flow from its screen or route through
API handlers and service functions to the data entity and tests that are
actually visible in the repository. The output should show the path, the
source lines that support every direct link, and an explicit gap when a link
cannot be established.

### Impact

A developer wants to inspect the code subjects that may be affected by a
change to a route, service, schema entity, or test. VibeWiki may derive a
candidate impact neighborhood from verified relations, but it must label that
derivation as inferred and must not call it an exhaustive runtime impact list.

### Gap

A developer wants to compare an expected product surface with observed static
evidence. If a page, flow, or test is not observed, the output must say
unknown (with a reason such as `not_observed`) rather than inventing coverage or
behavior.

## 3. MVP supported stack

The MVP commits to this narrow stack:

- Next.js App Router conventions under an `app/` directory: page modules and
  route-handler modules.
- TypeScript and TSX modules, including imports, exports, functions, React page
  components, and literal API calls.
- Prisma schema files under `prisma/schema.prisma`, including model names,
  fields, and simple relation declarations.
- TypeScript test modules whose imports or literal paths provide direct links to
  application subjects.

A fixture or repository may contain other files, but only the supported
patterns are candidates for verified MVP facts.

The generic analyzer expansion in `0.1.8-preview` adds conservative route and
API facts for common Express/Fastify/Hono-style JavaScript, React Router JSX,
Flask/FastAPI decorators, and Go `HandleFunc` registrations when the user opts
into `scan --generic`. It is pattern-based rather than a language compiler:
unrecognized constructs remain inventory/module evidence and are never promoted
to verified behavior by filename alone.

When a repository is scanned from its root, the same route detection applies
inside common workspace packages. For example, `packages/web/app/page.tsx`,
`apps/frontend/src/app/page.tsx`, and nested `pages/` files retain their exact
relative evidence paths. Package-scoped semantic keys keep two packages with
the same `/` route distinct without changing the user-facing route attribute.

### Unsupported behavior

When the repository is outside this supported surface, or a construct cannot be
resolved safely, the analyzer must do all of the following:

1. Keep the scan offline and continue where safe.
2. Emit a deterministic diagnostic with an `unsupported` or `unresolved`
   reason and the normalized relative path when a path is available.
3. Avoid emitting a verified fact or relation for the unsupported construct.
4. Represent a requested but unresolvable subject as `unknown`; never match it
   by filename similarity alone.
5. Return a non-zero command status only when the requested operation cannot
   produce its contract at all. A partially analyzable repository is not a
   license to fabricate conclusions.

The MVP does not execute application code, run migrations, contact a payment or
identity provider, submit forms, inspect a browser session, or infer behavior
from a filename. Pages Router, arbitrary JavaScript, dynamic API clients,
opaque generated code, and runtime-only dependencies are outside the verified
surface unless a future contract explicitly adds them.

## 4. Evidence vocabulary

The contract separates four things:

- **Fact:** a normalized observation about a repository subject, such as a page
  route, exported function, Prisma model, import, or literal API call. A fact
  has a semantic key and source evidence.
- **Claim:** a human-readable statement assembled from one or more facts. A
  claim may be direct or derived, but it must carry its supporting evidence and
  status.
- **Evidence:** a source reference with a relative POSIX path, a 1-indexed
  inclusive line range, a kind, and a status. Evidence identifies where a fact
  can be inspected; it is not a copy of the source file.
- **Unknown:** an explicit absence of sufficient evidence. Unknown is a useful
  result, not an error and not a low-confidence synonym for verified.

Facts and relations are the portable core. Claims are optional projections of
that core. An implementation must not turn an inferred claim into a verified
fact merely because it is displayed in a summary.

## 5. Statuses

Every fact, relation, claim, and evidence reference uses one of these statuses:

| Status | Contract meaning |
| --- | --- |
| `verified` | The repository contains direct, current, sufficient evidence for the stated fact or relation. Every material link in a composite answer has inspectable evidence. |
| `inferred` | The result is a transparent derivation from verified facts or relations. It is useful for navigation or candidate impact, but is not direct proof and must remain labeled inferred. |
| `unknown` | The available repository evidence cannot establish or rule out the statement. The output includes an explicit reason. |
| `stale` | Evidence was valid for an earlier scan but the referenced source changed, disappeared, moved, or was invalidated. Stale evidence is retained for history but cannot support a current verified answer. |

A fresh scan may replace stale evidence with current evidence. It must not silently
promote stale or inferred material to verified.

## 6. Normalized artifact schema

M0 golden facts use this implementation-independent JSON shape:

```json
{
  "schema_version": 1,
  "fixture": "next-ts-demo",
  "facts": [
    {
      "attributes": {"file": "app/signup/page.tsx", "route": "/signup"},
      "evidence": [
        {
          "kind": "page_declaration",
          "line_end": 7,
          "line_start": 5,
          "path": "app/signup/page.tsx",
          "status": "verified"
        }
      ],
      "kind": "page",
      "semantic_key": "page:/signup",
      "status": "verified"
    }
  ],
  "relations": [
    {
      "evidence": [{"kind": "api_call", "path": "app/signup/page.tsx", "line_end": 16, "line_start": 16, "status": "verified"}],
      "relation": "calls",
      "source": "page:/signup",
      "status": "verified",
      "target": "api:/api/users"
    }
  ],
  "unknowns": [
    {
      "reason": "not_observed",
      "status": "unknown",
      "subject": "coverage:page:/admin"
    }
  ]
}
```

The example is illustrative; the golden file is authoritative for its fixture.
Keys are serialized in lexicographic order. Arrays are stable: facts sort by
`kind` then `semantic_key`, relations by `source`, `relation`, then `target`,
evidence by `path`, `line_start`, `line_end`, then `kind`, and unknowns by
`subject` then `reason`. All paths are normalized relative POSIX paths. Golden
artifacts contain no timestamp, absolute machine path, source excerpt, or
machine-specific identifier.

A fact's `attributes` carry normalized semantic values such as a route, symbol,
entity name, or file. Its `evidence` array must be non-empty for `verified` or
`inferred` output. A relation always names existing fact semantic keys. An
`unknowns` entry explains an expected subject for which no verified relation or
fact was observed.

## 7. Five golden questions

These five questions are the stable M0 acceptance queries. Their expected
subjects and evidence constraints are also recorded in
`tests/expected/next-ts-demo/questions.json`.

1. **GQ-001 — Understand:** “Which files and entities are directly connected to
   the `/signup` flow?” The individual edges may be `verified`; the complete
   multi-hop answer is `inferred` because it is assembled from those edges.
2. **GQ-002 — Understand:** “Which files and entity are directly connected to
   the `/checkout` flow?” The individual edges may be `verified`; the complete
   multi-hop answer is `inferred` because it is assembled from those edges.
3. **GQ-003 — Impact:** “What could be impacted if the `User/users` entity
   changes?” The answer is `inferred` when it is derived from verified
   downstream relations to the users service, `/api/users`, `/signup`, and the
   signup test. It must not claim exhaustive runtime impact.
4. **GQ-004 — Understand:** “Which tests directly cover the signup and
   checkout/order paths?” The answer is `verified` for direct test imports or
   path references to the signup and checkout subjects, with evidence in both
   test files.
5. **GQ-005 — Gap:** “Is the `/admin` page covered by a test?” The expected
   answer is `unknown` with `not_observed` behavior because the page exists but
   no test evidence is present. Presence of a page must never be treated as
   test coverage.

A question result must include its stable ID, status, subjects considered,
evidence references, and unknown reason when applicable. If a required
constraint is missing, the result is `unknown`, not a best-effort assertion.

## 8. Command and output contract

The MVP has three offline/local commands. Exact CLI syntax may vary by
packaging, but the observable contract does not.

### `scan <repository>`

- Reads supported source files without executing the application or using
  outbound network access.
- Writes deterministic scan state below `<repository>/.vibewiki/`, beginning
  with a manifest and normalized facts.
- Does not write outside the repository output directory and does not store
  full source contents or secrets.
- Exits `0` when the scan contract completes, including a scan with explicit
  unknowns. Invalid arguments, inaccessible input, or an unusable repository
  return a non-zero status and a short actionable diagnostic.
- Emits a machine-readable summary containing `schema_version`, `command`,
  `status`, counts for scanned files/facts/relations/unknowns, and relative
  output paths. Counts and arrays are deterministic for identical input.

### `build <repository>`

- Reads a completed local scan and writes deterministic derived artifacts below
  `.vibewiki/`, including facts/claims JSON and offline Markdown/Mermaid wiki
  output.
- Refuses to pretend that an absent or invalid scan is a successful build.
- Does not call an LLM or network service in the MVP. Repeated builds over the
  same scan produce byte-stable artifacts.
- Emits a machine-readable summary with `schema_version`, `command`, `status`,
  output paths, and counts. A claim without evidence is emitted as `unknown`.

### `serve <repository>`

- Reads only a valid local scan/build output and serves the repository's local
  knowledge view on loopback by default.
- Default bind is `127.0.0.1`; a caller may choose another explicit local bind
  address. The command must print the bind address and port, but must not open a
  browser or contact an external service.
- Exits non-zero when scan/build state is missing or invalid. It does not claim
  runtime behavior merely because a local viewer is reachable.
- Its summary includes `schema_version`, `command`, `status`, bind address,
  port, and relative artifact root; no absolute paths or source secrets are
  returned.

### M2 manifest boundary

The first implemented scan slice is intentionally narrower than the full MVP
contract. It accepts only a direct `<repository>/app` App Router directory with
at least one regular `.ts` or `.tsx` file. A nested/monorepo App Router layout,
Pages Router marker, or JS-only app is unsupported. Discovery does not follow
symlinks, reads no special files, and skips build/cache or sensitive paths by
name before statting or hashing them.

M2 persists only `.vibewiki/manifest.json`. Each record contains a relative
POSIX path, language label, byte size, and SHA-256 digest. The manifest is
canonical JSON sorted by path; its cache identity is the path/language/size/
digest plus analyzer version. M2 emits no facts, relations, or unknowns, so
those summary counts remain zero until the static-analysis phase adds a
separate evidence-producing artifact.

### Current local end-to-end slice

The repository now includes the first deterministic end-to-end slice after M2:
`build` reads the manifest and emits `facts.json`, `claims.json`, `sources.json`,
`graph.json`, `graph.db`, and Markdown/Mermaid wiki files below `.vibewiki/`.
The analyzer covers the supported fixture surface only: App Router pages and
route handlers, literal API calls, exported functions, Prisma models, direct
imports/calls/writes, and TypeScript test references. `serve` validates the
built graph and exposes it through a loopback-only API consumed by the viewer.
The viewer must display current artifact counts and evidence when served by
VibeWiki; it may retain a static presentation fallback when opened directly.
This slice does not claim runtime behavior, exhaustive impact, LLM reasoning,
or production readiness.

The viewer also provides a `Browse source` action. It uses the browser's local
directory picker and sends selected files only to the loopback VibeWiki server;
the server filters sensitive/unsupported paths, builds a temporary workspace,
and replaces the current local graph. It does not upload source to a cloud
service or persist the temporary imported workspace after server shutdown.

## 9. Privacy and reproducibility requirements

The fixture and all M0 examples are synthetic and offline. Implementations must
redact secret-like values before persistence, avoid absolute user paths, and
keep network access disabled for scan/build. A source reference is enough for
M0; raw source excerpts belong to a later, separately secured read path.

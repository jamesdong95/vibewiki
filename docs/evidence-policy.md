# VibeWiki evidence policy

This policy is the authority for deciding whether a fact, relation, or claim
may be shown as current evidence. It applies to static repository analysis and
is intentionally stricter than a textual similarity score.

## 1. Evidence reference format

Every evidence reference has:

```text
path       relative POSIX path below the scanned repository root
line_start positive 1-indexed line number, inclusive
line_end   positive 1-indexed line number, inclusive
kind       stable evidence category such as page_declaration, import, call, test_reference, or schema_model
status     verified, inferred, unknown, or stale
```

The path must use `/`, must not be absolute, and must not contain `..` after
normalization. `line_start` must be less than or equal to `line_end`. Both ends
refer to real lines in the source version used for the scan. A line range is
inclusive, so a one-line observation has equal start and end values.

Evidence IDs, if an implementation adds them, must be derived from these
normalized fields and the scan's source identity. They must not contain an
absolute machine path or timestamp.

## 2. Status decision rules

### `verified`

Use `verified` only when all of these conditions hold:

1. A supported analyzer found the stated construct directly in source, schema,
   or a test file.
2. The evidence path is inside the repository root and the line range is
   valid, current, and inspectable.
3. The parser/linker rule is specific enough to identify the subject or
   relation; filename proximity and name similarity alone do not qualify.
4. Every material edge in a composite answer has its own direct evidence.

Examples include an exported page declaration at a page file, a literal
`fetch('/api/users')` call linked to the matching API route, an import plus call
from a route handler to a service function, and a Prisma model declaration.
`verified` describes what the cited source proves; it does not prove that the
code has run successfully.

### `inferred`

Use `inferred` when the output is a deterministic derivation from one or more
verified facts but the cited lines do not state the complete conclusion as one
direct construct. The output must name the verified inputs and the rule used.
Transitive impact neighborhoods, likely callers reached by graph traversal, and
summaries across several verified edges are inferred unless a direct source
construct proves the whole statement.

Inferred is not a weaker spelling of verified. It must never be silently
promoted to verified, and a missing edge makes the derived result unknown.

### `unknown`

Use `unknown` when the available evidence cannot establish or rule out the
requested statement. Reasons include an intentional coverage gap, unsupported
syntax, a dynamic/non-literal target, an absent file, an invalid line range, or
an analyzer that has no rule for the construct. The result should retain a
stable subject and a short reason such as `not_observed`, `unsupported_pattern`,
or `insufficient_evidence`.

Unknown does not mean false, broken, or empty. In particular, a page with no
matching test is not proof that the page is untested in every environment; it
is only an unknown answer to the repository-evidence question.

### `stale`

Use `stale` when a previously recorded evidence reference no longer matches the
current source identity: the file changed, moved, was deleted, or the analyzer
invalidated the range. Stale evidence may be retained for audit/history, but it
cannot support a current verified or inferred answer. A successful rescan must
create current evidence before changing the status back to verified or inferred.

## 3. Redaction and secret handling

- Scan and classify values before writing facts, claims, logs, or excerpts.
- Never persist credentials, private-key material, session cookies, bearer
  values, database connection secrets, or the contents of secret files.
- Replace a necessary sensitive token/value with `[REDACTED]`; retain only the
  safe structural fact, such as the presence of a configured environment
  variable or the name of a client call.
- Treat environment files, credential stores, generated deployment files, and
  private-key PEM material as sensitive by default. Do not copy their contents
  into evidence JSON.
- Do not put secrets in semantic keys, error messages, test fixtures, or
  absolute paths. Synthetic values in this fixture must remain harmless demo
  data and must not resemble a real credential.
- Redaction is not evidence that a service was configured or reachable. A
  redacted configuration reference can support only the structural fact that a
  reference exists.

## 4. Allowed and prohibited conclusions

| Evidence available | Allowed conclusion | Prohibited conclusion |
| --- | --- | --- |
| Page module and route convention | The page route exists in source. | The page renders successfully for users. |
| Literal frontend API call plus matching route handler | The page contains a static call to that API route. | The request succeeds in production or reaches a live server. |
| Route import and direct service call | The handler calls the named service function in source. | The database write completed at runtime. |
| Prisma model and fields | The schema declares the model and fields. | A database exists, migration ran, or records are valid. |
| Test import or literal reference | The test directly references the named subject. | The test passed, or all scenarios are covered. |
| Verified graph edges across several modules | A candidate transitive path or impact neighborhood, status `inferred`. | An exhaustive impact list or proof of runtime behavior. |
| No test fact for a page | Test coverage is `unknown` with `not_observed`. | The page has no tests anywhere. |

Auth, payment, deployment, performance, security, and user-outcome claims
require their own direct evidence. Static names, comments, imports, or schema
presence cannot prove them.

## 5. Concrete examples

### Direct verified chain

If the signup page contains the literal API call, the users route imports and
calls `createUser`, the users service calls the database's user operation, and
the Prisma schema declares `User`, each of those individual edges may be
`verified` with its own path and inclusive line range. The assembled multi-hop
chain remains `inferred`, even when all material direct edges are present and
current, because the transitive statement is a derivation rather than one direct
source observation.

### Inferred is not verified

Suppose the graph has verified edges `/signup` → `/api/users`,
`/api/users` → `createUser`, and `createUser` → `User`. It is reasonable to
produce an inferred candidate statement that a change to `User` may affect the
signup flow. It is not valid to label that candidate as a verified statement
that every signup behavior will change, because the transitive conclusion is
not written at one source location and runtime conditions are unobserved.

### Unknown intentional gap

If `app/admin/page.tsx` is present but no test imports it or references its path,
record the page fact as verified and the coverage subject as unknown with
`not_observed`. Do not create a synthetic `tested_by` relation merely because
other pages have tests.

## 6. Determinism and updates

Normalize paths before comparison, sort facts/relations/evidence by semantic
keys, and keep line ranges stable for an unchanged source version. A scan that
cannot validate a reference must emit unknown or stale according to the rules
above. It must never hide the problem by widening the range or replacing a
missing source with an absolute path.

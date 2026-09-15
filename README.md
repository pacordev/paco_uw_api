# Underwriting Rules Engine — API

A thin FastAPI layer over the [`underwritting`](../underwritting) SQL project. No business
logic lives here — every decision comes from the Postgres functions in that repo
(`evaluate_quote_full`, `evaluate_quote_short_circuit`, etc). This API just exposes them over
HTTP so a frontend can drive a quote through: pick a product, answer its questions, evaluate.

Endpoint contract and phase-by-phase build plan live in `underwritting/uw_plan.md` (Part 2).

## Stack

- **Python + FastAPI**
- **asyncpg** for the DB driver, with its own connection pool — no ORM, this layer is too
  thin to need one.
- Deploy target: **Vercel** (frontend) + **Render** (this API, as a persistent process, not
  serverless) + **Neon** (Postgres). Render's process stays up, so we connect to Neon's
  *direct/unpooled* connection string rather than the PgBouncer-backed pooled one — a
  long-lived process doesn't need PgBouncer's connection-churn protection, and going direct
  sidesteps PgBouncer transaction-mode pooling fighting asyncpg's prepared statements.
- Locally, points at the `docker-compose.yml` Postgres from the `underwritting` repo — no
  separate DB needed for local dev until we're ready to test against Neon.
- **Neon gotcha:** `app/db.py`'s `search_path` setting needs a matching server-side
  `ALTER DATABASE ... SET search_path TO myins` on the Neon database itself (already applied)
  — see `uw_plan.md` Part 2 for why the client-side setting alone isn't enough there. Applies
  to any fresh Neon database this ever gets pointed at.

## Project structure

```
underwritting_api/
├── app/
│   ├── __init__.py
│   ├── main.py       # FastAPI app: lifespan, CORS, rate-limit wiring, /health, routers
│   ├── db.py         # asyncpg pool: connect()/disconnect()/get_pool()
│   ├── limiter.py    # shared slowapi Limiter + custom 429 handler
│   ├── abuse_log.py  # structured JSON logging for rate-limit 429s
│   ├── models.py     # Pydantic response shapes
│   ├── products.py   # GET /products, GET /products/{code}/questions
│   ├── quotes.py     # POST /quotes, POST /quotes/{quote_id}/answers, POST /quotes/{quote_id}/evaluate
│   ├── admin.py      # POST /products, POST /products/{code}/questions, POST /products/{code}/rules
│   └── body_limit.py # ASGI middleware capping request body size (Content-Length + streamed backstop)
├── tests/            # pytest contract tests, see "Running the test suite" below
│   ├── conftest.py   # test DB rebuild, TestClient fixture, create_quote/db_fetch helpers
│   ├── test_health.py
│   ├── test_products.py
│   ├── test_quotes.py
│   ├── test_evaluate.py
│   ├── test_cors.py
│   ├── test_rate_limiting.py
│   ├── test_docs.py
│   └── test_body_limit.py
├── postman/          # Postman collection + environment, see "Testing with Postman" below
├── .github/
│   └── workflows/
│       └── pip-audit.yml  # dependency vulnerability scan, see "Dependency scanning" below
├── requirements.txt
├── requirements-dev.txt  # + pytest, httpx, pip-audit (test-only, not deployed)
├── pytest.ini
├── render.yaml      # Render Blueprint: service definition, build/start commands, env vars
├── .env.example
```

## Running locally

Make sure the `underwritting` repo's Postgres is up first:

```bash
cd ../underwritting && docker compose up -d
```

Then, from this repo:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults already point at the local docker Postgres
set -a && source .env && set +a
uvicorn app.main:app --reload
```

Check `http://localhost:8000/health` — should return `{"status": "ok"}` once it can reach
the DB. See "Viewing the API contract" below for the interactive docs.

## Viewing the API contract

FastAPI generates the contract straight from the route/Pydantic definitions in `app/` — it's
always in sync with the actual code, nothing to hand-maintain. With the server running
locally (see above), three views of it:

- **`http://localhost:8000/docs`** — Swagger UI. Interactive: expand an endpoint, fill in a
  request body, hit "Try it out" and it calls your real local API (including writing real
  rows to your dev DB — e.g. "Try it out" on `POST /quotes` creates an actual quote).
- **`http://localhost:8000/redoc`** — ReDoc. Same schema, a cleaner read-only reference view
  for just browsing the contract without executing anything.
- **`http://localhost:8000/openapi.json`** — the raw OpenAPI 3.x schema both pages render
  from. Useful if you want to feed it into another tool, e.g. generating a typed client for
  the future React frontend.

All three are controlled by `EXPOSE_API_DOCS` (see `app/main.py`) — on by default locally, so
no setup needed, and off in prod (`render.yaml` sets it to `"false"`) since a public
deployment doesn't need to expose its full schema. Testing what "off" looks like locally:

```bash
EXPOSE_API_DOCS=false uvicorn app.main:app --reload
```

All three routes 404 with that set; every real endpoint (`/health`, `/products`, `/quotes`,
...) is unaffected either way.

## Testing with Postman

There's a ready-made collection in `postman/` — no need to build requests by hand.

1. **Import both files** into Postman: `File → Import`, drag in
   - `postman/underwriting_api.postman_collection.json` (the requests)
   - `postman/underwriting_api_local.postman_environment.json` (the variables)
2. **Select the environment** — top-right dropdown in Postman, pick "Underwriting API -
   Local". It sets `base_url` (`http://localhost:8000`) and `product_code` (`LIFE_SIMPLE`),
   plus empty `quote_id`/`access_token`/`admin_product_code`/`admin_quote_id`/
   `admin_access_token` values that get filled in automatically as you run requests (see
   below).
3. **Fill in `admin_key` yourself** — it's deliberately left blank in the checked-in
   environment file (marked as a Postman "secret" variable) rather than baking in a real
   credential. Paste in the same value as this repo's local `.env`'s `ADMIN_API_KEY`. Only
   the requests in the two "Admin" folders use it; everything else works without it.
4. **Make sure the app is actually running** first — docker Postgres up (`docker compose up
   -d` in `../underwritting`) and `uvicorn app.main:app --reload` in this repo. Postman won't
   tell you *why* a request failed if there's nothing listening on `base_url`.
5. **Run it.** Either click through requests one at a time, or right-click the collection →
   "Run collection" to fire them all in order with the built-in test assertions.

The collection is split into five folders:

- **Health** — just `GET /health`.
- **Happy path** — list products, get a product's questions, create a quote, submit a full
  valid answer batch, submit one more answer to the same question to prove the upsert (it
  corrects the value instead of erroring), then evaluate under both Model A (`full`) and
  Model B (`short_circuit`). **`Create Quote` has a test script that reads `quote_id` and
  `access_token` out of the response and saves both into the environment** — that's the only
  reason the later requests (`{{quote_id}}` in their URLs, `{{access_token}}` in their
  `X-Quote-Token` header) work without copy-pasting anything around. Run this folder top to
  bottom the first time.
- **Error paths** — one request per error case: unknown product code (404), unknown
  `quote_id` (404), unknown `question_code` (404), a wrong `X-Quote-Token` on both answers
  and evaluate (404, same message as unknown `quote_id`), an `answer_text` that fails the
  question's `answer_type` validation (422), an empty `answers` array (400), a batch with one
  good answer + one bad one to confirm the whole batch rolls back atomically instead of
  partially saving, an unknown `quote_id` on evaluate (404), and an invalid `strategy` value
  on evaluate (422). Depends on `{{quote_id}}`/`{{access_token}}` too, so run "Happy path"
  first in the same environment.
- **Admin - Happy path** — create a product (a fresh `PM_TEST_<timestamp>` code every run,
  via a pre-request script, so this folder is always safely rerunnable), add a question to
  it, add a rule referencing that question, confirm the question shows up via the normal
  public `GET /products/{code}/questions`, then create a real quote against the brand-new
  product, answer it, and evaluate — proving the rule just built through this API actually
  decides something (`decline`), not just that the create calls returned 201. Needs
  `{{admin_key}}` filled in.
- **Admin - Error paths** — missing `X-Admin-Key` (422, FastAPI request validation before the
  handler even runs) vs. a wrong one (401, reaches the handler's own check), duplicate
  product code (409, deliberately reuses `{{admin_product_code}}` from Admin - Happy path so
  run that first), adding a question/rule to an unknown product code (404 each), an `enum`
  question with no `enum_options` (422, Pydantic validator), re-linking the same
  question+sequence to a product (409), a rule with zero conditions (422, Pydantic validator),
  and a rule condition naming a `question_code` that doesn't exist (404).

Each request has a `pm.test(...)` assertion on status code (and, where it matters, on the
response body) in its **Tests** tab in Postman — green checkmarks in the response panel mean
it passed. This was also verified headlessly with `npx newman run
postman/underwriting_api.postman_collection.json -e
postman/underwriting_api_local.postman_environment.json` (with `admin_key` filled in first),
which runs the same requests/tests from the terminal without opening the Postman app — 34
requests, 48 assertions, all passing, run clean twice in a row.

Note: `POST /quotes` is rate-limited to 10/minute per IP (see "Security" below) — the
collection calls it twice per run (once in Happy path, once in Admin - Happy path), so this
won't come up in normal use, but re-running the whole collection back-to-back many times
within the same minute could eventually 429.

Switching products: change the environment's `product_code` to something else (e.g.
`AUTO_BASIC`), but the request bodies in "Submit Answers" are hardcoded to `LIFE_SIMPLE`'s
question codes (`Q_SMOKER`, `Q_CANCER`, `Q_BMI`, `Q_HOSP`, `Q_SPORTS`) — check
`GET /products/{code}/questions` first and edit the answer bodies to match the new product's
questions and answer types.

Cleaning up: every run leaves a `quote`/`quote_answer` row behind, and every run of "Admin -
Happy path" also leaves a `PM_TEST_<timestamp>` product (plus its linked `Q_PM_ADMIN_FLAG`
question and rule) — that's the point, it's exercising real inserts, and there's no delete
endpoint on this API by design. This is throwaway local dev data, so either leave it or
delete it straight from the DB:

```bash
docker exec paco_uw psql -U uw_admin -d underwriting -c "
SET search_path TO myins;
DELETE FROM quote_answer WHERE quote_id = <id>; DELETE FROM quote WHERE id = <id>;
DELETE FROM uw_rule_condition WHERE rule_id IN (SELECT id FROM uw_rule WHERE product_id IN (SELECT id FROM insurance_product WHERE code LIKE 'PM_TEST_%'));
DELETE FROM uw_rule WHERE product_id IN (SELECT id FROM insurance_product WHERE code LIKE 'PM_TEST_%');
DELETE FROM product_question WHERE product_id IN (SELECT id FROM insurance_product WHERE code LIKE 'PM_TEST_%');
DELETE FROM insurance_product WHERE code LIKE 'PM_TEST_%';
"
```

## Endpoints

- `GET /health` — no auth, DB round-trip, used by Render's health check.
- `GET /products` → `[{code, name, description}]`
- `GET /products/{code}/questions` → ordered
  `[{question_code, text, answer_type, enum_options, sequence, is_mandatory}]`.
  404 on an unknown product code. Never includes `expected_answer`.
- `POST /quotes` `{product_code}` → `{quote_id, access_token}`. 404 on an unknown product
  code. **Save `access_token`** — it's generated once and never shown again, and every later
  request scoped to this `quote_id` requires it.
- `POST /quotes/{quote_id}/answers` — requires header `X-Quote-Token: <access_token>` from
  the matching `POST /quotes` response. `{answers: [{question_code, answer_text}, ...]}` →
  `{quote_id, answers: [...]}`. 404 on an unknown `question_code`; also 404 (same message) on
  an unknown `quote_id` **or** a wrong/missing token — the two are indistinguishable on
  purpose, so a caller can't use the response to probe whether a `quote_id` exists. 400 on an
  empty `answers` list, 422 (with the trigger's message) if `answer_text` fails the Phase 5
  validation trigger for its question's `answer_type`. Answers are upserted, so resubmitting
  a question corrects it. The whole batch is one transaction — one bad answer rolls back the
  rest, no partial writes.
- `POST /quotes/{quote_id}/evaluate` — same `X-Quote-Token` requirement and same-message
  404s as the answers endpoint. `{strategy: "full" | "short_circuit"}` → `{quote_id, strategy,
  outcome, evaluated_at}`. Thin wrapper over `evaluate_and_record_quote` — the caller always
  picks the strategy explicitly (no auto-evaluate on the last answer), and both Model A and
  Model B stay reachable. Every call is recorded as a new `quote_evaluation` row (full audit
  history, not overwritten) even if it's the same strategy re-run on the same quote. 422 if
  `strategy` isn't `full`/`short_circuit` (Pydantic rejects it before the DB is touched).

**Admin — build the catalog.** All three require `X-Admin-Key: <ADMIN_API_KEY>` (see
"Security" below); missing the header is a 422 (FastAPI request validation), a wrong value is
a 401.

- `POST /products` `{code, name, description?}` → the created product. 409 on a duplicate
  `code`.
- `POST /products/{code}/questions` `{question_code, text, answer_type, enum_options?,
  sequence, is_mandatory?, expected_answer?}` → the linked question. 404 on an unknown
  product `code`. `uw_question` is a shared pool (Phase 1) — an existing `question_code` gets
  reused/linked rather than erroring, so the same question can attach to more than one
  product; the given `text`/`answer_type`/`enum_options` only take effect the first time that
  code is created. 409 if that question or `sequence` is already linked to this product. 422
  if `answer_type` is `enum` with no `enum_options`.
- `POST /products/{code}/rules` `{name, priority?, outcome, stop_evaluation?, conditions:
  [{question_code, operator, value}, ...]}` → the created rule. 404 on an unknown product
  `code` or an unknown `question_code` inside `conditions`. 422 if `conditions` is empty (a
  rule with no conditions can never fire — see `uw_rule_condition` in the `underwritting`
  README).

## Security

This is a **public, applicant-facing API — no login** (confirmed decision, see `uw_plan.md`
Phase E). Auth stays limited to the per-quote token below; the rest of the hardening is CORS
+ rate limiting rather than a user-auth layer. The admin endpoints (below) are the one
exception — they're not applicant-facing, and use their own single-key model instead.

- **Quote ownership token.** `quote_id` is a plain sequential integer, so on its own it's
  guessable — anyone could increment it and read/overwrite someone else's answers. Phase 7
  (`sql/phase7_quote_access_token.sql` in the `underwritting` repo) adds a random
  `quote.access_token` (UUID), handed back once in the `POST /quotes` response and required
  as `X-Quote-Token` on every later request scoped to that `quote_id`. A wrong token and a
  nonexistent `quote_id` return the exact same 404, so the error response itself can't be
  used to enumerate valid quote ids.
- **CORS lockdown.** `ALLOWED_ORIGINS` has no wildcard fallback — `app/main.py` reads it with
  `os.environ[...]` (not `.get(...)`), so the app refuses to start at all if it isn't set,
  rather than silently defaulting to `*`. `allow_headers` is also scoped down to just
  `Content-Type`, `X-Quote-Token`, and `X-Admin-Key` instead of `*`. In Render, `ALLOWED_ORIGINS` is
  `sync: false` in `render.yaml` — it has to be set by hand in the dashboard to the real
  Vercel domain(s) (comma-separated for prod + preview), there's no default checked in.
- **Rate limiting.** `app/limiter.py` sets up a `slowapi` `Limiter`, in-memory (Render's free
  tier is a single instance — no shared backend needed; a paid tier with multiple instances
  would need Redis instead, since in-memory counts don't cross processes). Keyed off
  `X-Forwarded-For` rather than the raw connecting IP — Render terminates the connection at
  its edge proxy, so `request.client.host` would be the proxy for every caller, not the real
  client. Per-IP limits: `POST /quotes` **10/minute** (tightest — it's the resource-creation
  endpoint), `POST /quotes/{quote_id}/answers` **30/minute**, `POST /quotes/{quote_id}/evaluate`
  **20/minute**, everything else (the `GET` endpoints) a **60/minute** app-wide default. A
  429 uses the same `{"detail": ...}` shape as every other error response here, plus
  `Retry-After`/`X-RateLimit-*` headers. `RATE_LIMIT_ENABLED=false` turns it off entirely
  (used by the test suite — see below). Every 429 also logs a structured abuse record
  (`app/abuse_log.py`) — a bare JSON line with `event`, `ip`, `method`, `path`, the specific
  limit string that was hit, and a UTC timestamp, via its own logger/handler so nothing else
  prefixes the line and breaks parsing it as JSON. Queryable with grep/jq locally or Render's
  log search in prod — no new dependency or database table.
- **API docs hidden in prod.** `/docs`, `/redoc`, and `/openapi.json` hand anyone the full
  API shape — fine locally, not something a public deployment needs to expose. `app/main.py`
  reads `EXPOSE_API_DOCS` (default enabled, so local dev/tests need no extra config) and sets
  `docs_url`/`redoc_url`/`openapi_url` to `None` when it's `"false"`; `render.yaml` sets it to
  `"false"` for the deployed service. Verified live both ways: default gets `200` on all
  three, `EXPOSE_API_DOCS=false` gets `404` on all three while every real endpoint still
  works normally.

- **Request body size limit.** `app/body_limit.py` wraps the whole app in a pure ASGI
  middleware, outside even CORS/rate-limiting, capping request bodies at
  `MAX_REQUEST_BODY_BYTES` (default 100KB). Two layers: a `Content-Length` fast path that
  rejects an honestly-oversized request before reading any of it, plus a byte-counting
  backstop that catches a missing/lying header (e.g. chunked transfer-encoding) by aborting
  the moment the running total crosses the limit — bounded memory, never buffers the whole
  body first like `Starlette.BaseHTTPMiddleware` would. A 413 from this layer doesn't carry
  CORS headers (it runs before CORS does), unlike the 429 handler above — a deliberate
  trade-off, since this guards against abuse rather than a case a working frontend should
  ever hit.
- **Admin key.** `POST /products`, `POST /products/{code}/questions`, and
  `POST /products/{code}/rules` (Phase G) mutate the shared catalog every applicant reads
  from, so they're gated by a static `X-Admin-Key` header checked against the `ADMIN_API_KEY`
  env var — there's exactly one admin, not one caller per resource, so the per-quote-token
  ownership model doesn't fit here. Missing the header is a 422 (FastAPI's own request
  validation, before `app/admin.py` ever runs); present but wrong is a 401. Set in
  `render.yaml` (`sync: false`) to its own value on Render, always different from the local
  `.env` one.

Still open (Phase E): making error response shapes fully consistent — FastAPI's own
validation errors put a *list* under `detail`, while every hand-written error here puts a
plain *string* there. Both already use the `detail` key, so it's a minor nit, not a gap.

**DB role note:** `DATABASE_URL` connects as `uw_app`, a Neon role scoped to only what this
app needs (no `DELETE`, no schema `CREATE`/`DROP`) — but every Neon-provisioned role
(`uw_app` included) is automatically a member of `neon_superuser`, which grants full DML
across the schema regardless of narrower grants, and neither we nor `neondb_owner` can
revoke that membership (needs Neon-internal access we don't have). So `uw_app` blocks
schema-level damage but isn't true DML-level isolation — see `uw_plan.md`'s hardening
backlog for the full investigation. The app's own code never issues a `DELETE` or DDL
statement regardless, so this matters mainly for a leaked credential used directly, not for
anything the running app itself can be tricked into doing.

## Running the test suite

`tests/` has API-level contract tests (pytest) hitting real HTTP endpoints against a real,
dedicated Postgres database — no mocking. `tests/conftest.py` rebuilds a fresh
`underwriting_test` database every test session by dropping it and replaying the exact
`sql/phase1..7*.sql` files from the `underwritting` repo (schema + seed data), so it starts
from the same known state every run and never touches the `underwriting` dev database. It
assumes the sibling repo layout from "Project structure" above (`../underwritting`).

```bash
source venv/bin/activate
pip install -r requirements-dev.txt   # adds pytest, httpx, pip-audit on top of the app's own deps
docker compose up -d                  # in ../underwritting - needs to be reachable on :5432
pytest
```

`tests/conftest.py` also sets `DATABASE_URL`/`ALLOWED_ORIGINS` for the test session (and
`RATE_LIMIT_ENABLED=false` — see below) and hands tests a `client` fixture
(`fastapi.testclient.TestClient`, session-scoped, drives the app's real lifespan) plus two
small helpers: `create_quote()` (factory - makes a fresh quote and returns `(quote_id,
access_token)`) and `db_fetch(sql, *args)` (a sync wrapper around a raw asyncpg query, for the
handful of tests that need to confirm persisted state directly, like proving a rejected batch
left nothing behind).

Rate limiting is **off** for most of the suite - these tests fire many requests back-to-back
through one `TestClient`, which all share a single IP bucket ("testclient", since there's no
real proxy setting `X-Forwarded-For`), and that has nothing to do with what those tests are
checking. `tests/test_rate_limiting.py` re-enables it (`fastapi_app.state.limiter.enabled =
True` — `fastapi_app` is `app.main`'s underlying FastAPI instance, exported separately
because `app` itself is now wrapped by `MaxBodySizeMiddleware` and no longer exposes
`.state` directly, see `app/body_limit.py`) just for its own two assertions, using a
distinct `X-Forwarded-For` per test so it doesn't collide with anything else, and turns it
back off in a `finally` afterward.

Coverage: health, products/questions (including that `expected_answer` never leaks), quote
creation, answer submission (happy path, upsert, all 404/422/400 error paths, atomic
rollback), evaluation (both strategies, pre-answer default, history not overwritten, all
error paths), CORS preflight (allowed origin vs. rejected origin), rate limiting (10th
request from one IP succeeds, 11th gets 429; a different IP is unaffected), the API docs
being reachable by default, and the request body size limit (normal request unaffected,
oversized with `Content-Length` 413, oversized streamed body with no `Content-Length` 413,
within-limit request still reaches the real handler) — 29 tests, all passing, verified to
run clean twice in a row without touching the dev database's own data.

Note: `pytest` (the bare console command) can fail with `ModuleNotFoundError: No module
named 'app'` depending on how it resolves the working directory into `sys.path` — if that
happens, run `python -m pytest` instead (adds the current directory to `sys.path`
explicitly).

**Gap:** `app/admin.py` (Phase G) has no pytest coverage yet — it's exercised by the Postman
collection's "Admin - Happy path"/"Admin - Error paths" folders (see "Testing with Postman"
above) and was verified manually against Neon, but not by anything in `tests/`. Worth adding
before this goes anywhere near production.

## Dependency scanning

`pip-audit` checks `requirements.txt` (what's actually deployed) and `requirements-dev.txt`
(test-only, never shipped) separately against known-CVE databases:

```bash
pip install -r requirements-dev.txt   # includes pip-audit itself
pip-audit -r requirements.txt
pip-audit -r requirements-dev.txt
```

Runs automatically via `.github/workflows/pip-audit.yml` on every push/PR to `main`, plus a
weekly schedule (Monday 06:00 UTC) — the schedule matters because a pin that's clean today
can have a CVE disclosed against it later with no code change of ours to trigger a re-check.
The two dependency files are audited as separate steps so a failure is clearly scoped to
"something actually deployed" vs. "test-only tooling," not one undifferentiated red check.

Found and fixed one real hit setting this up: `pytest 8.4.2` had a known vulnerability
(`PYSEC-2026-1845`, fixed in `9.0.3`) — `requirements-dev.txt`'s pin widened to
`pytest>=9.0.3,<10`, full 29-test suite re-verified passing under the new version before
committing the bump. `requirements.txt` (runtime) had no findings.

## Where things stand

**Phase A (stack & scaffold) — done.** App boots, connects to Postgres via a pooled asyncpg
connection, `/health` round-trips the DB.

**Phase B (read endpoints) — done.** `GET /products` and `GET /products/{code}/questions`,
verified live against the local docker Postgres.

**Phase C (quote creation & answer submission) — done.** `POST /quotes` and
`POST /quotes/{quote_id}/answers`, implemented in `app/quotes.py`. Verified live against the
local docker Postgres: happy path, all 404/422/400 error paths, answer upsert, and atomic
rollback on a mixed valid/invalid batch.

**Phase D (evaluation endpoint) — done.** `POST /quotes/{quote_id}/evaluate`, implemented in
`app/quotes.py`. Verified live against the local docker Postgres: pre-answers evaluate
(defaults to `accept`), a scenario that fires a real `decline` rule for both `full` and
`short_circuit`, unknown-quote_id/wrong-token/invalid-strategy error paths, and that
`quote_evaluation` keeps every run as history (confirmed 3 rows for one quote across the
above) while `quote_latest_evaluation` surfaces only the newest per strategy.

**Phase E (mostly done)** — quote-ownership token, CORS lockdown, the public-vs-internal auth
decision (public, confirmed), and rate limiting are all done. See "Security" above. Verified
live against the local docker Postgres: 10 rapid `POST /quotes` calls from one IP succeed and
the 11th gets 429 with the right headers, a different `X-Forwarded-For` isn't affected, and
CORS headers still land on a 429. Only remaining item: making error-response shapes fully
consistent (minor, not a gap — see "Security" above).

**Phase F (API-level tests) — done.** 29 pytest contract tests in `tests/`, hitting real
endpoints against a dedicated, freshly-rebuilt `underwriting_test` database. See "Running the
test suite" above.

**Phase G (admin endpoints) — done, pytest coverage still a gap.** `POST /products`,
`POST /products/{code}/questions`, `POST /products/{code}/rules`, implemented in
`app/admin.py`, gated by `X-Admin-Key` (see "Security" above). Verified live against Neon: a
product/question/rule built purely through these endpoints, then a real quote against that
product correctly decided `decline`; also covered by the Postman collection's two "Admin"
folders (see "Testing with Postman" above). Not yet covered by `tests/` — see the gap noted
under "Running the test suite" above.

Every phase from `uw_plan.md` Part 2 is done, aside from the one cosmetic error-shape
consistency nit noted under Phase E and Phase G's missing pytest coverage. Beyond the
original plan: `/docs`/`/redoc`/`/openapi.json` are now also disabled in prod
(`EXPOSE_API_DOCS`), a request body size limit (`app/body_limit.py`, 4 of the 29 pytest
tests above) and structured abuse logging (`app/abuse_log.py`) close two more items from
`uw_plan.md`'s hardening backlog, and `pip-audit` runs in CI on every push/PR plus weekly
(caught and fixed a real `pytest` CVE) — see "Security" and "Dependency scanning" above for
all of it. Only two backlog items remain open: the least-privilege DB role (attempted,
blocked by a Neon platform limitation — see "DB role note" under "Security"), and
`app/admin.py`'s missing pytest coverage.

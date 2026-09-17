# Underwriting Rules Engine — API

A thin FastAPI layer over the [`underwritting`](../underwritting) SQL project. No business
logic lives here — every decision comes from the Postgres functions in that repo
(`evaluate_quote_full`, `evaluate_quote_short_circuit`, etc). This API just exposes them over
HTTP so a frontend can drive a quote through: pick a product, answer its questions, evaluate.

## Deployment

Live at **https://underwriting-api-4ky9.onrender.com** (Render, set up from the checked-in
`render.yaml` Blueprint). `EXPOSE_API_DOCS=false` there, so `/docs`, `/redoc`, and
`/openapi.json` aren't reachable — `GET /health` and `GET /products` are the quickest way
to confirm it's up. `ALLOWED_ORIGINS` on that deployment is scoped to local dev origins for
now, since the frontend isn't deployed yet; it'll need the real Vercel domain added once it
is (see "CORS lockdown" under Security).

## Stack

- **Python + FastAPI**
- **asyncpg** for the DB driver, with its own connection pool — no ORM, this layer is too
  thin to need one.
- Deploy target: **Vercel** (frontend, not deployed yet) + **Render** (this API, deployed —
  see "Deployment" above — as a persistent process, not serverless) + **Neon** (Postgres,
  already in real use). Render's process stays up, so we connect to Neon's
  *direct/unpooled* connection string rather than the PgBouncer-backed pooled one — a
  long-lived process doesn't need PgBouncer's connection-churn protection, and going direct
  sidesteps PgBouncer transaction-mode pooling fighting asyncpg's prepared statements.
- Locally, points at the `docker-compose.yml` Postgres from the `underwritting` repo — no
  separate DB needed for local dev until we're ready to test against Neon.
- **Neon gotcha:** `app/db.py`'s `search_path` setting needs a matching server-side
  `ALTER DATABASE ... SET search_path TO myins` on the Neon database itself (already
  applied) — a client-set `search_path` alone isn't enough there, since asyncpg's default
  `RESET ALL` on every connection release back to the pool wipes it, and Neon's connection
  proxy silently drops the `server_settings` startup param instead of forwarding it. Applies
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
│   ├── admin.py      # POST /products, .../questions, .../rules + GET .../rules (admin-only)
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
│   ├── test_body_limit.py
│   ├── test_error_shape.py
│   └── test_admin.py
├── postman/          # Postman collection + environment, see "Testing with Postman" below
├── notebooks/        # Jupyter client demo, see "Notebook console" below
├── .github/
│   └── workflows/
│       └── pip-audit.yml  # dependency vulnerability scan, see "Dependency scanning" below
├── requirements.txt
├── requirements-dev.txt  # + pytest, httpx, pip-audit (test-only, not deployed)
├── pytest.ini
├── render.yaml      # Render Blueprint: service definition, build/start commands, env vars
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
  from. Useful if you want to feed it into another tool, e.g. generating a typed client.


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
it passed. It can also run headlessly with `npx newman run
postman/underwriting_api.postman_collection.json -e
postman/underwriting_api_local.postman_environment.json` (with `admin_key` filled in first),
which runs the same requests/tests from the terminal without opening the Postman app — 34
requests, 48 assertions total.

## Notebook console

`notebooks/quote_console_demo.ipynb` is a Python client doing the same things
`underwritting_web` does from the browser — browse products, answer questions, evaluate a
quote under both models, browse/build the catalog — from a Jupyter notebook instead of a
page. Handy for scripting a batch of quotes or poking at the API interactively without a
browser.

```bash
pip install -r notebooks/requirements.txt
jupyter lab notebooks/quote_console_demo.ipynb
```

Set `UW_API_URL` if the API isn't on the default `http://localhost:8000`. Admin cells
prompt for `ADMIN_API_KEY` via `getpass` rather than a hardcoded value, so it never ends up
saved in the notebook's cell output. The **Build new** cells that actually create a
product/question/rule are commented out by default — real, permanent writes to the live
catalog, same as the web app's Build new, with no delete endpoint to undo them.

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
  outcome, evaluated_at, trigger}`. Thin wrapper over `evaluate_and_record_quote` — the
  caller always picks the strategy explicitly (no auto-evaluate on the last answer), and
  both Model A and Model B stay reachable. Every call is recorded as a new `quote_evaluation`
  row (full audit history, not overwritten) even if it's the same strategy re-run on the
  same quote. 422 if `strategy` isn't `full`/`short_circuit` (Pydantic rejects it before the
  DB is touched). `trigger` is `{rule_name, question_codes, stopped_early}` — which rule
  decided the outcome and which answer(s) drove it — or `null` when nothing matched (the
  `accept` default). Computed fresh on every call via
  `underwritting/sql/phase8_evaluation_trigger.sql`'s `uw_evaluation_trigger`, not persisted
  anywhere.

**Admin — build the catalog.** All four require `X-Admin-Key: <ADMIN_API_KEY>` (see
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
- `GET /products/{code}/rules` → `[{id, product_code, name, priority, outcome,
  stop_evaluation, conditions: [{question_code, operator, value}, ...]}]`. 404 on an unknown
  product `code`. Admin-only rather than on the public router above, for the same reason
  `product_question.expected_answer` is never returned publicly — a rule's conditions are the
  exact thresholds (e.g. `Q_BMI > 32 → decline`) that decide an outcome, so handing them to an
  applicant would hand them the answer key.

## Security

This is a **public, applicant-facing API — no login** (confirmed decision). Auth stays
limited to the per-quote token below; the rest of the hardening is CORS + rate limiting
rather than a user-auth layer. The admin endpoints (below) are the one
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
  **20/minute**, every admin route (all four in `app/admin.py`) also **20/minute** — tighter
  than the public `GET` default since these guard a single static secret with no lockout,
  everything else (the public `GET` endpoints) a **60/minute** app-wide default. A
  429 uses the same `{"detail": ...}` shape as every other error response here, plus
  `Retry-After`/`X-RateLimit-*` headers. `RATE_LIMIT_ENABLED=false` turns it off entirely
  (used by the test suite — see below). Every 429 also logs a structured abuse record
  (`app/abuse_log.py`) — a bare JSON line with `event`, `ip`, `method`, `path`, the specific
  limit string that was hit, and a UTC timestamp, via its own logger/handler so nothing else
  prefixes the line and breaks parsing it as JSON. Queryable with grep/jq locally or Render's
  log search in prod — no new dependency or database table.

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
- **Admin key.** `POST /products`, `POST /products/{code}/questions`, `POST /products/{code}/rules`,
  and `GET /products/{code}/rules` mutate or expose the shared catalog, so they're
  gated by a static `X-Admin-Key` header checked against the `ADMIN_API_KEY` env var — there's
  exactly one admin, not one caller per resource, so the per-quote-token ownership model
  doesn't fit here. Missing the header is a 422 (FastAPI's own request validation, before
  `app/admin.py` ever runs); present but wrong is a 401. Set in `render.yaml` (`sync: false`)
  to its own value on Render, always different from the local `.env` one. The comparison
  itself uses `secrets.compare_digest`, not `!=` — a plain string comparison short-circuits
  on the first mismatched byte, which leaks (in principle, over enough timed requests) how
  many leading characters of a guess were right.

**Consistent error shape.** Every error response, no matter which layer raises it, comes
back as `{"detail": <string>}`. `app/main.py` registers a `RequestValidationError` handler
that flattens FastAPI's own request-validation errors (a *list* of `{loc, msg, type}` dicts
by default) into a single `"loc: msg; loc: msg"` string, matching the plain string every
hand-written `HTTPException` already uses. Every router is also included with
`responses={422: {"model": ErrorOut}}` (`app/models.py`), so `/docs`/`/openapi.json`
document that same string shape instead of FastAPI's default list-shaped
`HTTPValidationError`. Covered by `tests/test_error_shape.py` and by assertions in the
Postman collection's 422 examples.

**DB role note:** `DATABASE_URL` connects as `uw_app`, a Neon role scoped to only what this
app needs (no `DELETE`, no schema `CREATE`/`DROP`).

## Running the test suite

`tests/` has API-level contract tests (pytest) hitting real HTTP endpoints against a real,
dedicated Postgres database — no mocking. `tests/conftest.py` rebuilds a fresh
`underwriting_test` database every test session by dropping it and replaying the exact
`sql/phase1..8*.sql` files from the `underwritting` repo (schema + seed data), so it starts
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
rollback), evaluation (both strategies, pre-answer default, history not overwritten, the
`trigger` object for both a compound-rule case and a stop-rule case, all error paths), CORS
preflight (allowed origin vs. rejected origin), rate limiting (10th request from one IP
succeeds, 11th gets 429; a different IP is unaffected), the API docs being reachable by
default, the request body size limit (normal request unaffected, oversized with
`Content-Length` 413, oversized streamed body with no `Content-Length` 413, within-limit
request still reaches the real handler), that FastAPI's own validation errors and
hand-written errors both come back with a string `detail`, and `GET /products/{code}/rules`
(happy path against `LIFE_SIMPLE`'s real 7 seeded rules, unknown product 404, wrong/missing
admin key 401/422) — 37 tests total, none of it touching the dev database's own data.

Note: `pytest` (the bare console command) can fail with `ModuleNotFoundError: No module
named 'app'` depending on how it resolves the working directory into `sys.path` — if that
happens, run `python -m pytest` instead (adds the current directory to `sys.path`
explicitly).

The three `POST` endpoints in `app/admin.py` (create product/question/rule) are exercised by
the Postman collection's "Admin - Happy path"/"Admin - Error paths" folders (see "Testing
with Postman" above) rather than by anything in `tests/`; `GET /products/{code}/rules` is
the one admin route with pytest coverage, in `tests/test_admin.py`.

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

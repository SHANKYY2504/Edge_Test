# EdgeTest

EdgeTest is a local-first API testing and debugging platform. It provides a request builder, asynchronous FastAPI execution, session-only browser history, a local demo API, boundary-condition testing, OpenAPI/Swagger-assisted test generation, persistent SQLite collections and execution history, request replay, failure analysis, and an analytics dashboard.

## Architecture

```text
frontend/                 React + TypeScript + Vite + Tailwind CSS
  src/services/           Centralized backend API client
  src/features/history/   Session-only, redacted request history
  src/features/platform/  Collection, failure, replay, AI, and analytics UI
backend/
  app/api/                 Health and versioned API routes
  app/core/                Environment-backed settings and limits
  app/models/              Pydantic request, response, and error models
  app/services/            Async HTTPX execution, SQLite persistence, and AI analysis
  data/                    Local SQLite database (created at runtime; ignored by Git)
  demo/                    Separate in-memory sample API
  tests/                   Pytest unit and controlled integration tests
```

The browser sends `ApiRequest` models to `POST /api/v1/requests/execute` on the EdgeTest backend. The backend validates outbound targets, executes one asynchronous HTTP request, then returns an `ApiResponse` with upstream status, headers, body, content type, JSON detection, timing, or a structured execution error. Every execution is recorded in SQLite; HTTP 4xx/5xx are failures, while transport errors are errored executions. Boundary and OpenAPI test runs continue to use this endpoint and are tracked automatically.

The demo API is a separate FastAPI application. Its in-memory CRUD state resets when that process restarts.

## Requirements

- Node.js 24.0+ (Vite 8 and current test dependencies)
- Python 3.9+

## Installation

### Frontend

```sh
cd frontend
npm install
cp -n .env.example .env
```

`VITE_API_BASE_URL` sets the EdgeTest backend origin. Its default is `http://127.0.0.1:8000`.

The backend uses environment variables described in `backend/.env.example`. `DATABASE_PATH` defaults to `data/edgetest.sqlite3` relative to the backend working directory. Set `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL` to enable Gemini debugging. `gemini-2.5-flash` is the default model. To keep using the OpenAI-compatible provider, set `AI_PROVIDER=openai` and configure `AI_API_KEY` (and optionally `AI_API_URL`/`AI_MODEL`). Provider keys are read only by the backend and are never returned to the frontend. After changing backend environment settings, restart the backend process.

### Backend

In another terminal, from the repository root:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
```

The default `LOCAL_DEMO_TARGETS` authorizes only `http://localhost:8001` and `http://127.0.0.1:8001`. Other outbound hosts must be added explicitly to `OUTBOUND_ALLOWED_HOSTS`. Localhost targets are otherwise denied; `ALLOW_LOCALHOST_TARGETS=true` enables loopback-only access for development.

## Run the applications

Run each command in its own terminal.

### 1. Start the demo API (port 8001)

```sh
cd backend
.venv/bin/uvicorn demo.main:app --reload --port 8001
```

Sample endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/users`, `/users/{id}` | List or retrieve in-memory users |
| POST | `/users` | Create a user |
| PUT | `/users/{id}` | Replace a user |
| PATCH | `/users/{id}` | Update a user's name or email |
| DELETE | `/users/{id}` | Delete a user |
| GET | `/delay?seconds=2` | Simulate a delayed response (0–10 seconds) |
| GET | `/errors/404` | Return an HTTP 404 response |
| GET | `/errors/500` | Return an HTTP 500 response |
| GET | `/health` | Demo service health |

### 2. Start the EdgeTest backend (port 8000)

```sh
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Interactive docs: `http://127.0.0.1:8000/docs`. Health: `http://127.0.0.1:8000/health`.

### 3. Start the frontend (port 5173)

```sh
cd frontend
npm run dev
```

Open the Vite URL, select a method, and start with `http://127.0.0.1:8001/users`. Requests are sent through the EdgeTest backend to the local demo API. For timeout testing, try `/delay?seconds=2` and set the request timeout below two seconds.

## Security and data handling

- Outbound schemes are limited to HTTP and HTTPS. URL credentials and request `Host`/proxy-control headers are rejected.
- Outbound domain/IP targets require explicit allowlisting. Every resolved address is checked; private, reserved, link-local, and cloud metadata destinations are rejected. The HTTP transport connects to the validated pinned address to prevent a second DNS lookup from bypassing validation.
- The only default local targets are the demo server hosts on port 8001, and those must resolve to loopback. Redirect following and environment proxy inheritance are disabled.
- Request URL, header, JSON-body, timeout, response-size, concurrency, and connection-pool limits are enforced. Compressed upstream responses are rejected so size limits apply to bytes actually received.
- The frontend stores recent history only in page memory. URL credentials and sensitive query parameters are redacted; sensitive headers and values under secret-like JSON body keys are stored/restored as `[REDACTED]`. Response data is not included in history.
- Backend execution errors are structured and do not expose stack traces or exception details.
- SQLite execution records and saved requests redact secret-like headers, URL query values, JSON fields, response headers, and known secret values echoed in responses. The database and WAL files are created with owner-only permissions. Persistent history is local to this backend and is not an account-based multi-user store.
- Replay uses the sanitized stored request, not an encrypted copy of credentials. Re-enter redacted values before replaying authenticated calls. The normal outbound allowlist/IP checks run again on every replay. Unsafe replay and saved execution require explicit confirmation.
- AI analysis requires a user confirmation in the UI and an explicit consent field in the API request. Only stored sanitized request/response data is sent to the configured HTTPS AI endpoint. Provider failures are reported without returning provider diagnostics or the API key. AI output is a recommendation, not a guaranteed diagnosis.

## Tests and checks

### Backend

```sh
cd backend
.venv/bin/python -m pytest
```

Tests use mocked HTTPX transports, an in-process controlled ASGI app, and a local loopback socket. They do not call production APIs.

### Frontend

```sh
cd frontend
npm test
npm run lint
npm run build
```

Vitest and React Testing Library cover request submission, response rendering, HTTP errors, timeouts, network failures, backend validation, JSON validation, editable key/value rows, and redacted/restorable/clearable history.

## Persistent test management and analytics

The local SQLite database has three primary tables:

- `collections`: named folders for saved test requests.
- `saved_tests`: validated request configuration plus user-provided/generated case JSON, linked to a collection.
- `executions`: timestamped request/result snapshots with status, outcome, duration, error category, optional test-case metadata, and replay relationship. Failed HTTP responses and transport errors are automatically retained.

The persistent workspace can create, rename, and delete collections; save and execute requests; browse/filter failure records by endpoint, method, status, and error category; replay and compare sanitized requests; request consent-gated AI analysis; export recorded executions as JSON/CSV; and view request totals, executed test-case runs, saved case definitions, case outcomes, recent failures, common failing endpoints, and daily trends. Saved test cases are stored as JSON along with their request. The current UI runs a saved request as a smoke execution; boundary and OpenAPI case execution continues in the existing runners.

Useful API routes are under `/api/v1`: `GET/POST /collections`, `PATCH/DELETE /collections/{id}`, `GET/POST /collections/{id}/tests`, `POST /tests/{id}/execute`, `GET /failures`, `POST /executions/{id}/replay`, `POST /executions/{id}/analyze`, `GET /executions/export?format=json|csv`, and `GET /analytics`. Requests and replay still pass through the existing execution service and SSRF checks.

Configuration:

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_PATH` | SQLite database location (relative paths use the backend working directory) | `data/edgetest.sqlite3` |
| `AI_PROVIDER` | AI provider (`gemini` or `openai`) | `openai` |
| `GEMINI_API_KEY` | Server-side Gemini API key, required for Gemini | empty |
| `GEMINI_MODEL` | Gemini model identifier | `gemini-2.5-flash` |
| `AI_API_KEY` | Server-side API key for an OpenAI-compatible LLM | empty (disabled) |
| `AI_API_URL` | HTTPS chat-completions endpoint | `https://api.openai.com/v1/chat/completions` |
| `AI_MODEL` | Provider model identifier | `gpt-4o-mini` |
| `OUTBOUND_ALLOWED_HOSTS` | Additional API/specification target hosts | empty |
| `LOCAL_DEMO_TARGETS` | Authorized local demo origin(s) | localhost and 127.0.0.1 port 8001 |
| `CORS_ALLOWED_ORIGINS` | Allowed browser origins | localhost and 127.0.0.1 port 5173 |

The full workflow is: execute a manual/boundary/OpenAPI request, persist its result, inspect failed executions, replay the sanitized request (confirming unsafe methods), optionally request consent-gated AI recommendations, then review the stored-data analytics and export results.

### Enable Gemini debugging

From the repository root, install the updated backend dependencies and create the local environment file if it does not exist:

```sh
cd backend
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
```

In `backend/.env`, set `AI_PROVIDER=gemini`, paste your key into `GEMINI_API_KEY`, and optionally set `GEMINI_MODEL=gemini-2.5-flash`. Keep `.env` local; never put the key in React environment variables. Restart the backend:

```sh
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

AI analysis remains consent-gated from the failure repository. It sends sanitized request and response context to Gemini and returns an error summary, likely root cause, and suggested fixes. Missing credentials, invalid credentials, quota/rate limits, timeouts, and other provider failures return safe messages without provider diagnostics or secrets.

## Boundary-condition testing

The Boundary testing panel lets users add up to 20 named inputs and choose a location (URL path placeholder, query parameter, header, or JSON body field) and type. JSON body names may use dot paths such as `profile.email`; URL path inputs replace `{name}` placeholders in the request path. Array and object generators are available for body fields. Number inputs may set optional minimum and maximum values; defaults are 1 and 100.

Generate to preview cases, deselect anything you do not want, then run the selection. String inputs include empty, 4,096-character, special-character, whitespace, invalid-number, and null cases. Number inputs include zero, negative, min/max, out-of-range, malformed, and null values. Arrays include empty, single, large (100 items), wrong-type, and null cases. Objects include empty, field removal, null, null child, unexpected field, and wrong-type cases. Each selected case is sent separately through the same `/api/v1/requests/execute` security and size controls as normal requests. URL path names must match a `{name}` placeholder.

Only HTTP GET, HEAD, and OPTIONS run without an additional prompt. POST, PUT, PATCH, and DELETE require explicit confirmation for every selected batch; cancelling sends no requests. The runner reports 2xx/3xx as passed, 4xx/5xx as failed, and transport/local validation problems as errored. This generic classification is informational: applications may consider a particular boundary value valid or invalid differently.

Focused coverage includes generator boundaries/limits, request input patching for each location, case selection, HTTP versus transport outcome classification, summaries, and cancellation of potentially destructive batches.

## OpenAPI / Swagger import and test generation

The OpenAPI test panel accepts `.json`, `.yaml`, and `.yml` files up to 1 MiB, or a specification URL. The backend parses OpenAPI 3.0/3.1 and Swagger 2.0, lists operations, path/query/header parameters, JSON request body schemas, and component/definition schemas. If a specification has no server definition, provide a Base URL; import reports a clear validation error until one is supplied. Existing server URLs are preserved, and specifications with multiple top-level servers expose a server selector (the first server is the initial default). Relative endpoint paths are joined to the selected server, including its base path. Local nested `$ref` references and schema compositions are resolved: `allOf` constraints are combined, `anyOf` payloads satisfy at least one branch, and `oneOf` payloads are checked to satisfy exactly one branch. Nullable values and optional fields are supported. It creates a representative request and test cases by reusing the Phase 2 boundary generator, adding baseline-valid and missing-parameter/body-field cases. Schema-derived tests cover valid examples/defaults, missing fields, nulls, invalid types, and data-type boundary values.

Select endpoints and individual cases before running a case or suite. Runs are sequential, limited to 100 selected cases at once, and use the same `POST /api/v1/requests/execute` path as manual and Phase 2 requests. Unsafe methods require explicit confirmation; batches larger than 10 also require confirmation. HTTP 4xx/5xx are failed cases; transport or local validation problems are errored cases.

Specification URLs are fetched by the backend through the existing guarded HTTPX execution service: only HTTP/HTTPS URLs are accepted, outbound host/IP policy and DNS pinning apply, redirects are not followed, and response size/time limits remain active. File contents and parsed specs are treated as untrusted. Server and supplied Base URLs are syntactically validated and reject credentials, query strings, and fragments. Importing a server URL does not authorize network access: imported API targets still pass the normal outbound allowlist, resolved-IP, localhost/private-network, timeout, and response-size checks whenever a test case executes. External `$ref`, YAML aliases, recursive references, and unsupported schema keywords (such as `not`, `patternProperties`, and conditional schemas) are rejected with a specific readable error; non-JSON request body formats, auth scheme synthesis, and complex query/header serialization are not automatically generated and are surfaced as warnings where applicable.

# Voice AI Patient Registration Agent

A caller dials a US phone number, talks to an LLM voice agent that collects their
demographics through natural conversation, and the record lands in a persistent database.
A REST API and a small web dashboard expose the records.

---

## 1. Live demo

| What | Where |
|---|---|
| Phone number | `+1 (XXX) XXX-XXXX` — _fill in after assigning a number in Vapi_ |
| API base URL | `https://<your-app>.up.railway.app` — _fill in after deploy_ |
| Dashboard | `https://<your-app>.up.railway.app/dashboard` |
| Interactive API docs | `https://<your-app>.up.railway.app/docs` |
| Health check | `https://<your-app>.up.railway.app/health` |

> **Status:** the application, dashboard, tests and prompt are complete and verified
> locally. The three placeholders above are filled in once the Railway deploy and the
> Vapi assistant are provisioned — see [§6](#6-deployment-railway) and [§7](#7-vapi-configuration),
> which give the exact steps.

---

## 2. Architecture

The voice layer is entirely Vapi's: it owns speech-to-text, the LLM turn loop,
text-to-speech, interruption handling and telephony. Our service is a plain HTTP API that
Vapi calls as a *tool* mid-conversation. That split is what keeps the system small — there
is no audio code, no LLM code and no conversation state on our side. The API is the single
source of truth for validation, so the voice agent and a `curl` request are held to
identical rules, and the dashboard is a static page that reads the same public endpoints.

```
                        ┌──────────────────────────────────┐
   ☎  Caller ──PSTN──▶  │  Vapi                            │
                        │  phone number → assistant        │
                        │  STT · LLM · TTS · turn-taking   │
                        └───────────┬──────────────────────┘
                                    │  HTTPS
                 tool calls mid-call │  POST /vapi/tools
             end-of-call transcript  │  POST /vapi/events
                                    ▼
                        ┌──────────────────────────────────┐
                        │  FastAPI (Railway)               │
                        │                                  │
                        │  routers/vapi.py ──┐             │
                        │                    ├─▶ services/ │
                        │  routers/patients ─┘   validation│
                        │                        + logging │
                        └───────────┬──────────────────────┘
                                    │ SQLAlchemy
                                    ▼
                        ┌──────────────────────────────────┐
                        │  SQLite on a persistent volume   │
                        │  patients · call_logs            │
                        └──────────────────────────────────┘
                                    ▲
                                    │ fetch()  GET /patients, DELETE /patients/{id}
                        ┌───────────┴──────────────────────┐
                        │  /dashboard (static HTML + JS)   │
                        └──────────────────────────────────┘
```

**Request path during a call.** The caller speaks → Vapi transcribes → the LLM decides to
call `create_patient` → Vapi POSTs `{"message": {"type": "tool-calls", "toolCallList": [...]}}`
to `/vapi/tools` → we validate, write, and answer with a short status string → the LLM reads
that string and says the appropriate thing back to the caller.

---

## 3. Tech stack & justification

| Layer | Choice | Why |
|---|---|---|
| Voice + telephony | **Vapi** (assistant + phone number configured in the Vapi dashboard, tools call our API) | Fastest path to a real phone number; handles STT/TTS/turn-taking/interruptions, which are the hardest parts of a voice agent and not where this project's value lies |
| LLM | Whatever the Vapi assistant is configured with (GPT-4o-mini by default) | No LLM code on our side; the model is a configuration choice, swappable without a redeploy |
| Backend | **Python 3.12, FastAPI, SQLAlchemy 2.x, Pydantic v2**, managed with **uv** | Fast to write, validation built into the type layer, auto-generated OpenAPI docs; uv gives fast installs, a committed lockfile, and is auto-detected by Railway |
| Database | **SQLite** on a persistent volume | Zero setup, survives restarts and redeploys; a single-writer store is a correct fit for one phone line |
| Dashboard | Single static HTML + vanilla JS served by FastAPI at `/dashboard` | No build step, no toolchain, nothing to break between the API and the page |
| Hosting | **Railway** (fallback: ngrok tunnel to localhost) | Deploys from GitHub in minutes and gives the public HTTPS URL Vapi needs for webhooks |
| Tests | pytest + FastAPI `TestClient` | 21 tests over the API contract and the voice tool handler, running against an in-memory database |

---

## 4. Setup

```bash
git clone <repo-url>
cd carecloud

cp .env.example .env          # defaults work as-is for local development

uv sync                       # installs from the committed uv.lock
uv run uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Dashboard: <http://127.0.0.1:8000/dashboard>
- Docs: <http://127.0.0.1:8000/docs>

On first start the app creates the tables and seeds two demo patients (Jane Doe and
Marcus Rivera) so the dashboard is not empty. Seeding only runs when the table is empty.

Run the tests:

```bash
uv run pytest -q     # 21 passed
```

---

## 5. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/patients.db` | SQLAlchemy URL. On Railway, point this at the mounted volume, e.g. `sqlite:////app/data/patients.db`. Any SQLAlchemy-supported URL works, including Postgres. |
| `VAPI_WEBHOOK_SECRET` | _(empty)_ | If set, `/vapi/tools` and `/vapi/events` require a matching `x-vapi-secret` header and return 401 otherwise. Empty disables the check. |
| `LOG_LEVEL` | `INFO` | Root log level. |

No secrets are committed; `.env` is gitignored and `.env.example` documents every variable.

---

## 6. Deployment (Railway)

1. Push this repository to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, select the repo. Nixpacks detects
   `uv.lock` and `.python-version` and builds without further configuration.
3. **Add a volume**: project → service → *Variables/Settings* → **Add Volume**, mount path
   `/app/data`. This is what makes records survive redeploys.
4. Set variables: `DATABASE_URL=sqlite:////app/data/patients.db` (four slashes — it is an
   absolute path), `LOG_LEVEL=INFO`, and optionally `VAPI_WEBHOOK_SECRET=<a random string>`.
5. Generate a public domain under **Settings → Networking → Generate Domain**.
6. Verify persistence:
   ```bash
   BASE=https://<your-app>.up.railway.app
   curl -s $BASE/health
   curl -s -X POST $BASE/patients -H 'Content-Type: application/json' -d '{
     "first_name":"Persist","last_name":"Test","date_of_birth":"01/15/1990","sex":"Other",
     "phone_number":"5125550100","address_line_1":"1 Test St","city":"Austin",
     "state":"TX","zip_code":"78701"}'
   # redeploy from the Railway dashboard, then:
   curl -s "$BASE/patients?last_name=Test"   # the record is still there
   ```

**Fallback without Railway:** run locally and expose it with `ngrok http 8000`, then use the
ngrok HTTPS URL everywhere `<your-domain>` appears below. The URL changes on each ngrok
restart, so the Vapi tool URLs must be updated when it does.

---

## 7. Vapi configuration

### Fast path: the setup script

`create_patient` has 16 parameters, and typing those into the dashboard form is slow and
error-prone. [`scripts/vapi_setup.py`](scripts/vapi_setup.py) pushes
[`prompts/vapi_tools.json`](prompts/vapi_tools.json) and the system prompt straight to the
Vapi API instead:

```bash
export VAPI_API_KEY=...        # private key, Dashboard → API Keys
uv run python scripts/vapi_setup.py --server-url https://<your-domain> --dry-run
uv run python scripts/vapi_setup.py --server-url https://<your-domain>
```

It is idempotent — tools are matched by function name, the assistant by name — so
re-running updates in place rather than creating duplicates. That makes the usual
follow-up a single command: when your public URL changes (ngrok → Railway), re-run with
the new `--server-url` and all three tools plus the events URL are repointed.

Flags: `--dry-run` previews payloads without sending, `--skip-assistant` syncs tools only,
`--secret` mirrors `VAPI_WEBHOOK_SECRET` onto the tools as an `x-vapi-secret` header, and
`--voice` picks the voice. If the assistant call fails the tools are still created, and
the script says so and points you at the manual steps below.

Only the phone number is left to do by hand (step 8).

### Manual path

Everything the script does can be done in the dashboard; there is no Vapi-specific code to
deploy beyond the two endpoints this repo already serves.

1. **Create an assistant** named "Patient Intake".
2. **Model:** GPT-4o-mini (or Claude Sonnet), temperature ≈ 0.4. Low enough to follow the
   script, high enough to not sound wooden.
3. **Voice:** any natural voice (ElevenLabs, or the Vapi default). Enable filler words /
   backchanneling if offered — it covers tool-call latency.
4. **First message:**
   > Hi, thanks for calling. I'm the virtual intake assistant and I'll help you register as a new patient. To start, could I get your first and last name?
5. **System prompt:** paste the clean block from [`prompts/system_prompt.md`](prompts/system_prompt.md).
6. **Tools:** create three **function tools** — `check_existing_patient`, `create_patient`,
   `update_patient`. The exact JSON parameter schemas are in
   [`prompts/vapi_tools.json`](prompts/vapi_tools.json); copy each `function` block and set
   every tool's `server.url` to `https://<your-domain>/vapi/tools`. Set the request-start
   message (e.g. "One moment while I save that") so the caller is not left in silence during
   the round trip.
7. **Server URL (assistant level):** `https://<your-domain>/vapi/events`, with the
   **end-of-call-report** event enabled. This is what stores transcripts.
8. **Phone number:** buy or assign a free US number in Vapi and attach it to the assistant.
9. Set `endCallFunctionEnabled = true` so the assistant can hang up after confirming.
10. Optionally set `maxDurationSeconds ≈ 900` and a silence timeout of ~20s.

If you set `VAPI_WEBHOOK_SECRET`, add the same value as a custom header `x-vapi-secret` on
each tool's server configuration and on the assistant server URL.

---

## 8. Prompt engineering notes

The full prompt, annotated section by section with the reasoning behind each rule, is in
[`prompts/system_prompt.md`](prompts/system_prompt.md). The four decisions that most affect
call quality:

**Status-token tool results, not prose.** Every tool returns a string beginning with a
literal token — `SUCCESS:`, `NOT_FOUND`, `VALIDATION_ERROR:`, `FOUND:`, `ERROR:` — and the
prompt branches on those tokens. The model is not asked to interpret an HTTP status code or
a JSON blob mid-call; it does an exact string match and reads the matching script. Errors
carry their own remediation text (`"phone_number must be exactly 10 digits"`), so the agent
tells the caller precisely what to repeat rather than apologizing vaguely.

**An explicit confirmation gate.** The prompt forbids calling `create_patient` until the
agent has read the whole record back and heard an explicit yes. Without that instruction,
models save as soon as the last field arrives, and any mis-transcription is committed
permanently. One read-back pass catches STT errors across every field at once.

**Spell-back on names.** Names are where speech-to-text fails unrecoverably — "Bryan"
and "Brian" are the same audio. Every name is spelled back letter by letter before it is
accepted, converting a silent data error into a two-second confirmation.

**A never-silent error path.** `/vapi/tools` catches every exception, rolls back the
session and still returns HTTP 200 with a readable `ERROR:` string. A 500 or a timeout
would leave the caller listening to nothing. The prompt pairs this with a scripted exit
("a member of our staff will call you back"), so a database outage ends the call
gracefully instead of in dead air or an apology loop.

Supporting choices: one question per turn (multi-part questions get partially answered);
an explicit ban on markdown (TTS reads asterisks aloud); guidance on saying dates and
phone numbers in spoken form; and bundling all three optional sections into a single
opt-in question to keep the call short.

---

## 9. API reference

Every response — success or failure — uses the envelope `{"data": ..., "error": ...}`.
Errors carry `{"code", "message", "details": [{"field", "message"}]}`.

| Method | Path | Behaviour | Status |
|---|---|---|---|
| `GET` | `/health` | Liveness probe | 200 |
| `GET` | `/patients` | List non-deleted patients. Filters: `last_name` (case-insensitive exact), `date_of_birth` (MM/DD/YYYY or ISO), `phone_number` (normalized to digits) | 200 / 400 |
| `GET` | `/patients/{id}` | One patient | 200 / 400 / 404 |
| `GET` | `/patients/{id}/calls` | Transcripts linked to this patient | 200 / 404 |
| `POST` | `/patients` | Create | 201 / 422 |
| `PUT` | `/patients/{id}` | Partial update; unknown fields ignored | 200 / 404 / 422 |
| `DELETE` | `/patients/{id}` | Soft delete | 200 / 404 |
| `POST` | `/vapi/tools` | Vapi tool-call webhook (always 200) | 200 / 401 |
| `POST` | `/vapi/events` | Vapi end-of-call report (always 200) | 200 / 401 |

```bash
BASE=http://127.0.0.1:8000

# Health
curl -s $BASE/health

# Create
curl -s -X POST $BASE/patients -H 'Content-Type: application/json' -d '{
  "first_name": "Ada", "last_name": "Lovelace", "date_of_birth": "12/10/1990",
  "sex": "female", "phone_number": "(512) 555-0147", "email": "ada@example.com",
  "address_line_1": "10 Analytical Way", "city": "Austin", "state": "texas",
  "zip_code": "78701" }'

# List, and filter
curl -s "$BASE/patients"
curl -s "$BASE/patients?last_name=lovelace"
curl -s "$BASE/patients?phone_number=512-555-0147"
curl -s "$BASE/patients?date_of_birth=12/10/1990"

# Read one
curl -s $BASE/patients/<patient_id>

# Partial update
curl -s -X PUT $BASE/patients/<patient_id> \
  -H 'Content-Type: application/json' -d '{"city": "Dallas"}'

# Soft delete
curl -s -X DELETE $BASE/patients/<patient_id>

# Simulate a voice tool call
curl -s -X POST $BASE/vapi/tools -H 'Content-Type: application/json' -d '{
  "message": {"type": "tool-calls", "toolCallList": [
    {"id": "t1", "name": "check_existing_patient",
     "arguments": {"phone_number": "512-555-0147"}}]}}'
```

Inputs are normalized on the way in and formatted on the way out: `"(512) 555-0147"` is
stored as `5125550147`, `"female"` as `Female`, `"texas"` as `TX`, and `date_of_birth` is
accepted as `MM/DD/YYYY` or ISO but always returned as `MM/DD/YYYY`.

---

## 10. Edge cases handled

| Case | Behaviour |
|---|---|
| **Invalid date of birth** | Future dates and pre-1900 dates rejected with a spoken-friendly reason; the agent re-asks. Accepts `MM/DD/YYYY`, ISO, and a couple of near-miss formats. |
| **Invalid phone number** | Formatting stripped, a leading `1` dropped, must be 10 digits with a 2–9 area code. Anything else → `VALIDATION_ERROR` and the agent asks the caller to repeat it. |
| **Invalid state / ZIP** | Full state names ("california") and abbreviations both accepted; anything outside the 50 states + DC is rejected. ZIP must be `12345` or `12345-6789`. |
| **Database write failure** | The tool handler catches everything, rolls back, logs a traceback and returns `ERROR: ...`. The agent apologizes, promises a callback and ends the call — never silence. |
| **Mid-call hangup** | Nothing is written until the confirmation gate passes, so a dropped call simply leaves no record. Partial data is intentionally discarded rather than saved incomplete. |
| **"Start over"** | The prompt instructs the agent to discard everything and restart from the name. Since nothing is persisted before confirmation, no cleanup is needed. |
| **Duplicate caller** | `check_existing_patient` fires as soon as the phone number is known; a match offers an update path instead of a second record. |
| **Out-of-order answers** | The prompt accepts volunteered fields in any order; the schema validates the complete set at save time regardless of the order collected. |
| **Caller declines optional fields** | `""`, `"none"`, `"no"` and `"n/a"` for email and other optional fields are normalized to `NULL` rather than stored as junk. |
| **Malformed webhook body** | Non-JSON or unrecognized payloads return 200 with an empty result list instead of erroring. |
| **Unknown tool name** | Returns `ERROR: unknown tool <name>` rather than raising. |
| **Arguments as a JSON string** | Vapi has sent tool arguments both as an object and as a JSON-encoded string; both are parsed, as is the older `function: {name, arguments}` nesting. |
| **Unknown fields on update** | Silently ignored, so a model hallucinating an extra field cannot 422 a valid update. |
| **Soft delete** | Rows are never destroyed; `deleted_at` is stamped and the record disappears from reads. |

---

## 11. Known limitations & trade-offs

- **SQLite is single-writer.** Correct for one phone line; concurrent calls at volume would
  contend on writes. The fix is one environment variable — `DATABASE_URL` pointed at Postgres.
- **No authentication on the API.** Anyone with the URL can read and delete records. This is
  deliberate for reviewability and is the first thing to change for real use.
- **Not HIPAA compliant.** No encryption at rest, no audit trail, no BAA with Vapi or the
  model provider. Test data only — do not put real patient information in it.
- **No retry on webhook failure.** If our service is down when Vapi calls a tool, the data
  for that call is lost; the agent tells the caller staff will follow up.
- **Addresses are not verified.** Format is validated; existence is not. No geocoding or
  USPS lookup, so a well-formed but fictional address is accepted.
- **Transcript linking is best-effort.** The end-of-call report is matched to a patient by
  the caller's phone number. A caller who registers a number different from the one they
  are calling from will have an unlinked transcript.
- **Validation errors surface field names.** The agent translates them, but a name like
  `emergency_contact_phone` can leak into speech if the model reads it verbatim.
- **Seeded demo data** ships in the database on first boot; delete the two records before
  treating the dashboard count as real.

---

## 12. Next steps

1. **Postgres** via `DATABASE_URL`, plus Alembic migrations instead of `create_all`.
2. **Authentication** — an API key or JWT on `/patients`, and a signed-secret requirement
   (not just optional) on the Vapi webhooks.
3. **Appointment scheduling tool** — a fourth function tool over a calendar, the natural
   next thing a caller wants after registering.
4. **Insurance eligibility check** against a payer API during the call.
5. **Rate limiting** and structured request tracing (correlation IDs from `call.id` through
   to every log line).
6. **Idempotency keys** on `create_patient` so a retried tool call cannot double-write.
7. **Post-call review queue** — flag low-STT-confidence fields for a human to verify.

---

## 13. Bonuses completed

- [x] **Call transcript storage** — `/vapi/events` consumes the end-of-call report into a
      `call_logs` table, linked to the patient, and surfaced in the dashboard detail panel.
- [x] **Duplicate patient detection** — `check_existing_patient` mid-call, with an update path.
- [x] **Update flow for returning callers** — `update_patient` tool + `PUT /patients/{id}`.
- [x] **Soft delete** rather than destructive delete.
- [x] **Structured JSON logging** of every tool call, result, and patient create/update.
- [x] **Optional webhook authentication** via `x-vapi-secret`.
- [x] **Multilingual support** — the agent switches to Spanish on request and records
      `preferred_language`.
- [x] **Auto-refreshing dashboard** so a record can be watched landing during a live call.
- [x] **21 automated tests** covering the API contract and the voice tool handler,
      including malformed payloads and both argument encodings.

---

## 14. Project layout

```
app/
  main.py                      FastAPI app, exception handlers, startup + seed
  config.py                    env-var settings (pydantic-settings)
  database.py                  engine, session, get_db dependency
  models.py                    Patient + CallLog ORM models
  schemas.py                   Pydantic request/response schemas
  validators.py                shared validation helpers (phone, DOB, state, ZIP, sex)
  responses.py                 {data, error} envelope helpers
  logging_config.py            one-line JSON logging to stdout
  routers/
    patients.py                REST CRUD
    vapi.py                    tool-call + end-of-call webhooks
    dashboard.py               serves the dashboard
  services/patient_service.py  business logic shared by REST and voice
  static/dashboard.html        the whole dashboard, one file
  seed.py                      two demo records
prompts/
  system_prompt.md             the assistant prompt, annotated
  vapi_tools.json              tool parameter schemas for the Vapi dashboard
scripts/
  vapi_setup.py                idempotent sync of tools + assistant to the Vapi API
tests/
  test_patients_api.py         API contract tests
  test_vapi_tools.py           voice tool handler tests
```

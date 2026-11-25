# AI Receptionist SaaS Backend

FastAPI backend for user authentication, business workspaces, receptionist and
Vapi configuration, and business-scoped Vapi call history.

## Architecture

- FastAPI REST API, versioned under `/api/v1`
- PostgreSQL with SQLAlchemy and Alembic migrations
- Pydantic request and response validation
- Argon2 password hashing and short-lived JWT access tokens
- Password changes immediately revoke previously issued access tokens
- Business membership roles with endpoint-level authorization
- Vapi webhook authentication and persisted call lifecycle events

## Environment

The application reads configuration from environment variables and `.env` in
the current working directory. Configure these variable names; do not commit
secret values:

| Variable | Purpose |
|---|---|
| `APP_NAME` | API title |
| `APP_ENV` | `development`, `test`, `staging`, or `production` |
| `DEBUG` | FastAPI debug mode |
| `API_V1_PREFIX` | Versioned API prefix |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `SECRET_KEY` | JWT signing key; must contain at least 32 characters |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime, from 1 to 1440 minutes |
| `VAPI_API_KEY` | Server-only Vapi API credential used for managed assistant and number setup |
| `PUBLIC_API_BASE_URL` | Public HTTPS backend origin used by Vapi to send authenticated webhooks |
| `VAPI_MODEL_PROVIDER` | Vapi assistant model provider |
| `VAPI_MODEL` | Vapi assistant model name |
| `VAPI_VOICE_PROVIDER` | Vapi assistant voice provider |
| `VAPI_VOICE_ID` | Vapi assistant voice identifier |
| `VAPI_RECOVERY_ENABLED` | Enables the standalone Vapi event recovery worker |
| `VAPI_RECOVERY_POLL_INTERVAL_SECONDS` | Delay between recovery batches; must be positive |
| `VAPI_RECOVERY_BATCH_SIZE` | Maximum receipts per batch, from 1 to 1000 |
| `VAPI_RECOVERY_MAX_ATTEMPTS` | Attempts before a receipt is marked terminally failed |
| `VAPI_RECOVERY_MAX_BACKOFF_SECONDS` | Maximum delay before retrying a failed receipt |
| `TWILIO_ACCOUNT_SID` | Server-only platform Twilio account for number provisioning |
| `TWILIO_AUTH_TOKEN` | Server-only platform Twilio credential |
| `VAPI_WEBHOOK_SECRET` | Bearer token expected by the Vapi webhook |
| `PAYMENT_PROVIDER` | Optional payment provider name |
| `PAYMENT_SECRET_KEY` | Optional payment provider credential |
| `PAYMENT_WEBHOOK_SECRET` | Optional payment webhook credential |
| `BILLING_ADMIN_USER_IDS` | Comma-separated UUIDs authorized to administer platform billing |
| `FRONTEND_ORIGIN` | Allowed browser origin for CORS |

`DB_PASSWORD` and `SECRET_KEY` are required. Keep `.env` out of version
control, use unique production credentials, and use HTTPS for public traffic.

## Local development

From the repository root, create the backend virtual environment and install
the declared dependencies:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
```

Configure PostgreSQL and the environment variables above, then apply migrations:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m alembic \
  -c backend/alembic.ini upgrade head
```

Start the API:

```bash
PYTHONPATH=backend backend/.venv/bin/uvicorn app.main:app --reload
```

Run the recovery worker in a separate process using the same database
configuration as the API:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.workers.vapi_recovery
```

The worker continuously polls bounded batches, can run as multiple independent
processes, and handles SIGINT/SIGTERM by finishing its current batch before
shutting down. Do not start it inside Uvicorn workers.

Interactive API documentation is available at `/docs`; the OpenAPI document is
at `/openapi.json`. Liveness and database readiness endpoints are available at
`/api/v1/health/live` and `/api/v1/health/ready`.

Run the backend tests with:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/ -q
```

### Knowledge retrieval

Knowledge search runs semantic retrieval with the configured, normalized
384-dimensional Sentence Transformers embedding and pgvector cosine distance.
It also runs PostgreSQL full-text search using `to_tsvector('simple', ...)`,
`plainto_tsquery('simple', ...)`, and `ts_rank_cd`. The two ranked lists are
combined with reciprocal rank fusion (`1 / (60 + rank)` by default); chunk
UUIDs are deduplicated and ties are ordered deterministically.

Both retrieval queries filter chunks by the authorized business ID and join
their parent document, requiring that the document's business ID matches and
its processing status is `completed`. The search request does not accept a
business ID in its JSON body. As with other business-scoped endpoints, the
business is selected with a `business_id` query parameter and authorized by
the existing membership/permission dependency.

Search an authorized workspace:

```http
POST /api/v1/knowledge/search?business_id=<business-uuid>
Authorization: Bearer <access-token>
Content-Type: application/json

{"query": "weekday opening hours", "top_k": 5}
```

Example response:

```json
{
  "query": "weekday opening hours",
  "results": [
    {
      "chunk_id": "00000000-0000-0000-0000-000000000001",
      "document_id": "00000000-0000-0000-0000-000000000002",
      "business_id": "00000000-0000-0000-0000-000000000003",
      "chunk_index": 0,
      "chunk_text": "We are open weekdays from 9 AM to 5 PM.",
      "chunk_metadata": {"page_number": 2},
      "document_filename": "hours.pdf",
      "document_source": "https://example.test/hours.pdf",
      "semantic_distance": 0.12,
      "keyword_score": 0.21,
      "semantic_rank": 1,
      "keyword_rank": 1,
      "rrf_score": 0.03278688524590164
    }
  ],
  "documents": [
    {
      "document_id": "00000000-0000-0000-0000-000000000002",
      "document_filename": "hours.pdf",
      "document_source": "https://example.test/hours.pdf"
    }
  ]
}
```

The response contains ranked chunks, document/source references, chunk
metadata, and available semantic/keyword ranking details. It never includes
embedding vectors. An empty query or a workspace with no completed knowledge
returns empty `results` and `documents` arrays. The separate context builder
can assemble whole ranked chunks within a character budget and preserve their
references; answer generation is not part of this stage.

Run the focused retrieval tests with:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest \
  backend/tests/test_knowledge.py \
  backend/tests/test_knowledge_retrieval.py \
  backend/tests/test_knowledge_postgres.py -q
```

The PostgreSQL tests require `VAPI_TEST_DATABASE_URL` to point to a local,
isolated `vapi_test_*` database with pgvector installed and enabled. They
create and remove a unique temporary schema per test.

### Vapi knowledge-search tool

New assistants created from the backend assistant payload register the
`search_business_knowledge` function. Vapi sends function requests to
`POST /api/v1/integrations/vapi/knowledge-tool`, authenticated with the same
Bearer credential used by the existing Vapi webhook. The endpoint accepts Vapi's
`message.type = "tool-calls"` format, supports up to eight tool calls per
request, and returns a flat string result for every `toolCallId`.

The endpoint resolves the active business only from registered assistant and/or
phone-number IDs matched against enabled Vapi and receptionist configurations
and an active provisioning record. It ignores any business ID in function
arguments, passes the resolved business ID to the existing hybrid retriever,
and does not return database IDs, embeddings, or source URLs. Empty results
explicitly tell the assistant not to guess. Existing status-update and
end-of-call-report processing remains on the original webhook endpoint.

The backend payload change does not update an already-existing Vapi-native
assistant. To enable the tool for one, add a Function tool in the Vapi Dashboard
pointing to the endpoint above, with a required `query` string parameter and
the configured Bearer authentication. This is a manual dashboard change; the
application does not make Vapi API calls to modify existing assistants.

### Frontend

The React frontend uses JavaScript and JSX. From the repository root:

```bash
npm install
cp .env.example .env.local
npm run dev
```

Vite reads `VITE_API_BASE_URL` from `.env.local`; the development default is
`http://127.0.0.1:8000/api/v1`. Keep this file limited to frontend `VITE_`
settings; never put backend secrets in Vite environment files. Restart Vite
after changing environment variables. The frontend provides authentication,
public marketing and pricing pages, business workspaces and members, managed
receptionist onboarding, call history, account settings, and subscription/usage
pages. Customers do not enter Vapi credentials or identifiers. The published
pricing catalog is publicly readable; workspace billing information remains
business-permission protected.
Call records are only available to business owners and admins. Unsupported
features such as appointment management and billing are not simulated.

Validate frontend changes with:

```bash
npm run lint
npm test
npm run build
```

## Managed onboarding and Vapi integration

Business owners complete `/onboarding` with a profile, natural-language
receptionist brief, fallback guidance, and country for a local voice number.
Before provisioning begins, the owner must explicitly authorize the platform to
purchase a number; provider charges and local availability requirements apply.
The only offered service is inbound AI call answering. Appointment booking,
call transfers, and CRM lead capture are not enabled because their backend
services are not implemented.

The backend creates the Vapi assistant, buys a voice-enabled local number on
the platform Twilio account, imports it into Vapi, and configures the webhook.
The phone is not assigned to the assistant until the owner activates the
receptionist. Failed setup retains completed provider resource IDs and the
reserved number so retries can resume without intentionally creating duplicate
resources. Setup currently runs synchronously in the API request; a failed
request can be retried from onboarding. Provider account funding, number
availability, and regional regulatory requirements apply. Keep all provider
credentials on the backend, never in Vite environment variables or browser code.

Provisioning moves a business to `ready` only after the assistant and phone
number are configured. The owner must explicitly activate it before calls are
handled. Failed provisioning is reported as `failed`; it is never presented as
active. Number purchase consent is recorded with the consenting user and time.

For an existing Vapi-native assistant/phone connection, use the separate
authenticated endpoint
`POST /api/v1/businesses/{business_id}/receptionist/activate-existing`.
It requires the existing Vapi connection and a valid saved receptionist
configuration, and uses the existing business permission checks. Confirm the
phone assignment in the Vapi Dashboard before calling it: this path does not
make provider requests or independently verify the assignment. It records the
connection as `vapi_native`, does not create phone-purchase consent or Twilio
records, and does not change the assistant or phone configuration. It requires
`VAPI_API_KEY`, `VAPI_WEBHOOK_SECRET`, and `PUBLIC_API_BASE_URL`, but not Twilio
credentials. Apply database migrations before using this endpoint.

Assistant model and voice selections are controlled by the server-side
`VAPI_MODEL_PROVIDER`, `VAPI_MODEL`, `VAPI_VOICE_PROVIDER`, and `VAPI_VOICE_ID`
settings. Ensure the corresponding provider credentials are available to the
Vapi account.

The existing webhook endpoint is:

```text
https://<your-public-host>/api/v1/integrations/vapi/webhook
```

New managed assistants receive this webhook URL and server-only authorization
automatically. The manual setup instructions below are retained for existing
legacy integrations. The webhook accepts documented `status-update` and `end-of-call-report`
messages. It stores deduplicated call lifecycle events and available call
metadata. Call summaries are available at
`GET /api/v1/businesses/{business_id}/calls`; transcript and recording
metadata are returned only from the authorized call detail endpoint. Call data
is restricted to business owners and admins; regular members cannot read it.

### Webhook authentication

The endpoint requires a Bearer credential and does not accept unauthenticated
requests:

1. In Vapi, create a Custom Credential for the server URL.
2. Configure it to send the token in the `Authorization` header and enable the
   Bearer prefix.
3. Set that token to the value configured as `VAPI_WEBHOOK_SECRET` on this
   backend.
4. Attach the credential to the effective server URL configuration (organization,
   assistant, or phone number), then save and test the configuration.

The backend expects `Authorization: Bearer <VAPI_WEBHOOK_SECRET>`. An unset
backend secret returns `503`; a missing, malformed, or mismatched credential
returns `401`. Diagnostic logs record only whether the header was present,
whether the Bearer scheme was received, and whether the credential matched.
They do not log the credential or caller data.

Vapi's server-authentication documentation notes that organization credentials
are not automatically attached when a server URL is supplied in an API request.
For that configuration, provide the credential reference in the server
configuration itself. A `401` means the deployed secret and received
Authorization credential do not match (or Vapi did not attach the expected
Bearer credential); confirm the effective URL scope and Bearer-prefix setting
in the Vapi dashboard rather than disabling authentication.

## Current scope

Authentication, profile and password changes, business and member management,
business onboarding, supported-service selection, managed assistant and phone
provisioning, activation status, authenticated webhook processing, call history,
health checks, billing usage metering, plan administration, and billing views
are implemented. Business-facing billing data is tenant-scoped; platform billing
administration is restricted to UUIDs in `BILLING_ADMIN_USER_IDS`.

### Billing integration status

The database-backed billing foundation records plans, subscriptions, provider
events, invoices, payments, per-period usage, and audited minute grants. Usage
is derived from finalized Vapi call records: exact billable seconds are summed
for the applicable billing period and rounded up once to whole minutes.
Inbound calls continue to be answered after the included allowance is exhausted;
the allowance blocks new phone provisioning, not inbound service. No automatic
overage charge is created.

Payment actions are intentionally unavailable until a concrete payment
provider adapter is implemented and configured. The current provider interface
is an integration boundary, not a live gateway. The normalized signed webhook
contract must be mapped and verified against the chosen provider's official
signature and event format before production use. Provider events are not a
substitute for reconciliation with that provider. Do not treat configured
prices, locally created plans, or a successful API response as proof that a
payment was collected.

Set `BILLING_ADMIN_USER_IDS` to a comma-separated list of trusted, already
provisioned user UUIDs; do not use email addresses or allowlist values supplied
by customers. Apply schema changes using Alembic after verifying the target
database, and test migrations against a disposable or isolated database before
production rollout.

Appointments, call transfer, structured contact/lead capture, and email-based
invitation delivery are not implemented.

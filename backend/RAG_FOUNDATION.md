# Hybrid RAG foundation

This document covers business-scoped knowledge ingestion, hybrid retrieval,
and the authenticated Vapi knowledge-search tool. LLM answer generation is
intentionally not part of this phase.

## Requirements

The backend runs in `backend/.venv`. The RAG dependencies are pinned in
`requirements.txt`. The PyTorch dependency is the CPU wheel; install using the
CPU wheel index to avoid GPU/CUDA packages:

```bash
cd backend
.venv/bin/python -m pip install --only-binary=:all: \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  -r requirements.txt
```

The embedding model is loaded lazily from Hugging Face when the first document is
ingested. The first request therefore requires network access to download the
model unless it is already cached.

## PostgreSQL and migration

Install the PostgreSQL 18 pgvector server package for the host OS before
migrating. On Ubuntu 26.04, the package is `postgresql-18-pgvector`. A database
administrator must enable the extension in the development database before the
application role can apply the migration:

```bash
sudo -u postgres psql -d <development-database> \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

The migration also contains `CREATE EXTENSION IF NOT EXISTS vector` as a
declaration of its requirement; it cannot grant the application role elevated
extension privileges. It creates the knowledge tables and a cosine HNSW index.
Check the current revision and upgrade only the intended development database:

```bash
cd backend
.venv/bin/alembic current
.venv/bin/alembic upgrade head
```

The migration downgrade removes its tables and indexes. It intentionally leaves
the `vector` extension installed because it may have existed before this
migration or be used by other database objects.

## Configuration

Knowledge settings are optional environment settings with conservative defaults:

| Setting | Default | Description |
|---|---:|---|
| `KNOWLEDGE_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Sentence Transformers model identifier |
| `KNOWLEDGE_EMBEDDING_DIMENSIONS` | `384` | Fixed to this model's output size |
| `KNOWLEDGE_EMBEDDING_BATCH_SIZE` | `8` | CPU embedding batch size |
| `KNOWLEDGE_CHUNK_SIZE` | `1000` | Maximum chunk characters |
| `KNOWLEDGE_CHUNK_OVERLAP` | `150` | Approximate overlap in characters |
| `KNOWLEDGE_MAX_UPLOAD_BYTES` | `10485760` | Maximum uploaded file size |
| `KNOWLEDGE_MAX_EXTRACTED_CHARACTERS` | `1000000` | Maximum extracted text |
| `KNOWLEDGE_MAX_PDF_PAGES` | `500` | Maximum PDF page count |

The current model and schema are fixed at 384 dimensions. Changing the model to
one with another output dimension requires a reviewed schema migration and
re-embedding existing chunks.

## Ingestion flow

`POST /api/v1/knowledge/documents?business_id=<uuid>` accepts a multipart `upload`
and optional `source`. It requires an authenticated business owner or admin
with operational-resource management permission. The service validates
filename, extension, MIME type and size; extracts UTF-8 text or PDF page text;
cleans and chunks the text; generates normalized CPU embeddings; and commits all
chunks with the document completion status in one transaction. Extraction and
embedding failures persist a failed document status without partial chunks.
Re-uploading the same file for the same business retries a failed ingestion or
returns the already completed document.

Supported formats are PDF (`application/pdf`), TXT (`text/plain`), and Markdown
(`text/markdown`, `text/x-markdown`, or `text/plain`). Uploaded content is read
in memory only; it is not executed or written to a filesystem. Source files are
not retained, so retry requires uploading the file again.

## Tests

Run the unit/API tests with:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_knowledge.py \
  tests/test_knowledge_retrieval.py
```

PostgreSQL retrieval and integrity tests run only when
`VAPI_TEST_DATABASE_URL` points to a local, isolated database named
`vapi_test_*` where pgvector is enabled:

```bash
.venv/bin/python -m pytest tests/test_knowledge_postgres.py
```

SQLite tests cover extraction, chunking, mocked embeddings, ingestion
transactions, API authorization, retrieval ranking, and context construction.
PostgreSQL integration tests exercise cosine similarity, full-text search,
hybrid retrieval, tenant isolation, and document-status filtering in a
temporary schema. SQLite is not a substitute for PostgreSQL-specific search
behavior.

## Current limitations

`POST /api/v1/knowledge/search?business_id=<uuid>` returns authorized ranked
chunks using pgvector cosine distance and PostgreSQL simple-language full-text
search. It merges the independently ranked lists using reciprocal rank fusion
with `k=60`. Both queries require matching chunk/document business IDs and a
completed parent document. The request body contains `query` and optional
`top_k`, never a tenant ID.

The context builder formats ranked whole chunks under a character budget and
returns chunk/document/page references for the included content. Search does
not generate LLM answers or provide a structured business service catalog.

## Vapi knowledge-search tool

Newly created assistants receive the `search_business_knowledge` function
through their model `tools` configuration. Vapi sends `tool-calls` requests to
the tool's dedicated server URL:

```text
POST /api/v1/integrations/vapi/knowledge-tool
Authorization: Bearer <VAPI_WEBHOOK_SECRET>
```

The endpoint also accepts multiple function calls in one Vapi message. It
resolves the assistant and/or phone-number IDs from the Vapi call object to one
unique, enabled configuration with a matching active provisioning record.
Business IDs in tool arguments and arbitrary request metadata are ignored.
The existing webhook authorization mechanism is used, and the endpoint
delegates to `retrieve_hybrid()` with the resolved business ID. Both the
retriever's SQL filters and a response-scope check enforce tenant isolation.

Successful tool results are flat JSON strings containing only a `found` flag,
retrieved text, and source filenames. Internal UUIDs, database fields, source
URLs, and embeddings are not returned. Empty retrieval tells the assistant
there is no verified knowledge and not to guess. Retrieval failures use Vapi's
per-call `error` field with a safe fallback message. The endpoint limits a
batch to eight calls, uses a five-chunk retrieval limit, and caps context at
6,000 characters; the configured Vapi tool server timeout is 20 seconds.

The existing status-update and end-of-call-report webhook remains unchanged.
For an already-existing Vapi-native assistant, adding this tool to the backend
payload does not alter the live assistant. Add the Function tool in the Vapi
Dashboard using the endpoint above, the required `query` string argument, and
the same Bearer credential configured for `VAPI_WEBHOOK_SECRET`. Verify the
assignment and configuration in the Dashboard. No assistant update or provider
API call is made by this integration.

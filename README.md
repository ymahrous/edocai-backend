<div align="center">

# edocAI-backend

**Asynchronous AI-powered document processing API.**

*Transform unstructured invoices and receipts into structured JSON, at scale, without timeouts.*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Celery](https://img.shields.io/badge/Celery-5.3-green?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-2.5%20Flash-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

</div>

---

## The Problem

Standard web APIs have a hard limit: if a server doesn't respond within ~30 seconds, the connection is dropped. AI document extraction can easily take 10–30 seconds per file. Naive implementations fail under real-world load.

edocAI solves this with an asynchronous producer-consumer architecture: the API responds in ~100ms, and a background worker does the heavy lifting independently.

---

## How it works

```
Client uploads file
       │
       ▼
┌──────────────┐    saves file     ┌─────────────────┐
│   FastAPI    │ ─────────────────▶│ Supabase Storage│
│  (Producer)  │                   └─────────────────┘
│              │    writes PENDING ┌─────────────────┐
│              │ ─────────────────▶│  Neon Postgres  │
│              │                   └─────────────────┘
│              │    pushes job     ┌─────────────────┐
└──────────────┘ ─────────────────▶│  Upstash Redis  │
                                   └────────┬────────┘
                                            │ pulls job
                                            ▼
                                   ┌─────────────────┐
                                   │  Celery Worker  │
                                   │  (Consumer)     │
                                   │                 │
                                   │  1. Download    │
                                   │  2. Rasterize   │──▶ PyMuPDF (PDF → PNG)
                                   │  3. AI Extract  │──▶ Gemini 3.5 flash (vision)
                                   │  4. COMPLETED   │
                                   └─────────────────┘
```

### AI Extraction Pipeline

The worker downloads the uploaded file from object storage. If it's a PDF, the first page is rasterized into a high-resolution PNG using PyMuPDF; image files pass through unchanged. The resulting image is sent to **Google Gemini 3.5 flash** via the Interactions API for vision-based extraction, returning strictly typed JSON (`vendor`, `total_amount`, `date`).

If extraction fails for any reason, the document's status is set to `FAILED` and the error is logged — there is currently no secondary extraction strategy.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **API Framework** | FastAPI | RESTful endpoints, automatic OpenAPI docs |
| **ORM** | SQLModel | Typed models over SQLAlchemy |
| **Task Queue** | Celery 5.3 | Distributed async workers |
| **Message Broker** | Upstash Redis | Serverless Redis over TLS |
| **Database** | Neon (Serverless Postgres) | Managed relational storage |
| **Object Storage** | Supabase Storage | S3-compatible file store |
| **AI Inference** | Google Gemini 2.5 Flash | Multimodal vision extraction |
| **PDF Processing** | PyMuPDF (fitz) | Rasterizes PDF pages to PNG |
| **Auth** | python-jose (JWT) + bcrypt | Stateless bearer-token authentication |
| **Logging** | structlog | Structured JSON logs |
| **Hosting** | Railway | Docker-based, auto-deploy on push |
| **Testing** | pytest | Unit and integration tests |

---

## API Reference

All routes are prefixed with `/api/v1`.

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/signup` | Create an account. Returns a bearer token. |
| `POST` | `/auth/login` | Authenticate. Returns a bearer token. |
| `POST` | `/auth/change-password` | Change password. Requires current password. |
| `DELETE` | `/auth/delete` | Permanently delete the authenticated user. |

### Documents

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload/` | Upload a document (multipart). Returns immediately; processing happens in the background. |
| `GET` | `/documents/` | List all documents owned by the authenticated user. |
| `GET` | `/extraction/{document_id}` | Retrieve the structured extraction for a completed document. 404 if not yet processed. |

All document and extraction routes require an `Authorization: Bearer <token>` header and are scoped to the authenticated user via `owner_id`.

---

## Security Model

**Authentication**: Stateless JWTs signed with HS256, verified on every request via FastAPI's `HTTPBearer` dependency. No server-side sessions.

**Data Isolation**: Every document query is filtered by `owner_id == current_user.id` at the query level. Users cannot list or fetch documents they don't own.

**Password Hashing**: Uses the `bcrypt` library directly (not `passlib`), generating a fresh salt per password via `bcrypt.gensalt()`.

---

## Project Structure

```
edocai-backend/
├── main.py              # FastAPI app, CORS, document/extraction routes
├── auth.py               # JWT creation/decoding, password hashing
├── auth_routes.py         # Signup, login, change-password, delete routes
├── dependencies.py        # get_current_user() auth dependency
├── models.py              # SQLModel: User, Document, Extraction
├── database.py            # Engine setup, session dependency
├── storage_client.py      # Supabase Storage upload client
├── celery_app.py          # Celery app config, Upstash Redis broker
├── tasks.py                # process_document_task — the Celery consumer
├── ai_extractor.py         # PDF rasterization + Gemini extraction pipeline
├── tests/                  # pytest suite
├── conftest.py
├── pytest.ini
├── requirements.txt
├── Procfile                # Railway process definitions (web, worker)
├── railway.toml
├── Makefile
└── .env.example
```

---

## Local Development

### Prerequisites

- Python 3.11+
- A Postgres database (e.g. [Neon](https://neon.tech))
- A Redis instance (e.g. [Upstash](https://upstash.com))
- A [Supabase](https://supabase.com) project with a storage bucket
- A [Google AI Studio](https://aistudio.google.com) API key

### Setup

```bash
git clone https://github.com/ymahrous/edocai-backend.git
cd edocai-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your database, Redis, Supabase, and Gemini credentials.

### Running

Run the API:
```bash
uvicorn main:app --reload
```

Run the Celery worker (separate terminal):
```bash
celery -A celery_app worker --loglevel=info
```

Or use the `Makefile` if targets are defined for both.

API docs are available at `http://localhost:8000/docs` (FastAPI's automatic Swagger UI).

---

## Testing

```bash
pytest
```

Test configuration lives in `pytest.ini`, with shared fixtures in `conftest.py`.

---

## Deployment

1. Connect this repository to [Railway](https://railway.app).
2. Railway reads `Procfile` and deploys two services from it: `web` (FastAPI) and `worker` (Celery).
3. Set environment variables in the Railway dashboard — see `.env.example` for the full list.
4. `railway.toml` configures build/deploy behavior.

### Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string (Neon) |
| `SECRET_KEY` | JWT signing secret |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon/public API key |
| `SUPABASE_BUCKET_NAME` | Storage bucket for uploaded documents |
| `UPSTASH_REDIS_ENDPOINT` | Upstash Redis host |
| `UPSTASH_REDIS_PASSWORD` | Upstash Redis password |
| `GEMINI_API_KEY` | Google AI Studio API key |

---

## Key Decisions

**Why Celery + Redis instead of background threads or `asyncio.create_task`?** Long-running AI inference work needs to survive process restarts and scale independently of the API's request-handling capacity. A dedicated worker process, decoupled via a message broker, allows the `web` and `worker` Railway services to scale separately.

**Why raw `bcrypt` instead of `passlib`?** `passlib`'s bcrypt backend has had compatibility issues on some platforms. Calling `bcrypt` directly removes a dependency layer and keeps the hashing logic explicit.

**Why rasterize PDFs instead of extracting text directly?** Gemini's vision capability handles messy, non-machine-readable invoice layouts (scanned documents, photos of receipts) far better than raw text extraction, which fails on receipts with unusual formatting or embedded images.

**Why SQLModel over plain SQLAlchemy?** SQLModel combines Pydantic validation with SQLAlchemy's ORM in a single model definition, reducing duplication between API schemas and database tables in a small codebase like this one.

---

## Limitations

- No password-reset flow for forgotten passwords (only authenticated password change)
- No endpoint to delete individual documents
- No retry or fallback strategy if Gemini extraction fails — the document is simply marked `FAILED`
- CORS currently allows all origins (`allow_origins=["*"]`); should be restricted to the deployed frontend domain before wider production use

---

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change. See [CONTRIBUTING.md](./CONTRIBUTING.md).

---

## License

[MIT](./LICENSE)

---

<div align="center">

Built with FastAPI · Celery · Google Gemini · Supabase · Neon · Upstash · Railway

</div>

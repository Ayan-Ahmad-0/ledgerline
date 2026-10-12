# Ledgerline Automated Invoice Extraction Pipeline

Ledgerline is an automated pipeline for invoice and receipt extraction, emphasizing mathematically validated data, calibrated confidence routing, and append-only auditability. It enforces a strict subtotal consistency rule ($\sum \text{qty} \times \text{unit\_price} \equiv \text{subtotal} \pm 1.00$), bounds out-of-distribution Expected Calibration Error ($\text{ECE} \le 0.10$), and guarantees zero duplicate LLM network invocations on re-runs.

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?style=flat&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688?style=flat&logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon%20Async-4169E1?style=flat&logo=postgresql)
![Google Gemini](https://img.shields.io/badge/LLM-Gemini%20Flash-4285F4?style=flat&logo=google)
![Cloudflare R2](https://img.shields.io/badge/Storage-Cloudflare%20R2-F38020?style=flat&logo=cloudflare)
![Pydantic](https://img.shields.io/badge/Pydantic-v2.7-E92063?style=flat&logo=pydantic)

---

## 📋 Overview

1. **Ingest** PDF and image documents via drop folder or REST API with Sha256 content deduplication.
2. **Classify** pages into digital native text (cheap path at $0.0005/page) vs. raster scans (multimodal vision path at $0.015/page).
3. **Stitch** multi-page tabular boundaries using 1D x-interval clustering and drop repeated running headers via normalized token hashing.
4. **Validate** arithmetic consistency ($\sum \text{quantity} \times \text{unit\_price} \equiv \text{subtotal} \pm 1.00$); park failures directly to dead-letter tables with zero dirty writes.
5. **Calibrate** confidence scores using multi-signal Isotonic Regression with Platt scaling baseline, guaranteeing out-of-distribution $\text{ECE} \le 0.10$.
6. **Manage** human review workflows with row-level leases (`SELECT FOR UPDATE SKIP LOCKED`) and optimistic concurrency (HTTP 409 diffs).
7. **Guard** batch runs with a 50-document canary gate that aborts the pipeline if macro-$F_1$ drops by $> 2.0$ points against the rolling baseline.

Measured across 45 automated unit tests and benchmarked against 1,000 CORD receipts and 8 synthetic layout suites. Deployed locally or as a lightweight containerized service.

---

## 🏗️ Architecture

Raw Document (PDF/Image) ──► Ingress Guardrails ──► Smart Router ──► Table Stitcher ──► Math Validator ──► Calibrator ──► Neon PostgreSQL / R2

| Stage | Purpose | Format | Storage |
|---|---|---|---|
| Ingress | Validates size, encryption, and page limits | Raw binary stream | Cloudflare R2 (`raw/{prefix}/{hash}/`) |
| Classification | Discriminates digital PDF vs scanned raster | Text stream / PNG | Memory buffer |
| Extraction | Extracts fields via cheap text vs vision LLM | Pydantic JSON schema | Page cache (`page_hash:ext_v:pmt_v`) |
| Stitching | Clusters horizontal coordinates across pages | Bounding box tuples | Stitched row models |
| Validation | Proves line-item arithmetic consistency | Decimal / Currency | Clean DB or `dead_letter` table |
| Calibration | Normalizes confidence via Isotonic regression | 5D feature vectors | `fields.confidence_calibrated` |
| Persistence | Appends temporal audit trail with time travel | Relational tables | Neon PostgreSQL |

### Architecture Diagram
<!-- Add: images/architecture_overview.png -->
![Architecture Diagram](images/architecture_overview.png)

### Tech Stack
- **Database**: PostgreSQL 16 on Neon with asyncpg connection pooling (10 connections, 20 max overflow).
- **Blob Storage**: Cloudflare R2 S3-compatible object storage with in-memory fallback.
- **Backend**: FastAPI with async SQLAlchemy 2.0, Pydantic v2 structured outputs, and Starlette request ID middleware.
- **Extraction & OCR**: `pdfplumber` for digital vector PDF text, Tesseract OCR for raster scans, Google Gemini (`gemini-3-flash-preview`).
- **ML / Calibration**: Scikit-Learn Isotonic Regression and Logistic Regression (Platt scaling).
- **CI / CD**: GitHub Actions with Break-It fault injection testing and mutmut mutation testing.

---

## 📊 Extraction Audit & Review UI

<!-- Add: images/ledgerline_extraction_demo.png -->
![Ledgerline Extraction Demo](images/ledgerline_extraction_demo.png)

<!-- Add: images/review_queue_diff.png -->
![Review Queue Collision Diff](images/review_queue_diff.png)

---

## 🔄 Pipeline Flow

```
Document Ingest
   │
   ▼
[Check Guardrails] ──► (Invalid: 0-byte, encrypted, >100 pgs) ──► Write to dead_letter
   │
   ▼ (Valid)
[Compute Hash & Idempotency Key]
   │
   ▼
[Check Page Cache] ──► (Cache Hit: 0 network calls) ─────────────► Return Cached Extraction
   │
   ▼ (Cache Miss)
[Route Page]
   ├── Digital Native ──► Native Layout + Text Model ($0.0005)
   └── Scanned Raster  ──► OCR + Multimodal Vision ($0.0150)
   │
   ▼
[Stitch Multi-Page Tables] (1D X-interval clustering; drop token-hashed headers)
   │
   ▼
[Validate Math] ──────► (|Sum - Subtotal| > 1.00) ───────────────► Write to dead_letter (Abort clean write)
   │
   ▼ (|Sum - Subtotal| <= 1.00)
[Calibrate Confidence] (5D features -> Isotonic Regression)
   │
   ▼
[Persist Clean Tables] (documents, pages, extraction_versions, fields, field_features)
   │
   ▼
[Update CostMeter & RunLedger]
```

*Note: All extraction stages are fully idempotent. Re-running the batch against identical documents executes exactly zero external LLM API calls.*

---

## ✅ Evaluation Results

Evaluated across two isolated benchmarks:
1. **Synthetic Multi-Layout Benchmark**: 8 distinct invoice layouts generated with `reportlab` and `Faker`, degraded to 150 DPI with skew, shadow, and Gaussian noise. Scored on Vendor, Date, and Math.
2. **CORD Benchmark**: 1,000 real-world receipt photographs. Scored strictly on `line_items`, `subtotal`, `tax`, and `total` (unblended).

| Dataset | Sample Size | Field Macro-F1 | Mean Latency | Mean Cost / Doc | Calibration ECE |
|---|---|---|---|---|---|
| Synthetic Clean (Layouts 1–4) | 200 docs | 0.985 | 320 ms | $0.0005 | 0.042 |
| Synthetic Degraded (Layouts 5–7) | 200 docs | 0.948 | 410 ms | $0.0012 | 0.068 |
| Synthetic OOD Holdout (Layout 8) | 100 docs | 0.924 | 460 ms | $0.0015 | 0.089 |
| CORD Receipts (Line items & Math) | 1,000 docs | 0.912 | 580 ms | $0.0078 | 0.094 |

<!-- Add: images/calibration_reliability_diagram.png -->
![Calibration Reliability Diagram](images/calibration_reliability_diagram.png)

**Instructive Failure & Resolution**: When table rows split across page boundaries on multi-page invoices, running table headers on subsequent pages were initially parsed as regular line items, corrupting the subtotal math sum by hundreds of dollars. The failure was resolved by implementing normalized token SHA-256 fingerprinting (`compute_header_fingerprint`), which detects recurring header structures on Page 2+ and drops them before cell interval clustering.

---

## 🗂️ Database Schema

| Table Name | Primary Purpose | Key Columns / Constraints |
|---|---|---|
| `documents` | Master file catalog and deduplication | `id`, `file_hash` (unique idx), `r2_storage_key`, `page_count` |
| `pages` | Page-level layout and routing classification | `document_id`, `page_number`, `page_hash`, `is_native_text` |
| `extraction_versions` | Append-only temporal history for time-travel queries | `valid_from`, `valid_to`, `is_current`, `idempotency_key` |
| `fields` | Normalized field values and calibrated confidence | `raw_value`, `normalized_value` (JSONB), `confidence_calibrated` |
| `field_features` | 5D feature vectors used for confidence calibration | `ocr_char_confidence`, `token_probability`, `math_validation_passed` |
| `review_items` | Human review queue with row-level leases | `version`, `status`, `leased_by`, `lease_expires_at` (`SKIP LOCKED`) |
| `run_ledger` | Pipeline spine batch execution history | `run_id`, `watermark`, `canary_f1_score`, `baseline_f1_score` |
| `dead_letter` | Parked unrecoverable documents | `file_hash`, `failure_reason`, `error_details` (JSONB), `resolved` |
| `cost_meter` | Live inference token and USD cost auditing | `routing_path`, `input_tokens`, `output_tokens`, `estimated_cost_usd` |
| `backfill_progress` | Resumable zero-downtime migration tracker | `migration_name`, `last_processed_id`, `rows_processed`, `is_completed` |

---

## 🔒 Reliability & Key Design Decisions

- **Append-Only Time Travel** — Extraction results are never overwritten in-place. The `extraction_versions` table maintains `valid_from` and `valid_to` timestamps, allowing temporal `as_of(timestamp)` audit queries.
- **Optimistic Concurrency & Collision Diffs** — Human review conflicts return HTTP 409 Conflict alongside a calculated field-level diff (`compute_field_diff`) showing server state vs. submitted payload.
- **Lease Expiry Reaper** — Background worker (`app/queue/reaper.py`) queries expired leases using `SELECT ... FOR UPDATE SKIP LOCKED` and resets stale items back to `pending`.
- **Zero-Network Idempotency** — Page extractions are keyed on `hash(page_content) + extractor_version + prompt_version`. Batch re-runs execute with zero network calls and zero cost.
- **Fail-Safe Parking** — Documents with failing arithmetic ($\sum \ne \text{subtotal}$) are immediately diverted to `dead_letter`. No invalid records ever reach clean reporting tables.
- **Break-It Fault Gates** — Tests are verified with deliberate 1-line fault injection branches (`FAULT_MULTI_PAGE_STITCH`, `FAULT_CALIBRATION`, `FAULT_IDEMPOTENCY`, `FAULT_CANARY_GATE`) that must turn CI red.

---

## 📁 Repository Structure

```
ledgerline/
├── app/
│   ├── api/
│   │   ├── auth.py             # X-API-Key security header dependency
│   │   ├── middleware.py       # Request ID propagation & execution timer middleware
│   │   └── router.py           # FastAPI routes: /extract, /reviews/lease, /ledger
│   ├── calibration/
│   │   ├── ece.py              # 10 equal-mass bin Expected Calibration Error calculator
│   │   ├── features.py         # 5D feature vector builder (token, OCR, layout, math)
│   │   └── fit.py              # MultiSignalCalibrator with Isotonic regression & Platt scaling
│   ├── core/
│   │   ├── config.py           # Pydantic BaseSettings loading .env configuration
│   │   ├── logging.py          # Structured JSON logger with request_id context
│   │   ├── request_id.py       # ContextVar request ID generator
│   │   ├── sentry.py           # Sentry error monitoring integration
│   │   └── storage.py          # Cloudflare R2 S3-compatible client with in-memory fallback
│   ├── db/
│   │   ├── migrations/         # Alembic migration scripts and env configuration
│   │   └── session.py          # SQLAlchemy 2.0 schema models and async sessionmaker
│   ├── extract/
│   │   ├── llm_adapter.py      # LLM adapter with live Gemini API & page-level caching
│   │   ├── native.py           # Digital PDF layout and text extractor (pdfplumber)
│   │   ├── ocr.py              # Scanned page OCR extractor with character confidence
│   │   ├── router.py           # Smart routing: cheap text ($0.0005) vs vision ($0.015)
│   │   └── stitch.py           # 1D x-interval clustering & token-hash header dropping
│   ├── pipeline/
│   │   ├── batch.py            # Batch drop folder processor with ingress guardrails
│   │   ├── canary.py           # 50-document canary quality gate with 2-point F1 drop alert
│   │   └── ledger.py           # Run ledger tracking and rolling baseline computation
│   ├── prompts/
│   │   └── v1/
│   │       ├── fewshot.json    # Cached static few-shot extraction examples
│   │       └── system.txt      # Versioned financial extraction system instructions
│   ├── queue/
│   │   ├── leases.py           # Row-level leasing with SELECT FOR UPDATE SKIP LOCKED
│   │   └── reaper.py           # Daemon reclaiming expired review item leases
│   ├── scoring/
│   │   ├── comparators.py      # Typed comparators: Money, Date ambiguity, String legal suffixes
│   │   └── metrics.py          # Macro F1 scorer enforcing CORD unblended scoring rules
│   ├── timefiscal/
│   │   ├── fiscal.py           # Hardcoded fiscal calendar engine (Oct 1 start, 4 quarters)
│   │   └── tz.py               # UTC instant + source timezone model with DST resolution
│   └── main.py                 # FastAPI application factory and route mounting
├── scripts/
│   ├── backfill.py             # Resumable chunked database backfill with progress table
│   ├── load_cord.py            # CORD dataset loader with strict field isolation
│   └── make_invoices.py        # 8-layout synthetic generator with 150 DPI degradation
├── tests/
│   ├── test_breakit.py         # Break-It fault injection suite asserting red failures
│   ├── test_phase2_db_leases.py# Database schema and optimistic concurrency unit tests
│   ├── test_phase3_scoring_fiscal.py # Typed scoring, date ambiguity, and DST tests
│   ├── test_phase4_stitching_math.py # Multi-page stitching and subtotal math tests
│   ├── test_phase5_calibration_routing.py # Routing, zero-call cache, and ECE tests
│   ├── test_phase6_datasets.py # Synthetic generator and CORD loader tests
│   ├── test_phase7_pipeline_api.py # Ingress guardrails and canary gate tests
│   └── test_storage.py         # Cloudflare R2 upload, download, and fallback tests
├── .github/
│   └── workflows/
│       ├── batch.yml           # Scheduled pipeline trigger every 6 hours
│       ├── breakit.yml         # CI fault verification workflow
│       └── ci.yml              # Matrix test and coverage workflow
├── alembic.ini                 # Alembic migration configuration
├── pyproject.toml              # Project metadata, dependencies, mutmut, and pytest config
└── README.md                   # Project documentation
```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and configure credentials:

```dotenv
# Google Gemini API Key (https://aistudio.google.com/)
GEMINI_API_KEY="your-gemini-api-key"

# Application Runtime
ENV="development"
DEBUG="true"
PROJECT_NAME="Ledgerline"

# Neon PostgreSQL Connection Strings
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerline"
SYNC_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/ledgerline"

# Cloudflare R2 Storage (Optional: defaults to local in-memory store if unset)
R2_ACCOUNT_ID=""
R2_ACCESS_KEY_ID=""
R2_SECRET_ACCESS_KEY=""
R2_BUCKET_NAME="ledgerline-documents"
R2_ENDPOINT_URL=""

# API Authentication & Security
API_KEYS=["dev-ledgerline-key"]

# Pipeline Quality & Cost Thresholds
CANARY_SAMPLE_SIZE="50"
CANARY_MAX_F1_DROP="2.0"
MAX_PARK_RATE="0.03"
LEASE_TIMEOUT_SECONDS="300"
EXTRACTOR_VERSION="v1.0.0"
PROMPT_VERSION="v1"
```

### Connection Notes
- **Neon PostgreSQL**: When connecting to serverless Postgres on Neon, supply your pooled connection string for `DATABASE_URL`.
- **Cloudflare R2**: If R2 credentials remain empty, `app/core/storage.py` operates automatically in local memory mode for zero-configuration testing.

---

## 🚀 Getting Started

### 1. Clone & Install
```bash
git clone https://github.com/Ayan-Ahmad-0/ledgerline.git
cd ledgerline
pip install -e .[dev]
```

### 2. Run Database Migrations
```bash
alembic upgrade head
```

### 3. Generate Synthetic Benchmark Datasets
```bash
python scripts/make_invoices.py
python scripts/load_cord.py
```

### 4. Execute Full Verification Test Suite
```bash
pytest tests/ -v
```

### 5. Start Local Application Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

| Service | Endpoint | Description |
|---|---|---|
| API Documentation | `http://localhost:8000/docs` | Interactive OpenAPI Swagger UI |
| Health Check | `http://localhost:8000/api/v1/health` | Operational health probe |
| Document Extraction | `http://localhost:8000/api/v1/extract` | Multipart upload extraction |
| Review Lease Queue | `http://localhost:8000/api/v1/reviews/lease` | Lease acquisition endpoint |
| Run Ledger Status | `http://localhost:8000/api/v1/ledger` | Batch pipeline execution history |

---

## 🚧 Challenges Solved

- **Repeated Headers Corrupting Line Items** — *Symptom*: Multi-page consulting invoices produced line-item sums double the true subtotal. *Diagnosis*: Subsequent pages repeated table column headers, which were parsed as line-item text. *Fix*: Implemented token-hash fingerprinting (`compute_header_fingerprint`) that detects recurring headers across page splits and drops them prior to extraction.
- **Overconfident Token Probabilities** — *Symptom*: Raw LLM token probabilities hovered at 0.95–0.99 even when field values were incorrect. *Diagnosis*: Language models exhibit severe overconfidence on structured outputs. *Fix*: Fit a multi-signal Isotonic Regression calibrator on held-out layout and vendor data, reducing out-of-distribution ECE from 0.28 to $\le 0.089$.
- **Review Item Lock Contention** — *Symptom*: Concurrent review workers were assigned identical review items or overwrote each other's submissions. *Diagnosis*: Absence of row-level lock skipping and optimistic version validation. *Fix*: Implemented `SELECT ... FOR UPDATE SKIP LOCKED` for lease acquisition and added integer version checking that returns HTTP 409 with field-level diffs on collision.
- **Unbounded Document Parser Crashes** — *Symptom*: Password-protected or 400-page input PDFs exhausted memory and threw unhandled worker exceptions. *Diagnosis*: Ingestion occurred prior to file sanity checks. *Fix*: Implemented strict pre-extraction ingress guardrails (`inspect_file_guardrails`) that reject 0-byte, encrypted, or oversized documents and park them to `dead_letter` without touching extraction models.

---

## 🛠️ Future Improvements

- **Webhooks & Event Streaming** — Dispatch Apache Kafka / Cloudflare Queue events upon batch completion and canary degradation alerts.
- **Active Learning Queue** — Automatically surface unreviewed documents near the calibration decision boundary ($0.45 \le p \le 0.60$) directly to the top of human review queues.
- **Multi-Currency Dynamic Conversion** — Incorporate ECB real-time daily currency exchange tables to normalize foreign currency invoices to base reporting currency.

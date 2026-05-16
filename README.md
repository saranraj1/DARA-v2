# 🤖 DARA — Distributed Architecture Root-cause Agent

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-5-008CC1?style=for-the-badge&logo=neo4j&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**An autonomous, production-grade AI pipeline that detects software bugs, traces root causes, generates verified patches, and submits GitHub Pull Requests — all without human intervention.**

[Features](#-features) · [Architecture](#-architecture) · [Quick Start](#-quick-start) · [Configuration](#-configuration) · [API Reference](#-api-reference) · [Pipeline Stages](#-pipeline-stages) · [Contributing](#-contributing)

</div>

---

## 🌟 Overview

**DARA** (Distributed Architecture Root-cause Agent) is an end-to-end autonomous bug resolution system. When an error is ingested — via OpenTelemetry traces, Slack alerts, GitHub webhooks, or direct API — DARA spins up a multi-agent pipeline that:

1. **Diagnoses** the root cause using LLM-powered analysis and vector-search context
2. **Generates** a unified diff patch using a dedicated FixerAgent
3. **Validates** the fix in an isolated Docker sandbox (with iterative self-healing)
4. **Reviews** the fix for quality and security (Bandit + Semgrep)
5. **Submits** a GitHub Pull Request and sends a Slack notification

DARA is designed to handle **polyglot codebases** (Python, Go, TypeScript), integrates a **Neo4j blast-radius analyzer** to prevent cascading failures, and ships with **full observability** via Prometheus, Grafana, and Jaeger.

---

## ✨ Features

| Category | Capability |
|---|---|
| 🧠 **Multi-Agent AI** | Orchestrator, Debugger, Fixer, Reviewer, Strategy agents working in concert |
| 🔍 **Root-cause Analysis** | LLM reasoning augmented by semantic vector search (Qdrant) |
| 🛡️ **Security Auditing** | Bandit + Semgrep static analysis with automatic security-retry loop |
| 🧪 **Sandbox Validation** | Docker-isolated test execution with iterative fix refinement (≤3 iterations) |
| 📊 **Blast Radius Analysis** | Neo4j graph traversal to detect cross-service impact before merging |
| 🌐 **Polyglot AST Parsing** | Tree-sitter support for Python, Go, and TypeScript |
| 📡 **Multi-source Ingestion** | OpenTelemetry, GitHub webhooks, Slack, REST API |
| 🔄 **RLHF Feedback Loop** | PR outcome tracking for continuous strategy improvement |
| 📈 **Full Observability** | Prometheus metrics, Grafana dashboards, Jaeger distributed tracing |
| 🔐 **Multi-tenancy & Auth** | JWT-based authentication, role-based access control |
| ⚡ **Auto-approve** | High-confidence, low-risk fixes can be merged without human review |
| 📦 **Pattern Library** | Learned fix templates for recurring error classes (fast path, ≥85% success rate) |

---

## 🏗️ Architecture

```
                        ┌─────────────────────────────────────────────────┐
                        │                  DARA Platform                   │
                        │                                                   │
  Error Sources         │  ┌────────────┐     ┌───────────────────────┐   │
  ─────────────         │  │  Ingestion  │────▶│     Orchestrator       │   │
  • OTel Traces    ────▶│  │   Layer    │     │  (12-Stage Pipeline)  │   │
  • GitHub Webhook ────▶│  │            │     └───────────┬───────────┘   │
  • Slack Alert    ────▶│  │ Classifier │                 │               │
  • REST API       ────▶│  │ Normalizer │     ┌───────────▼───────────┐   │
                        │  │Deduplicator│     │      Agent Layer       │   │
                        │  └─────┬──────┘     │  DebuggerAgent        │   │
                        │        │             │  FixerAgent           │   │
                        │        ▼             │  ReviewerAgent        │   │
                        │  ┌─────────────┐    │  StrategyEvaluator    │   │
                        │  │  PostgreSQL  │    │  PatternMemory        │   │
                        │  │  (TimescaleDB)    └───────────┬───────────┘   │
                        │  └─────────────┘                │               │
                        │                     ┌───────────▼───────────┐   │
  Storage Layer         │  ┌─────────────┐    │   Validation Layer    │   │
  ─────────────         │  │   Qdrant    │    │  SandboxRunner(Docker)│   │
  • PostgreSQL          │  │(Vector DB)  │    │  SecurityAuditor      │   │
  • Redis               │  ├─────────────┤    │  StaticAnalyzer       │   │
  • Qdrant              │  │    Redis    │    │  TestRunner           │   │
  • Neo4j               │  │  (Cache/    │    └───────────┬───────────┘   │
  • MinIO               │  │   Queue)   │                 │               │
  • Elasticsearch       │  ├─────────────┤    ┌───────────▼───────────┐   │
                        │  │   Neo4j     │    │    Output Layer       │   │
  Observability         │  │ (Graph DB)  │    │  GitHub PR Creator    │   │
  ─────────────         │  ├─────────────┤    │  Slack Notifier       │   │
  • Prometheus          │  │   MinIO     │    └───────────────────────┘   │
  • Grafana             │  │(Object Store│                                 │
  • Jaeger              │  └─────────────┘                                 │
                        └─────────────────────────────────────────────────┘
```

### Agent Roles

| Agent | Responsibility |
|---|---|
| **Orchestrator** | Coordinates all 12 pipeline stages; handles escalation gates and auto-approve logic |
| **DebuggerAgent** | Analyzes error context and produces a `RootCauseResult` with confidence score |
| **FixerAgent** | Generates unified diff patches; supports security-constrained rewrites |
| **ReviewerAgent** | Code quality review + security audit (Bandit/Semgrep); outputs `ReviewResult` |
| **StrategyEvaluator** | Selects the optimal fix strategy based on historical success rates |
| **StrategyGenerator** | Proposes new fix strategies via LLM when no known pattern matches |
| **StrategyMonitor** | Tracks live strategy performance for A/B evaluation |
| **PatternMemory** | Caches successful fix templates; enables fast-path resolution for known errors |

---

## 🚀 Quick Start

### Prerequisites

- **Docker Desktop** 24+ (with Compose v2)
- **Python 3.11+**
- **Poetry** (dependency management)
- At least one LLM API key: [Groq](https://console.groq.com) (free, recommended) or [Google AI Studio](https://aistudio.google.com)

### 1. Clone the Repository

```bash
git clone https://github.com/saranraj1/DARA.git
cd DARA
```

### 2. Configure Environment

```bash
# Copy the template and fill in your values
cp .env.example .env
```

At minimum, set:
```env
GROQ_API_KEY=your_groq_key_here
GITHUB_APP_ID=your_app_id
GITHUB_PRIVATE_KEY_PATH=config/github_app.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret
SLACK_BOT_TOKEN=xoxb-your-slack-token
```

### 3. Start the Infrastructure

```bash
# Starts: PostgreSQL, Redis, Qdrant, Neo4j, Elasticsearch, MinIO, Prometheus, Grafana, Jaeger
docker compose -f docker-compose.dev.yml up -d

# Wait for all services to be healthy
docker compose -f docker-compose.dev.yml ps
```

### 4. Install Python Dependencies

```bash
poetry install
```

### 5. Run Database Migrations

```bash
poetry run alembic upgrade head
```

### 6. Start the DARA API Server

```bash
# Option A: Using uvicorn directly
poetry run uvicorn main:app --reload --port 8000

# Option B: Using the PowerShell launcher (Windows)
.\start_dara.ps1
```

### 7. Verify the Installation

```bash
# Health check
curl http://localhost:8000/health

# Run the demo pipeline
poetry run python run_demo.py
```

The API will be available at **http://localhost:8000** and the interactive docs at **http://localhost:8000/docs**.

---

## ⚙️ Configuration

All configuration is managed via environment variables. See [`.env.example`](.env.example) for the full reference.

### LLM Providers

DARA supports multiple LLM backends with automatic fallback:

| Variable | Description | Default |
|---|---|---|
| `LLM_MODE` | `"groq"` for API or `"local"` for Ollama | `local` |
| `LLM_MODEL_NAME` | Model name (e.g., `deepseek-coder-v2:16b`) | `deepseek-coder-v2:16b` |
| `GROQ_API_KEY` | Groq API key (primary, free tier available) | — |
| `GOOGLE_API_KEY` | Google AI Studio key (fallback) | — |
| `HUGGINGFACE_API_TOKEN` | HuggingFace token (embeddings fallback) | — |

### Pipeline Tuning

| Variable | Description | Default |
|---|---|---|
| `AUTO_MERGE_CONFIDENCE_THRESHOLD` | Minimum confidence for auto-PR merge | `0.88` |
| `MAX_CONTEXT_TOKENS` | LLM context window token cap | `8000` |
| `MAX_PIPELINE_RETRIES` | Max retries per agent stage | `3` |
| `SANDBOX_TIMEOUT_SECONDS` | Docker sandbox hard kill timeout | `120` |

### Service Ports

| Service | Port | URL |
|---|---|---|
| DARA API | `8000` | http://localhost:8000 |
| PostgreSQL | `5432` | — |
| Redis | `6379` | — |
| Qdrant | `6333` | http://localhost:6333/dashboard |
| Neo4j Browser | `7474` | http://localhost:7474 |
| Elasticsearch | `9200` | http://localhost:9200 |
| MinIO Console | `9001` | http://localhost:9001 |
| Prometheus | `9090` | http://localhost:9090 |
| Grafana | `3000` | http://localhost:3000 (admin/dara_dev) |
| Jaeger UI | `16686` | http://localhost:16686 |

---

## 🔄 Pipeline Stages

DARA's Orchestrator runs a deterministic 12-stage pipeline for every error:

```
Stage 1  → Load error from PostgreSQL
Stage 2  → Pattern library check (fast path if ≥85% historical success)
Stage 3  → Build context bundle (semantic search + file content)
Stage 4  → DebuggerAgent → RootCauseResult (with confidence score)
Stage 5  → Escalation gate (confidence < 0.35 → Slack escalation)
Stage 6  → FixerAgent → unified diff patches
Stage 6b → Blast Radius Analysis (Neo4j graph traversal)
Stage 7  → Sandbox validation (Docker) + iterative self-healing (≤3 loops)
Stage 7b → Static analysis (Ruff S*/E9* codes)
Stage 8  → ReviewerAgent + Security audit (Bandit + Semgrep)
           └─ HIGH severity → FixerAgent security-retry (≤2 retries)
Stage 9  → Persist fix to PostgreSQL
Stage 10 → Auto-approve gate (high confidence + low risk + sandbox passed)
Stage 11 → Slack notification
Stage 12 → GitHub PR creation
```

### Auto-Approve Conditions

A fix is automatically approved (no human review required) when **all** of the following are true:

- ✅ ReviewerAgent recommendation: `approve`
- ✅ Fix confidence ≥ `AUTO_MERGE_CONFIDENCE_THRESHOLD` (default: 0.88)
- ✅ Regression risk: `low`
- ✅ Sandbox validation: passed
- ✅ Docker sandbox tests: passed

### Escalation Triggers

| Condition | Action |
|---|---|
| Root cause confidence < 0.35 | Slack escalation + status: `escalated` |
| Strategy: `human_escalation` | Slack escalation + status: `escalated` |
| Blast radius: `critical` | Slack escalation + forced human review |
| Security blocked after 2 retries | Slack escalation + status: `security_blocked` |

---

## 📡 API Reference

Interactive API documentation is available at **http://localhost:8000/docs** (Swagger UI) and **http://localhost:8000/redoc** (ReDoc).

### Core Endpoints

#### Error Ingestion

```http
POST /api/v1/errors
Content-Type: application/json

{
  "error_class": "NullPointerException",
  "message": "Cannot read property 'id' of undefined",
  "stack_trace": "...",
  "file_path": "src/services/user.service.ts",
  "line_number": 42,
  "service": "user-service",
  "severity": "high",
  "commit_sha": "abc123",
  "branch": "main"
}
```

#### Trigger Pipeline

```http
POST /api/v1/fixes/{error_id}/run
```

#### Check Pipeline Status

```http
GET /api/v1/errors/{error_id}/status
```

#### Get Fix Details

```http
GET /api/v1/fixes/{fix_id}
```

#### Submit RLHF Feedback

```http
POST /api/v1/fixes/{fix_id}/feedback
Content-Type: application/json

{
  "outcome": "accepted",  // "accepted" | "rejected" | "modified"
  "feedback_notes": "Patch was correct but needed minor style changes"
}
```

### Webhook Endpoints

| Endpoint | Trigger |
|---|---|
| `POST /api/v1/webhooks/github` | GitHub App webhook events (push, PR, issue) |
| `POST /api/v1/webhooks/slack` | Slack event subscriptions |
| `POST /api/v1/webhooks/otel` | OpenTelemetry error traces |

### Admin Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Service health check |
| `GET /metrics` | Prometheus metrics scrape endpoint |
| `GET /api/v1/admin/strategies` | List all fix strategies with success rates |
| `GET /api/v1/admin/patterns` | View pattern library templates |
| `POST /api/v1/admin/patterns/promote` | Manually promote a fix to pattern library |

---

## 📁 Project Structure

```
DARA/
├── agents/                     # AI agent implementations
│   ├── orchestrator.py         # 12-stage pipeline coordinator
│   ├── debugger.py             # Root cause analysis agent
│   ├── fixer.py                # Patch generation agent
│   ├── reviewer.py             # Code review + security audit agent
│   ├── memory.py               # Pattern memory (fix template library)
│   ├── memory_consolidation.py # RLHF-driven memory distillation
│   ├── strategy_evaluator.py   # Strategy selection & A/B testing
│   ├── strategy_generator.py   # LLM-based strategy proposal
│   └── strategy_monitor.py     # Live strategy performance tracking
│
├── api/                        # FastAPI application layer
│   ├── main.py                 # App factory, middleware registration
│   ├── routers/                # Route handlers
│   │   ├── admin.py            # Admin & management endpoints
│   │   ├── auth.py             # JWT authentication
│   │   ├── errors.py           # Error ingestion endpoints
│   │   ├── fixes.py            # Fix management & RLHF feedback
│   │   ├── webhooks.py         # GitHub, Slack, OTel webhooks
│   │   ├── health.py           # Health check
│   │   └── metrics.py          # Prometheus scrape endpoint
│   ├── middleware/             # Logging, rate limiting, auth middleware
│   └── models/                 # Pydantic schemas
│
├── context/                    # Context retrieval & bundling
│   ├── builder.py              # Assembles context bundle for agents
│   └── retriever.py            # Semantic search via Qdrant
│
├── graph/                      # Neo4j graph intelligence
│   ├── blast_radius.py         # Cross-service impact analysis
│   ├── fault_propagation.py    # Fault propagation modeling
│   ├── topology.py             # Service dependency graph builder
│   └── queries.py              # Cypher query library
│
├── ingestion/                  # Error ingestion pipeline
│   ├── classifier.py           # Error severity & type classification
│   ├── normalizer.py           # Cross-source error normalization
│   ├── deduplicator.py         # Duplicate detection & merging
│   └── otel_receiver.py        # OpenTelemetry trace receiver
│
├── monitoring/                 # Observability
│   ├── metrics.py              # Prometheus counter/histogram definitions
│   └── anomaly_detector.py     # Statistical anomaly detection
│
├── output/                     # External integrations
│   ├── github_pr.py            # GitHub App PR creator
│   └── slack_notifier.py       # Slack message sender
│
├── patch/                      # Patch application utilities
│
├── storage/                    # Database clients
│   ├── postgres.py             # Async PostgreSQL (SQLAlchemy + asyncpg)
│   ├── redis_client.py         # Redis (state, pub/sub, caching)
│   ├── qdrant_client.py        # Qdrant vector DB client
│   └── neo4j_client.py         # Neo4j graph DB client
│
├── validation/                 # Fix validation layer
│   ├── engine.py               # Validation orchestrator
│   ├── sandbox_runner.py       # Docker sandbox test execution
│   ├── security_auditor.py     # Bandit + Semgrep analysis
│   ├── static_analyzer.py      # Ruff static analysis
│   └── test_runner.py          # Test suite execution
│
├── config/                     # Settings & secrets
│   └── settings.py             # Pydantic-settings configuration
│
├── migrations/                 # Alembic database migrations
├── tests/                      # Test suite
├── docker/                     # Docker infrastructure configs
├── grafana/                    # Grafana dashboard provisioning
├── prompts/                    # LLM prompt templates
├── scripts/                    # Utility & maintenance scripts
├── workers/                    # Celery background workers
│
├── main.py                     # Application entry point (uvicorn)
├── docker-compose.dev.yml      # Full local development stack
├── pyproject.toml              # Python project & tool configuration
├── prometheus.yml              # Prometheus scrape config
├── alembic.ini                 # Database migration config
└── .env.example                # Environment variables template
```

---

## 🧪 Testing

DARA includes a comprehensive test suite covering unit, integration, and end-to-end scenarios.

```bash
# Run all tests
poetry run pytest

# Run with verbose output
poetry run pytest -v

# Run a specific test file
poetry run pytest tests/test_orchestrator.py

# Run with coverage report
poetry run pytest --cov=. --cov-report=html

# Run end-to-end smoke tests (requires running infrastructure)
poetry run python run_e2e.py

# Run benchmarks
poetry run python run_benchmarks.py
```

### Test Configuration

Tests are configured in `pyproject.toml`:
- Coverage threshold: **30%** (infrastructure-heavy files excluded)
- Async mode: `auto`
- E2E and context tests excluded by default (require live infrastructure)

---

## 📊 Observability

DARA ships with a pre-configured observability stack:

### Metrics (Prometheus + Grafana)
- **`dara_pipelines_total`** — Total pipelines by status (fixed, escalated, security_blocked)
- **`dara_active_pipelines`** — Currently running pipelines
- **`dara_pipeline_duration_seconds`** — Pipeline latency histogram
- **`dara_fixes_generated_total`** — Fixes by strategy type
- **`dara_fixes_reviewed_total`** — Reviews by recommendation

Access Grafana at http://localhost:3000 (credentials: `admin` / `dara_dev`).

### Distributed Tracing (Jaeger)
All pipeline stages are instrumented with OpenTelemetry. View traces at http://localhost:16686.

### Structured Logging
DARA uses `structlog` for structured JSON logging. Logs are shipped to Elasticsearch and visualized in Kibana at http://localhost:5601.

---

## 🔐 Security

DARA implements multiple layers of security:

- **SecurityAuditor**: Runs Bandit and Semgrep on every generated patch before review
- **Security Retry Loop**: HIGH severity findings trigger up to 2 automatic fix rewrites with security constraints injected into the LLM prompt
- **Service Allowlist**: GitHub repo resolution validates service names against a known allowlist (prevents path traversal attacks, SEV-6)
- **JWT Authentication**: All API endpoints require valid JWT tokens
- **Docker Sandbox Isolation**: All validation runs in ephemeral Docker containers
- **Secret Management**: Production deployments support HashiCorp Vault integration

---

## 🌐 GitHub App Setup

1. Go to **GitHub Settings → Developer Settings → GitHub Apps → New GitHub App**
2. Set the webhook URL to your server: `https://your-domain.com/api/v1/webhooks/github`
3. Grant permissions: **Contents** (Read & Write), **Pull Requests** (Read & Write), **Issues** (Read)
4. Subscribe to events: **Push**, **Pull request**, **Issues**
5. Download the private key `.pem` file to `config/github_app.pem`
6. Note your App ID and installation ID
7. Set all values in `.env`

For local development, use [ngrok](https://ngrok.com) to tunnel webhooks:
```bash
ngrok http 8000
```

---

## 🤝 Contributing

We welcome contributions! Please read our contributing guidelines before submitting a PR.

### Development Workflow

1. **Fork** the repository
2. **Create** a feature branch: `git checkout -b feature/my-feature`
3. **Install** dev dependencies: `poetry install`
4. **Make** your changes, following the existing code style
5. **Run** linting: `poetry run ruff check .`
6. **Run** tests: `poetry run pytest`
7. **Commit** with a descriptive message
8. **Push** and open a Pull Request

### Code Style

DARA uses `ruff` for linting and formatting:

```bash
# Check
poetry run ruff check .

# Format
poetry run ruff format .
```

---

## 📋 Roadmap

- [ ] **Fine-tuning Export** — Export RLHF training data to MinIO for model fine-tuning (Phase 3 Week 19-20)
- [ ] **Multi-repo Support** — Single DARA instance managing multiple GitHub repositories
- [ ] **PagerDuty Integration** — Automatic incident creation for critical escalations
- [ ] **JIRA Integration** — Ticket creation and tracking for escalated bugs
- [ ] **Custom Strategy Plugins** — Plugin interface for domain-specific fix strategies
- [ ] **Web Dashboard** — React-based admin UI for pipeline monitoring

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

DARA is built on the shoulders of giants:

- [FastAPI](https://fastapi.tiangolo.com/) — High-performance async API framework
- [Qdrant](https://qdrant.tech/) — Vector similarity search engine
- [Neo4j](https://neo4j.com/) — Graph database for service topology
- [Tree-sitter](https://tree-sitter.github.io/) — Polyglot AST parsing
- [Semgrep](https://semgrep.dev/) — Static analysis for security
- [Prefect](https://www.prefect.io/) — Workflow orchestration
- [OpenTelemetry](https://opentelemetry.io/) — Distributed tracing

---

<div align="center">

*Autonomous bug resolution for the modern engineering team.*

</div>

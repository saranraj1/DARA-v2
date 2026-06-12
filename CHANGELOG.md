# CHANGELOG

All notable changes to DARA are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Security
- Removed hardcoded personal ngrok tunnel domain from `.env.example` (#1)
- Docker sandbox containers now run with `network_mode=none`, `cap_drop=ALL`, and `no-new-privileges` (#10)

### Added
- GitHub Actions CI pipeline: lint (ruff), unit tests (coverage ≥70%), demo smoke test (#5)
- `docker-compose.prod.yml` — production compose with all secrets externalized (#8)
- `scripts/validate_env.py` — startup env validator with actionable error messages (#7)
- `docs/confidence_threshold.md` — calibration guide for `AUTO_MERGE_CONFIDENCE_THRESHOLD` (#9)
- `CHANGELOG.md` — this file (#17)
- `scripts/retention_cleanup.py` — configurable data retention policy (#20)
- Admin UI documentation in `admin_ui/README.md` (#11)
- Multi-repo support via `KNOWN_GITHUB_REPOS` env var (documented in README) (#14)
- LLM cost tracking: `dara_llm_tokens_total` and `dara_llm_estimated_cost_usd_total` Prometheus metrics (#15)
- `[tool.poetry.extras]` `gpu` extra for `torch` dependency (#19)

### Changed
- `run_demo.py` — full rewrite: probes Postgres/Redis/Qdrant at startup; runs real persisted pipeline when available; graceful light-mode fallback with clear per-service status (#4)
- Coverage threshold raised from 30% to 70% (#6)
- `RateLimitMiddleware` wired into `api/main.py` middleware chain (was implemented but never added) (#12)
- `strategy_monitor.py` — added `compute_strategy_win_rates()`: reads human feedback from `pipeline_runs.outcome`, computes Wilson-score win rates per strategy (#13)
- `strategy_evaluator.py` — added `select_strategy_with_feedback()`: reads win rates from monitor and re-ranks strategy candidates; feedback now directly changes strategy selection (#13)
- Elasticsearch + Kibana moved to `--profile logging` in both `docker-compose.dev.yml` and `docker-compose.prod.yml` (saves ~1 GB RAM by default) (#18)
- `pyproject.toml` — author updated to Saranraj (#16)
- `config/settings.py` — `model_validator` added for friendly startup error messages (#7)

### Removed
- Deleted `artisan-admin/` (PHP Laravel artifact in a Python repo) (#3)
- Deleted `cProduction level projectsADAAadmin_ui/` (path-escaped garbage directory) (#2)

---

## [0.1.0] — 2026-06-01

### Added
- Initial DARA release
- 12-stage autonomous bug resolution pipeline:
  - Error ingestion (GitHub webhooks, OpenTelemetry, Slack, REST API)
  - Error normalisation and deduplication
  - AST-aware code chunking (tree-sitter, Python/Go/TypeScript)
  - DebuggerAgent — LLM root cause analysis with context bundle
  - FixerAgent — unified diff patch generation
  - ValidationEngine — Docker sandbox + static analysis (Ruff/Bandit/Semgrep)
  - ReviewerAgent — code quality and security review
  - Auto-approve gate (confidence ≥ 0.88, risk=low, sandbox passed)
  - GitHub PR creation via GitHub App
  - Slack notifications with Approve/Reject buttons
- Multi-agent architecture: Orchestrator, Debugger, Fixer, Reviewer, StrategyEvaluator, StrategyMonitor, StrategyGenerator, PatternMemory, MemoryConsolidation
- Full observability stack: Prometheus, Grafana, Jaeger
- Neo4j blast-radius analysis for cross-service impact detection
- RLHF feedback endpoint (`POST /api/v1/fixes/{fix_id}/feedback`)
- JWT authentication and role-based access control
- Admin UI (Vite/React dashboard)
- Full Docker Compose development stack
- Poetry-managed dependencies with Python 3.11+ support

[Unreleased]: https://github.com/saranraj1/DARA/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saranraj1/DARA/releases/tag/v0.1.0

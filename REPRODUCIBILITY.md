# DARA-v2 Reproducibility & Verification Guide

This document provides step-by-step instructions for reproducing the test results, benchmark evaluations, and pipeline validation of DARA-v2.

---

## 1. System Requirements & Environment

### Hardware & Operating System
- **Operating System**: Linux (Ubuntu 22.04+), macOS (13+), or Windows 11 (PowerShell / WSL2)
- **CPU**: 4+ cores (recommended for parallel test isolation)
- **RAM**: 8 GB minimum (16 GB recommended for Docker stack)
- **Python**: 3.11+ (CPython 3.11.5 tested)

### Required External Software
- **Docker Desktop / Docker Engine**: 24.0+ with Docker Compose v2
- **Poetry**: 1.8+ / 2.0+ (`poetry install`)

---

## 2. Installation & Environment Setup

### 2.1. Clone & Install Dependencies
```bash
# Clone the repository
git clone https://github.com/saranraj1/DARA.git
cd DARA

# Install all Python dependencies via Poetry
poetry install --all-extras
```

### 2.2. Configure Environment Variables
```bash
# Copy example configuration
cp .env.example .env
```
For standalone and unit test evaluation, mock credentials are automatically used. For live LLM execution, set:
```env
GROQ_API_KEY=your_groq_api_key
```

### 2.3. Start Infrastructure Stack (Optional for Live Database Tests)
```bash
# Start PostgreSQL (TimescaleDB), Redis, Qdrant, and Neo4j
docker compose -f docker-compose.dev.yml up -d

# Apply database migrations
poetry run python -m alembic upgrade head
```

---

## 3. Reproduction Commands

### 3.1. Run the Full Test Suite
Executes all 371 unit, integration, security, and persistence tests:
```bash
poetry run pytest tests/ -v --cov=.
```
**Expected Output**:
- `371 passed, 2 skipped`
- Code coverage $\ge 53\%$

### 3.2. Run the Evaluation Benchmark Suite
Executes the standardized 8-case benchmark suite and exports machine-readable telemetry:
```bash
poetry run python run_benchmarks.py --output metrics_results/benchmark_results.json --csv metrics_results/confidence_vs_outcome.csv
```
**Expected Output**:
- Subsystem smoke benchmarks: B1–B6 `PASS`
- Benchmark evaluation: 8 cases executed
- Summary metrics printed to stdout
- Generated files: `metrics_results/benchmark_results.json` and `metrics_results/confidence_vs_outcome.csv`

### 3.3. Run Ablation Experiments
You can evaluate DARA under various component ablations by overriding environment flags:

```bash
# Example 1: Disable Pattern Memory (forces all fixes through neural generation)
ENABLE_PATTERN_MEMORY=false poetry run python run_benchmarks.py

# Example 2: Disable Iterative Self-Healing in Sandbox
ENABLE_SELF_HEALING=false poetry run python run_benchmarks.py

# Example 3: Disable Security Static Analysis (measure raw LLM security)
ENABLE_SECURITY_VALIDATION=false poetry run python run_benchmarks.py
```

### 3.4. Run the End-to-End Smoke Pipeline
Executes the live end-to-end ingestion $\to$ diagnosis $\to$ patch generation $\to$ validation $\to$ review lifecycle on a sample error:
```bash
poetry run python run_e2e.py
```

---

## 4. Troubleshooting & Known Environment Caveats

1. **Windows Console Encoding**:
   - On Windows PowerShell with code page 1252, all benchmark CLI outputs use clean ASCII markers (`[OK]`, `[PASS]`, `*`) to avoid `charmap` codec encoding errors.
2. **Docker Daemon Access in Non-Elevated Shells**:
   - If Docker is stopped or unavailable, `SandboxRunner` automatically falls back to the file-system based `TestRunner` without crashing.
3. **Poetry 2.x Schema**:
   - `pyproject.toml` uses standard `[tool.poetry.group.dev.dependencies]` for compatibility with Poetry 1.x and 2.x.

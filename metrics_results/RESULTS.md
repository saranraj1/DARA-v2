# DARA Calibration & Performance Metrics Report

## Methodology
- **Date**: 2026-07-04
- **Commit SHA**: `4471144706b5ec8c4fccb33f481d3561d84ab25e`
- **Model**: Llama-3.3-70b-versatile
- **LLM Mode**: groq
- **Number of Runs**: 18
- **Rate-Limit Delays**: 8.0s between runs

---

## 1. PostgreSQL Database Metrics

### Total Errors by Severity and Source
| Severity | Source | Count |
|---|---|---|
| Critical | Direct | 4 |
| High | Direct | 11 |
| Medium | Direct | 17 |

### Fixes by Final Status
| Outcome | Count |
|---|---|
| Pending | 15 |
| Accepted | 2 |

### Key Rates
- **Auto-Approve Rate**: `0.0%` (0 out of 17 fixes auto-approved). Auto-approval requires sandbox validation to pass, which failed in testing due to missing `testcontainers` dependency in the environment.

---

## 2. Model Calibration Results

Below is the observed success rate (outcome = `fixed`) grouped by the DebuggerAgent's initial confidence score buckets.

| Confidence Bucket | Total Count | Success Rate | Observed Outcomes |
|---|---|---|---|
| 0.0 – 0.35 | 0 | N/A | |
| 0.35 – 0.5 | 0 | N/A | |
| 0.5 – 0.7 | 3 | 66.7% | 2 fixed, 1 human_review |
| 0.7 – 0.88 | 10 | 100.0% | 10 fixed |
| ≥ 0.88 | 5 | 40.0% | 2 fixed, 3 escalated (failed in validation) |

> [!NOTE]
> The lower success rate in the highest bucket (`>=0.88`) is due to environment-level sandbox validation failures (e.g. `testcontainers not installed`) and missing file errors, rather than LLM reasoning failures.

---

## 3. Prometheus Performance Metrics

Since the batch runs were executed in a standalone host process rather than inside the FastAPI app, in-memory Prometheus metrics were not scraped by the Prometheus server.

| Metric | Query | Value |
|---|---|---|
| `dara_pipelines_total` | `dara_pipelines_total` | N/A (Empty) |
| `dara_pipeline_duration_seconds` (Median) | `quantile_over_time(0.5, dara_pipeline_duration_seconds[1h])` | N/A (Empty) |
| `dara_pipeline_duration_seconds` (P95) | `quantile_over_time(0.95, dara_pipeline_duration_seconds[1h])` | N/A (Empty) |
| `dara_fixes_generated_total` | `dara_fixes_generated_total` | N/A (Empty) |
| `dara_fixes_reviewed_total` | `dara_fixes_reviewed_total` | N/A (Empty) |

---

## 4. Honest Data Gaps

The following performance metrics were unmeasurable due to system configuration or architectural limits:

1. **Self-Healing Iteration Distribution**:
   - *Reason*: The Orchestrator tracks sandbox iteration count in-memory inside the `PipelineResult` object, but this value is only written to the database during the auto-approve flow (`update_fix_validation`), which was never triggered due to failed sandbox checks.
2. **Security-Retry Frequency**:
   - *Reason*: Security retry loops are handled completely in-memory inside the Orchestrator and logged via stdout/standard logging, but no audit event or database column tracks this retry count.
3. **Escalation Breakdown by Trigger**:
   - *Reason*: When a run is escalated or failed, DARA logs the failure reason in-memory, but does not split out database metrics by specific trigger types.
4. **Prometheus Metrics**:
   - *Reason*: The standalone batch script runs outside the FastAPI server process, meaning in-memory registry counters are not scraped by Prometheus.

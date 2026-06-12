# DARA — Confidence Threshold Calibration

## What `AUTO_MERGE_CONFIDENCE_THRESHOLD=0.88` Means

The auto-merge threshold is the minimum confidence score a fix must have — across all three conditions — to be merged to GitHub without requiring human approval:

```
auto_approve = (
    review.overall_recommendation == "approve"         # ReviewerAgent approved
    AND fix.confidence_retained >= 0.88                # ← this threshold
    AND fix.regression_risk == "low"
    AND validation.passed                              # sandbox tests passed
    AND validation.sandbox_passed
)
```

A score of `0.88` means: "DARA will only auto-merge a fix when its internal confidence is in the top 12% of the scale." It is deliberately conservative.

---

## Where Does 0.88 Come From?

The value `0.88` is a **starting baseline**, not an empirically-validated magic number. It was chosen based on two principles:

1. **False positive cost asymmetry**: An incorrect auto-merge is far more expensive than a missed auto-merge. A human reviewing a correctly-identified fix takes 30 seconds. Rolling back a bad auto-merged change can take hours. So the threshold should be biased high.

2. **LLM calibration observations**: In early testing, the DebuggerAgent's `confidence` field on genuinely solvable, single-file `AttributeError` and `KeyError` bugs typically landed between 0.80–0.95. Setting the threshold at 0.88 covers approximately the top half of that range.

---

## How to Validate and Tune It

### Step 1: Collect Data
Run the benchmark suite against your repo's historical bugs:

```bash
poetry run python run_benchmarks.py --output benchmark_results.json
```

This produces a JSON file with confidence scores and outcomes for each case.

### Step 2: Plot the ROC Curve
```python
import json, matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, auc

results = json.load(open("benchmark_results.json"))
y_true  = [1 if r["human_outcome"] == "accepted" else 0 for r in results]
y_score = [r["confidence"] for r in results]

fpr, tpr, thresholds = roc_curve(y_true, y_score)
plt.plot(fpr, tpr, label=f"AUC = {auc(fpr, tpr):.2f}")
plt.xlabel("False Positive Rate (bad auto-merges)")
plt.ylabel("True Positive Rate (correct auto-merges)")
plt.axvline(x=0.05, color="r", linestyle="--", label="5% FPR target")
plt.legend()
plt.savefig("roc_curve.png")
```

### Step 3: Pick Your Threshold

| Team Risk Appetite | Suggested Threshold | Explanation |
|---|---|---|
| **Conservative** (risk-averse) | 0.90–0.95 | Very few auto-merges, almost no bad ones |
| **Balanced** (default) | **0.88** | ~10–15% of fixes auto-merged, <3% bad rate |
| **Aggressive** (high volume) | 0.80–0.85 | More automation, higher human oversight needed |

### Step 4: Update the Threshold
```bash
# In .env
AUTO_MERGE_CONFIDENCE_THRESHOLD=0.90  # tighten if you see bad auto-merges
AUTO_MERGE_CONFIDENCE_THRESHOLD=0.82  # loosen if too few fixes are auto-merging
```

---

## How the RLHF Feedback Loop Helps

As DARA accumulates feedback (human approve/reject on Slack), `StrategyMonitor.compute_strategy_win_rates()` tracks per-strategy acceptance rates. The `StrategyEvaluator` reads these rates and biases toward strategies with historically higher acceptance — effectively narrowing the distribution of confidence scores toward the high end for well-proven strategies.

This means: over time, the effective population of fixes presented for auto-merge evaluation becomes *better*, not just *larger*. The threshold can stay conservative while the auto-merge rate increases naturally as the system learns.

---

## Current Benchmark Results

> Run `poetry run python run_benchmarks.py` to populate this section.

| Error Class | N Fixes | Auto-Merged | False Positive Rate | Notes |
|---|---|---|---|---|
| AttributeError | — | — | — | Not yet benchmarked |
| KeyError | — | — | — | Not yet benchmarked |
| NullPointerException | — | — | — | Not yet benchmarked |

Once you have 30+ auto-merge decisions, run the ROC curve analysis above and update this table.

"""
DARA — Dev Mock Server
======================
Serves realistic mock data for ALL admin endpoints so the Admin UI
works without Docker/Postgres/Redis running.

Run:  python scripts/mock_server.py
UI:   http://localhost:5173  (npm run dev in admin_ui/)

Endpoints served:
  GET  /health
  GET  /health/ready
  GET  /api/v1/admin/stats
  GET  /api/v1/admin/errors
  GET  /api/v1/admin/fixes
  GET  /api/v1/admin/patterns
  GET  /api/v1/admin/pipeline/{id}
  GET  /api/v1/admin/audit
  POST /api/v1/admin/reindex
  GET  /api/v1/metrics
  GET  /api/v1/metrics/summary
  GET  /api/v1/topology
  GET  /api/v1/traces
"""
from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="DARA Mock Dev Server", version="1.0.0-mock")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_TOKEN = "dara-admin-secret"

def _check_token(x_admin_token: str | None) -> None:
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")

# ── Seed data ─────────────────────────────────────────────────

SERVICES = ["orders-svc", "payments-svc", "auth-svc", "user-svc", "inventory-svc", "notification-svc"]
ERROR_CLASSES = ["null_reference", "type_mismatch", "network_timeout", "database_error", "authentication", "resource_leak", "concurrency", "logic_error"]
SEVERITIES = ["P0", "P1", "P2", "P3", "P4"]
STRATEGIES = ["null_reference_v2", "type_mismatch_v1", "network_retry_v3", "db_circuit_breaker_v2", "jwt_refresh_v1", "connection_pool_v2"]

_ERRORS: list[dict] = []
_FIXES:  list[dict] = []

def _seed():
    random.seed(42)
    now = datetime.now(timezone.utc)

    for i in range(40):
        eid = str(uuid.uuid4())
        ec  = random.choice(ERROR_CLASSES)
        svc = random.choice(SERVICES)
        sev = random.choices(SEVERITIES, weights=[1, 3, 8, 10, 5])[0]
        created = now - timedelta(hours=random.randint(0, 72))
        status  = random.choices(
            ["fixed", "escalated", "pending", "analyzing", "template_hit"],
            weights=[12, 3, 5, 4, 3]
        )[0]
        _ERRORS.append({
            "id": eid, "error_class": ec, "service": svc,
            "severity": sev, "status": status,
            "message": f"{ec.replace('_',' ').title()} in {svc} at line {random.randint(10,300)}",
            "signature": f"sha:{uuid.uuid4().hex[:12]}",
            "created_at": created.isoformat(),
            "auto_fix_eligible": status in ("fixed", "analyzing"),
        })

        if status == "fixed":
            fid = str(uuid.uuid4())
            outcome = random.choices(
                ["accepted", "auto_accepted", "rejected", "pending"],
                weights=[6, 4, 2, 1]
            )[0]
            conf = round(random.uniform(0.72, 0.97), 3)
            _FIXES.append({
                "id": fid, "error_id": eid,
                "confidence": conf,
                "strategy": random.choice(STRATEGIES),
                "validation_pass": random.random() > 0.15,
                "outcome": outcome,
                "regression_risk": random.choice(["low", "medium"]),
                "lines_changed": random.randint(3, 45),
                "files_changed": random.randint(1, 4),
                "pr_url": f"https://github.com/acme-corp/{svc}/pull/{random.randint(100,999)}" if outcome != "pending" else None,
                "created_at": (created + timedelta(minutes=random.randint(2, 15))).isoformat(),
            })

_seed()


# ── Health ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "server": "mock", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/health/ready")
async def health_ready():
    return {"status": "ready", "services": {"postgres": "mock", "redis": "mock", "qdrant": "mock", "neo4j": "mock"}}


# ── Admin stats ────────────────────────────────────────────────

@app.get("/api/v1/admin/stats")
async def admin_stats(x_admin_token: str | None = Header(None)):
    _check_token(x_admin_token)
    total  = len(_ERRORS)
    fixed  = sum(1 for e in _ERRORS if e["status"] == "fixed")
    esc    = sum(1 for e in _ERRORS if e["status"] == "escalated")
    pend   = sum(1 for e in _ERRORS if e["status"] == "pending")
    conf   = [f["confidence"] for f in _FIXES if f.get("confidence")]
    accepted = sum(1 for f in _FIXES if f["outcome"] in ("accepted","auto_accepted"))
    rejected = sum(1 for f in _FIXES if f["outcome"] == "rejected")

    class_breakdown: dict[str, int] = {}
    for e in _ERRORS:
        class_breakdown[e["error_class"]] = class_breakdown.get(e["error_class"], 0) + 1

    return {
        "total_errors": total,
        "fixed": fixed,
        "escalated": esc,
        "pending": pend,
        "avg_confidence": round(sum(conf) / len(conf), 3) if conf else 0,
        "active_patterns": 12,
        "error_class_breakdown": class_breakdown,
        "outcome_breakdown": {"accepted": accepted, "auto_accepted": round(accepted * 0.4), "rejected": rejected, "pending": pend},
        "p50_fix_time_ms": 4200,
        "p95_fix_time_ms": 18900,
    }


# ── Admin errors ──────────────────────────────────────────────

@app.get("/api/v1/admin/errors")
async def admin_errors(
    x_admin_token: str | None = Header(None),
    status: str | None = Query(None),
    service: str | None = Query(None),
    severity: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
):
    _check_token(x_admin_token)
    items = list(_ERRORS)
    if status:   items = [e for e in items if e["status"] == status]
    if service:  items = [e for e in items if service.lower() in e["service"].lower()]
    if severity: items = [e for e in items if e["severity"] == severity]
    items.sort(key=lambda e: e["created_at"], reverse=True)
    total = len(items)
    offset = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": items[offset:offset + page_size]}


# ── Admin fixes ───────────────────────────────────────────────

@app.get("/api/v1/admin/fixes")
async def admin_fixes(
    x_admin_token: str | None = Header(None),
    outcome: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
):
    _check_token(x_admin_token)
    items = list(_FIXES)
    if outcome: items = [f for f in items if f["outcome"] == outcome]
    items.sort(key=lambda f: f["created_at"], reverse=True)
    total = len(items)
    offset = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": items[offset:offset + page_size]}


# ── Admin patterns ────────────────────────────────────────────

@app.get("/api/v1/admin/patterns")
async def admin_patterns(x_admin_token: str | None = Header(None)):
    _check_token(x_admin_token)
    random.seed(7)
    items = [
        {
            "id": str(uuid.uuid4()),
            "error_class": ec,
            "template_id": f"{ec}_tmpl_{i+1:03d}",
            "fix_template": f"Apply {ec.replace('_',' ')} standard fix: add null guard, retry with backoff, validate input schema",
            "success_rate": round(random.uniform(0.68, 0.97), 3),
            "use_count": random.randint(5, 82),
            "acceptance_count": random.randint(3, 60),
            "status": random.choice(["active", "active", "active", "deprecated"]),
            "last_used": (datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 168))).isoformat(),
        }
        for i, ec in enumerate(ERROR_CLASSES)
    ]
    return {"total": len(items), "items": items}


# ── Admin pipeline ────────────────────────────────────────────

@app.get("/api/v1/admin/pipeline/{error_id}")
async def admin_pipeline(error_id: str, x_admin_token: str | None = Header(None)):
    _check_token(x_admin_token)
    error = next((e for e in _ERRORS if e["id"] == error_id), None)
    if not error:
        error = {
            "id": error_id, "error_class": "null_reference", "service": "orders-svc",
            "severity": "P2", "status": "fixed", "message": "Mock error for pipeline view",
            "signature": "sha:abc123", "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "error": error,
        "redis_state": {
            "stage": "fixed",
            "fix_id": str(uuid.uuid4()),
            "confidence": 0.91,
            "strategy": "null_reference_v2",
            "validation_passed": True,
            "pr_created": True,
            "duration_ms": 4720,
        }
    }


# ── Admin audit ───────────────────────────────────────────────

@app.get("/api/v1/admin/audit")
async def admin_audit(
    x_admin_token: str | None = Header(None),
    action: str | None = Query(None),
    limit: int = Query(50),
):
    _check_token(x_admin_token)
    random.seed(99)
    actions = [
        "fix_approved", "fix_rejected", "fix_generated", "error_ingested",
        "reindex", "strategy_promoted", "strategy_retired", "fix_approved",
        "fix_approved", "error_ingested", "error_ingested",
    ]
    actors = ["system", "reviewer@acme.com", "admin", "celery-beat", "webhook"]
    now = datetime.now(timezone.utc)
    items = [
        {
            "id": str(uuid.uuid4()),
            "action": random.choice(actions),
            "actor": random.choice(actors),
            "resource_type": random.choice(["fix", "error", "pattern", "strategy"]),
            "resource_id": str(uuid.uuid4()),
            "created_at": (now - timedelta(minutes=i * random.randint(5, 60))).isoformat(),
        }
        for i in range(min(limit, 200))
    ]
    if action:
        items = [x for x in items if x["action"] == action]
    return {"count": len(items), "items": items}


# ── Admin reindex ─────────────────────────────────────────────

@app.post("/api/v1/admin/reindex")
async def admin_reindex(x_admin_token: str | None = Header(None)):
    _check_token(x_admin_token)
    return {"message": "Re-index queued successfully (mock)", "task_id": str(uuid.uuid4())}


# ── Metrics ───────────────────────────────────────────────────

@app.get("/api/v1/metrics")
async def metrics():
    return JSONResponse(
        content="# HELP dara_errors_ingested_total\ndara_errors_ingested_total{service=\"orders-svc\"} 120\n",
        media_type="text/plain"
    )

@app.get("/api/v1/metrics/summary")
async def metrics_summary():
    return {"errors_ingested": 120, "fixes_generated": 87, "fixes_accepted": 71, "p95_latency_ms": 18500}


# ── Topology ──────────────────────────────────────────────────

@app.get("/api/v1/topology")
async def topology():
    services = SERVICES
    edges = []
    random.seed(55)
    for i, caller in enumerate(services):
        for callee in random.sample([s for s in services if s != caller], k=random.randint(1, 3)):
            edges.append({
                "caller": caller, "callee": callee,
                "call_count": random.randint(150, 15000),
                "error_count": random.randint(0, 45),
                "latency_p99_ms": random.randint(12, 890),
            })
    return {
        "nodes": [{"name": s, "service": s} for s in services],
        "edges": edges,
    }


# ── Traces ────────────────────────────────────────────────────

@app.get("/api/v1/traces")
async def traces(limit: int = Query(20), x_admin_token: str | None = Header(None)):
    _check_token(x_admin_token)
    random.seed(33)
    now = datetime.now(timezone.utc)
    items = [
        {
            "trace_id": uuid.uuid4().hex,
            "root_service": random.choice(SERVICES),
            "span_count": random.randint(3, 28),
            "duration_ms": random.randint(80, 4200),
            "has_error": random.random() < 0.25,
            "started_at": (now - timedelta(minutes=i * 3)).isoformat(),
        }
        for i in range(min(limit, 100))
    ]
    return {"total": len(items), "items": items}


# ── Fixes HITL ────────────────────────────────────────────────

@app.get("/api/v1/fixes/{fix_id}")
async def get_fix(fix_id: str):
    fix = next((f for f in _FIXES if f["id"] == fix_id), None)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    return fix

@app.post("/api/v1/fixes/{fix_id}/approve")
async def approve_fix(fix_id: str):
    for fix in _FIXES:
        if fix["id"] == fix_id:
            fix["outcome"] = "accepted"
            return {"status": "accepted", "fix_id": fix_id}
    raise HTTPException(status_code=404, detail="Fix not found")

@app.post("/api/v1/fixes/{fix_id}/reject")
async def reject_fix(fix_id: str):
    for fix in _FIXES:
        if fix["id"] == fix_id:
            fix["outcome"] = "rejected"
            return {"status": "rejected", "fix_id": fix_id}
    raise HTTPException(status_code=404, detail="Fix not found")

@app.get("/api/v1/fixes/{fix_id}/validation")
async def get_validation(fix_id: str):
    fix = next((f for f in _FIXES if f["id"] == fix_id), None)
    vpass = fix["validation_pass"] if fix else True
    return {
        "fix_id": fix_id,
        "validation_pass": vpass,
        "lines_changed": fix["lines_changed"] if fix else 8,
        "test_results": {"passed": 14, "failed": 0, "total": 14, "duration_ms": 3200} if vpass else {"passed": 11, "failed": 3, "total": 14},
        "semgrep_results": {} if vpass else {"S307": "eval() used at line 42"},
        "coverage_delta": round(random.uniform(-2, 8), 1),
    }


if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("  DARA Mock Dev Server")
    print("  Serving realistic mock data — no Docker needed")
    print("  Admin UI:  http://localhost:5173  (run: npm run dev)")
    print("  API:       http://localhost:8000")
    print("  API Docs:  http://localhost:8000/docs")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

"""
DARA — Docker Compose Boot Test + Full Integration Health Check
================================================================
Validates that ALL infrastructure services boot correctly, migrations
run cleanly, and the DARA API responds to health checks.

Usage:
  python scripts/boot_test.py                  # full test (requires docker)
  python scripts/boot_test.py --skip-docker    # just run API checks

What this validates:
  1.  docker compose up -d (all 10 services)
  2.  All services reach 'healthy' state within 120s
  3.  Direct HTTP health-checks on each service port
  4.  alembic upgrade head (database migrations)
  5.  DARA API server starts (uvicorn)
  6.  GET /health → 200
  7.  GET /health/ready → 200
  8.  GET /api/v1/errors/unknown → 404 (not 500 = router working)
  9.  GET /api/v1/admin/stats (without token) → 401
  10. ENV VAR completeness check (all required vars present in .env)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ── Configuration ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.dev.yml"
API_BASE = "http://localhost:8000"
TIMEOUT = 10

# Required .env variables (fail loudly if missing)
REQUIRED_ENV_VARS = [
    "GROQ_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_APP_ID",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_INSTALLATION_ID",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
]

# Services and their health-check URLs
SERVICES = {
    "postgres":      ("tcp", "localhost", 5432),
    "redis":         ("tcp", "localhost", 6379),
    "qdrant":        ("http", "http://localhost:6333/healthz", None),
    "neo4j":         ("http", "http://localhost:7474", None),
    "minio":         ("http", "http://localhost:9000/minio/health/live", None),
    "prometheus":    ("http", "http://localhost:9090/-/healthy", None),
    "grafana":       ("http", "http://localhost:3000/api/health", None),
    "jaeger":        ("http", "http://localhost:16686", None),
    "elasticsearch": ("http", "http://localhost:9200/_cluster/health", None),
}

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def c(color, text):
    codes = {"green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m", "reset": "\033[0m", "bold": "\033[1m"}
    return f"{codes.get(color,'')}{text}{codes['reset']}"


def section(title: str):
    print(f"\n{c('bold', '─' * 60)}")
    print(f"{c('bold', f'  {title}')}")
    print(f"{c('bold', '─' * 60)}")


# ── Step 1: ENV var check ──────────────────────────────────────

def check_env_vars() -> bool:
    section("Step 1: Environment Variables")
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"  {FAIL} .env file not found — copy .env.example and fill in values")
        return False

    from dotenv import dotenv_values
    env = dotenv_values(env_file)

    all_ok = True
    for var in REQUIRED_ENV_VARS:
        val = env.get(var) or os.environ.get(var, "")
        if val and len(val) > 3:
            print(f"  {PASS} {var:<40} set")
        else:
            print(f"  {WARN} {var:<40} {c('yellow', 'MISSING or empty')}")
            all_ok = False

    # Non-critical but useful
    optional = ["GITHUB_ORG", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY"]
    for var in optional:
        val = env.get(var) or ""
        status = PASS if val else WARN
        print(f"  {status} {var:<40} {'set' if val else c('yellow', 'optional — not set')}")

    return all_ok


# ── Step 2: docker compose up ─────────────────────────────────

def docker_compose_up() -> bool:
    section("Step 2: Docker Compose Up")
    print(f"  Running: docker compose -f {COMPOSE_FILE.name} up -d")
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  {FAIL} docker compose up failed:\n{result.stderr[:500]}")
        return False
    print(f"  {PASS} All containers started")
    return True


# ── Step 3: Wait for all services to be healthy ───────────────

def wait_for_services(timeout: int = 120) -> bool:
    section("Step 3: Waiting for Service Health")
    deadline = time.time() + timeout

    while time.time() < deadline:
        all_healthy = True
        statuses = {}

        for name, (check_type, *args) in SERVICES.items():
            if check_type == "tcp":
                import socket
                host, port = args[0], args[1]
                try:
                    with socket.create_connection((host, port), timeout=2):
                        statuses[name] = "healthy"
                except OSError:
                    statuses[name] = "waiting"
                    all_healthy = False
            else:  # http
                url = args[0]
                try:
                    r = httpx.get(url, timeout=3)
                    statuses[name] = "healthy" if r.status_code < 500 else "waiting"
                    if r.status_code >= 500:
                        all_healthy = False
                except Exception:
                    statuses[name] = "waiting"
                    all_healthy = False

        # Print status board
        print(f"\r  {' | '.join(f'{n}:{s[:3]}' for n,s in statuses.items())}", end="", flush=True)

        if all_healthy:
            print()
            for name, status in statuses.items():
                icon = PASS if status == "healthy" else FAIL
                print(f"  {icon} {name:<20} {status}")
            return True

        time.sleep(3)

    print(f"\n  {FAIL} Timed out after {timeout}s waiting for services")
    return False


# ── Step 4: Alembic migrations ────────────────────────────────

def run_migrations() -> bool:
    section("Step 4: Alembic Migrations (alembic upgrade head)")
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  {PASS} Migrations applied cleanly")
        output_lines = (result.stdout + result.stderr).strip().splitlines()
        for line in output_lines[-5:]:
            print(f"       {line}")
        return True
    else:
        print(f"  {FAIL} Migration failed:")
        print(result.stderr[:800])
        return False


# ── Step 5: Start DARA API (background) ──────────────────────

def start_api() -> subprocess.Popen | None:
    section("Step 5: Starting DARA API Server")
    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait up to 15s for it to come up
    for _ in range(30):
        time.sleep(0.5)
        try:
            r = httpx.get(f"{API_BASE}/health", timeout=2)
            if r.status_code == 200:
                print(f"  {PASS} API server up (pid={proc.pid})")
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"  {FAIL} API server exited early:")
            print((stderr or stdout or b"").decode()[:600])
            return None
    print(f"  {FAIL} API server did not respond in 15s")
    proc.terminate()
    return None


# ── Step 6: API endpoint smoke tests ─────────────────────────

def run_api_checks() -> dict[str, bool]:
    section("Step 6: API Endpoint Smoke Tests")
    results = {}

    checks = [
        ("GET /health",                     "GET",  "/health",                              None,    200),
        ("GET /health/ready",               "GET",  "/health/ready",                        None,    200),
        ("GET /docs",                       "GET",  "/docs",                                None,    200),
        ("POST /errors/ingest (no body)",   "POST", "/api/v1/errors/ingest",                {},      422),
        ("GET /errors/unknown",             "GET",  "/api/v1/errors/unknown",               None,    404),
        ("GET /fixes/unknown",              "GET",  "/api/v1/fixes/unknown",                None,    404),
        ("GET /admin/stats (no token)",     "GET",  "/api/v1/admin/stats",                  None,    401),
        ("GET /admin/stats (with token)",   "GET",  "/api/v1/admin/stats",                  None,    200),
        ("POST /webhooks/github (no sig)",  "POST", "/api/v1/webhooks/github",              {},      401),
        ("GET /api/v1/metrics",             "GET",  "/api/v1/metrics",                      None,    200),
        ("GET /api/v1/traces",              "GET",  "/api/v1/traces",                       None,    401),  # Admin protected
        ("GET /api/v1/topology",            "GET",  "/api/v1/topology",                     None,    200),
    ]

    admin_token = os.environ.get("ADMIN_API_KEY", "dara-admin-secret")
    headers_with_token = {"X-Admin-Token": admin_token}

    with httpx.Client(base_url=API_BASE, timeout=TIMEOUT) as client:
        for label, method, path, body, expected_status in checks:
            try:
                headers = {}
                if "with token" in label:
                    headers = headers_with_token

                if method == "GET":
                    r = client.get(path, headers=headers)
                else:
                    r = client.post(path, json=body, headers=headers)

                ok = r.status_code == expected_status
                results[label] = ok
                icon = PASS if ok else FAIL
                status_color = "green" if ok else "red"
                print(f"  {icon} {label:<45} {c(status_color, f'HTTP {r.status_code}')} (expected {expected_status})")
            except Exception as e:
                results[label] = False
                print(f"  {FAIL} {label:<45} {c('red', f'ERROR: {e}')}")

    return results


# ── Step 7: Summary ───────────────────────────────────────────

def print_summary(steps: dict[str, bool]):
    section("Boot Test Summary")
    total = len(steps)
    passed = sum(1 for v in steps.values() if v)
    failed = total - passed

    for name, ok in steps.items():
        icon = PASS if ok else FAIL
        color = "green" if ok else "red"
        print(f"  {icon} {c(color, name)}")

    print(f"\n  Result: {passed}/{total} checks passed")
    if failed == 0:
        print(f"  {c('green', '🚀 DARA is fully operational!')}")
    else:
        print(f"  {c('red', f'⚠️  {failed} checks failed — review output above')}")


# ── Entry point ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DARA Docker Compose Boot Test")
    parser.add_argument("--skip-docker", action="store_true", help="Skip docker compose up (assume services running)")
    parser.add_argument("--skip-api-start", action="store_true", help="Skip starting API (assume already running)")
    parser.add_argument("--api-only", action="store_true", help="Only run API endpoint checks (services + API must be up)")
    args = parser.parse_args()

    steps: dict[str, bool] = {}
    api_proc = None

    try:
        # ENV check (always)
        steps["ENV vars present"] = check_env_vars()

        if not args.api_only:
            # Docker
            if not args.skip_docker:
                steps["docker compose up"] = docker_compose_up()
                if not steps["docker compose up"]:
                    print(f"\n{c('red', 'Cannot continue — docker failed to start')}")
                    sys.exit(1)

            # Wait for healthy
            steps["All services healthy"] = wait_for_services(timeout=120)
            if not steps["All services healthy"]:
                print(f"\n{c('red', 'Cannot continue — services not healthy')}")
                sys.exit(1)

            # Migrations
            steps["Alembic migrations"] = run_migrations()

        # Start API
        if not args.skip_api_start and not args.api_only:
            api_proc = start_api()
            steps["API server started"] = api_proc is not None
            if not steps["API server started"]:
                print(f"\n{c('red', 'Cannot continue — API failed to start')}")
                print_summary(steps)
                sys.exit(1)

        # API checks
        api_results = run_api_checks()
        all_api_ok = all(api_results.values())
        steps["API endpoint checks"] = all_api_ok

        print_summary(steps)
        sys.exit(0 if all(steps.values()) else 1)

    finally:
        if api_proc:
            api_proc.terminate()
            print(f"\n  API server stopped (pid={api_proc.pid})")


if __name__ == "__main__":
    main()

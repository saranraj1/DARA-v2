"""
DARA — Docker Sandbox Dockerfile
Minimal Python image for isolated test execution.
No network, read-only filesystem, restricted resources.
"""

FROM python:3.11-slim

# Security: run as non-root
RUN groupadd -r dara && useradd -r -g dara dara

# Install minimal test tooling
RUN pip install --no-cache-dir pytest pytest-cov pytest-json-report ruff==0.4.0

WORKDIR /sandbox

# Mount the target repo at /sandbox at runtime
# The sandbox.py orchestrator will bind-mount the repo directory

USER dara

# Default command — overridden by sandbox.py at runtime
CMD ["pytest", "--json-report", "--json-report-file=/tmp/report.json", "-x", "--tb=short"]

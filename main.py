"""
DARA — Main entry point
Run with: uvicorn main:app --reload --port 8000
"""
import uvicorn

from api.main import app  # noqa: F401 — re-export for uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
        access_log=False,  # Handled by our LoggingMiddleware
    )

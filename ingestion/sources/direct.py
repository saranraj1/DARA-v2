"""
DARA — Direct API Source Parser
Pass-through normalization for errors submitted directly via the REST API.
"""
from __future__ import annotations


def parse_direct_api(payload: dict) -> dict:
    """
    Normalize a direct API submission into the ingestion pipeline format.
    The payload is already in the ErrorIngestRequest schema — minimal transformation needed.
    """
    return {
        **payload,
        "source": "direct",
        "raw_payload": payload,
        "environment": payload.get("environment", "production"),
        "severity": payload.get("severity", "medium"),
    }

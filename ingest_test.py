import urllib.request
import json
import time

url = "http://localhost:8000/api/v1/errors/ingest"

errors = [
    {
        "service": "user-service",
        "environment": "production",
        "error_class": "PermissionError",
        "message": "PermissionError: user role 'guest' cannot access admin endpoint /users/delete",
        "severity": "high",
        "stack_trace": "Traceback (most recent call last):\n  File auth/rbac.py, line 58, in check_permission\n    raise PermissionError(msg)\nPermissionError: guest cannot access /users/delete",
        "file_path": "auth/rbac.py",
        "line_number": 58
    },
    {
        "service": "search-service",
        "environment": "production",
        "error_class": "MemoryError",
        "message": "MemoryError: search index exceeded 4GB memory limit during reindex operation",
        "severity": "critical",
        "stack_trace": "Traceback (most recent call last):\n  File search/indexer.py, line 134, in build_index\n    self.index.append(doc.vector)\nMemoryError",
        "file_path": "search/indexer.py",
        "line_number": 134
    },
    {
        "service": "billing-service",
        "environment": "production",
        "error_class": "OverflowError",
        "message": "OverflowError: invoice amount 9999999999.99 exceeds decimal field max precision",
        "severity": "critical",
        "stack_trace": "Traceback (most recent call last):\n  File billing/invoice.py, line 77, in create_invoice\n    record.amount = Decimal(raw_amount)\nOverflowError: value exceeds max decimal precision",
        "file_path": "billing/invoice.py",
        "line_number": 77
    }
]

print("=" * 52)
print("  DARA  |  Error Ingestion Demo")
print("=" * 52)

for i, err in enumerate(errors, 1):
    print("\n  [" + str(i) + "/3]  " + err["error_class"] + " in " + err["service"])
    print("         " + err["message"][:55] + "...")
    req = urllib.request.Request(
        url,
        data=json.dumps(err).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        res = urllib.request.urlopen(req, timeout=15)
        r = json.loads(res.read())
        print("         id     : " + str(r.get("id", "?")))
        print("         status : " + str(r.get("status", "?")))
    except Exception as e:
        print("         ERROR  : " + str(e))
    time.sleep(0.5)

print("\n" + "=" * 52)
print("  Open http://localhost:8081 to see the pipeline")
print("=" * 52)

import urllib.request
import json
import time

BASE = "http://localhost:8000/api/v1/errors/ingest"

ERRORS = [
    {
        "service": "inventory-service",
        "environment": "production",
        "error_class": "AttributeError",
        "message": "AttributeError: NoneType object has no attribute stock_count",
        "severity": "critical",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            "  File inventory/manager.py, line 89, in reserve_stock\n"
            "    available = item.stock_count - quantity\n"
            "AttributeError: NoneType object has no attribute stock_count"
        ),
        "file_path": "inventory/manager.py",
        "line_number": 89,
    },
    {
        "service": "notification-service",
        "environment": "production",
        "error_class": "ValueError",
        "message": "ValueError: invalid email address format in bulk notification job",
        "severity": "medium",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            "  File notifications/email.py, line 44, in send_bulk\n"
            "    validated = validate_email(recipient)\n"
            "ValueError: user@@domain..com is not a valid email address"
        ),
        "file_path": "notifications/email.py",
        "line_number": 44,
    },
    {
        "service": "analytics-service",
        "environment": "production",
        "error_class": "RecursionError",
        "message": "RecursionError: maximum recursion depth exceeded in category tree traversal",
        "severity": "high",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            "  File analytics/tree.py, line 27, in traverse\n"
            "    return self.traverse(node.children)\n"
            "  ... 996 more frames ...\n"
            "RecursionError: maximum recursion depth exceeded"
        ),
        "file_path": "analytics/tree.py",
        "line_number": 27,
    },
]

SEP = "=" * 56

print(SEP)
print("  DARA  |  Live Error Ingestion Demo")
print(SEP)

for i, err in enumerate(ERRORS, 1):
    label = err["error_class"]
    svc   = err["service"]
    msg   = err["message"][:58]
    print("\n  [" + str(i) + "/3]  " + label + "  ->  " + svc)
    print("         " + msg + "...")

    data = json.dumps(err).encode("utf-8")
    req = urllib.request.Request(
        BASE,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        res = urllib.request.urlopen(req, timeout=15)
        r = json.loads(res.read())
        eid    = str(r.get("id", "?"))
        status = str(r.get("status", "?"))
        dedup  = str(r.get("deduplicated", False))
        print("         error_id : " + eid)
        print("         status   : " + status + "  |  deduped: " + dedup)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("         HTTP " + str(e.code) + ": " + body[:80])
    except Exception as e:
        print("         ERROR: " + str(e))

    time.sleep(0.5)

print("\n" + SEP)
print("  Done! Open http://localhost:8081 to watch the pipeline.")
print(SEP)

"""Quick HMAC webhook test — simulates a real GitHub ping to verify the endpoint."""
import hashlib
import hmac
import json
import os
import urllib.request

# Read webhook secret from .env
secret = ""
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
with open(env_path) as f:
    for line in f:
        if line.strip().startswith("GITHUB_WEBHOOK_SECRET="):
            secret = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
            break

print(f"Secret loaded: {'YES' if secret else 'NO'} (len={len(secret)})")

body = json.dumps({
    "zen": "Non-blocking is better than blocking.",
    "hook_id": 999,
    "repository": {"full_name": "test/repo"},
}).encode()

sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
print(f"Signature: {sig[:30]}...")

for base_url in ["http://localhost:8000", "https://tremor-laptop-prepay.ngrok-free.dev"]:
    url = f"{base_url}/api/v1/webhooks/github"
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": sig,
        "ngrok-skip-browser-warning": "true",
    })
    try:
        r = urllib.request.urlopen(req, timeout=15)
        print(f"\n[{r.status}] {url}")
        print("Response:", r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"\n[{e.code}] {url}")
        print("Body:", e.read().decode()[:200])
    except Exception as e:
        print(f"\n[ERR] {url} -> {e}")

import sys
sys.path.insert(0, '.')

passed = 0
failed = 0

def ok(msg): 
    global passed
    print("PASS:", msg)
    passed += 1

def fail(msg):
    global failed
    print("FAIL:", msg)
    failed += 1

# Test 1: AuditLog model has resource_id not fix_id
from storage.models import AuditLog
col_names = [c.name for c in AuditLog.__table__.columns]
if 'resource_id' in col_names and 'fix_id' not in col_names:
    ok("AuditLog.resource_id exists, fix_id correctly absent")
else:
    fail(f"AuditLog columns wrong: {col_names}")

# Test 2: JWT encode/decode round-trip
from api.routers.auth import _make_token, _decode_token
td = _make_token('dara', org_id='acme', role='admin')
p = _decode_token(td['access_token'])
if p['sub'] == 'dara' and p['org'] == 'acme' and p['role'] == 'admin':
    ok(f"JWT round-trip sub={p['sub']} org={p['org']} role={p['role']}")
else:
    fail(f"JWT payload wrong: {p}")

# Test 3: middleware _validate_jwt
from api.middleware.auth import _validate_jwt
p2 = _validate_jwt(td['access_token'])
if p2['sub'] == 'dara':
    ok(f"middleware _validate_jwt decoded sub={p2['sub']}")
else:
    fail("middleware _validate_jwt failed")

# Test 4: expired token raises 401
import jwt as pyjwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from config.settings import get_settings
s = get_settings()
exp_token = pyjwt.encode(
    {'sub': 'dara', 'org': 'x', 'role': 'admin',
     'exp': datetime.now(timezone.utc) - timedelta(hours=1)},
    s.jwt_secret, algorithm='HS256'
)
try:
    _validate_jwt(exp_token)
    fail("expired token should have raised 401")
except HTTPException as e:
    if e.status_code == 401:
        ok(f"expired token gives 401: {e.detail[:45]}")
    else:
        fail(f"wrong status code: {e.status_code}")

# Test 5: settings phase4 fields
if s.jwt_secret and s.admin_username == 'dara' and s.default_org_id == 'default':
    ok(f"settings phase4: admin={s.admin_username} org={s.default_org_id} expire={s.jwt_expire_hours}h")
else:
    fail("settings phase4 fields missing")

# Test 6: login endpoint via TestClient
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.auth import router as auth_router
app = FastAPI()
app.include_router(auth_router)
with TestClient(app) as c:
    r = c.post('/api/v1/auth/login', json={'username': 'dara', 'password': 'dara_admin_2024', 'org_id': 'test-org'})
    if r.status_code == 200:
        d = r.json()
        if d.get('username') == 'dara' and d.get('org_id') == 'test-org' and d.get('role') == 'admin':
            ok(f"login returns username={d['username']} org={d['org_id']} role={d['role']}")
        else:
            fail(f"login response missing fields: {list(d.keys())}")
    else:
        fail(f"login returned {r.status_code}: {r.text[:100]}")
        d = {}

    r2 = c.post('/api/v1/auth/login', json={'username': 'dara', 'password': 'wrongpass'})
    if r2.status_code == 401:
        ok("wrong password returns 401")
    else:
        fail(f"expected 401 got {r2.status_code}")

    if d.get('access_token'):
        r3 = c.get('/api/v1/auth/me', headers={'Authorization': f"Bearer {d['access_token']}"})
        if r3.status_code == 200:
            me = r3.json()
            ok(f"/auth/me: username={me.get('username')} role={me.get('role')} org={me.get('org_id')}")
        else:
            fail(f"/auth/me returned {r3.status_code}")

# Test 7: _get_org_id helper
from api.routers.admin import _get_org_id
from fastapi import Request as FRequest
app2 = FastAPI()

@app2.get('/test-org')
async def test_route(request: FRequest):
    return {'org_id': _get_org_id(request)}

with TestClient(app2) as c2:
    r4 = c2.get('/test-org', headers={'X-Org-Id': 'my-tenant'})
    if r4.json().get('org_id') == 'my-tenant':
        ok(f"X-Org-Id header extracted: {r4.json()['org_id']}")
    else:
        fail(f"X-Org-Id not extracted correctly: {r4.json()}")

    r5 = c2.get('/test-org')
    if r5.json().get('org_id') == 'default':
        ok(f"org_id default fallback: {r5.json()['org_id']}")
    else:
        fail(f"org_id fallback wrong: {r5.json()}")

print()
print(f"==== Results: {passed} PASSED, {failed} FAILED out of {passed + failed} tests ====")
sys.exit(0 if failed == 0 else 1)

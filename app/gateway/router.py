"""게이트웨이 중계(/v1/chat/completions) + 관리자 CRUD(/admin/*). 계약: docs/contract.md B·C."""
import ipaddress
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import vault
from app.contract import ADMIN_KEY, DEMO_MODE, PROVIDERS, cost_krw, hash_token, mask_key
from app.db import get_conn

router = APIRouter()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- B. 중계 ----------------

def _deny(status: int, reason: str, ip: str, token: str):
    masked = mask_key(token) if token else None
    with get_conn() as c:
        c.execute("INSERT INTO access_denied_log(source_ip,reason,token_masked,at) VALUES(?,?,?,?)",
                  (ip, reason, masked, time.time()))
        c.execute("INSERT INTO event(at,kind,message,data) VALUES(?,?,?,?)",
                  (time.time(), "ACCESS_DENIED", f"접근 거부({reason}) {ip}",
                   json.dumps({"source_ip": ip, "reason": reason, "token_masked": masked})))
    return JSONResponse({"error": reason}, status_code=status)


def _ip_allowed(ip: str, cidrs) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for r in cidrs:
        try:
            if addr in ipaddress.ip_network(r["cidr"], strict=False):
                return True
        except ValueError:
            continue
    return False


_wif_cache: dict[str, tuple[str, float]] = {}  # audience -> (token, 만료 epoch)


async def _upstream_key(client: httpx.AsyncClient, cred) -> str:
    if cred["auth_mode"] == "API_KEY":
        return vault.decrypt(cred["secret_enc"])
    aud = cred["audience"]
    tok, exp = _wif_cache.get(aud, ("", 0.0))
    if time.time() > exp - 60:  # 만료 60초 전 갱신
        r = await client.post(f"{PROVIDERS['mock']}/sts/token", json={"audience": aud, "identity": "novem-gateway"})
        r.raise_for_status()
        d = r.json()
        tok, exp = d["token"], time.time() + d["expires_in"]
        _wif_cache[aud] = (tok, exp)
    return tok


def _log_usage(service_id, cred_id, model, usage: dict, status_code: int):
    with get_conn() as c:
        c.execute("INSERT INTO usage_log(service_id,credential_id,model,input_tokens,output_tokens,cost_krw,status_code,at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (service_id, cred_id, model, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                   cost_krw(usage.get("total_tokens", 0)), status_code, time.time()))


def _bad_gateway(cred):
    if cred["auth_mode"] == "WIF":
        _wif_cache.pop(cred["audience"], None)  # 폐기·만료 토큰 재사용 방지
    return JSONResponse({"error": "upstream_error"}, status_code=502)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(""),
                           x_demo_source_ip: Optional[str] = Header(None)):
    ip = x_demo_source_ip if (DEMO_MODE and x_demo_source_ip) else (request.client.host if request.client else "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""

    with get_conn() as c:
        cidrs = c.execute("SELECT cidr FROM allowed_cidr").fetchall()
        tok = c.execute("SELECT * FROM gateway_token WHERE token_hash=?", (hash_token(token),)).fetchone() if token else None
        svc = c.execute("SELECT * FROM service WHERE id=?", (tok["service_id"],)).fetchone() if tok else None
        cred = c.execute("SELECT * FROM credential WHERE provider='mock' AND role='PRIMARY' AND status='ACTIVE'"
                         " ORDER BY id LIMIT 1").fetchone()

    if not _ip_allowed(ip, cidrs):
        return _deny(403, "ip_not_allowed", ip, token)
    if not tok or tok["status"] != "ACTIVE":
        return _deny(401, "invalid_token", ip, token)
    if not svc or svc["status"] != "ACTIVE":
        return _deny(403, "service_suspended", ip, token)
    if not cred:
        return JSONResponse({"error": "no_active_credential"}, status_code=503)

    body = await request.json()
    client = httpx.AsyncClient(timeout=60)
    try:
        key = await _upstream_key(client, cred)
        req = client.build_request("POST", f"{PROVIDERS['mock']}/v1/chat/completions", json=body,
                                   headers={"Authorization": f"Bearer {key}"})
        resp = await client.send(req, stream=bool(body.get("stream")))
    except httpx.HTTPError:
        await client.aclose()
        return _bad_gateway(cred)

    if resp.status_code == 401:
        await resp.aclose()
        await client.aclose()
        return _bad_gateway(cred)

    if not body.get("stream"):
        await client.aclose()
        data = resp.json()
        if resp.status_code == 200 and data.get("usage"):
            _log_usage(svc["id"], cred["id"], data.get("model", body.get("model")), data["usage"], resp.status_code)
        return JSONResponse(data, status_code=resp.status_code)

    async def relay():
        usage = None
        try:
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        usage = json.loads(line[6:]).get("usage") or usage
                    except ValueError:
                        pass
                yield line + "\n"
        finally:
            await resp.aclose()
            await client.aclose()
            if usage and resp.status_code == 200:
                _log_usage(svc["id"], cred["id"], body.get("model"), usage, resp.status_code)

    return StreamingResponse(relay(), status_code=resp.status_code, media_type="text/event-stream")


# ---------------- C. 관리자 CRUD ----------------

def _admin(x_admin_key: str = Header("")):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "invalid_admin_key")


admin = APIRouter(prefix="/admin", dependencies=[Depends(_admin)])


def _cred_out(r) -> dict:  # 원문·secret_enc 비노출
    return {"id": r["id"], "provider": r["provider"], "auth_mode": r["auth_mode"], "role": r["role"],
            "status": r["status"], "masked": r["masked"], "audience": r["audience"],
            "provider_ref_masked": mask_key(r["provider_ref"]), "replaced_by_id": r["replaced_by_id"],
            "created_at": r["created_at"]}


class CredIn(BaseModel):
    provider: str = "mock"
    auth_mode: str
    role: str
    secret: Optional[str] = None
    audience: Optional[str] = None


@admin.get("/credentials")
def list_credentials():
    with get_conn() as c:
        return [_cred_out(r) for r in c.execute("SELECT * FROM credential ORDER BY id")]


@admin.post("/credentials")
def create_credential(b: CredIn):
    if b.provider not in PROVIDERS or b.role not in ("PRIMARY", "STANDBY"):
        raise HTTPException(400, "invalid provider/role")
    if b.auth_mode == "API_KEY" and b.secret:
        enc, masked, ref = vault.encrypt(b.secret), mask_key(b.secret), b.secret
    elif b.auth_mode == "WIF" and b.audience:
        enc, masked, ref = None, f"wif:{b.audience}", f"wif:{b.audience}"
    else:
        raise HTTPException(400, "API_KEY needs secret, WIF needs audience")
    with get_conn() as c:
        if b.role == "PRIMARY" and c.execute(
                "SELECT 1 FROM credential WHERE provider=? AND role='PRIMARY' AND status='ACTIVE'", (b.provider,)).fetchone():
            raise HTTPException(400, "active PRIMARY already exists")
        cur = c.execute("INSERT INTO credential(provider,auth_mode,role,secret_enc,masked,audience,provider_ref,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (b.provider, b.auth_mode, b.role, enc, masked, b.audience, ref, now_iso()))
        return _cred_out(c.execute("SELECT * FROM credential WHERE id=?", (cur.lastrowid,)).fetchone())


@admin.delete("/credentials/{cid}")
def delete_credential(cid: int):
    with get_conn() as c:
        c.execute("UPDATE credential SET status='REVOKED' WHERE id=?", (cid,))
    return {"ok": True}


@admin.get("/settings")
def get_settings():
    with get_conn() as c:
        r = c.execute("SELECT value FROM setting WHERE key='loss_limit_krw'").fetchone()
    return {"loss_limit_krw": int(r["value"]) if r else 100_000}


class SettingsIn(BaseModel):
    loss_limit_krw: int


@admin.put("/settings")
def put_settings(b: SettingsIn):
    if b.loss_limit_krw <= 0:
        raise HTTPException(400, "loss_limit_krw must be > 0")
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO setting(key,value) VALUES('loss_limit_krw',?)", (str(b.loss_limit_krw),))
    return {"loss_limit_krw": b.loss_limit_krw}


class CidrIn(BaseModel):
    cidr: str
    label: str = "INTERNAL"


@admin.get("/cidrs")
def list_cidrs():
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT id,cidr,label FROM allowed_cidr ORDER BY id")]


@admin.post("/cidrs")
def add_cidr(b: CidrIn):
    try:
        cidr = str(ipaddress.ip_network(b.cidr, strict=False))
    except ValueError:
        raise HTTPException(400, "invalid cidr")
    with get_conn() as c:
        cur = c.execute("INSERT INTO allowed_cidr(cidr,label) VALUES(?,?)", (cidr, b.label))
    return {"id": cur.lastrowid, "cidr": cidr, "label": b.label}


@admin.delete("/cidrs/{cid}")
def delete_cidr(cid: int):
    with get_conn() as c:
        c.execute("DELETE FROM allowed_cidr WHERE id=?", (cid,))
    return {"ok": True}


@admin.get("/services")
def list_services():
    with get_conn() as c:
        return [{**dict(s), "tokens": [dict(t) for t in c.execute(
            "SELECT id,token_masked,status,created_at FROM gateway_token WHERE service_id=? ORDER BY id", (s["id"],))]}
            for s in c.execute("SELECT id,name,status FROM service ORDER BY id").fetchall()]


class TokenIn(BaseModel):
    service_name: str


@admin.post("/tokens")
def issue_token(b: TokenIn):
    token = "nvm-" + secrets.token_urlsafe(24)
    with get_conn() as c:
        s = c.execute("SELECT id FROM service WHERE name=?", (b.service_name,)).fetchone()
        sid = s["id"] if s else c.execute("INSERT INTO service(name) VALUES(?)", (b.service_name,)).lastrowid
        cur = c.execute("INSERT INTO gateway_token(service_id,token_hash,token_masked,created_at) VALUES(?,?,?,?)",
                        (sid, hash_token(token), mask_key(token), now_iso()))
    return {"id": cur.lastrowid, "service_id": sid, "token": token, "token_masked": mask_key(token)}


@admin.delete("/tokens/{tid}")
def revoke_token(tid: int):
    with get_conn() as c:
        c.execute("UPDATE gateway_token SET status='REVOKED' WHERE id=?", (tid,))
    return {"ok": True}


@admin.get("/access-denied")
def access_denied(limit: int = 20):
    with get_conn() as c:
        rows = c.execute("SELECT * FROM access_denied_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "at": iso(r["at"])} for r in rows]


router.include_router(admin)

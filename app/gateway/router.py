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

from app import providers, vault
from app.contract import (ADMIN_KEY, DEFAULT_LIMIT_CALLS, DEFAULT_LIMIT_KRW, DEMO_MODE, LIVE_KEY_PATH,
                          OPENROUTER_MODEL, PROVIDERS, hash_token, mask_key)
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
blocked_ips: dict[str, float] = {}  # 허니키 사용 IP -> 차단 해제 epoch. ponytail: 메모리 보관, 재시작 시 사라짐
reset_gen = [0]  # /demo/reset마다 증가. 리셋 전에 시작한 중계는 usage_log를 쓰지 않는다(id 재사용 충돌 방지)
HONEY_BLOCK_S, HONEY_DEDUP_S = 600, 60


def _honeykey_hit(hk, ip: str, path: str):
    """허니키 사용: honeykey_event + HONEYKEY 이벤트 + 사고(60초 중복 방지) + IP 임시 차단."""
    now = time.time()
    blocked_ips[ip] = now + HONEY_BLOCK_S
    msg = f"허니키 사용 탐지: {hk['masked']}({hk['planted_at']}) 출발 IP {ip}"
    with get_conn() as c:
        dup = c.execute("SELECT 1 FROM honeykey_event WHERE honeykey_id=? AND source_ip=? AND at>?",
                        (hk["id"], ip, now - HONEY_DEDUP_S)).fetchone()
        c.execute("INSERT INTO honeykey_event(honeykey_id,source_ip,path,at) VALUES(?,?,?,?)", (hk["id"], ip, path, now))
        iid = None
        if not dup:
            tl = [{"at": iso(now), "step": "DETECTED", "ok": True, "detail": msg},
                  {"at": iso(now), "step": "NOTIFY_ADMIN", "ok": True,
                   "detail": f"관리자 통보, {ip} {HONEY_BLOCK_S}초 차단"}]
            iid = c.execute("INSERT INTO incident(credential_id,trigger_type,status,detected_at,blocked_at,timeline)"
                            " VALUES(NULL,'HONEYKEY','OPEN',?,?,?)", (now, now, json.dumps(tl, ensure_ascii=False))).lastrowid
        c.execute("INSERT INTO event(at,kind,message,data) VALUES(?,?,?,?)",
                  (now, "HONEYKEY", msg, json.dumps({"honeykey_id": hk["id"], "planted_at": hk["planted_at"],
                                                      "source_ip": ip, "incident_id": iid}, ensure_ascii=False)))


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


def _log_usage(service_id, cred, model, usage: dict, status_code: int):
    with get_conn() as c:
        c.execute("INSERT INTO usage_log(service_id,credential_id,model,input_tokens,output_tokens,cost_krw,status_code,at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (service_id, cred["id"], model, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                   providers.call_cost_krw(cred["provider"], usage), status_code, time.time()))


def _bad_gateway(cred):
    if cred["auth_mode"] == "WIF":
        _wif_cache.pop(cred["audience"], None)  # 폐기·만료 토큰 재사용 방지
    return JSONResponse({"error": "upstream_error"}, status_code=502)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(""),
                           x_demo_source_ip: Optional[str] = Header(None)):
    ip = x_demo_source_ip if (DEMO_MODE and x_demo_source_ip) else (request.client.host if request.client else "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""

    gen = reset_gen[0]
    with get_conn() as c:
        hk = c.execute("SELECT * FROM honeykey WHERE value_hash=?", (hash_token(token),)).fetchone() if token else None
        cidrs = c.execute("SELECT cidr FROM allowed_cidr").fetchall()
        tok = c.execute("SELECT * FROM gateway_token WHERE token_hash=?", (hash_token(token),)).fetchone() if token else None
        svc = c.execute("SELECT * FROM service WHERE id=?", (tok["service_id"],)).fetchone() if tok else None
        cred = c.execute("SELECT * FROM credential WHERE role='PRIMARY' AND status='ACTIVE'"
                         " ORDER BY id LIMIT 1").fetchone()

    if hk:  # 허니키는 IP 검사보다 먼저. 호출자에겐 일반 401로 숨긴다
        _honeykey_hit(hk, ip, request.url.path)
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    if blocked_ips.get(ip, 0) > time.time():
        return _deny(403, "ip_blocked", ip, token)
    if not _ip_allowed(ip, cidrs):
        return _deny(403, "ip_not_allowed", ip, token)
    if not tok or tok["status"] != "ACTIVE":
        return _deny(401, "invalid_token", ip, token)
    if not svc or svc["status"] != "ACTIVE":
        return _deny(403, "service_suspended", ip, token)
    if not cred:
        return JSONResponse({"error": "no_active_credential"}, status_code=503)

    body = await request.json()
    url = f"{PROVIDERS['mock']}/v1/chat/completions"
    if cred["provider"] == "openrouter":  # 실제 비용을 응답 usage.cost로 받는다
        m = str(body.get("model") or "")
        body = {**body, "model": m if m and not m.startswith(("mock", "demo")) else OPENROUTER_MODEL,
                "usage": {"include": True}}
        url = f"{PROVIDERS['openrouter']}/chat/completions"
    client = httpx.AsyncClient(timeout=60)
    try:
        key = await _upstream_key(client, cred)
        req = client.build_request("POST", url, json=body,
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
        if resp.status_code == 200 and data.get("usage") and gen == reset_gen[0]:
            _log_usage(svc["id"], cred, data.get("model", body.get("model")), data["usage"], resp.status_code)
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
            if usage and resp.status_code == 200 and gen == reset_gen[0]:
                _log_usage(svc["id"], cred, body.get("model"), usage, resp.status_code)

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
    if b.provider == "openrouter":
        raise HTTPException(400, "openrouter는 POST /admin/openrouter/provision 사용")
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


def insert_openrouter_cred(c, role: str, raw: str, key_hash: str) -> dict:
    """OpenRouter 키를 vault에 암호화 저장. provider_ref = OpenRouter 키 hash. PRIMARY면 라이브 페이지용 파일에도 저장."""
    cid = c.execute("INSERT INTO credential(provider,auth_mode,role,secret_enc,masked,provider_ref,created_at)"
                    " VALUES('openrouter','API_KEY',?,?,?,?,?)",
                    (role, vault.encrypt(raw), mask_key(raw), key_hash, now_iso())).lastrowid
    if role == "PRIMARY":
        with open(LIVE_KEY_PATH, "w", encoding="utf-8") as f:  # data/ 는 gitignore
            json.dump({"key": raw}, f)
    return {"id": cid, "masked": mask_key(raw), "role": role, "hash_masked": mask_key(key_hash)}


class ProvisionIn(BaseModel):
    role: str
    limit_usd: float = 0.5


@admin.post("/openrouter/provision")
async def provision_openrouter(b: ProvisionIn):
    if b.role not in ("PRIMARY", "STANDBY") or b.limit_usd <= 0:
        raise HTTPException(400, "invalid role/limit_usd")
    with get_conn() as c:
        if b.role == "PRIMARY" and c.execute("SELECT 1 FROM credential WHERE role='PRIMARY' AND status='ACTIVE'").fetchone():
            raise HTTPException(400, "active PRIMARY already exists")
    async with httpx.AsyncClient(timeout=20) as http:
        raw, h = await providers.or_create_key(http, f"november-{b.role}-{secrets.token_hex(3)}", b.limit_usd)
    with get_conn() as c:
        out = insert_openrouter_cred(c, b.role, raw, h)
    return out | ({"leaked_key": raw} if b.role == "PRIMARY" else {})


SETTING_DEFAULTS = {"unauthorized_call_limit": DEFAULT_LIMIT_CALLS, "loss_limit_krw": DEFAULT_LIMIT_KRW}


@admin.get("/settings")
def get_settings():
    with get_conn() as c:
        rows = dict(c.execute("SELECT key,value FROM setting").fetchall())
    return {k: int(rows.get(k, d)) for k, d in SETTING_DEFAULTS.items()}


class SettingsIn(BaseModel):
    unauthorized_call_limit: Optional[int] = None
    loss_limit_krw: Optional[int] = None


@admin.put("/settings")
def put_settings(b: SettingsIn):
    sent = b.model_dump(exclude_none=True)
    for k, v in sent.items():
        if v <= 0:
            raise HTTPException(400, f"{k} must be > 0")
    with get_conn() as c:
        c.executemany("INSERT OR REPLACE INTO setting(key,value) VALUES(?,?)", [(k, str(v)) for k, v in sent.items()])
    return get_settings()


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


def _set_service_status(sid: int, status: str):
    with get_conn() as c:
        if not c.execute("UPDATE service SET status=? WHERE id=?", (status, sid)).rowcount:
            raise HTTPException(404, "service not found")
    return {"id": sid, "status": status}


@admin.post("/services/{sid}/suspend")
def suspend_service(sid: int):
    return _set_service_status(sid, "SUSPENDED")


@admin.post("/services/{sid}/resume")
def resume_service(sid: int):
    return _set_service_status(sid, "ACTIVE")


@admin.get("/honeykeys")
def list_honeykeys():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT h.id,h.masked,h.planted_at,h.created_at,COUNT(e.id) AS hits FROM honeykey h "
            "LEFT JOIN honeykey_event e ON e.honeykey_id=h.id GROUP BY h.id ORDER BY h.id")]


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

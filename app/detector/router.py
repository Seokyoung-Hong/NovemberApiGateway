"""탐지·대응 워커, 대시보드 API, 데모 공격/리셋 (계약 D절)."""
import asyncio
import json
import secrets
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app import vault
from app.contract import (ADMIN_KEY, ATTACK_MAX_TOKENS, ATTACK_RPS, BLOCK_RATIO, DEFAULT_LIMIT_CALLS,
                          LEAKED_KEY, MOCK_USAGE_LAG_S, POLL_INTERVAL_S, PROVIDERS, WARN_RATIO,
                          effective_calls_diff, effective_diff, hash_token, judge, mask_key)
from app.db import get_conn
from app.gateway.router import _wif_cache, blocked_ips, reset_gen

MOCK = PROVIDERS["mock"]
router = APIRouter()
lock = asyncio.Lock()  # ponytail: 워커 1틱·리셋을 직렬화하는 전역 락. 다중 워커 배포면 DB 락으로.
state = {"attack": None, "judged": {}, "updated_at": None, "tasks": set(), "honeykeys": {}}
# judged: credential_id -> 최근 판정, tasks: 대응 백그라운드 작업(리셋 시 취소), honeykeys: planted_at -> 원문(데모용)


def spawn(coro):
    t = asyncio.create_task(coro)
    state["tasks"].add(t)
    t.add_done_callback(state["tasks"].discard)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None


def admin(x_admin_key: str = Header(None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "bad admin key")


def limit_krw(c) -> float:
    return float(c.execute("SELECT value FROM setting WHERE key='loss_limit_krw'").fetchone()[0])


def limit_calls(c) -> int:
    r = c.execute("SELECT value FROM setting WHERE key='unauthorized_call_limit'").fetchone()
    return int(r[0]) if r else DEFAULT_LIMIT_CALLS


def calls(c, cid):
    """(gateway 호출 수, provider 최신 스냅샷 호출 수). gateway는 공급자 집계와 같은 시점까지만 센다
    (usage_log 중 at <= 스냅샷 시각 - MOCK_USAGE_LAG_S). 안 맞추면 직원 호출이 차액을 깎아 판정이 출렁인다."""
    p = c.execute("SELECT requests,at FROM provider_usage WHERE credential_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    if not p:
        return 0, 0
    g = c.execute("SELECT COUNT(*) FROM usage_log WHERE credential_id=? AND at<=?",
                  (cid, p["at"] - MOCK_USAGE_LAG_S)).fetchone()[0]
    return g, p["requests"]


def event(c, kind, message, data=None):
    c.execute("INSERT INTO event(at,kind,message,data) VALUES(?,?,?,?)",
              (time.time(), kind, message, json.dumps(data, ensure_ascii=False) if data else None))


def costs(c, cid):
    """(gateway 누적, provider 최신 스냅샷) 원."""
    g = c.execute("SELECT COALESCE(SUM(cost_krw),0) FROM usage_log WHERE credential_id=?", (cid,)).fetchone()[0]
    p = c.execute("SELECT cost_krw FROM provider_usage WHERE credential_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    return g, (p[0] if p else 0.0)


async def bearer(http, cred) -> str:
    if cred["auth_mode"] == "API_KEY":
        return vault.decrypt(cred["secret_enc"])
    r = await http.post(f"{MOCK}/sts/token", json={"audience": cred["audience"], "identity": "november-detector"})
    r.raise_for_status()
    return r.json()["token"]


# ---------------- 워커 ----------------
async def worker_loop():
    async with httpx.AsyncClient(timeout=5) as http:
        while True:
            try:
                async with lock:
                    await tick(http)
            except Exception as e:  # 워커는 죽지 않는다
                print("detector tick error:", repr(e))
            await asyncio.sleep(POLL_INTERVAL_S)


async def tick(http):
    with get_conn() as c:
        creds = c.execute("SELECT * FROM credential WHERE status='ACTIVE' ORDER BY id").fetchall()
    for cred in creds:
        u = (await http.get(f"{MOCK}/usage", params={"owner": cred["provider_ref"]})).json()
        with get_conn() as c:
            c.execute("INSERT INTO provider_usage(credential_id,cost_krw,tokens,requests,at) VALUES(?,?,?,?,?)",
                      (cred["id"], u["cost_krw"], u["tokens"], u.get("requests", 0), time.time()))
            gc, pc = calls(c, cred["id"])
            diff, lim = effective_calls_diff(pc, gc), limit_calls(c)
            g, p = costs(c, cred["id"])
            krw = (effective_diff(p, g), limit_krw(c))  # 참고 표시용
            v = judge(diff, lim)
            prev = state["judged"].get(cred["id"], "NORMAL")
            state["judged"][cred["id"]] = v
            if v in ("SUSPECT", "WARNING") and v != prev:
                event(c, v, f"{cred['masked']} 설명되지 않는 호출 {diff}건 ({diff / lim:.0%}, 참고 {krw[0]:,.0f}원)",
                      {"credential_id": cred["id"], "diff_calls": diff, "diff_krw": krw[0]})
        if v == "BREACH":
            await respond(http, cred, diff, lim, *krw)
    state["updated_at"] = time.time()


STEP_DELAY_S = 1.0  # 데모 연출: 폐기 이후 단계 사이 간격(차단 자체는 지연 없음)
RECOVERED_HOLD_S = 10  # RECOVERED 표시 유지 시간


def save_tl(iid, tl, **cols):
    sets = "".join(f",{k}=?" for k in cols)
    with get_conn() as c:
        c.execute(f"UPDATE incident SET timeline=?{sets} WHERE id=?", (json.dumps(tl), *cols.values(), iid))


async def respond(http, cred, diff, lim, diff_krw, lim_krw):
    """BREACH 자동 대응. 탐지·폐기는 즉시(워커 락 안), 나머지는 백그라운드로 연출 지연. 크레덴셜당 incident 1개.
    diff/lim = 호출 수(판정 기준), diff_krw/lim_krw = 참고 금액."""
    with get_conn() as c:
        if c.execute("SELECT 1 FROM incident WHERE credential_id=?", (cred["id"],)).fetchone():
            return
        tl = [{"at": iso(time.time()), "step": "DETECTED", "ok": True,
               "detail": f"설명되지 않는 호출 {diff}건 ≥ 차단선 {lim * BLOCK_RATIO:g}건 (참고 {diff_krw:,.0f}원)"}]
        iid = c.execute("INSERT INTO incident(credential_id,status,detected_at,loss_krw,limit_krw,loss_calls,limit_calls,"
                        "timeline) VALUES(?,?,?,?,?,?,?,?)", (cred["id"], "OPEN", time.time(), diff_krw, lim_krw, diff,
                                                              lim, json.dumps(tl))).lastrowid
    r = await http.post(f"{MOCK}/revoke", json={"owner": cred["provider_ref"]})
    tl.append({"at": iso(time.time()), "step": "REVOKE_PROVIDER_KEY", "detail": f"{cred['masked']} 공급자 폐기",
               "ok": r.status_code == 200})
    with get_conn() as c:  # 폐기·승격은 즉시 DB 반영(직원 호출 503 방지)
        c.execute("UPDATE credential SET status='REVOKED' WHERE id=?", (cred["id"],))
        sb = c.execute("SELECT * FROM credential WHERE provider=? AND role='STANDBY' AND status='ACTIVE' "
                       "ORDER BY id LIMIT 1", (cred["provider"],)).fetchone()
        if sb:
            c.execute("UPDATE credential SET role='PRIMARY' WHERE id=?", (sb["id"],))
            c.execute("UPDATE credential SET replaced_by_id=? WHERE id=?", (sb["id"], cred["id"]))
    save_tl(iid, tl, blocked_at=time.time(), new_credential_id=sb["id"] if sb else None)
    spawn(final_loss(cred, iid))
    spawn(recover(cred, iid, tl, diff, lim, sb))


async def recover(cred, iid, tl, diff, lim, sb):
    # 데모 연출: PROMOTE_STANDBY·NOTIFY_ADMIN 시각은 실제 처리(폐기 직후 DB 반영) 시각이 아니라 1초 간격 표시용.
    def step(name, detail, ok=True):
        tl.append({"at": iso(time.time()), "step": name, "detail": detail, "ok": ok})

    await asyncio.sleep(STEP_DELAY_S)
    if sb:
        step("PROMOTE_STANDBY", f"{sb['masked']}({sb['auth_mode']}) PRIMARY 승격")
        save_tl(iid, tl, status="ROTATED")
    else:
        step("PROMOTE_STANDBY", "예비 키 없음", False)
        save_tl(iid, tl)

    await asyncio.sleep(STEP_DELAY_S)
    step("NOTIFY_ADMIN", "관리자 통보(BLOCKED 이벤트)")
    with get_conn() as c:
        event(c, "BLOCKED", f"{cred['masked']} 자동 차단. 설명되지 않는 호출 {diff}건 / 한도 {lim}건",
              {"incident_id": iid, "credential_id": cred["id"], "new_credential_id": sb["id"] if sb else None})
    save_tl(iid, tl)
    if not sb:
        return

    await asyncio.sleep(STEP_DELAY_S)
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            key = await bearer(http, sb)
            v = await http.post(f"{MOCK}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                json={"model": "mock", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1})
        ok = v.status_code == 200
    except Exception:
        ok = False
    step("VERIFIED", f"{sb['masked']} 호출 {'성공' if ok else '실패'}", ok)
    if ok:
        save_tl(iid, tl, status="RESOLVED", resolved_at=time.time())
        with get_conn() as c:
            event(c, "RECOVERED", f"{sb['masked']}로 전환 완료, 서비스 정상", {"incident_id": iid})
    else:
        save_tl(iid, tl)


async def final_loss(cred, iid):
    """폐기 후 집계 지연이 지나면 최종 손실(공급자-게이트웨이)로 갱신."""
    await asyncio.sleep(MOCK_USAGE_LAG_S + 0.5)
    async with httpx.AsyncClient(timeout=5) as http:
        u = (await http.get(f"{MOCK}/usage", params={"owner": cred["provider_ref"]})).json()
    with get_conn() as c:
        g, _ = costs(c, cred["id"])
        c.execute("INSERT INTO provider_usage(credential_id,cost_krw,tokens,requests,at) VALUES(?,?,?,?,?)",
                  (cred["id"], u["cost_krw"], u["tokens"], u.get("requests", 0), time.time()))
        gc, _ = calls(c, cred["id"])
        c.execute("UPDATE incident SET loss_krw=?, loss_calls=? WHERE id=?",
                  (max(0.0, u["cost_krw"] - g), max(0, u.get("requests", 0) - gc), iid))


# ---------------- 대시보드 ----------------
@router.get("/admin/dashboard", dependencies=[Depends(admin)])
def dashboard():
    with get_conn() as c:
        lim, lc = limit_krw(c), limit_calls(c)
        rows, worst, gt, pt, dt, gct, pct, dct = [], "NORMAL", 0.0, 0.0, 0.0, 0, 0, 0
        order = ["NORMAL", "SUSPECT", "WARNING", "BREACH"]
        for cr in c.execute("SELECT * FROM credential ORDER BY id").fetchall():
            g, p = costs(c, cr["id"])
            gc, pc = calls(c, cr["id"])
            d, dc = effective_diff(p, g), effective_calls_diff(pc, gc)
            gt, pt, dt, gct, pct, dct = gt + g, pt + p, dt + d, gct + gc, pct + pc, dct + dc
            if cr["status"] == "ACTIVE":
                worst = max(worst, judge(dc, lc), key=order.index)
            rows.append({k: cr[k] for k in ("id", "provider", "auth_mode", "role", "status", "masked")}
                        | {"gateway_cost_krw": g, "provider_cost_krw": p, "diff_krw": d,
                           "gateway_calls": gc, "provider_calls": pc, "diff_calls": dc})
        inc = c.execute("SELECT * FROM incident WHERE trigger_type!='HONEYKEY' ORDER BY id DESC LIMIT 1").fetchone()
        status = "WARNING" if worst == "BREACH" else worst  # BREACH는 워커가 곧 BLOCKED로 바꾼다
        if inc and inc["status"] != "RESOLVED":
            status = "BLOCKED"
        elif inc and time.time() - inc["resolved_at"] < RECOVERED_HOLD_S:
            status = "RECOVERED"
        hits = [{"at": iso(r["at"]), "honeykey_masked": r["masked"], "planted_at": r["planted_at"],
                 "source_ip": r["source_ip"]} for r in c.execute(
            "SELECT e.at,e.source_ip,h.masked,h.planted_at FROM honeykey_event e JOIN honeykey h ON h.id=e.honeykey_id "
            "ORDER BY e.id DESC LIMIT 5")]
        act = c.execute("SELECT id,provider,auth_mode,masked FROM credential "
                        "WHERE role='PRIMARY' AND status='ACTIVE' ORDER BY id LIMIT 1").fetchone()
    return {"status": status, "limit_krw": lim, "warn_at_krw": lim * WARN_RATIO, "block_at_krw": lim * BLOCK_RATIO,
            "diff_krw": dt, "gateway_cost_krw": gt, "provider_cost_krw": pt,
            "limit_calls": lc, "warn_at_calls": lc * WARN_RATIO, "block_at_calls": lc * BLOCK_RATIO,
            "diff_calls": dct, "gateway_calls": gct, "provider_calls": pct,
            "active_credential": dict(act) if act else None, "credentials": rows,
            "attack_running": attack_running(), "incident_id": inc["id"] if inc and status in ("BLOCKED", "RECOVERED") else None,
            "updated_at": iso(state["updated_at"] or time.time()), "honeykey_hits": hits,
            "blocked_ips": [{"source_ip": ip, "until": iso(u)} for ip, u in blocked_ips.items() if u > time.time()]}


@router.get("/admin/timeseries", dependencies=[Depends(admin)])
def timeseries(range: int = 300):
    now = time.time()
    start = now - range
    with get_conn() as c:
        ul = c.execute("SELECT at,cost_krw FROM usage_log ORDER BY at").fetchall()
        pu = c.execute("SELECT credential_id,at,cost_krw,requests FROM provider_usage ORDER BY at").fetchall()
    points, gi, pi, g, last = [], 0, 0, 0.0, {}
    t = start - start % 2
    while t <= now + 2:  # ponytail: 버킷마다 앞에서부터 누적 스캔. 데이터가 커지면 SQL 집계로.
        while gi < len(ul) and ul[gi]["at"] <= t:
            g += ul[gi]["cost_krw"]; gi += 1
        while pi < len(pu) and pu[pi]["at"] <= t:
            last[pu[pi]["credential_id"]] = (pu[pi]["cost_krw"], pu[pi]["requests"]); pi += 1
        points.append({"t": iso(t), "gateway_krw": g, "provider_krw": sum(v[0] for v in last.values()),
                       "gateway_calls": gi, "provider_calls": sum(v[1] for v in last.values())})
        t += 2
    return {"points": points}


def incident_json(c, r):
    m = lambda cid: (c.execute("SELECT masked FROM credential WHERE id=?", (cid,)).fetchone() or [None])[0]
    return {"id": r["id"], "trigger_type": r["trigger_type"], "status": r["status"], "credential_id": r["credential_id"],
            "credential_masked": m(r["credential_id"]), "detected_at": iso(r["detected_at"]),
            "blocked_at": iso(r["blocked_at"]), "resolved_at": iso(r["resolved_at"]),
            "duration_s": round(r["blocked_at"] - r["detected_at"], 3) if r["blocked_at"] else None,
            "loss_krw": r["loss_krw"], "limit_krw": r["limit_krw"],
            "loss_calls": r["loss_calls"], "limit_calls": r["limit_calls"], "new_credential_id": r["new_credential_id"],
            "new_credential_masked": m(r["new_credential_id"]), "timeline": json.loads(r["timeline"])}


@router.get("/admin/incidents", dependencies=[Depends(admin)])
def incidents():
    with get_conn() as c:
        return [incident_json(c, r) for r in c.execute("SELECT * FROM incident ORDER BY id DESC").fetchall()]


@router.get("/admin/incidents/{iid}", dependencies=[Depends(admin)])
def incident(iid: int):
    with get_conn() as c:
        r = c.execute("SELECT * FROM incident WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, "not found")
        return incident_json(c, r)


@router.get("/admin/events", dependencies=[Depends(admin)])
def events(since_id: int = 0):
    with get_conn() as c:
        rows = c.execute("SELECT * FROM event WHERE id>? ORDER BY id", (since_id,)).fetchall()
    return {"events": [{"id": r["id"], "at": iso(r["at"]), "kind": r["kind"], "message": r["message"],
                        "data": json.loads(r["data"]) if r["data"] else None} for r in rows]}


# ---------------- 데모 ----------------
def attack_running():
    t = state["attack"]
    return bool(t and not t.done())


async def attack():
    with get_conn() as c:
        cred = c.execute("SELECT * FROM credential WHERE role='PRIMARY' AND status='ACTIVE' ORDER BY id LIMIT 1").fetchone()
    reason = "PRIMARY 키 없음"
    if cred:
        async with httpx.AsyncClient(timeout=5) as http:
            key = await bearer(http, cred)
            while True:
                t0 = time.time()
                r = await http.post(f"{MOCK}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                    json={"model": "mock", "messages": [{"role": "user", "content": "x"}],
                                          "max_tokens": ATTACK_MAX_TOKENS})
                if r.status_code == 401:
                    reason = f"{cred['masked']} 401(폐기됨)"
                    break
                await asyncio.sleep(max(0.0, 1 / ATTACK_RPS - (time.time() - t0)))
    with get_conn() as c:
        event(c, "ATTACK_STOPPED", f"공격 종료: {reason}")


@router.post("/demo/attack/start")
async def attack_start():
    if not attack_running():
        with get_conn() as c:
            event(c, "ATTACK_STARTED", f"유출 키로 Mock 직접 호출 시작 ({ATTACK_RPS}건/초)")
        state["attack"] = asyncio.create_task(attack())
        await asyncio.sleep(0)
    return {"attack_running": attack_running()}


async def stop_attack():
    t = state["attack"]
    if t and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return True
    return False


@router.post("/demo/attack/stop")
async def attack_stop():
    if await stop_attack():
        with get_conn() as c:
            event(c, "ATTACK_STOPPED", "공격 수동 종료")
    return {"attack_running": attack_running()}


@router.post("/demo/reset")
async def demo_reset():
    await stop_attack()
    for t in list(state["tasks"]):  # 직전 사고의 연출·손실 갱신 작업이 새 데이터(같은 id)를 건드리지 않게
        t.cancel()
    reset_gen[0] += 1
    blocked_ips.clear()
    _wif_cache.clear()
    async with lock:
        async with httpx.AsyncClient(timeout=5) as http:
            (await http.post(f"{MOCK}/demo/reset")).raise_for_status()
        state["judged"].clear()
        now = datetime.now(timezone.utc).isoformat()
        tokens = {}
        with get_conn() as c:
            for t in ("setting", "allowed_cidr", "service", "gateway_token", "credential", "usage_log",
                      "provider_usage", "access_denied_log", "incident", "event", "honeykey", "honeykey_event"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM sqlite_sequence")
            c.execute("INSERT INTO setting(key,value) VALUES('loss_limit_krw','100000')")
            c.execute("INSERT INTO setting(key,value) VALUES('unauthorized_call_limit',?)", (str(DEFAULT_LIMIT_CALLS),))
            c.executemany("INSERT INTO allowed_cidr(cidr,label) VALUES(?,?)",
                          [("127.0.0.1/32", "INTERNAL"), ("::1/128", "INTERNAL"),
                           ("10.0.0.0/16", "INTERNAL"), ("10.8.0.0/24", "VPN")])
            for name, role in (("K1", "PRIMARY"), ("S1", "STANDBY")):
                key = LEAKED_KEY if name == "K1" else f"sk-mock-{name}-{secrets.token_hex(4)}"
                c.execute("INSERT INTO credential(provider,auth_mode,role,secret_enc,masked,provider_ref,created_at) "
                          "VALUES('mock','API_KEY',?,?,?,?,?)", (role, vault.encrypt(key), mask_key(key), key, now))
            c.execute("INSERT INTO credential(provider,auth_mode,role,masked,audience,provider_ref,created_at) "
                      "VALUES('mock','WIF','STANDBY',?,'novem-wif','wif:novem-wif',?)", ("WIF:novem-wif", now))
            for name in ("직원A", "직원B"):
                sid = c.execute("INSERT INTO service(name) VALUES(?)", (name,)).lastrowid
                tok = "nvm-" + secrets.token_urlsafe(24)
                c.execute("INSERT INTO gateway_token(service_id,token_hash,token_masked,created_at) VALUES(?,?,?,?)",
                          (sid, hash_token(tok), mask_key(tok), now))
                tokens[name] = tok
            state["honeykeys"] = {}
            for planted in ("공개 저장소 .env.example", "사내 위키 설정 예시"):
                hk = "nvm-honey-" + secrets.token_urlsafe(18)
                c.execute("INSERT INTO honeykey(value_hash,masked,planted_at,created_at) VALUES(?,?,?,?)",
                          (hash_token(hk), mask_key(hk), planted, now))
                state["honeykeys"][planted] = hk
    return {"tokens": tokens, "honeykeys": state["honeykeys"]}


@router.post("/demo/honeypot/hit")
async def honeypot_hit(request: Request):
    """데모 버튼: 외부 공격자(203.0.113.77)가 유출된 허니키로 게이트웨이를 호출."""
    if not state["honeykeys"]:
        raise HTTPException(409, "honeykey 없음. /demo/reset 먼저")
    planted, hk = next(iter(state["honeykeys"].items()))
    ip = "203.0.113.77"
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(f"{str(request.base_url).rstrip('/')}/v1/chat/completions",
                            headers={"Authorization": f"Bearer {hk}", "X-Demo-Source-IP": ip},
                            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]})
    return {"source_ip": ip, "planted_at": planted, "status_code": r.status_code, "response": r.json()}

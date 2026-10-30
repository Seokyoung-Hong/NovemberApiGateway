"""탐지·대응 워커, 대시보드 API, 데모 공격/리셋 (계약 D절)."""
import asyncio
import json
import os
import secrets
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app import providers, vault
from app.clientip import admin_ok
from app.contract import (ADMIN_KEY, ATTACK_MAX_TOKENS, ATTACK_MAX_TOKENS_VLLM, ATTACK_RPS, CALL_COST_MAX_USD, CALL_COST_MIN_USD,
                          CALL_COST_RECENT_N, DEFAULT_CALL_COST_USD, DEFAULT_LIMIT_CALLS, HYSTERESIS_S, LEAKED_KEY,
                          LIVE_KEY_PATH, OPENROUTER_LIMIT_CALLS, POLL_INTERVAL_S, PROVIDERS, RATIOS, TOLERANCE_CALLS,
                          USD_TO_KRW, effective_calls_diff, effective_diff, hash_token, judge, mask_key)
from app.db import get_conn
from app.gateway.router import _wif_cache, blocked_ips, insert_provider_cred, reset_gen

MOCK = PROVIDERS["mock"]
router = APIRouter()
lock = asyncio.Lock()  # ponytail: 워커 1틱·리셋을 직렬화하는 전역 락. 다중 워커 배포면 DB 락으로.
state = {"attack": None, "judged": {}, "view": {}, "updated_at": None, "tasks": set(), "honeykeys": {}}
# judged: credential_id -> (판정, 하강 대기 시작 시각), view: credential_id -> 최근 assess 결과, tasks: 대응 백그라운드 작업(리셋 시 취소), honeykeys: planted_at -> 원문(데모용)


def spawn(coro):
    t = asyncio.create_task(coro)
    state["tasks"].add(t)
    t.add_done_callback(state["tasks"].discard)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None


def admin(x_admin_key: str = Header(None)):
    if not admin_ok(x_admin_key):
        raise HTTPException(401, "bad admin key")


def limit_krw(c) -> float:
    return float(c.execute("SELECT value FROM setting WHERE key='loss_limit_krw'").fetchone()[0])


def limit_calls(c) -> int:
    r = c.execute("SELECT value FROM setting WHERE key='unauthorized_call_limit'").fetchone()
    return int(r[0]) if r else DEFAULT_LIMIT_CALLS


ORDER = ["NORMAL", "SUSPECT", "WARNING", "BREACH"]
TOKENS_COL = "COALESCE(input_tokens,0)+COALESCE(output_tokens,0)"


def ratios(provider):
    return RATIOS.get(provider, RATIOS["mock"])


def _gw(c, cid, col="COUNT(*)", until=None, before=None):
    """게이트웨이 usage_log 집계(cid). until: at<=, before: at<."""
    q, args = f"SELECT {col} FROM usage_log WHERE credential_id=?", [cid]
    if until is not None:
        q, args = q + " AND at<=?", args + [until]
    if before is not None:
        q, args = q + " AND at<?", args + [before]
    return c.execute(q, args).fetchone()[0] or 0


def per_call(c, cid, metric):
    """비용(원)/토큰 -> 호출 수 환산용 1건 기대값: 최근 N건 평균을 [하한, 상한]으로 보정.
    ponytail: 사용자 호출과 무단 호출의 길이가 다르면 여전히 오차가 있다(추정치, calls_estimated=true)."""
    col, lo, hi, default = (("cost_krw", CALL_COST_MIN_USD * USD_TO_KRW, CALL_COST_MAX_USD * USD_TO_KRW,
                             DEFAULT_CALL_COST_USD * USD_TO_KRW) if metric == "cost" else (TOKENS_COL, 20, 1000, 150))
    avg = c.execute(f"SELECT AVG(v) FROM (SELECT {col} AS v FROM usage_log WHERE credential_id=? ORDER BY id DESC LIMIT ?)",
                    (cid, CALL_COST_RECENT_N)).fetchone()[0]
    return min(hi, max(lo, avg)) if avg else default


def assess(c, cred, u, now):
    """공급자 보고 u(채워진 필드만)와 게이트웨이 기록 비교. 전제: 공급자 값은 항상 게이트웨이 기록보다 늦다.
    - 정렬 비교(diff_calls, 표시용): buckets가 있으면 반영 완료 버킷(끝 <= now-lag_s) 누적 vs 같은 경계까지의 게이트웨이,
      requests면 스냅샷 vs (스냅샷-lag_s)까지의 게이트웨이, cost/tokens뿐이면 차액 / 1건 기대값으로 호출 수 추정.
    - 확실 유출(certain_diff_calls): 공급자 누적 > 게이트웨이 '현재까지 전체' 누적. 지연이 있어도 오탐이 없다.
    둘 다 TOLERANCE_CALLS(진행 중 요청, 승격 검증 호출 1건 등) 차감."""
    ad, cid = providers.get(cred["provider"]), cred["id"]
    total, est = _gw(c, cid), False
    if u.get("buckets") is not None:
        done = [b for b in u["buckets"] if b[1] <= now - ad.lag_s]
        edge = max((b[1] for b in done), default=None)
        pc, gc = sum(b[2] for b in done), (_gw(c, cid, before=edge) if edge else 0)
        certain = u["requests"] - total
    elif u.get("requests") is not None:
        gc, pc = _gw(c, cid, until=now - ad.lag_s), u["requests"]
        certain = pc - total
    else:
        cost = u.get("cost_usd") is not None
        col = "COALESCE(SUM(cost_krw),0)" if cost else f"COALESCE(SUM({TOKENS_COL}),0)"
        val = u["cost_usd"] * USD_TO_KRW if cost else (u.get("tokens") or 0)
        per = per_call(c, cid, "cost" if cost else "tokens")
        gc = _gw(c, cid, until=now - ad.lag_s)
        pc = gc + max(0, round((val - _gw(c, cid, col, until=now - ad.lag_s)) / per))
        certain, est = round((val - _gw(c, cid, col)) / per), True
    return {"gateway_calls": gc, "provider_calls": pc, "diff_calls": effective_calls_diff(pc, gc),
            "certain_diff_calls": max(0, certain - TOLERANCE_CALLS), "certain_raw": max(0, certain),
            "calls_estimated": est, "provider_total_calls": u.get("requests", pc)}


def snapshot(c, cred, u, a, now):
    c.execute("INSERT INTO provider_usage(credential_id,cost_krw,tokens,requests,at) VALUES(?,?,?,?,?)",
              (cred["id"], (u.get("cost_usd") or 0) * USD_TO_KRW, u.get("tokens") or 0, a["provider_total_calls"], now))


def settle(cid, raw, now):
    """히스테리시스: 상승은 즉시, 하강은 낮은 판정이 HYSTERESIS_S 동안 이어질 때만."""
    cur, low_since = state["judged"].get(cid, ("NORMAL", None))
    if ORDER.index(raw) >= ORDER.index(cur):
        state["judged"][cid] = (raw, None)
    elif low_since is None:
        state["judged"][cid] = (cur, now)
    elif now - low_since >= HYSTERESIS_S:
        state["judged"][cid] = (raw, None)
    return state["judged"][cid][0]


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
        try:
            u = await providers.get(cred["provider"]).fetch_usage(http, cred)
        except httpx.HTTPError as e:  # 한 공급자 조회 실패가 다른 크레덴셜 판정을 막지 않게
            print("usage fetch error:", cred["id"], repr(e)[:120])
            continue
        now = time.time()
        with get_conn() as c:
            a = assess(c, cred, u, now)
            snapshot(c, cred, u, a, now)
            state["view"][cred["id"]] = a
            # 안전 경로: 확실 유출 수가 정렬 비교보다 크면 그 값으로 판정(차단선을 넘으면 정렬값과 무관하게 즉시 BREACH)
            diff, lim = max(a["diff_calls"], a["certain_diff_calls"]), limit_calls(c)
            g, p = costs(c, cred["id"])
            krw = (effective_diff(p, g), limit_krw(c))  # 참고 표시용
            prev = state["judged"].get(cred["id"], ("NORMAL", None))[0]
            v = settle(cred["id"], judge(diff, lim, ratios(cred["provider"])), now)
            est = "(추정)" if a["calls_estimated"] else ""
            if v in ("SUSPECT", "WARNING") and v != prev:
                event(c, v, f"{cred['masked']} 설명되지 않는 호출 {diff}건{est} ({diff / lim:.0%}, 참고 {krw[0]:,.0f}원)",
                      {"credential_id": cred["id"], "diff_calls": diff, "certain_diff_calls": a["certain_diff_calls"],
                       "diff_krw": krw[0]})
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
               "detail": f"설명되지 않는 호출 {diff}건{'(추정)' if state['view'].get(cred['id'], {}).get('calls_estimated') else ''}"
                         f" ≥ 차단선 {lim * ratios(cred['provider'])[2]:g}건 (참고 {diff_krw:,.0f}원)"}]
        iid = c.execute("INSERT INTO incident(credential_id,status,detected_at,loss_krw,limit_krw,loss_calls,limit_calls,"
                        "timeline) VALUES(?,?,?,?,?,?,?,?)", (cred["id"], "OPEN", time.time(), diff_krw, lim_krw, diff,
                                                              lim, json.dumps(tl))).lastrowid
    ad = providers.get(cred["provider"])
    try:
        ok = await ad.revoke(http, cred)
    except httpx.HTTPError:
        ok = False
    lag = f", 공급자 반영까지 약 {ad.revoke_lag_s:g}초 소요" if ad.revoke_lag_s >= 1 else ""
    tl.append({"at": iso(time.time()), "step": "REVOKE_PROVIDER_KEY", "detail": f"{cred['masked']} 공급자 폐기{lag}",
               "ok": ok})
    with get_conn() as c:  # 폐기·승격은 즉시 DB 반영(사용자 호출 503 방지)
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
        async with httpx.AsyncClient(timeout=15) as http:
            key = await bearer(http, sb)
            ad = providers.get(sb["provider"])  # 실제 공급자는 실제 호출 1건(max_tokens=1)
            v = await http.post(ad.chat_url, headers={"Authorization": f"Bearer {key}"},
                                json={"model": ad.model,
                                      "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1})
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
    ad = providers.get(cred["provider"])
    await asyncio.sleep(ad.revoke_lag_s + ad.lag_s)  # 폐기 반영 동안 들어온 호출까지 공급자에 집계되도록
    async with httpx.AsyncClient(timeout=20) as http:
        u = await ad.fetch_usage(http, cred)
    now = time.time()
    with get_conn() as c:
        a = assess(c, cred, u, now)
        snapshot(c, cred, u, a, now)
        state["view"][cred["id"]] = a
        g, p = costs(c, cred["id"])
        c.execute("UPDATE incident SET loss_krw=?, loss_calls=? WHERE id=?",
                  (max(0.0, p - g), max(0, a["provider_calls"] - a["gateway_calls"], a["certain_raw"]), iid))


# ---------------- 대시보드 ----------------
@router.get("/admin/dashboard", dependencies=[Depends(admin)])
def dashboard():
    with get_conn() as c:
        lim, lc = limit_krw(c), limit_calls(c)
        rows, worst, gt, pt, dt, gct, pct, dct, cct = [], "NORMAL", 0.0, 0.0, 0.0, 0, 0, 0, 0
        for cr in c.execute("SELECT * FROM credential ORDER BY id").fetchall():
            g, p = costs(c, cr["id"])
            a = state["view"].get(cr["id"], {})  # 워커가 마지막 틱에 계산한 값
            gc, pc, dc, cc = (a.get(k, 0) for k in ("gateway_calls", "provider_calls", "diff_calls", "certain_diff_calls"))
            d = effective_diff(p, g)
            gt, pt, dt, gct, pct, dct, cct = gt + g, pt + p, dt + d, gct + gc, pct + pc, dct + dc, cct + cc
            if cr["status"] == "ACTIVE":
                worst = max(worst, state["judged"].get(cr["id"], ("NORMAL",))[0], key=ORDER.index)
            rows.append({k: cr[k] for k in ("id", "provider", "auth_mode", "role", "status", "masked")}
                        | {"gateway_cost_krw": g, "provider_cost_krw": p, "diff_krw": d,
                           "diff_usd": max(0.0, p - g) / USD_TO_KRW,
                           "gateway_calls": gc, "provider_calls": pc, "diff_calls": dc, "certain_diff_calls": cc,
                           "calls_estimated": a.get("calls_estimated", cr["provider"] == "openrouter")})
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
        cidrs = [r["cidr"] for r in c.execute("SELECT cidr FROM allowed_cidr")]
    _, warn_r, block_r = ratios(act["provider"] if act else "mock")
    return {"status": status, "limit_krw": lim, "warn_at_krw": lim * warn_r, "block_at_krw": lim * block_r,
            "diff_krw": dt, "diff_usd": max(0.0, pt - gt) / USD_TO_KRW, "gateway_cost_krw": gt, "provider_cost_krw": pt,
            "limit_calls": lc, "warn_at_calls": lc * warn_r, "block_at_calls": lc * block_r,
            "diff_calls": dct, "certain_diff_calls": cct, "gateway_calls": gct, "provider_calls": pct,
            "active_credential": dict(act) if act else None, "credentials": rows,
            "attack_running": attack_running(), "incident_id": inc["id"] if inc and status in ("BLOCKED", "RECOVERED") else None,
            "updated_at": iso(state["updated_at"] or time.time()), "honeykey_hits": hits,
            "blocked_ips": [{"source_ip": ip, "until": iso(u)} for ip, u in blocked_ips.items() if u > time.time()],
            "ip_policy": "restricted" if cidrs and "*" not in cidrs else "open"}


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
    prov = (c.execute("SELECT provider FROM credential WHERE id=?", (r["credential_id"],)).fetchone() or [None])[0]
    return {"id": r["id"], "trigger_type": r["trigger_type"], "status": r["status"], "credential_id": r["credential_id"],
            "calls_estimated": prov == "openrouter",  # 호출 수를 주지 않는 공급자(비용으로 추정)
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


def primary_cred():
    with get_conn() as c:
        return c.execute("SELECT * FROM credential WHERE role='PRIMARY' AND status='ACTIVE' ORDER BY id LIMIT 1").fetchone()


# 공격 시뮬레이터 대상 공급자 -> (표시 이름, model(None=어댑터 기본), max_tokens). 그 밖(실제 과금)은 409
ATTACK_TARGETS = {"mock": ("Mock", "mock", ATTACK_MAX_TOKENS), "vllm": ("vLLM", None, ATTACK_MAX_TOKENS_VLLM)}


async def attack():
    cred = primary_cred()
    reason = "PRIMARY 키 없음"
    if cred:
        ad = providers.get(cred["provider"])
        _, model, max_tokens = ATTACK_TARGETS[ad.name]
        async with httpx.AsyncClient(timeout=10) as http:
            key = await bearer(http, cred)
            while True:
                t0 = time.time()
                try:
                    r = await http.post(ad.chat_url, headers={"Authorization": f"Bearer {key}"},
                                        json={"model": model or ad.model, "messages": [{"role": "user", "content": "x"}],
                                              "max_tokens": max_tokens})
                except httpx.HTTPError:  # GPU 지연·일시 오류로 공격이 죽지 않게
                    await asyncio.sleep(1 / ATTACK_RPS)
                    continue
                if r.status_code == 401:
                    reason = f"{cred['masked']} 401(폐기됨)"
                    break
                await asyncio.sleep(max(0.0, 1 / ATTACK_RPS - (time.time() - t0)))
    with get_conn() as c:
        event(c, "ATTACK_STOPPED", f"공격 종료: {reason}")


@router.post("/demo/attack/start")
async def attack_start():
    cred = primary_cred()
    if cred and cred["provider"] not in ATTACK_TARGETS:  # 실제 과금 공급자(openrouter·openai)
        raise HTTPException(409, "이 공급자는 공격 시뮬레이터 대상 아님. /live 청중 호출로 시연하세요")
    if not attack_running():
        name = ATTACK_TARGETS[cred["provider"]][0] if cred else "Mock"
        with get_conn() as c:
            event(c, "ATTACK_STARTED", f"유출 키로 {name} 직접 호출 시작 ({ATTACK_RPS}건/초)")
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
    return await _reset("mock")


@router.post("/demo/reset-{provider}")
async def demo_reset_provider(provider: str):
    """reset과 같되 크레덴셜은 실제 공급자(openrouter|openai|vllm) 키 2개(K1 PRIMARY, S1 STANDBY)를 새로 만들어 시드."""
    if provider not in ("openrouter", "openai", "vllm"):
        raise HTTPException(404, "unknown provider")
    return await _reset(provider)


@router.post("/demo/cleanup-{provider}")
async def demo_cleanup(provider: str):
    """발표 후 정리: 이름이 november-로 시작하는 키(OpenRouter)·서비스 계정(OpenAI)만 삭제."""
    if provider not in ("openrouter", "openai"):
        raise HTTPException(404, "unknown provider")
    async with httpx.AsyncClient(timeout=30) as http:
        return {"deleted": await providers.get(provider).cleanup(http)}


async def _reset(provider: str):
    await stop_attack()
    for t in list(state["tasks"]):  # 직전 사고의 연출·손실 갱신 작업이 새 데이터(같은 id)를 건드리지 않게
        t.cancel()
    reset_gen[0] += 1
    blocked_ips.clear()
    _wif_cache.clear()
    async with lock:
        real = provider != "mock"
        keys = []  # (role, 원문, provider_ref). 네트워크는 DB 트랜잭션 밖에서
        async with httpx.AsyncClient(timeout=30) as http:
            (await http.post(f"{MOCK}/demo/reset", headers={"X-Admin-Key": ADMIN_KEY})).raise_for_status()
            if real:
                ad = providers.get(provider)
                await ad.cleanup(http)
                for role in ("PRIMARY", "STANDBY"):
                    keys.append((role, *await ad.provision(http, role, None, 0.5)))
        if provider not in ("openrouter", "vllm"):
            try:
                os.remove(LIVE_KEY_PATH)  # 라이브 페이지를 Mock 유출 키(LEAKED_KEY)로 되돌린다
            except FileNotFoundError:
                pass
        state["judged"].clear()
        state["view"].clear()
        now = datetime.now(timezone.utc).isoformat()
        tokens = {}
        with get_conn() as c:
            for t in ("trusted_identity", "setting", "allowed_cidr", "service", "gateway_token", "credential", "usage_log",
                      "provider_usage", "access_denied_log", "incident", "event", "honeykey", "honeykey_event"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM sqlite_sequence")
            c.execute("INSERT INTO setting(key,value) VALUES('loss_limit_krw','100000')")
            c.execute("INSERT INTO setting(key,value) VALUES('unauthorized_call_limit',?)",
                      (str(OPENROUTER_LIMIT_CALLS if real else DEFAULT_LIMIT_CALLS),))
            for role, raw, ref in keys:
                insert_provider_cred(c, provider, role, raw, ref)
            if not real:
                for name, role in (("K1", "PRIMARY"), ("S1", "STANDBY")):
                    key = LEAKED_KEY if name == "K1" else f"sk-mock-{name}-{secrets.token_hex(4)}"
                    c.execute("INSERT INTO credential(provider,auth_mode,role,secret_enc,masked,provider_ref,created_at) "
                              "VALUES('mock','API_KEY',?,?,?,?,?)", (role, vault.encrypt(key), mask_key(key), key, now))
                c.execute("INSERT INTO credential(provider,auth_mode,role,masked,audience,provider_ref,created_at) "
                          "VALUES('mock','WIF','STANDBY',?,'novem-wif','wif:novem-wif',?)", ("WIF:novem-wif", now))
            for name in ("userA", "userB"):
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
    return {"tokens": tokens, "honeykeys": state["honeykeys"],
            "note": "trusted_identity(WIF 신뢰 규칙)도 삭제됨. WIF는 POST /demo/wif/seed 재실행"} | ({"leaked_key": keys[0][1]} if real else {})


@router.post("/demo/honeypot/hit")
async def honeypot_hit(request: Request):
    """데모 버튼: 외부 공격자(203.0.113.77)가 유출된 허니키로 게이트웨이를 호출."""
    if not state["honeykeys"]:
        raise HTTPException(409, "honeykey 없음. /demo/reset 먼저")
    planted, hk = next(iter(state["honeykeys"].items()))
    ip = "203.0.113.77"
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(f"{str(request.base_url).rstrip('/')}/v1/chat/completions",
                            headers={"Authorization": f"Bearer {hk}", "X-Demo-Source-IP": ip, "X-Admin-Key": ADMIN_KEY},
                            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]})
    return {"source_ip": ip, "planted_at": planted, "status_code": r.status_code, "response": r.json()}

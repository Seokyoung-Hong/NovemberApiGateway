"""데모 리허설: 서버(Mock 9000 + Gateway 8000) 기동 후 `.venv/Scripts/python scripts/rehearse.py`"""
import sys
import threading
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")
GW, ADM = "http://127.0.0.1:8000", {"X-Admin-Key": "demo-admin"}
BODY = {"model": "mock", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50}
c = httpx.Client(timeout=10)
results = []  # (이름, 통과, 설명)


def check(name, ok, info=""):
    results.append((name, ok, info))
    print(f"[{'OK' if ok else 'NG'}] {name} {info}")


def call(tok, ip=None):
    h = {"Authorization": f"Bearer {tok}"} | ({"X-Demo-Source-IP": ip} if ip else {})
    return c.post(f"{GW}/v1/chat/completions", headers=h, json=BODY).status_code


def dash():
    return c.get(f"{GW}/admin/dashboard", headers=ADM).json()


print("== 1. 리셋")
toks = c.post(f"{GW}/demo/reset").json()["tokens"]
check("리셋·토큰 발급", set(toks) == {"직원A", "직원B"}, str(list(toks)))

print("== 2. 정상 호출 / 허용 밖 IP")
for n, t in toks.items():
    check(f"{n} 정상 호출 200", (s := call(t)) == 200, f"status={s}")
s = call(toks["직원A"], "203.0.113.9")
check("허용 밖 IP 403", s == 403, f"status={s}")

print("== 3. 공격 시작 (직원은 0.5초 간격 호출 유지)")
stop, stats = threading.Event(), {"ok": 0, "fail": 0}


def employee():
    i = 0
    while not stop.is_set():
        try:
            s = call(list(toks.values())[i % 2])
        except Exception:
            s = 0
        stats["ok" if s == 200 else "fail"] += 1
        i += 1
        stop.wait(0.5)


th = threading.Thread(target=employee, daemon=True)
th.start()
c.post(f"{GW}/demo/attack/start")
t0, seen, last, recovered = time.time(), [], None, False
while time.time() - t0 < 90:
    d = dash()
    if d["status"] != last or int(time.time() - t0) % 5 == 0:
        print(f"  t={time.time() - t0:5.1f}s  {d['status']:<9} 차액={d['diff_krw']:>9,.0f}원 "
              f"(게이트웨이 {d['gateway_cost_krw']:,.0f} / 공급자 {d['provider_cost_krw']:,.0f})")
    if d["status"] != last:
        seen.append(d["status"])
        last = d["status"]
    if d["status"] == "RECOVERED":
        recovered = True
        break
    time.sleep(1)
for _ in range(4):  # 직원 호출이 새 키로 계속 성공하는지 잠시 더 관찰
    time.sleep(0.5)
stop.set()
th.join()
check("상태 전이 관찰", all(s in seen for s in ("SUSPECT", "WARNING", "BLOCKED", "RECOVERED")), "->".join(seen))
check("차단 중 직원 호출 실패 0건", stats["fail"] == 0, f"성공 {stats['ok']} / 실패 {stats['fail']}")
check("차단 후 직원 호출 200", all(call(t) == 200 for t in toks.values()))

print("== 4. 사고 상세")
inc = c.get(f"{GW}/admin/incidents", headers=ADM).json()
if inc:
    time.sleep(3)  # 최종 손실 재계산(폐기 2.5초 후) 대기
    i = c.get(f"{GW}/admin/incidents/{inc[0]['id']}", headers=ADM).json()
    print(f"  손실 {i['loss_krw']:,.0f}원 / 한도 {i['limit_krw']:,.0f}원, 차단 소요 {i['duration_s']}s, "
          f"{i['credential_masked']} -> {i['new_credential_masked']}, 상태 {i['status']}")
    for t in i["timeline"]:
        print(f"    {t['at'][11:23]} {t['step']:<19} {'OK' if t['ok'] else 'NG'} {t['detail']}")
    check("손실 < 한도", 0 < i["loss_krw"] < i["limit_krw"], f"{i['loss_krw']:,.0f} < {i['limit_krw']:,.0f}")
    check("사고 RESOLVED", i["status"] == "RESOLVED")
else:
    check("사고 생성", False)

print("== 5. 허니키 (엔드포인트가 있을 때만)")
r = c.post(f"{GW}/demo/honeypot/hit")
if r.status_code == 404:
    print("  /demo/honeypot/hit 없음 -> 건너뜀")
else:
    check("허니키 hit 응답", r.status_code < 400, f"status={r.status_code} {r.text[:80]}")

bad = [n for n, ok, _ in results if not ok]
print(f"\n=== {'PASS' if not bad else 'FAIL: ' + ', '.join(bad)} ({len(results) - len(bad)}/{len(results)})")
sys.exit(1 if bad else 0)

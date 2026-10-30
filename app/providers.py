"""공급자 어댑터: 'mock' | 'openrouter'. 사용액 조회·폐기·응답 비용 추출·OpenRouter 키 관리. 계약: docs/contract.md G."""
import os

from app.contract import PROVIDERS, USD_TO_KRW, cost_krw

OR = PROVIDERS["openrouter"]
OUR_PREFIX = "november-"  # 이 접두어로 시작하는 OpenRouter 키만 만들고·고치고·지운다


def _mgmt() -> dict:
    return {"Authorization": f"Bearer {os.getenv('OPENROUTER_MANAGEMENT_KEY', '')}"}


async def usage(http, cred) -> dict:
    """누적 사용량 {cost_krw, tokens, requests}. OpenRouter는 호출 수를 주지 않으므로 requests=None(추정 대상)."""
    if cred["provider"] == "openrouter":
        r = await http.get(f"{OR}/keys/{cred['provider_ref']}", headers=_mgmt())
        r.raise_for_status()
        return {"cost_krw": float(r.json()["data"]["usage"] or 0) * USD_TO_KRW, "tokens": 0, "requests": None}
    u = (await http.get(f"{PROVIDERS['mock']}/usage", params={"owner": cred["provider_ref"]})).json()
    return {"cost_krw": u["cost_krw"], "tokens": u["tokens"], "requests": u.get("requests", 0)}


async def revoke(http, cred) -> bool:
    if cred["provider"] == "openrouter":
        r = await http.patch(f"{OR}/keys/{cred['provider_ref']}", headers=_mgmt(), json={"disabled": True})
    else:
        r = await http.post(f"{PROVIDERS['mock']}/revoke", json={"owner": cred["provider_ref"]})
    return r.status_code == 200


def call_cost_krw(provider: str, u: dict) -> float:
    """중계 응답 usage -> 원. OpenRouter는 usage.cost(USD, usage:{include:true}로 요청)."""
    if provider != "openrouter":
        return cost_krw(u.get("total_tokens", 0))
    if u.get("cost") is not None:
        return float(u["cost"]) * USD_TO_KRW
    # ponytail: cost 누락(스트림 중단 등) 시 gpt-4o-mini 단가($0.15/$0.6 per 1M)로 추정. 다른 모델이면 부정확.
    return ((u.get("prompt_tokens") or 0) * 0.15e-6 + (u.get("completion_tokens") or 0) * 0.6e-6) * USD_TO_KRW


async def or_create_key(http, name: str, limit_usd: float) -> tuple[str, str]:
    """(원문 키, hash). 원문은 이 응답에서만 받을 수 있다."""
    assert name.startswith(OUR_PREFIX)
    r = await http.post(f"{OR}/keys", headers=_mgmt(), json={"name": name, "limit": limit_usd})
    r.raise_for_status()
    d = r.json()
    return d["key"], d["data"]["hash"]


async def or_delete_ours(http) -> int:
    """이름이 november-로 시작하는 키(비활성 포함)만 삭제. 그 외 키는 절대 건드리지 않는다."""
    r = await http.get(f"{OR}/keys", headers=_mgmt(), params={"include_disabled": "true"})
    r.raise_for_status()
    n = 0
    for k in r.json()["data"]:
        if str(k.get("name") or "").startswith(OUR_PREFIX):
            (await http.delete(f"{OR}/keys/{k['hash']}", headers=_mgmt())).raise_for_status()
            n += 1
    return n

"""공용 계약: 모든 트랙이 여기서 import한다. 수정 금지(오케스트레이터만)."""
import hashlib
import os

GATEWAY_PORT = 8000
MOCK_PORT = 9000
MOCK_BASE_URL = os.getenv("MOCK_BASE_URL", "http://127.0.0.1:9000")
PROVIDERS = {"mock": MOCK_BASE_URL}  # 공급자 이름 -> base_url
ADMIN_KEY = "demo-admin"  # X-Admin-Key (/admin/*)
DB_PATH = os.getenv("DB_PATH", "data/gateway.db")
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"

COST_PER_TOKEN_KRW = 0.4  # 게이트웨이·Mock 동일 공식
DEFAULT_LIMIT_KRW = 100_000  # 참고 표시용(금액은 더 이상 판정 기준 아님)
DEFAULT_LIMIT_CALLS = 100  # 판정 기준: 허용하는 '설명되지 않는 호출 수'(사장이 설정)
TOLERANCE_CALLS = 3  # 진행 중 요청·집계 지연 흡수
SUSPECT_RATIO, WARN_RATIO, BLOCK_RATIO = 0.1, 0.5, 0.7
TOLERANCE_KRW = 5_000
POLL_INTERVAL_S = 2  # 탐지 워커가 공급자 사용량을 읽는 주기
MOCK_USAGE_LAG_S = 2  # Mock 집계 지연
LEAKED_KEY = os.getenv("LEAKED_KEY", "sk-mock-K1-leaked-demo")  # 시드 K1의 키 값(청중에게 '유출'로 공개되는 값)
ATTACK_RPS = 5
ATTACK_MAX_TOKENS = 2000  # 건당 800원
WIF_TOKEN_TTL_S = 900


def cost_krw(tokens: int) -> float:
    return tokens * COST_PER_TOKEN_KRW


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mask_key(key: str) -> str:
    return f"{key[:3]}...{key[-4:]}" if len(key) > 8 else "***"


def effective_diff(provider_krw: float, gateway_krw: float) -> float:
    """허용 오차 차감 후 설명되지 않는 사용액. 음수는 0."""
    return max(0.0, provider_krw - gateway_krw - TOLERANCE_KRW)


def effective_calls_diff(provider_calls: int, gateway_calls: int) -> int:
    """공급자가 보고했지만 게이트웨이가 모르는 호출 수(오차 차감 후). 판정 기준."""
    return max(0, provider_calls - gateway_calls - TOLERANCE_CALLS)


def judge(diff: float, limit: float) -> str:
    """effective_calls_diff(호출 수)와 한도(호출 수)로 판정: NORMAL | SUSPECT | WARNING | BREACH"""
    r = diff / limit
    if r >= BLOCK_RATIO:
        return "BREACH"
    if r >= WARN_RATIO:
        return "WARNING"
    if r >= SUSPECT_RATIO:
        return "SUSPECT"
    return "NORMAL"


if __name__ == "__main__":  # 최소 자체 검증
    assert cost_krw(2000) == 800
    assert effective_diff(4000, 0) == 0 and effective_diff(30_000, 5_000) == 20_000
    assert [judge(d, 100) for d in (0, 10, 50, 70)] == ["NORMAL", "SUSPECT", "WARNING", "BREACH"]
    assert effective_calls_diff(20, 10) == 7 and effective_calls_diff(5, 10) == 0
    print("contract ok")

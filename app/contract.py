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
DEFAULT_LIMIT_KRW = 100_000
SUSPECT_RATIO, WARN_RATIO, BLOCK_RATIO = 0.1, 0.5, 0.7
TOLERANCE_KRW = 5_000
POLL_INTERVAL_S = 2  # 탐지 워커가 공급자 사용량을 읽는 주기
MOCK_USAGE_LAG_S = 2  # Mock 집계 지연
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


def judge(diff_krw: float, limit_krw: float) -> str:
    """effective_diff 기준 판정: NORMAL | SUSPECT | WARNING | BREACH"""
    r = diff_krw / limit_krw
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
    assert [judge(d, 100_000) for d in (0, 10_000, 50_000, 70_000)] == ["NORMAL", "SUSPECT", "WARNING", "BREACH"]
    print("contract ok")

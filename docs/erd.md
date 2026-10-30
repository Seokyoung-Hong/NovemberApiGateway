# AI Gateway ERD (해커톤 데모용)

```mermaid
erDiagram
    SERVICE ||--o{ GATEWAY_TOKEN : "발급"
    SERVICE ||--o{ USAGE_LOG : "호출"
    PROVIDER ||--o{ CREDENTIAL : "보유"
    CREDENTIAL ||--o| CREDENTIAL : "replaced_by (교체)"
    CREDENTIAL ||--o{ USAGE_LOG : "사용됨"
    CREDENTIAL ||--o{ PROVIDER_USAGE : "공급자 집계"
    CREDENTIAL ||--o{ RECONCILIATION : "대조 대상"
    CREDENTIAL ||--o{ INCIDENT : "침해 대상"
    RECONCILIATION ||--o| INCIDENT : "탐지 근거"
    HONEYKEY ||--o{ HONEYKEY_EVENT : "접근됨"
    HONEYKEY_EVENT ||--o| INCIDENT : "탐지 근거"

    SERVICE {
        bigint id PK
        string name "내부 서비스명"
        string status "ACTIVE|SUSPENDED"
        datetime created_at
    }
    GATEWAY_TOKEN {
        bigint id PK
        bigint service_id FK
        string token_hash "원문 저장 금지"
        string status "ACTIVE|REVOKED"
        datetime expires_at
    }
    PROVIDER {
        bigint id PK
        string name "openai|claude|gemini"
        string auth_mode "WIF|VAULT"
    }
    CREDENTIAL {
        bigint id PK
        bigint provider_id FK
        bigint replaced_by_id FK "nullable, 교체된 새 인증정보"
        string role "PRIMARY|STANDBY"
        string status "ACTIVE|REVOKED"
        string secret_ref "Vault 경로 또는 WIF 설정 ref (평문 금지)"
        datetime expires_at "WIF 단기토큰이면 값 있음"
        datetime created_at
    }
    USAGE_LOG {
        bigint id PK
        bigint service_id FK
        bigint credential_id FK
        string model
        int input_tokens
        int output_tokens
        decimal cost_usd
        int status_code
        datetime requested_at
    }
    PROVIDER_USAGE {
        bigint id PK
        bigint credential_id FK
        datetime bucket_start "집계 구간 (예: 1시간)"
        datetime bucket_end
        bigint total_tokens
        decimal cost_usd
        datetime fetched_at
    }
    RECONCILIATION {
        bigint id PK
        bigint credential_id FK
        datetime bucket_start
        datetime bucket_end
        bigint gateway_tokens "USAGE_LOG 합계"
        bigint provider_tokens "PROVIDER_USAGE 합계"
        bigint diff_tokens "provider - gateway"
        float risk_score "0~1, 지연·오차 허용치 반영"
        string verdict "NORMAL|SUSPECT|BREACH"
    }
    HONEYKEY {
        bigint id PK
        string kind "API_KEY|GATEWAY_TOKEN"
        string decoy_value_hash "게이트웨이에 심어둔 가짜 키"
        string planted_at "심은 위치 (예: .env.example, 로그)"
    }
    HONEYKEY_EVENT {
        bigint id PK
        bigint honeykey_id FK
        string source_ip
        string path
        datetime occurred_at
    }
    INCIDENT {
        bigint id PK
        string trigger_type "RECONCILIATION|HONEYKEY"
        bigint credential_id FK "nullable (허니키는 없을 수 있음)"
        bigint reconciliation_id FK "nullable"
        bigint honeykey_event_id FK "nullable"
        string status "OPEN|REVOKED|ROTATED|RESOLVED"
        bigint new_credential_id FK "nullable, 전환된 예비키"
        datetime detected_at
        datetime resolved_at
        datetime notified_at "관리자 통보 시각"
    }
```

## 설계 메모
- **탐지 흐름**: `USAGE_LOG`(게이트웨이 기록) vs `PROVIDER_USAGE`(공급자 조회) → `RECONCILIATION`에서 구간별 대조 → `verdict=BREACH`면 `INCIDENT` 생성.
- **자동 대응**: `INCIDENT`가 `CREDENTIAL`을 `REVOKED`로 바꾸고, `role=STANDBY`인 키를 `PRIMARY`로 승격 (`replaced_by_id` / `new_credential_id` 기록).
- **WIF vs Vault**: `PROVIDER.auth_mode`로 구분. WIF는 `expires_at`이 있는 단기 토큰, Vault는 `secret_ref`만 저장 — 키 원문은 DB에 없다.
- **허니키**: 게이트웨이 자체 공격 탐지용. `HONEYKEY_EVENT`가 한 건이라도 생기면 즉시 `INCIDENT`(trigger=HONEYKEY).
- **생략(YAGNI)**: 관리자/사용자 테이블, 알림 채널 테이블, 프로젝트·팀 계층. 필요하면 `INCIDENT.notified_at`을 알림 테이블로 분리.

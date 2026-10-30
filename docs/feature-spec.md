# NovemberAiGateway 기능 명세 (PoC)

전제: LiteLLM 포크, Mock Provider 사용, 마감 04:00. 시나리오는 `demo-scenario.md`, 데이터는 `erd.md` 참조.
미확정 항목(문서 6절)은 **권장안으로 가정**했고 `[가정]`으로 표시한다.

우선순위: **P0** = 데모 필수 / **P1** = 있으면 좋음 / **P2** = 시간 남으면.

## 1. 기능 목록

| ID | 기능 | 우선순위 | 시나리오 장면 |
|---|---|---|---|
| F1 | 공급자 크레덴셜 등록(API Key / WIF / 예비키) | P0 | 1 |
| F2 | 무단 사용 허용 손실 한도 설정 | P0 | 1 |
| F3 | 허용 IP 대역(CIDR) 관리 | P0 | 1 |
| F4 | 직원 게이트웨이 토큰 발급·폐기 | P0 | 1 |
| F5 | 요청 중계 + 접근 통제(IP+토큰) | P0 | 2 |
| F6 | 게이트웨이 사용량 기록(원화 비용) | P0 | 2 |
| F7 | 공급자 사용량 수집(3초 주기) | P0 | 2, 4 |
| F8 | 사용량 대조·위험도 판정 | P0 | 4, 5 |
| F9 | 자동 대응(폐기→예비키 승격→통보) | P0 | 5, 6 |
| F10 | 사고 기록·타임라인 | P0 | 5, 6 |
| F11 | 대시보드 조회 API | P0 | 전체 |
| F12 | 공격 시뮬레이터(데모 전용) | P0 | 4 |
| F13 | Mock Provider | P0 | 전체 |
| F14 | 데모 리셋·시드 | P1 | 준비 |
| F15 | 허니키 탐지 | P2 | 2 말미 |

## 2. 기능별 명세

### F1 크레덴셜 등록
- 입력: `provider`, `auth_mode`(`API_KEY`|`WIF`), `role`(`PRIMARY`|`STANDBY`), API Key 원문(API_KEY일 때) 또는 `audience`·`trusted_identity`(WIF일 때).
- API Key는 **암호화 저장**(PoC: Fernet 대칭키, 키는 환경변수). 응답·로그에는 마스킹(`sk-...a9f2`)만.
- WIF: 장기 키를 저장하지 않는다. 호출 시 Mock STS에서 **15분 단기 토큰**을 발급·캐시하고 만료 전 갱신.
- 프로바이더당 PRIMARY는 최대 1개(ACTIVE).
- 수용 기준: 등록 후 목록에서 원문이 어디에도 보이지 않는다.

### F2 한도 설정
- 판정 기준 값 `unauthorized_call_limit`(허용하는 '설명되지 않는 호출 수', 기본 100). 대표가 수정 가능. 변경은 즉시 판정에 반영.
- `loss_limit_krw`(기본 100,000)는 참고 표시용으로만 남는다(판정에 안 씀).
- 경고/차단 비율은 상수(`WARN=0.5`, `BLOCK=0.7`, `SUSPECT=0.1`). `[가정]`
- 허용 오차 `TOLERANCE_CALLS=3`건(진행 중 요청·집계 지연 흡수). `[가정]`

### F3 허용 IP 대역
- CRUD: `cidr`, `label`(`INTERNAL`|`VPN`). 예: `10.0.0.0/16`, `10.8.0.0/24`, 데모용 `127.0.0.1/32`.
- 대역이 0개이면 전부 거부(안전 기본값).

### F4 직원 토큰
- 직원(서비스)별 토큰 발급. 원문은 **발급 시 1회만** 노출, 저장은 해시.
- 폐기(REVOKE) 시 즉시 무효.
- LiteLLM 가상 키 기능에 매핑한다(구현 시 가능 범위 확인, 안 되면 자체 테이블).

### F5 요청 중계 + 접근 통제
- 엔드포인트: `POST /v1/chat/completions`(OpenAI 호환, 스트리밍 포함).
- 검사 순서: ① 출발 IP ∈ 허용 대역 → ② 토큰 유효 → ③ 서비스 상태 ACTIVE → 통과 시 현재 PRIMARY 크레덴셜로 공급자에 중계.
- 실패: `403`(IP) / `401`(토큰). 모든 거부를 `access_denied` 로그(시각, 출발 IP, 사유, 토큰 마스킹)로 기록.
- 크레덴셜 전환(F9) 중에도 직원은 같은 토큰으로 계속 호출 가능해야 한다.

### F6 게이트웨이 사용량 기록
- 호출마다 `usage_log`: service, credential, model, input/output 토큰, **cost_krw**, status, 시각.
- 비용 = `total_tokens × 0.4원`(Mock 단가, 데모 상수). Mock Provider와 **동일 공식**이어야 대조가 맞는다.

### F7 공급자 사용량 수집
- 3초마다 Mock Provider `GET /usage?key_id=&since=`를 호출, 크레덴셜별 누적 `provider_cost_krw`를 `provider_usage`에 적재.
- Mock은 **집계 지연 약 3초**를 흉내 낸다.

### F8 대조·판정
- 원리: 게이트웨이가 기록한 호출 수와 공급자가 보고한 호출 수를 비교한다. 게이트웨이가 모르는 호출이 있으면 그 키를 다른 누군가가 쓴 것이다.
- 크레덴셜별로 `diff_calls = provider_calls − gateway_calls − TOLERANCE_CALLS`(누적, 0 미만은 0). 금액 차액(`diff_krw`)은 참고 표시만.
- 판정표(한도 L = `unauthorized_call_limit` 호출 수):

| diff_calls | 상태 | 동작 |
|---|---|---|
| < 0.1L | NORMAL | 없음 |
| ≥ 0.1L | SUSPECT | 상태 배지 노랑 |
| ≥ 0.5L | WARNING | 경고 알림 이벤트 |
| ≥ 0.7L | BREACH | F9 자동 대응 |

- 대조마다 `reconciliation` 행 기록(`risk_score = min(diff/L, 1)`).
- 이미 REVOKED인 크레덴셜은 대조하지 않는다.
- 차단선 0.7L 이유: 차단 뒤에도 집계 지연(2초)+폴링(2초) 동안 들어온 호출이 뒤늦게 잡힌다(5건/초 기준 약 15~20건). L=100이면 최종 < L 유지. L이 작으면(예 30) 최종이 L을 넘을 수 있다.

### F9 자동 대응
BREACH 시 아래를 **순서대로** 수행하고 각 단계를 사고 타임라인에 시각과 함께 기록.
1. Mock Provider `POST /keys/{id}/revoke` → 크레덴셜 `REVOKED`.
2. STANDBY → PRIMARY 승격 (`replaced_by_id` 기록). STANDBY가 없으면 `NO_STANDBY` 상태로 기록하고 서비스 중단을 알림.
3. 사고 `INCIDENT` 생성/갱신 → 상태 `ROTATED`.
4. 관리자 통보(이벤트 + 대시보드 배너). 외부 채널은 P2.
5. 승격 키로 호출 성공을 한 번 확인하면 `RESOLVED`(복구).
- 멱등: 같은 크레덴셜에 대해 사고는 1개만 생성.
- WIF 크레덴셜: 폐기 = 신뢰 바인딩 비활성화 + 캐시 토큰 파기 (설명용, 라이브 시연 안 함).

### F10 사고 기록
- 사고 상세: 탐지 시각, 차단 시각, 소요 초, 손실 호출 수 `loss_calls`(차단 시점 diff_calls, 폐기 후 최종값으로 재계산), 한도 `limit_calls`, 참고 금액 `loss_krw`/`limit_krw`, 폐기 키, 전환 키, 타임라인(단계, 시각, 결과).
- 상태: `OPEN → ROTATED → RESOLVED`.

### F11 대시보드 API (프론트 계약)
폴링 1~2초. JSON.

| 메서드·경로 | 용도 |
|---|---|
| `GET/POST /admin/credentials`, `DELETE /admin/credentials/{id}` | F1 |
| `GET/PUT /admin/settings` | F2 (`unauthorized_call_limit`, 참고 `loss_limit_krw`) |
| `GET/POST/DELETE /admin/cidrs` | F3 |
| `GET/POST/DELETE /admin/tokens` | F4 |
| `GET /admin/dashboard` | 현재 상태, 차액, 한도, 크레덴셜별 gateway/provider 누적 |
| `GET /admin/timeseries?range=5m` | 두 선 차트용 시계열(3초 버킷) |
| `GET /admin/access-denied?limit=20` | 최근 거부 로그 |
| `GET /admin/incidents`, `GET /admin/incidents/{id}` | F10 |
| `GET /admin/events?since=` | 경고·차단 알림 토스트용 |

- 대시보드 응답 핵심 필드: `status`(NORMAL/SUSPECT/WARNING/BLOCKED/RECOVERED), `diff_calls`, `limit_calls`, `warn_at_calls`, `block_at_calls`, `gateway_calls`, `provider_calls`, `active_credential`. 금액(`diff_krw`, `limit_krw`, `*_cost_krw`)은 참고. 상세는 `docs/contract.md` D절.
- 관리자 인증: PoC는 고정 `X-Admin-Key` 헤더 1개. `[가정]`

### F12 공격 시뮬레이터 (데모 전용)
- `POST /demo/attack/start`·`/stop`: 서버 내부 백그라운드 작업이 **K1으로 Mock Provider에 직접** 초당 5회 × 800원 호출(게이트웨이를 거치지 않음). `[가정: 대표 버튼 클릭 트리거]`
- 중지 조건: 사용자가 stop, 또는 호출이 401(키 폐기)을 받으면 자동 종료하고 "공격 차단됨 401" 이벤트 발생.
- 프로덕션 빌드에서는 비활성(환경변수 `DEMO_MODE=1`일 때만 라우트 등록).

### F13 Mock Provider
OpenAI 호환 별도 프로세스(포트 분리).

| 경로 | 동작 |
|---|---|
| `POST /v1/chat/completions` | 키 검증 후 가짜 응답. `usage.total_tokens`와 비용 누적. 폐기 키는 401 |
| `GET /usage` | 키별 누적 비용(집계 지연 3초 반영) |
| `POST /keys/{id}/revoke` | 키 폐기 |
| `POST /sts/token` | WIF용 15분 단기 토큰 발급 |
- 요청 토큰 수는 요청 본문의 `max_tokens`(없으면 300)를 따른다 → 공격자는 2,000 토큰(=800원)으로 호출.

### F14 데모 리셋·시드 (P1)
- `POST /demo/reset`: 사용량·사고 초기화, K1(PRIMARY)·S1(STANDBY)·허용 대역·직원 토큰 2개 시드. 리허설 반복용.

### F15 허니키 (P2)
- 게이트웨이에 가짜 토큰/키 N개를 심어 두고, 그 값으로 접근하면 `honeykey_event` + 사고(trigger=HONEYKEY) 생성. 데모에서는 10초 장면.

## 3. 상태 머신 (대시보드 표시)

```
NORMAL → SUSPECT → WARNING → BLOCKED → RECOVERED
   ↑___________(diff 감소 시 복귀, BLOCKED 이후는 복귀 없음)
```
- BLOCKED는 BREACH 처리 시작~승격 확인 전, RECOVERED는 승격 키 호출 성공 후.

## 4. 데이터 보강 (ERD 반영)
- `ALLOWED_CIDR(id, cidr, label)`, `SETTING(key, value)` 추가.
- `ACCESS_DENIED_LOG(id, source_ip, reason, token_masked, at)` 추가(`USAGE_LOG`와 분리).
- `INCIDENT`에 `loss_krw`, `limit_krw`, `timeline(JSON)` 추가.

## 5. 구현 매핑 (병렬 분할 안)
| 트랙 | 범위 | 의존 |
|---|---|---|
| A Mock Provider | F13 | 없음(먼저 시작) |
| B 게이트웨이 코어 | F1, F3~F6 (LiteLLM 가상 키·콜백·커스텀 인증 활용) | A와 계약만 합의 |
| C 탐지·대응 워커 | F7~F10, F12 | A, B의 `usage_log` 스키마 |
| D 관리자 API | F2, F11, F14 | B, C 스키마 |
| E 프론트(담당자) | 화면 4종 | F11 계약만 있으면 Mock JSON으로 선행 가능 |

계약 먼저 고정할 것: 비용 공식(0.4원/토큰), `usage_log`·`provider_usage` 스키마, F11 응답 JSON 형태, Mock Provider 경로.

## 6. 데모 합격 체크리스트
- [ ] K1·K2·S1 등록 후 원문이 화면·로그에 없다
- [ ] 허용 대역 밖 호출이 403, 거부 로그에 출발 IP가 보인다
- [ ] 정상 호출 중 두 선이 겹치고 diff ≈ 0, NORMAL
- [ ] 공격 시작 후 10초 안에 SUSPECT, 약 17초에 BLOCKED
- [ ] 최종 설명되지 않는 호출 수 < 한도(100건), 공격 스크립트가 401로 종료
- [ ] 차단 직후 직원 호출이 같은 토큰으로 성공(예비키 사용)
- [ ] 사고 상세에 탐지·차단 시각, 손실액, 폐기/전환 키가 표시
- [ ] 한도를 30건으로 바꾸고 재시연하면 더 일찍 차단된다
- [ ] `/demo/reset` 후 처음부터 재실행 가능

## 7. 리스크
- LiteLLM 환경(Python 3.14, Postgres/Prisma 의존성)이 첫 병목일 수 있다. **첫 30분에 기동 확인**하고 막히면 LiteLLM은 중계·키 관리만 쓰고 사용량 대조·대응은 별도 워커로 분리한다.
- 비용 공식이 Gateway와 Mock에서 1원이라도 다르면 항상 SUSPECT가 뜬다. 공식은 한 곳에서 import.

## 구현 반영 사항 (PoC 구현 기준, 위 본문과 다른 점)
- 탐지 워커 폴링 주기 `POLL_INTERVAL_S`=2초, Mock 집계 지연 `MOCK_USAGE_LAG_S`=2초.
- 대시보드 `status`는 ACTIVE 크레덴셜 중 최악 판정. 열린 incident가 있으면 BLOCKED, RESOLVED 후 10초간 RECOVERED, 이후 NORMAL.
- 타임라인: DETECTED·REVOKE는 즉시, PROMOTE_STANDBY·NOTIFY_ADMIN·VERIFIED는 연출용 1초 간격(표시 시각일 뿐 실제 승격은 폐기 직후 DB에 즉시 반영).
- 폐기 즉시 STANDBY 승격(직원 호출 503 방지). 검증은 승격 키로 Mock에 `max_tokens=1` 호출.
- 손실 호출 수(`loss_calls`)는 탐지 시점 diff_calls로 먼저 기록하고, 폐기 2.5초 후 공급자 최종 호출 수로 재계산(`공급자 - 게이트웨이`). 금액 `loss_krw`도 같은 규칙(참고).
- 대조는 별도 테이블 없이 워커가 매 틱 `usage_log` 행 수 vs `provider_usage.requests`로 계산. PROVIDER 테이블도 없고 `credential.provider` 문자열.
- 판정은 호출 수 기준: 허용 오차 3건 차감 후 한도(기본 100건) 대비 10%/50%/70%에서 SUSPECT/WARNING/차단. 통화(원, 토큰당 0.4원)는 참고 표시.
- 리허설: `scripts/rehearse.py`가 위 흐름을 자동 점검(PASS/FAIL).


## 구현 반영 사항 (2차)
- **판정 기준 변경**: 금액이 아니라 **설명되지 않는 호출 수**로 판정한다(F2, F8). 한도 기본 100호출, 허용 오차 3호출. 금액은 참고 표시. 시나리오는 `demo-scenario.md` 참조.
- **공급자별 지연 보정**: 공급자 보고는 게이트웨이 기록보다 늦으므로, 게이트웨이 기록은 공급자 스냅샷 시각에서 공급자 지연만큼 뺀 시점까지로 맞춰 비교한다. `공급자 값 > 게이트웨이 전체 기록`이면 확실한 유출.
- **비용만 주는 공급자**(OpenRouter)는 비용 차이로 비교하고 호출 수를 평균 호출 비용으로 추정한다(`calls_estimated`).
- **청중 라이브 모드**와 **OpenRouter 라이브 모드**, **허니키**, **서비스 정지/재개**, **사용량 집계·단가표·예측**(`/admin/usage/*`)을 추가했다. 엔드포인트는 `contract.md` E~H절.
- **미구현**: 직원 측 WIF(OIDC), OpenAI 어댑터는 진행 중. 실제 공급자 WIF 연동은 컷.

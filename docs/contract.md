# 계약 (고정). 상수·스키마는 `app/contract.py`, `app/db.py`가 원본.

실행: Mock `uvicorn app.mock_provider.main:app --port 9000` / 게이트웨이 `uvicorn app.main:app --port 8000` (프로젝트 루트, `.venv`).
관리자 API(`/admin/*`)는 헤더 `X-Admin-Key: demo-admin`. `/demo/*`와 `/v1/*`는 불필요. 모두 JSON, CORS 전체 허용.
시간은 응답에서 ISO8601(UTC) 문자열, DB는 epoch 초(float).

## A. Mock Provider (port 9000)
- 키 규칙: `Authorization: Bearer <key>`. 폐기되지 않은 키는 처음 보는 값이어도 **유효**(자동 등록). 소유자(owner)=키 문자열.
- `POST /v1/chat/completions` body `{model, messages, max_tokens?(기본 300), stream?}` → OpenAI 형식 응답. `usage.total_tokens = max_tokens`.
  비용은 `contract.cost_krw(total_tokens)`로 owner에 누적. stream=true면 SSE 청크, **마지막 청크에 `usage`** 포함 후 `data: [DONE]`.
  폐기된 owner/만료 토큰은 401 `{"error":"revoked"}`.
- `GET /usage?owner=<ref>` → `{"owner","cost_krw","tokens","requests"}`. **`MOCK_USAGE_LAG_S`초보다 최근 요청은 제외**한 값(집계 지연).
- `POST /revoke` `{"owner": "<ref>"}` → `{"ok":true}`. 이후 그 owner의 키/토큰 모두 401.
- `POST /sts/token` `{"audience","identity"}` → `{"token":"wif-...","expires_in":900,"owner":"wif:<audience>"}`. 토큰은 Bearer로 쓸 수 있고 owner=`wif:<audience>`로 집계. 만료(900초) 후 401.
- `POST /demo/reset` → Mock 상태 전부 초기화.

## B. 게이트웨이 중계 (port 8000)
- `POST /v1/chat/completions` (OpenAI 호환, 스트리밍 포함). 헤더 `Authorization: Bearer <게이트웨이 토큰>`.
- 검사: ① 출발 IP ∈ allowed_cidr (`request.client.host`; `DEMO_MODE`일 때만 헤더 `X-Demo-Source-IP`가 있으면 그 값 사용) ② 토큰 해시 일치·ACTIVE ③ service ACTIVE.
  실패 → 403 `{"error":"ip_not_allowed"}` / 401 `{"error":"invalid_token"}` / 403 `{"error":"service_suspended"}`. 모두 `access_denied_log` 기록 + `event(ACCESS_DENIED)`.
  허용 대역이 0개면 전부 거부.
- 통과 시 `provider='mock'`이며 `role='PRIMARY' AND status='ACTIVE'`인 credential 하나를 골라(없으면 503 `{"error":"no_active_credential"}`) 키 확보
  (API_KEY: `vault.decrypt(secret_enc)`, WIF: Mock `/sts/token` 토큰을 audience별로 캐시, 만료 60초 전 갱신) → `PROVIDERS['mock']`로 중계.
- 업스트림 응답 `usage`로 `usage_log` 1행(`cost_krw=contract.cost_krw(total_tokens)`, `credential_id`=사용한 것). 스트리밍은 마지막 청크의 usage 사용, 클라이언트에는 그대로 흘려보낸다.
- 업스트림 401/네트워크 오류는 502로 변환, usage_log 기록 안 함.

## C. 관리자 CRUD (Gateway 트랙 담당)
- `GET /admin/credentials` → `[{id,provider,auth_mode,role,status,masked,audience,provider_ref_masked,replaced_by_id,created_at}]` (원문·secret_enc 절대 노출 금지)
- `POST /admin/credentials` `{provider:"mock", auth_mode:"API_KEY"|"WIF", role:"PRIMARY"|"STANDBY", secret?, audience?}` → 위 항목 1개. 같은 provider에 ACTIVE PRIMARY가 이미 있으면 400. `provider_ref`: API_KEY=secret, WIF=`wif:<audience>`.
- `DELETE /admin/credentials/{id}` → `{"ok":true}` (status=REVOKED로만 표시, 공급자 폐기는 안 함)
- `GET /admin/settings` → `{"unauthorized_call_limit": 100, "loss_limit_krw": 100000}`.
  `PUT /admin/settings` 본문에 **보낸 키만** 갱신(둘 다 양의 정수, 아니면 400), 응답은 갱신 후 GET과 같은 형태. 변경은 다음 워커 틱(≤2초)부터 판정에 반영.
  `unauthorized_call_limit` = 허용하는 '설명되지 않는 호출 수'(**판정 기준**). `loss_limit_krw` = 참고 표시용 금액 한도(판정에 안 씀).
- `GET /admin/cidrs` → `[{id,cidr,label}]`, `POST` `{cidr,label}`, `DELETE /admin/cidrs/{id}`
- `GET /admin/services` → `[{id,name,status,tokens:[{id,token_masked,status,created_at}]}]`
- `POST /admin/tokens` `{"service_name"}` → `{"id","service_id","token":"nvm-...(원문 1회)","token_masked"}` (service 없으면 생성)
- `DELETE /admin/tokens/{id}` → REVOKED
- `GET /admin/access-denied?limit=20` → `[{id,at,source_ip,reason,token_masked}]` 최신순

## D. 탐지·대시보드·데모 (Detector 트랙 담당)
- `GET /admin/dashboard` →
  `{status:"NORMAL|SUSPECT|WARNING|BLOCKED|RECOVERED", limit_calls, warn_at_calls, block_at_calls, diff_calls, gateway_calls, provider_calls, limit_krw, warn_at_krw, block_at_krw, diff_krw, gateway_cost_krw, provider_cost_krw, active_credential:{id,provider,auth_mode,masked}|null, credentials:[{id,provider,auth_mode,role,status,masked,gateway_calls,provider_calls,diff_calls,gateway_cost_krw,provider_cost_krw,diff_krw}], attack_running, incident_id|null, updated_at}`
  - **판정 기준(호출 수)**: `gateway_calls` = 게이트웨이가 중계·기록한 호출 수(`usage_log` 행 수, 단 공급자 집계와 같은 시점인 `최신 스냅샷 시각 − MOCK_USAGE_LAG_S`까지만 셈. 그래서 실시간 값보다 조금 작다), `provider_calls` = 공급자 `/usage`가 보고한 누적 호출 수(`requests`, 2초 집계 지연),
    `diff_calls` = `effective_calls_diff(provider_calls, gateway_calls)` = max(0, 공급자 − 게이트웨이 − 3) = **게이트웨이가 모르는 호출 수**(누군가 키를 직접 썼다는 증거).
    `limit_calls` = 설정 `unauthorized_call_limit`, `warn_at_calls`=limit×0.5, `block_at_calls`=limit×0.7(실수일 수 있음, 예 30×0.7=21.0).
  - 금액 필드(`*_krw`, `*_cost_krw`)는 **참고 표시용**. 판정·차단에 쓰지 않는다.
  - 합계 필드는 모든 credential(REVOKED 포함) 합. `credentials[]`는 크레덴셜별 값. `status`는 ACTIVE 크레덴셜의 호출 수 판정 중 최악값.
- `GET /admin/timeseries?range=300` → `{points:[{t, gateway_calls, provider_calls, gateway_krw, provider_krw}]}` 최근 `range`초, 2초 버킷, 전 크레덴셜 누적값(호출 수 두 선이 판정용, 금액은 참고).
- `GET /admin/incidents` → 목록(최신순), `GET /admin/incidents/{id}` →
  `{id,status,credential_id,credential_masked,detected_at,blocked_at,resolved_at,duration_s,loss_calls,limit_calls,loss_krw,limit_krw,new_credential_id,new_credential_masked,timeline:[{at,step,detail,ok}]}`
  `loss_calls` = 설명되지 않는 호출 수. 차단 시점 `diff_calls`로 기록 후, 폐기 2.5초 후 공급자 최종 호출 수로 재계산(`공급자 − 게이트웨이`, 오차 미차감). `limit_calls` = 차단 당시 한도. `loss_krw`/`limit_krw`는 같은 규칙의 참고 금액. 허니키 사고는 네 값 모두 null.
  timeline step: `DETECTED, REVOKE_PROVIDER_KEY, PROMOTE_STANDBY, NOTIFY_ADMIN, VERIFIED`.
- `GET /admin/events?since_id=0` → `{events:[{id,at,kind,message,data}]}` 오름차순.
- `POST /demo/attack/start` / `POST /demo/attack/stop` → `{attack_running}`. 공격 = 현재 PRIMARY(ACTIVE) credential 키로 **Mock에 직접** `ATTACK_RPS`건/초, `max_tokens=ATTACK_MAX_TOKENS`. 401 받으면 자동 종료+`ATTACK_STOPPED` 이벤트.
- `POST /demo/reset` → Mock `/demo/reset` 호출 + DB 전체 초기화 후 시드: 설정 `unauthorized_call_limit`=100(`DEFAULT_LIMIT_CALLS`)·`loss_limit_krw`=100000, allowed_cidr(`127.0.0.1/32` INTERNAL, `::1/128` INTERNAL, `10.0.0.0/16` INTERNAL, `10.8.0.0/24` VPN),
  credential K1(API_KEY, PRIMARY, 키 값 고정 `contract.LEAKED_KEY` 기본 `sk-mock-K1-leaked-demo`, 마스킹 `sk-...demo`) → S1(API_KEY, STANDBY, `sk-mock-S1-<랜덤>`) → K2(WIF, STANDBY, audience `novem-wif`) 순서로 삽입(id 순서가 승격 순서),
  service `직원A`,`직원B` 토큰 발급. 응답 `{"tokens":{"직원A":"nvm-...","직원B":"nvm-..."}}`.
- 워커 `worker_loop()`: `POLL_INTERVAL_S`마다 ACTIVE credential 각각 `GET {mock}/usage?owner=provider_ref` → `provider_usage`(cost_krw, tokens, requests) 적재, 게이트웨이 호출 수(`usage_log` 행 수)와 비교.
  `judge(effective_calls_diff(provider_calls, gateway_calls), unauthorized_call_limit)` 결과(판정표: diff_calls < 0.1L NORMAL, ≥0.1L SUSPECT, ≥0.5L WARNING, ≥0.7L BREACH): SUSPECT/WARNING은 상태·이벤트 기록, BREACH는 **자동 대응**: 폐기(`POST /revoke`) → credential REVOKED → 가장 id 작은 STANDBY(ACTIVE)를 PRIMARY로 승격(`replaced_by_id`) → incident 생성/갱신(멱등, credential당 1개) → 이벤트 `BLOCKED` → 승격 키로 Mock에 소액 호출 성공 확인 시 `RESOLVED` + 이벤트 `RECOVERED`.
  STANDBY가 없으면 timeline에 실패로 기록하고 status BLOCKED 유지.
- 대시보드 `status` 계산: 열린 incident가 ROTATED 이전이면 BLOCKED, RESOLVED 직후 `RECOVERED`(다음 정상 호출 누적 전까지 유지), 아니면 최신 judge 결과(BREACH 없음).

## E. 추가 엔드포인트 (구현 반영)
- 허니키: 게이트웨이에 심어 둔 가짜 토큰(`nvm-honey-...`). 중계에서 일치하면 호출자에게는 **일반 401 `invalid_token`**, 내부적으로 `honeykey_event` + 이벤트 `HONEYKEY` + 사고(`trigger_type=HONEYKEY`, `credential_id=null`) 생성, 출발 IP를 600초 임시 차단(이후 403 `ip_blocked`).
- `GET /admin/honeykeys` → `[{id,masked,planted_at,created_at,hits}]`
- `POST /demo/honeypot/hit` → `{source_ip:"203.0.113.77",planted_at,status_code:401,response:{error:"invalid_token"}}` (리셋 전이면 409)
- `POST /demo/reset` 응답에 `honeykeys:{planted_at: 원문}` 추가.
- `GET /admin/dashboard` 추가 필드: `honeykey_hits:[{at,honeykey_masked,planted_at,source_ip}]`, `blocked_ips:[{source_ip,until}]`
- `GET /admin/incidents`, `/admin/incidents/{id}`: `trigger_type` 추가. 허니키 사고는 `credential_id`·`credential_masked`가 null.
- `POST /admin/services/{id}/suspend` → `{id,status:"SUSPENDED"}`, `POST /admin/services/{id}/resume` → `{id,status:"ACTIVE"}` (없는 id는 404). 정지 서비스 토큰은 403 `service_suspended`.
- 거부 사유 `reason` 값: `ip_not_allowed | invalid_token | service_suspended | ip_blocked`.
- 실제 AI: Mock Provider에 환경변수 `UPSTREAM_API_KEY`(+선택 `UPSTREAM_BASE_URL`, 기본 OpenRouter)가 있고 요청 `model`이 `mock`/`demo`로 시작하지 않으면 실제 업스트림으로 전달하고 실제 usage로 집계한다. 실패 시 가짜 응답으로 자동 대체.
- 구 스키마 DB는 서버 시작 시 자동으로 재생성된다(데모 데이터 삭제됨).

## F. 청중 라이브 데모 (Mock Provider, port 9000)
청중이 "유출된 키"(`LEAKED_KEY` = 시드 K1)로 게이트웨이를 거치지 않고 공급자에 직접 호출 → 탐지기가 호출 수 차이로 감지. 실행 절차는 `scripts/live.md`.
- `GET /live` → 청중용 채팅 HTML(스타일 클래스 `.live-*`, 상태는 `body[data-state=active|busy|revoked|limited]`).
- `GET /live/config` → `{key, model}`
- `POST /live/chat` `{key, message}` → 200 `{reply, status:200}` / 401 `{error:"revoked"}` / 429 `{error:"rate_limited"}`. 제한: 메시지 300자, max_tokens 150, 모델 고정, IP당 20초 10회(X-Forwarded-For 우선).
- `GET /live/stats` → `{participants, calls, key_status:"active"|"revoked", updated_at}` (슬라이드·대시보드 표시용)
- 환경변수: `UPSTREAM_API_KEY`, `UPSTREAM_BASE_URL`(기본 OpenRouter), `LIVE_MODEL`(기본 `gpt-4o-mini`; OpenRouter는 `openai/gpt-4o-mini`가 필요할 수 있음), `LEAKED_KEY`.
- 실측(청중 40명 가정, 초당 약 6.5건): 한도 100호출에서 78건에 BLOCKED, 청중 401, 최종 손실 94건. 청중이 더 빠르면 한도를 넘길 수 있으니 한도 여유를 두거나 속도 제한을 유지할 것.

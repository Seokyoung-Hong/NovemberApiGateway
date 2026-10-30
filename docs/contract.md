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
- `GET /admin/settings` / `PUT /admin/settings` `{"loss_limit_krw": 100000}` (정수>0)
- `GET /admin/cidrs` → `[{id,cidr,label}]`, `POST` `{cidr,label}`, `DELETE /admin/cidrs/{id}`
- `GET /admin/services` → `[{id,name,status,tokens:[{id,token_masked,status,created_at}]}]`
- `POST /admin/tokens` `{"service_name"}` → `{"id","service_id","token":"nvm-...(원문 1회)","token_masked"}` (service 없으면 생성)
- `DELETE /admin/tokens/{id}` → REVOKED
- `GET /admin/access-denied?limit=20` → `[{id,at,source_ip,reason,token_masked}]` 최신순

## D. 탐지·대시보드·데모 (Detector 트랙 담당)
- `GET /admin/dashboard` →
  `{status:"NORMAL|SUSPECT|WARNING|BLOCKED|RECOVERED", limit_krw, warn_at_krw, block_at_krw, diff_krw, gateway_cost_krw, provider_cost_krw, active_credential:{id,provider,auth_mode,masked}|null, credentials:[{id,provider,auth_mode,role,status,masked,gateway_cost_krw,provider_cost_krw,diff_krw}], attack_running, incident_id|null, updated_at}`
  (`diff_krw`는 `effective_diff` 합. `warn_at`=limit×0.5, `block_at`=limit×0.7. 합계는 모든 credential 누적 합.)
- `GET /admin/timeseries?range=300` → `{points:[{t, gateway_krw, provider_krw}]}` 최근 `range`초, 2초 버킷, 누적값.
- `GET /admin/incidents` → 목록(최신순), `GET /admin/incidents/{id}` →
  `{id,status,credential_id,credential_masked,detected_at,blocked_at,resolved_at,duration_s,loss_krw,limit_krw,new_credential_id,new_credential_masked,timeline:[{at,step,detail,ok}]}`
  timeline step: `DETECTED, REVOKE_PROVIDER_KEY, PROMOTE_STANDBY, NOTIFY_ADMIN, VERIFIED`.
- `GET /admin/events?since_id=0` → `{events:[{id,at,kind,message,data}]}` 오름차순.
- `POST /demo/attack/start` / `POST /demo/attack/stop` → `{attack_running}`. 공격 = 현재 PRIMARY(ACTIVE) credential 키로 **Mock에 직접** `ATTACK_RPS`건/초, `max_tokens=ATTACK_MAX_TOKENS`. 401 받으면 자동 종료+`ATTACK_STOPPED` 이벤트.
- `POST /demo/reset` → Mock `/demo/reset` 호출 + DB 전체 초기화 후 시드: 설정 limit=100000, allowed_cidr(`127.0.0.1/32` INTERNAL, `::1/128` INTERNAL, `10.0.0.0/16` INTERNAL, `10.8.0.0/24` VPN),
  credential K1(API_KEY, PRIMARY, `sk-mock-K1-<8자리 랜덤>`) → S1(API_KEY, STANDBY, `sk-mock-S1-<랜덤>`) → K2(WIF, STANDBY, audience `novem-wif`) 순서로 삽입(id 순서가 승격 순서),
  service `직원A`,`직원B` 토큰 발급. 응답 `{"tokens":{"직원A":"nvm-...","직원B":"nvm-..."}}`.
- 워커 `worker_loop()`: `POLL_INTERVAL_S`마다 ACTIVE credential 각각 `GET {mock}/usage?owner=provider_ref` → `provider_usage` 적재, gateway 누적(`usage_log` 합)과 비교.
  `judge(effective_diff, limit)` 결과: SUSPECT/WARNING은 상태·이벤트 기록, BREACH는 **자동 대응**: 폐기(`POST /revoke`) → credential REVOKED → 가장 id 작은 STANDBY(ACTIVE)를 PRIMARY로 승격(`replaced_by_id`) → incident 생성/갱신(멱등, credential당 1개) → 이벤트 `BLOCKED` → 승격 키로 Mock에 소액 호출 성공 확인 시 `RESOLVED` + 이벤트 `RECOVERED`.
  STANDBY가 없으면 timeline에 실패로 기록하고 status BLOCKED 유지.
- 대시보드 `status` 계산: 열린 incident가 ROTATED 이전이면 BLOCKED, RESOLVED 직후 `RECOVERED`(다음 정상 호출 누적 전까지 유지), 아니면 최신 judge 결과(BREACH 없음).

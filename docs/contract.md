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
- 환경변수: `UPSTREAM_API_KEY`, `UPSTREAM_BASE_URL`(기본 OpenRouter), `LIVE_MODEL`(기본 `openai/gpt-4o-mini`), `LEAKED_KEY`.
- 실측(청중 40명 가정, 초당 약 6.5건): 한도 100호출에서 78건에 BLOCKED, 청중 401, 최종 손실 94건. 청중이 더 빠르면 한도를 넘길 수 있으니 한도 여유를 두거나 속도 제한을 유지할 것.

## G. OpenRouter 라이브 모드
Mock 대신 실제 OpenRouter 키로 같은 탐지·차단 흐름을 보여 준다. 게이트웨이 `.env`에 `OPENROUTER_MANAGEMENT_KEY`(관리 키) 필요(`app/contract.py`가 `.env`를 직접 읽음). 공급자 분기는 `app/providers.py`.
- 관리 키로만 키를 만들고·비활성화하고·지운다. **이름이 `november-`로 시작하는 키만** 다룬다(다른 키는 건드리지 않음).
- `POST /admin/openrouter/provision` `{role:"PRIMARY"|"STANDBY", limit_usd?:0.5}` → `{id, masked, role, hash_masked, leaked_key?}`.
  `november-<role>-<랜덤>` 키 생성(한도 limit_usd), vault 암호화 저장, credential(`provider='openrouter'`, `auth_mode='API_KEY'`, `provider_ref`=OpenRouter 키 hash).
  원문은 PRIMARY일 때만 `leaked_key`로 1회 반환하고 `data/live_key.json`(gitignore)에도 저장 → Mock `/live/config`가 그 키를 쓴다. ACTIVE PRIMARY가 이미 있으면 400. `POST /admin/credentials`에 `provider:"openrouter"`는 400.
- `POST /demo/reset-openrouter` → `/demo/reset`과 같은 시드(CIDR·직원A/B 토큰·허니키·`loss_limit_krw`)에 기존 `november-` 키 삭제 후 K1(PRIMARY)·S1(STANDBY) 각 $0.5를 새로 만들어 넣는다. `unauthorized_call_limit`=500(`OPENROUTER_LIMIT_CALLS`). 응답 = reset 응답 + `leaked_key`.
  `POST /demo/reset`(Mock 모드)은 `data/live_key.json`을 지워 라이브 페이지를 `LEAKED_KEY`로 되돌린다(OpenRouter 키는 지우지 않음).
- `POST /demo/cleanup-openrouter` → `{deleted:n}`. `november-` 키(비활성 포함)만 삭제.
- 중계: PRIMARY·ACTIVE 크레덴셜의 provider로 분기(이제 provider 필터 없음). OpenRouter면 `https://openrouter.ai/api/v1/chat/completions`, `model`은 그대로(없거나 `mock`/`demo`로 시작하면 `openai/gpt-4o-mini`), 본문에 `usage:{include:true}`를 넣어 응답 `usage.cost`(USD)로 `usage_log.cost_krw = USD × USD_TO_KRW(1400)`. 스트리밍은 마지막 청크 usage, cost가 없으면 gpt-4o-mini 단가로 토큰 추정.
- 탐지: 워커가 `GET /keys/{hash}`(관리 키)의 `data.usage`(USD)×1400을 `provider_usage.cost_krw`로 적재. OpenRouter는 호출 수를 주지 않으므로(`/activity`는 당일 미지원) **호출 수는 추정**:
  `provider_calls(추정) = gateway_calls + round((공급자 비용 − 같은 시점 게이트웨이 비용) / 호출당 평균 비용)`, 평균 = 해당 크레덴셜 `usage_log` 평균(이력 없으면 `DEFAULT_CALL_COST_USD`=0.00005×1400). 게이트웨이 쪽은 `스냅샷 − OPENROUTER_USAGE_LAG_S(8)`까지만 센다. `diff_calls = effective_calls_diff(...)`(오차 3건 차감)는 Mock과 같다.
  이 추정값이 `provider_usage.requests`에 저장되므로 대시보드·시계열·사고의 `provider_calls`/`loss_calls`는 추정치다. `/admin/dashboard`의 `credentials[]`와 `/admin/incidents/{id}`에 `calls_estimated: true`(OpenRouter) 추가.
  비율 상한은 공급자별 `RATIOS`: Mock 0.1/0.5/0.7, OpenRouter **0.05/0.2/0.35**(지연이 커서 낮춤). 대시보드 `warn_at_*`/`block_at_*`는 현재 PRIMARY의 provider 비율.
- 폐기: `PATCH /keys/{hash}` `{disabled:true}`. 타임라인 REVOKE_PROVIDER_KEY 상세에 "공급자 반영까지 약 20초 소요". 승격·사고·이벤트는 Mock과 같다. VERIFIED는 승격 키로 OpenRouter 실제 호출 1건(`max_tokens=1`). 최종 손실 재계산은 폐기 28초 후(`OPENROUTER_REVOKE_LAG_S` 20 + 8).
- 지연 특성(실측): 사용액 반영 약 6초, 비활성화 후 401까지 최대 약 20초(그 사이 호출은 200). 그래서 탐지까지 수십 초, 손실은 차단선을 넘을 수 있다. 금액 참고값(`diff_krw`)은 `TOLERANCE_KRW`(5,000원)보다 작아 보통 0으로 보인다.
- 라이브(Mock `/live/chat`): 키가 `sk-or-`로 시작하면 **현재 공개한 키(`/live/config`)와 같을 때만** OpenRouter에 직접 전달(`LIVE_MODEL` 기본 `openai/gpt-4o-mini`, `max_tokens` 150, 기존 속도 제한 유지). OpenRouter 401/403 → 401 `{"error":"revoked"}`, 그 외 오류 → 502 `{"error":"upstream_error","upstream_status"}`. `/live/stats`의 `key_status`는 마지막 OpenRouter 응답이 401/403이면 `revoked`. Mock 키 경로는 그대로.

## H. 사용량 집계·예측
`app/usage.py`(라우터 `/admin/usage/*`, X-Admin-Key 필요), 단가표 `app/pricing.py`. `usage_log`의 `status_code=200`만 집계. 기간은 서버 로컬 시간: day=오늘 0시~, week=최근 7일, month=이번 달 1일~, all=전체. `from`/`to`는 UTC ISO.
- `cost_krw` = usage_log에 기록된 값(OpenRouter `usage.cost` 실제 비용 또는 게이트웨이 계산). `estimated_cost_krw` = 단가표(USD/1M 토큰, 입력·출력 분리) × 토큰 × `USD_TO_KRW`로 게이트웨이가 재계산한 값 → 공급자가 비용만 주거나 아무것도 안 줘도 서비스별 예상 비용 산출 가능. 모델명의 `openai/` 같은 접두는 떼고 찾으며, 표에 없는 모델(mock 등)은 gpt-4o-mini 단가로 계산하고 `models[].estimated:true`.
- `GET /admin/usage/summary?range=day|week|month|all` → `{range,from,to,total:{calls,input_tokens,output_tokens,cost_krw,estimated_cost_krw},services:[{service_id,name,...같은 합계,models:[{model,...같은 합계,estimated}]}]}`
- `GET /admin/usage/timeseries?range=...&bucket=hour|day` → `{range,bucket,from,to,services:[{service_id,name,points:[{t:"2026-10-31T01:00"|"2026-10-31",calls,tokens,cost_krw}]}]}` (t는 로컬 시간, 호출 없는 버킷은 생략)
- `GET /admin/usage/forecast?window_hours=1` → `{window_hours,rate_per_hour:{calls,tokens,cost_krw},projected:{end_of_day_cost_krw,end_of_month_cost_krw},per_service:[{service_id,name,rate_cost_krw_per_hour,projected_month_cost_krw}],budget:{monthly_budget_krw|null,hours_to_budget|null},method:"linear",note}`.
  예측 = 이미 쓴 금액(오늘/이번 달) + 최근 window 시간당 속도 × 남은 시간. `hours_to_budget` = (예산 − 이번 달 사용액) / 시간당 비용(이미 초과면 0, 속도 0이면 null). 한계: 단순 선형, 시간대·요일 패턴·급증(공격) 미반영, 창이 짧으면 변동 큼, 단가표는 수동 관리(공급자 가격 변경 시 갱신 필요).
- `GET /admin/usage/budget` → `{monthly_budget_krw}`; `PUT` `{monthly_budget_krw: 양의 정수|null}`(null=해제, 0 이하 400). setting `monthly_budget_krw`에 저장.

## I. 공급자 어댑터·OpenAI
- 어댑터(`app/providers.py`, `ADAPTERS`): 공급자마다 `lag_s`(보고 지연), `revoke_lag_s`(폐기 후 401까지), `model`, `chat_url`, `fetch_usage(http, cred)`, `revoke`, `prepare(body)`, `call_cost_usd(usage)`, (실제 공급자) `provision`, `cleanup`. 새 공급자 = 어댑터 클래스 1개 + `contract.USAGE_LAG_S`/`RATIOS`/`PROVIDERS` 항목.
  `fetch_usage`는 키별 누적값 중 **있는 것만** 준다: `{cost_usd?, tokens?, requests?, buckets?}` (`buckets=[(start,end,requests)]`).
  | provider | lag_s | 주는 값 | 폐기 |
  |---|---|---|---|
  | mock | 2 | requests, tokens, cost_usd | `POST /revoke` |
  | openrouter | 8 | cost_usd | `PATCH /keys/{hash}` disabled (약 20초) |
  | openai | 420 (실측 381초) | requests, tokens, 1분 buckets | 서비스 계정 삭제 (실측 2.8초) |
- 판정(탐지기 `assess`): 전제 = 공급자 값은 항상 게이트웨이 기록보다 늦다.
  - 정렬 비교 `diff_calls`(대시보드 표시): buckets가 있으면 반영 완료 버킷(끝 ≤ now − lag_s)의 누적 호출 수 vs 같은 경계 이전 게이트웨이 호출 수(버킷 경계 정렬, 누적 비교라 분 경계 오차가 쌓이지 않음). requests만 있으면 스냅샷 vs 스냅샷 − lag_s까지의 게이트웨이. cost만(또는 tokens만) 있으면 차액 ÷ 1건 기대값으로 호출 수 추정(`calls_estimated: true`).
  - 확실 유출 `certain_diff_calls`: 공급자 누적 − 게이트웨이 **현재까지 전체** 누적(오차 3건 차감). 공급자가 늦으므로 양수면 무단 사용이 확실하다(오탐 없음).
  - 판정값 = max(diff_calls, certain_diff_calls). 그래서 정렬 비교가 아직 반영 전이어도 확실 유출이 차단선을 넘으면 바로 BREACH. Mock에서는 항상 certain ≤ diff라 기존 동작과 같다.
  - 히스테리시스: 판정 상승은 즉시, 하강은 낮은 판정이 `HYSTERESIS_S`(10초) 이어질 때만.
- 추정 보정(OpenRouter): 1건 기대값 = 최근 `CALL_COST_RECENT_N`(20)건 게이트웨이 평균 비용을 [$0.000012, $0.0002]로 제한, 이력 없으면 `DEFAULT_CALL_COST_USD`=$0.000016(청중 1건 실측). tokens만이면 평균 토큰 [20, 1000], 기본 150.
- `/admin/dashboard` 추가 필드: 최상위·`credentials[]`에 `certain_diff_calls`, `diff_usd`(= max(0, 공급자 − 게이트웨이) USD, 허용오차 없음). `diff_krw`는 그대로. 크레덴셜 값은 워커 마지막 틱 계산값.
- `provider_usage.requests` = 공급자 누적 호출 수(OpenAI는 부분 반영 포함 총합, OpenRouter는 추정치).
- 엔드포인트(일반화): `POST /admin/{openrouter|openai}/provision` `{role, limit_usd?(OpenRouter만), name?}` → `{id, masked, role, hash_masked, leaked_key?}`. 이름은 항상 `november-` 접두. `POST /demo/reset-{openrouter|openai}`, `POST /demo/cleanup-{openrouter|openai}` → `{deleted}`.
- OpenAI: 키 = 프로젝트(`OPENAI_PROJECT_ID`, 기본 proj_xkSCJaCHHKA3GEm5bEm02pHJ) 서비스 계정, Admin 키 `.env`의 `OPENAI_ADMIN_KEY`. `provider_ref = key_id|service_account_id|project_id`. 사용량 `GET /v1/organization/usage/completions?bucket_width=1m&group_by=api_key_id&api_key_ids=<key_id>`, 어댑터가 15초 캐시. 중계 `https://api.openai.com/v1/chat/completions`, 모델 기본 gpt-4o-mini, 비용은 토큰 × 상수($0.15/$0.60 per 1M, 근사). `/live` 청중 페이지는 OpenAI 키를 지원하지 않는다(`data/live_key.json`은 OpenRouter PRIMARY만 기록).
- 시드 한도: reset-openrouter·reset-openai 모두 `unauthorized_call_limit`=100. 비율은 실제 공급자 0.05/0.2/0.35.
- 최종 손실 재계산: 폐기 `revoke_lag_s + lag_s` 후(OpenAI 약 7분). `loss_calls = max(정렬 차이, 확실 유출)`, 오차 미차감.

## J. 직원 측 WIF(OIDC)
직원/서비스가 장기 토큰 대신 IdP가 서명한 단기 JWT(OIDC ID 토큰)로 게이트웨이에 인증한다. 구현 `app/wif_inbound.py`, 테이블 `trusted_identity`.
- 중계 분기: 허니키 → IP 차단 → **IP 허용 검사** 뒤, Bearer 값이 점으로 나뉜 3조각이고 `nvm-`로 시작하지 않으면 JWT 경로. 통과하면 매핑된 service로 기존 경로(service ACTIVE 검사·중계·usage_log·탐지) 그대로.
- 검증: `alg`는 **RS256만**(none·HS256 거부). `iss`로 신뢰 규칙 조회 → 헤더 `kid`로 JWKS 키 선택·서명 검증 → `exp`/`nbf` 30초 여유 → `aud` = 규칙 `audience`(없으면 setting `wif_audience`, 기본 `november-gateway`) → `sub`가 `subject_pattern`(fnmatch 글롭)과 일치.
  외부 JWKS는 규칙의 `jwks_uri`를 httpx로 조회해 5분 캐시. 로컬 IdP issuer는 프로세스 내 키 직접 사용.
- 거부: 401 `{"error":"wif_invalid_signature|wif_expired|wif_audience_mismatch|wif_untrusted_identity|wif_malformed"}`. `access_denied_log` + `event(ACCESS_DENIED)` 기록, `token_masked` = 마스킹한 `sub`(JWT 원문 미저장). 허용 밖 IP는 JWT여도 403 `ip_not_allowed`.
- `GET /admin/trusted-identities` → `[{id,issuer,subject_pattern,audience,service_id,jwks_uri,created_at}]`
- `POST /admin/trusted-identities` `{issuer, subject_pattern, audience?, service_id | service_name, jwks_uri?}` → 위 항목 1개(서비스 없으면 404). `DELETE /admin/trusted-identities/{id}` → `{"ok":true}`(없으면 404).
- `GET /admin/wif/settings` → `{"wif_audience"}`, `PUT` 같은 형태(빈 값 400).
- 신뢰 규칙 예시(GitHub Actions): `{"issuer":"https://token.actions.githubusercontent.com","subject_pattern":"repo:november/*:ref:refs/heads/main","audience":"november-gateway","service_name":"직원A","jwks_uri":"https://token.actions.githubusercontent.com/.well-known/jwks"}`
- 데모 로컬 IdP(`DEMO_MODE`만): 시작 시 RSA 키쌍을 메모리에 생성(kid `local-1`), issuer = 환경변수 `IDP_ISSUER`(기본 `http://localhost:8000/idp`).
  `GET /idp/.well-known/jwks.json` → `{keys:[{kty,use,alg,kid,n,e}]}`, `POST /idp/token` `{sub, audience?, ttl_s?(기본 600, 1~3600)}` → `{token, expires_in}`.
  `POST /demo/wif/seed` → 규칙(issuer=로컬 IdP, `repo:november/*` → `직원A`) 1개, 멱등(재실행 시 service_id만 갱신). `직원A` 없으면 404 "먼저 /demo/reset".
  `POST /demo/wif/token` `{sub?}`(기본 `repo:november/app:ref:refs/heads/main`) → `{token, expires_in}`.
- 한계: 실제 외부 IdP(Google/GitHub) 연동은 미검증(jwks_uri 조회만 구현), 키 교체 자동화 없음(새 kid는 캐시 5분 만료 후 반영), 로컬 IdP 키는 재시작 시 바뀜(기존 토큰 무효), `/demo/reset`은 `trusted_identity`를 지우지 않음(리셋 후 `/demo/wif/seed` 재실행 필요), JWKS 조회는 동기(최대 5초), `jti` 재사용 방지·토큰 폐기 목록 없음(짧은 TTL로 완화), IP 거부 로그의 `token_masked`는 기존 방식(JWT 앞 3자·뒤 4자).

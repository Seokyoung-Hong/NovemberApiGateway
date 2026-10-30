# 문서 점검 기록 (2026-10-31)

기준: `app/` 코드 직접 대조 + 로컬 Mock 모드(8000/9000, PUBLIC_MODE 꺼짐) 실측. 코드는 수정하지 않았다.

| 문서 | 무엇이 틀렸나 | 어떻게 고쳤나 |
|---|---|---|
| contract.md 머리말 | `/demo/*`는 관리자 키 불필요라고만 적음(공개 모드 예외 누락), 관리자 키 오류 문구가 두 가지인 점 누락 | 공개 모드 조건과 `invalid_admin_key`/`bad admin key` 구분 추가 |
| contract.md B | 검사 순서가 IP→토큰→서비스 3단계로만 적힘(허니키·`ip_blocked`·WIF·503 순서 누락) | 실제 순서 ①~⑦과 각 응답·로그 여부로 정정 |
| contract.md B | `provider='mock'`인 PRIMARY만 고른다고 적음(현재는 provider 필터 없음) | PRIMARY·ACTIVE 최소 id, 어댑터 분기로 정정 |
| contract.md B | `usage_log.cost_krw = cost_krw(total_tokens)` 고정 공식 | `call_cost_usd × USD_TO_KRW`(Mock은 결과적으로 동일), 200만 기록, 기타 업스트림 코드 그대로 전달 추가 |
| contract.md C | `POST /admin/cidrs` 응답·기본 label, provider≠mock 400, 토큰 목록 GET 없음 누락 | 보충 |
| contract.md D | 대시보드 필드 목록에 `certain_diff_calls`·`diff_usd`·`calls_estimated`·`honeykey_hits`·`blocked_ips`가 D·E·I절에 흩어짐 | `detector/router.py` 반환 dict 키 기준 표로 통합(최상위·`credentials[]`) |
| contract.md D | RECOVERED가 "다음 정상 호출 누적 전까지 유지" | 실제는 RESOLVED 후 `RECOVERED_HOLD_S`=10초 |
| contract.md D | BLOCKED 조건이 "ROTATED 이전" | RESOLVED가 아니면(OPEN·ROTATED 모두) BLOCKED |
| contract.md D | 자동 대응 순서가 폐기→승격→incident 생성 | incident 생성(DETECTED)이 폐기보다 먼저, 이후 1초 간격 연출 단계로 정정. "생성/갱신"→ 생성만(이미 있으면 무시) |
| contract.md D | `gateway_calls` 정렬 기준이 `MOCK_USAGE_LAG_S` 고정 | 공급자별 `lag_s`로 정정, 합계에 REVOKED 포함되는 효과 명시 |
| contract.md D | reset 응답이 `tokens`만, K2 마스킹·허니키·live_key 삭제·trusted_identity 삭제 누락 | 보충(E·K절 중복 서술 통합) |
| contract.md D | 공격 시뮬레이터가 Mock 전용이라는 점 누락 | 명시 |
| contract.md D | 이벤트 kind 목록·SUSPECT/WARNING 발생 조건, 허니키 사고 timeline·status 누락 | 보충 |
| contract.md E | 거부 사유에 `wif_*` 누락, 허니키 60초 중복 방지·메모리 보관 누락 | 보충 |
| contract.md F | 속도 제한이 "X-Forwarded-For 우선" | 신뢰 프록시일 때만 XFF(K절 규칙)로 정정, 502 응답·participants 정의 추가 |
| contract.md G | `reset-openrouter` 한도 500(`OPENROUTER_LIMIT_CALLS`) | 코드값 100으로 정정(I절과 일치) |
| contract.md G | `DEFAULT_CALL_COST_USD`=0.00005, 평균 = 전체 평균 | 0.000016, 최근 20건 평균을 [$0.000012,$0.0002]로 제한으로 정정 |
| contract.md G | `/demo/reset`만 live_key.json 삭제 | `reset-openai`도 삭제 추가 |
| contract.md I | 어댑터 표에 `revoke_lag_s`·판정 비율 없음, provision 404·limit_usd 검증 누락 | 열·문구 보충 |
| contract.md J | "`/demo/reset`은 trusted_identity를 지우지 않음"(K절과 모순) | 지움으로 정정 |
| contract.md K | 테스트 서버가 실제 키 3종을 지운 채 실행한다는 점, `DEMO_MODE` 누락 | 보충 |
| erd.md | `trusted_identity` 테이블 없음 | 엔터티·관계 추가 |
| erd.md | `provider_usage.requests`, `incident.loss_calls`·`limit_calls` 누락 | 컬럼 추가 |
| erd.md | `access_denied_log.reason` 3종만, setting 키 1종만, provider는 mock만 | `ip_blocked`·`wif_*`, setting 4종, 공급자 3종·provider_ref 형식으로 정정 |
| erd.md | 대조가 `effective_diff`(금액)로 계산된다고 적음 | `effective_calls_diff`(호출 수)로 정정, FK 제약 없음 명시 |
| architecture.md | G1·다이어그램·4-1의 검사 순서가 IP→허니키 | 허니키→IP 차단→IP 허용→JWT/토큰→서비스로 정정 |
| architecture.md | `clientip.py`·`main.py` 모듈 누락, G2·D2 책임 일부 누락 | C2·C3 행 추가, 프로비저닝·정지·정리·공격(Mock 전용) 보충 |
| architecture.md | 환경변수 목록 6개만 | `DEMO_MODE`·`VAULT_KEY`·`LEAKED_KEY`·`IDP_ISSUER`·`LIVE_MODEL`·공급자 키 등 보충 |
| architecture.md | 비율 "OpenRouter만 낮춤", 시퀀스의 사고 기록 시점 | OpenRouter·OpenAI 0.05/0.2/0.35, 사고는 폐기 직전 생성으로 정정 |
| feature-spec.md | LiteLLM 포크 전제(실제는 미사용) | 초기 계획임과 실제 자체 구현 명시 |
| feature-spec.md | 수집 주기·Mock 지연 3초, `GET /usage?key_id=&since=`, `POST /keys/{id}/revoke` | 2초, `GET /usage?owner=`, `POST /revoke {owner}`로 정정 |
| feature-spec.md | `reconciliation` 테이블·`risk_score`, `NO_STANDBY` 상태 | 없음을 명시, 실제 기록 방식으로 정정 |
| feature-spec.md | F11 `GET/POST/DELETE /admin/tokens`, `timeseries?range=5m`(3초 버킷), `events?since=` | 실제 경로 `POST`/`DELETE`+`GET /admin/services`, `range=300`(2초), `since_id`로 정정 |
| feature-spec.md | F12 "`DEMO_MODE=1`일 때만 라우트 등록" | 항상 등록됨, `DEMO_MODE`의 실제 범위로 정정 |
| feature-spec.md | 상태 머신에서 RECOVERED 이후 복귀 없음, 한도 변경 "즉시" 반영 | RECOVERED 10초 후 NORMAL·히스테리시스, 다음 틱 반영으로 정정 |
| feature-spec.md | "미구현: 사용자 측 WIF, OpenAI 어댑터" | 구현 완료로 정정 |
| demo-scenario.md | 사용자 측 WIF "개발 진행 중" | 구현 완료로 정정, 실측 전이 시각·RECOVERED 10초·허니키 버튼·테스트 서버 Mock 전용 보충 |
| demo-page-spec.md | 502 `upstream_error`, 페이지 B 에러 코드 일부, 현재 live.html이 stats를 폴링하지 않는 점 누락 | 보충 |
| README.md | 문서 목록에 architecture·demo-page-spec 없음, 관리자 키가 `demo-admin` 고정처럼 적힘, hack.sio2.kr이 "설정 후" | 목록 갱신(새 문서 포함), 키 환경변수·테스트 서버 현황으로 정정 |
| (신규) frontend-handoff.md | — | 프론트·디자인 인계 문서 작성 |

## 코드 확인 필요
(문서와 다르거나 동작이 의심스러운 코드. 고치지 않았다.)
1. `app/detector/router.py:38-40` 관리자 키 오류가 `bad admin key`, `app/gateway/router.py:195-197`은 `invalid_admin_key`. 대시보드 계열만 문구가 다르다. `gateway.router._admin` 하나로 통일 권장.
2. (해소: 공격 시뮬레이터가 PRIMARY 공급자로 대상 선택, openrouter·openai는 409. contract M절) `app/detector/router.py:392-403` 공격 시뮬레이터가 공급자와 무관하게 항상 Mock으로 호출한다. OpenRouter/OpenAI 모드에서 누르면 공급자 사용량이 늘지 않아 탐지되지 않는다(Mock은 처음 보는 키도 받음).
3. `app/detector/router.py:412-461` `/demo/attack/*`·`/demo/reset*`가 `DEMO_MODE`와 무관하게 등록된다. feature-spec F12의 원래 의도("DEMO_MODE=1일 때만")와 다르다. 공개 모드에선 관리자 키로 보호되지만, `DEMO_MODE=0`인 비공개 실행에서는 누구나 리셋할 수 있다.
4. `app/detector/router.py:350-354` 사고·대시보드의 `calls_estimated`가 `provider == "openrouter"`로 하드코딩돼 있다. 새 비용 전용 공급자가 생기면 틀린다(어댑터 속성 또는 `assess` 결과 사용 권장).
5. `app/db.py:37` `access_denied_log.reason` 주석이 3종만 적혀 있다(`ip_blocked`·`wif_*` 누락). 주석만의 문제.
6. `app/gateway/router.py:149` 본문이 JSON이 아니면 `request.json()` 예외로 500이 날 것으로 보인다(실측 안 함, 400 반환 권장).
7. `app/detector/router.py:293-306` 대시보드 최상위 호출 수 합계가 REVOKED 크레덴셜을 포함해 복구 후 NORMAL인데도 `diff_calls`가 큰 값으로 남는다(실측 84). 의도라면 문서대로 두고, 아니면 ACTIVE만 합산할지 결정 필요.
8. `app/providers.py:21` 단가 상수(gpt-4o-mini)와 `app/pricing.py` 단가표가 중복된다(주석에도 교체 예정 명시).

- 2026-10-31 용어 통일: 관리자 키 `demo-admin`→`admin`(데모 고정값, 약한 키 경고 추가), "직원/employee"→"사용자/user"(시드 서비스 `직원A`·`직원B`→`userA`·`userB`).
- 2026-10-31 IP 정책: 허용 CIDR 0개면 `/v1` 모두 허용(기존: 전부 거부), `/demo/reset`의 CIDR 4개 시드 제거, `POST /admin/cidrs`에 `*` 허용, 대시보드 `ip_policy`(`open`|`restricted`) 추가.
- 2026-10-31 vLLM 공급자: `vllm` 어댑터·`/demo/reset-vllm`·공격 시뮬레이터 공급자별 대상(openrouter·openai 409)·`/live/chat` `sk-vllm-` 직접 전달 추가. contract M절, architecture P4, demo-scenario·frontend-handoff 표 갱신.

| architecture.md 외 | vLLM(자체 GPU) 구성이 실제 아키텍처 그림에 들어갈 수 있게 서술됨 | vLLM을 "해커톤 데모 전용, 아키텍처 그림 제외"로 표기하고 architecture.md 컴포넌트 표에서 P4 행을 제거, 2-6절·그림 작성 가이드에 제외 규칙 추가 |
| architecture.md | 구간별 보안 프로토콜 서술 없음 | 5-1절 "통신 구간별 보안 프로토콜" 표(8개 구간)와 그림 작성 가이드의 구간 라벨 항목 추가 |
| architecture.md | 컴포넌트도에 구간별 보안 프로토콜·NPM·Tailscale 경계가 없었음 | 3절 Mermaid 다이어그램을 NPM 노드, 인터넷/Tailscale 경계, 화살표별 프로토콜 라벨로 다시 그림(5-1절과 일치) |
| architecture.md | 게이트웨이 구간이 Tailscale 기준으로 서술됨 | 결정 반영: NPM이 단일 진입점, 서버는 Proxmox CT. 다이어그램 경계를 Proxmox 사설 LAN으로 바꾸고 5·5-1·6절 정정. 이 PC+Tailscale은 "현재 데모 구동(임시)"로 구분 |
| vllm-gateway-spec.md 외 | vLLM 주소가 로컬/Tailscale 기준 | vLLM 서비스 주소를 `https://vllm.dorm.sio2.kr`(숙소 NPM, TLS)로 정정하고 관리 키 설정 필수 명시. 배포용 VLLM_BASE_URL 기본값 안내 변경 |
| architecture.md, README | 서버가 이 PC+Tailscale 구동이라고 서술됨 | Proxmox CT 1003(172.30.1.43) 이전 완료 반영: NPM→CT, 재배포 스크립트 `deploy_test_server.sh`. IP는 VMID−100 규칙 적용 불가(903)로 CT 최상위 .42 위의 빈 주소 .43 선정 |
| key-security-design.md(신규) | 공급자 API 키 보관 설계 문서 없음 | 위협 모델, 봉투 암호화 키 계층, 평면 분리, 런타임 규칙, 현재 PoC 격차 8건과 P1~P4 로드맵 작성. architecture.md에서 링크 |
| architecture.md | 키 보관이 Vault(Fernet) 한 줄로만 표현됨 | 키 보관 설계 반영(코드는 PoC 그대로): 저장소 S2를 KMS/Vault Transit으로, S3 감사 로그·S4 관리 키 저장소 추가, 다이어그램·5절 신뢰 경계·5-1절 프로토콜 표(7,9,10,11행)·8절 전환 표 갱신 |
| architecture.md | 개인 사용자(사람) 대상 WIF가 아키텍처에 없었음 | 행위자 U6·IdP 확장, G3 설명, 다이어그램(U6 SSO 로그인·토큰 호출), 4-3-1 개인 사용자 WIF 시퀀스, 5절 신뢰 경계 3-2, 5-1절 12행, 8절 전환 표, 그림 가이드 갱신. PoC는 서비스 단위 `sub` 매핑까지임을 명시 |
| architecture.md | 다이어그램·표에 실제 기술명이 부족함 | 다이어그램 노드에 FastAPI·Uvicorn·httpx·cryptography·SQLite·Nginx Proxy Manager·Proxmox VE(LXC·systemd)·Let's Encrypt·AWS KMS/Vault·OpenAI/OpenRouter·Google/Entra/GitHub OIDC 등 기술명 병기, 2-7절 "기술 스택 한눈에" 표 추가 |
| tech-stack-logos.md(신규) | 아키텍처 그림용 로고 출처 정리 없음 | 기술 약 34개의 로고 공식 출처 URL·형식·사용 조건·확인 여부 표와 컴포넌트-기술 대응표 작성(이미지 미포함). architecture.md 2-7절에서 링크 |
| tech-stack-logos.md, architecture.md | 일부 로고(Let's Encrypt CC BY-NC, WireGuard 허락, Proxmox 조건)의 사용 가능 여부가 불명확 | 비상업 해커톤 발표용이라 전부 사용하기로 한 결정을 0절에 기록(표기 의무·변형 금지·오인 금지는 유지, 상업 전환 시 재검토) |
| multi-key-management-design.md(신규) | 사업 모델(기본 10만원 + 키당 1만원)과 다중 키 분할 관리 설계 없음 | 키 구조별 탐지 품질, 분할·테넌트 격리·예비 키 풀, 수명 주기 자동화, 과금 계량, 데이터 모델 추가, 위험·결정 사항을 설계. architecture.md·key-security-design.md에서 링크 |
| contract.md N(신규), architecture.md 4-2, frontend-handoff.md | 공급자 폐기가 실패·무효여도 "차단·복구됨"으로 표시되던 문제 | 폐기 재시도·401 확인·`revoke_guard`·재활성 감시, 이벤트 `REVOKE_FAILED`·`REVOKE_CONFIRMED`·`REVOKE_NOT_EFFECTIVE`, `credentials[].revocation`, RECOVERED 조건, 청중 경로 즉시 차단과 그 한계 기록 |
| contract.md O(신규), architecture.md 4-2, frontend-handoff.md | 서버가 판정 방식(사용량 %/건수)과 사용자 임계를 몰라 한도 하나로만 판정 | `detection_mode`·임계 6개 설정, 대시보드 `detection`·`credentials[].value`, 사고 `block_label`, vLLM 키 발급 허용, 미설정 시 레거시 유지 기록 |

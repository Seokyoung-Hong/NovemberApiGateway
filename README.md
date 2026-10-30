# NovemberAiGateway (해커톤 PoC)

AI API 키 유출 시 게이트웨이 기록과 공급자 사용량의 차액으로 무단 사용을 탐지하고, 한도 도달 전에 자동 차단·예비키 전환하는 게이트웨이.

## 실행 (Windows)
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\scripts\dev.ps1        # Mock Provider(9000) + Gateway(8000) 기동
```
- 데모 초기화: `POST http://127.0.0.1:8000/demo/reset` (사용자 토큰·허니키 새로 발급, WIF 신뢰 규칙도 지워지므로 WIF를 쓰려면 이어서 `POST /demo/wif/seed`)
- 공격 시작/중지: `POST /demo/attack/start`, `/demo/attack/stop`
- 리허설(서버 기동 후): `.\.venv\Scripts\python scripts\rehearse.py` - 리셋, 정상 호출, 공격, 차단·복구를 자동 점검하고 PASS/FAIL 출력.
- 구 스키마의 data/gateway.db는 서버 시작 시 자동 재생성됨(데모 데이터 삭제).
- OpenRouter 라이브 모드(실제 키로 유출·차단 시연): `POST /demo/reset-openrouter` (`.env`에 `OPENROUTER_MANAGEMENT_KEY`), 정리 `POST /demo/cleanup-openrouter`. 절차는 `scripts/live.md`, 명세는 `docs/contract.md` G절.
- 공급자 어댑터(mock·openrouter·openai, `app/providers.py`): `POST /demo/reset-openai`·`/demo/cleanup-openai`(`.env`에 `OPENAI_ADMIN_KEY`, 탐지 약 7분 지연). 명세 I절.
- vLLM 공급자(자체 GPU, **해커톤 데모 전용 — 실제 아키텍처 그림 제외**): `POST /demo/reset-vllm`, 환경변수 `VLLM_BASE_URL`(코드 기본 `http://127.0.0.1:8100`은 로컬 개발용, 배포는 `https://vllm.dorm.sio2.kr`)·`VLLM_ADMIN_KEY`·`VLLM_MODEL`·`USAGE_LAG_S_VLLM`(기본 2). 명세 M절, 공급자 인터페이스 `docs/vllm-gateway-spec.md`.
- 관리자 API는 `X-Admin-Key` 헤더(로컬 기본값 `admin`, 환경변수 `ADMIN_KEY`). 명세는 `docs/contract.md`.
- 공급자 키 보관: `KEY_BACKEND=fernet`(로컬 기본)은 `VAULT_KEY`가 없으면 무작위 키를 만들어 `data/.vault_key`(권한 600)에 저장한다(고정 기본값 없음). 서버는 `KEY_BACKEND=kms`(AWS KMS 봉투 암호화, CMK `alias/november-gateway-kek`). 설계는 `docs/key-security-design.md`.
- 기능 테스트 페이지: `http://127.0.0.1:8000/test`, 청중 페이지: `http://127.0.0.1:9000/live`.

## 테스트 서버 실행 (공개 모드, 계약 K절)
- **서버는 Proxmox CT 1003(172.30.1.43)에서 구동**되고 NPM(`sio2.kr`)이 앞단이다. 재배포: `bash scripts/deploy_test_server.sh 1003`.
- (로컬 점검용) `.\scripts\run_test_server.ps1` - 이 PC에서 Gateway 8800·Mock 9800을 숨김 실행(Tailscale 필요, 현재 운영 경로 아님, 종료 `.\scripts\stop_test_server.ps1`).
- 관리자 키는 `admin`(데모 서버 고정값, 공개 서버에서는 추측 가능하므로 접근 제한 필요). `/admin/*`·`/demo/*`·`/idp/token`에 `X-Admin-Key`. 로그 `novemberdeploy\logs\`.
- 외부 접속 `https://sio2.kr`(NPM 경유, 신뢰 프록시 NPM 172.30.1.101, 게이트웨이 CT `172.30.1.43:8000`, `/live*`는 같은 CT의 Mock `:9000`으로). 실제 AI 키는 넘기지 않으므로 Mock 모드만 동작.

## 문서
`docs/contract.md`(API 계약, 원본) · `docs/frontend-handoff.md`(프론트·디자인 인계) · `docs/demo-page-spec.md` · `docs/demo-scenario.md` · `docs/feature-spec.md` · `docs/architecture.md` · `docs/erd.md` · `docs/CHANGELOG-docs.md`(문서 점검 기록)

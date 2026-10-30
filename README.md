# NovemberAiGateway (해커톤 PoC)

AI API 키 유출 시 게이트웨이 기록과 공급자 사용량의 차액으로 무단 사용을 탐지하고, 한도 도달 전에 자동 차단·예비키 전환하는 게이트웨이.

## 실행 (Windows)
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\scripts\dev.ps1        # Mock Provider(9000) + Gateway(8000) 기동
```
- 데모 초기화: `POST http://127.0.0.1:8000/demo/reset` (직원 토큰 발급)
- 공격 시작/중지: `POST /demo/attack/start`, `/demo/attack/stop`
- 리허설(서버 기동 후): `.\.venv\Scripts\python scripts\rehearse.py` - 리셋, 정상 호출, 공격, 차단·복구를 자동 점검하고 PASS/FAIL 출력.
- 구 스키마의 data/gateway.db는 서버 시작 시 자동 재생성됨(데모 데이터 삭제).
- OpenRouter 라이브 모드(실제 키로 유출·차단 시연): `POST /demo/reset-openrouter` (`.env`에 `OPENROUTER_MANAGEMENT_KEY`), 정리 `POST /demo/cleanup-openrouter`. 절차는 `scripts/live.md`, 명세는 `docs/contract.md` G절.
- 공급자 어댑터(mock·openrouter·openai, `app/providers.py`): `POST /demo/reset-openai`·`/demo/cleanup-openai`(`.env`에 `OPENAI_ADMIN_KEY`, 탐지 약 7분 지연). 명세 I절.
- 관리자 API는 `X-Admin-Key: demo-admin` 헤더. 명세는 `docs/contract.md`.

## 테스트 서버 실행 (공개 모드, 계약 K절)
- `.\scripts\run_test_server.ps1` - origin/main을 `C:\Users\tjrdu\novemberdeploy\src`에 풀고 Gateway 8800·Mock 9800을 Tailscale IP로 숨김 실행(종료 `.\scripts\stop_test_server.ps1`).
- 관리자 키는 `C:\Users\tjrdu\novemberdeploy\admin_key.txt`(`/admin/*`·`/demo/*`·`/idp/token`에 `X-Admin-Key`), 로그 `novemberdeploy\logs\`.
- 외부 접속 `hack.sio2.kr`은 NPM 설정 후(신뢰 프록시 100.69.18.101).

## 문서
`docs/erd.md` · `docs/demo-scenario.md` · `docs/feature-spec.md` · `docs/contract.md`

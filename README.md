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
- 관리자 API는 `X-Admin-Key: demo-admin` 헤더. 명세는 `docs/contract.md`.

## 문서
`docs/erd.md` · `docs/demo-scenario.md` · `docs/feature-spec.md` · `docs/contract.md`

# 발표 라이브(청중 참여) 절차
1. `.env`(저장소 루트)에 직접 입력. 키는 채팅·슬라이드에 붙이지 말 것: `UPSTREAM_API_KEY=<OpenRouter 키>`, `UPSTREAM_BASE_URL=https://openrouter.ai/api/v1`, 필요 시 `LIVE_MODEL=openai/gpt-4o-mini`(OpenRouter는 `openai/` 접두어 필요할 수 있음).
2. 업스트림 키에는 공급자 대시보드에서 **사용 한도(크레딧 상한, 예: $5)** 를 꼭 걸어 둔다.
3. 서버 2개 기동: `./.venv/Scripts/python -m uvicorn app.mock_provider.main:app --port 9000`, `./.venv/Scripts/python -m uvicorn app.main:app --port 8000` (또는 `scripts/dev.ps1`).
4. 공개 URL: `cloudflared tunnel --url http://localhost:9000` → 출력된 `https://<랜덤>.trycloudflare.com` 뒤에 `/live` 붙인 주소를 QR로(아무 QR 생성기, 또는 `python -m qrcode` 류) 슬라이드에 넣는다. 터널 URL은 재시작마다 바뀐다.
5. 슬라이드 숫자: `GET <터널>/live/stats` → participants, calls, key_status.
리허설 체크리스트
- [ ] 폰(LTE)으로 QR → /live 열림, 전송 시 실제 GPT 답이 옴(가짜면 "mock response")
- [ ] 대시보드에서 탐지·자동 폐기 후 폰에 "🚫 이 키는 폐기되었습니다 (401)" 표시, /live/stats key_status=revoked
- [ ] `POST /demo/reset`(게이트웨이) 후 다시 active로 돌아옴
- [ ] 연타 시 "잠시 후 다시"(IP당 20초 10회 제한) 동작
장애 백업: 와이파이·터널이 죽으면 대시보드의 공격 시뮬레이터 버튼(`POST /demo/attack/start`)으로 같은 탐지 흐름을 보여 준다.

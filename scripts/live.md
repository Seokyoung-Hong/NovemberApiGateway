# 발표 라이브(청중 참여) 절차
1. `.env`(저장소 루트)에 직접 입력. 키는 채팅·슬라이드에 붙이지 말 것: `UPSTREAM_API_KEY=<OpenRouter 키>`, `UPSTREAM_BASE_URL=https://openrouter.ai/api/v1`, `LIVE_MODEL`(기본 `openai/gpt-4o-mini`).
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

## OpenRouter 모드 (청중이 실제 OpenRouter 키로 실제 GPT 사용)
1. `.env`에 `OPENROUTER_MANAGEMENT_KEY=<관리 키>`(직접 입력, 출력·슬라이드 금지). 서버 2개 기동(위 3번).
2. 프로비저닝: `POST http://127.0.0.1:8000/demo/reset-openrouter` → 응답 `leaked_key`(K1 원문), `tokens`(직원A/B). 이전 `november-` 키는 자동 삭제, K1·S1 각 $0.5 한도, 판정 한도 500호출(추정).
   응답의 `leaked_key`만 슬라이드에 표시(이 키는 발표용이라 노출 전제). `/live` 페이지에는 자동으로 같은 키가 채워진다.
3. 청중 접속: 터널 URL + `/live` QR(위 4번). 청중 호출은 게이트웨이를 거치지 않고 OpenRouter로 직접 간다.
4. 차단: 대시보드가 추정 호출 수로 SUSPECT(5%)→WARNING(20%)→BLOCKED(35%). 사용액 반영 약 6초 + 폴링 때문에 실제보다 늦게 보이고, 폐기 후 청중 401까지 최대 약 20초(실측 7초). 직원 호출은 S1로 계속 200.
   청중이 적거나 느리면 한도를 낮춘다: `PUT /admin/settings {"unauthorized_call_limit": 100}`(실측: 청중 약 2건/초, 24건째에 BLOCKED).
5. 정리: `POST /demo/cleanup-openrouter` → `{"deleted":2}`. 이후 `POST /demo/reset`으로 Mock 모드 복귀(라이브 페이지 키도 Mock 키로 돌아감).
- 리허설 체크: 폰에서 실제 GPT 답 → BLOCKED 후 폰에 401 → `/live/stats` key_status=revoked → 정리 후 OpenRouter에 `november-` 키 0개.

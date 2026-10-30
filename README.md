# NovemberAiGateway (Ntrophy Gateway) — 해커톤 PoC

> **AI API 키가 새도, 공급자가 알려 주는 사용량과 게이트웨이 기록의 차이로 무단 사용을 잡아내고, 한도에 닿기 전에 그 키를 자동으로 폐기한 뒤 예비 키로 바꿔 서비스를 끊김 없이 이어 가는 게이트웨이.**

<p align="center"><img src="docs/screenshots/00-landing.png" alt="랜딩 페이지" width="720"></p>

## 목차
1. [문제와 핵심 아이디어](#1-문제와-핵심-아이디어)
2. [라이브 데모](#2-라이브-데모)
3. [데모 스크린샷](#3-데모-스크린샷)
4. [동작 원리](#4-동작-원리)
5. [시스템 아키텍처](#5-시스템-아키텍처)
6. [구현 현황과 한계](#6-구현-현황과-한계)
7. [빠른 시작](#7-빠른-시작)
8. [주요 API](#8-주요-api)
9. [보안 노트와 데모 전용 구성](#9-보안-노트와-데모-전용-구성)
10. [문서 목차 · 라이선스](#10-문서-목차--라이선스)

---

## 1. 문제와 핵심 아이디어

**문제**
- AI API 키는 개발자 PC, `.env`, 공개 저장소, 사내 위키를 거치며 쉽게 샌다.
- 샌 키는 공격자가 **공급자(OpenAI·OpenRouter 등)에 직접** 부르므로 회사 쪽 시스템에는 흔적이 남지 않는다.
- 공급자 대시보드·청구서로 알게 되는 시점은 이미 늦다. 그렇다고 키를 급히 지우면 그 키를 쓰던 **정상 서비스가 멈춘다**.

**핵심 아이디어**
- 회사의 모든 AI 호출을 게이트웨이 하나로 모은다. 사용자는 공급자 키를 받지 않고 게이트웨이 토큰(또는 WIF JWT)으로 호출한다.
- 게이트웨이는 **자기가 중계한 호출 수**를 알고, 공급자는 **그 키로 실제 쓰인 사용량**을 (늦게) 알려 준다.
- 공급자 보고는 항상 게이트웨이 기록보다 늦다. 그러므로 **공급자 값이 게이트웨이 기록을 넘으면, 그 차이는 게이트웨이를 거치지 않은 호출 = 유출**이다(정상 호출은 게이트웨이에 먼저 기록되므로 이 경로에는 오탐이 없다).
- 차이가 차단선에 닿으면 자동으로 **키 폐기 → 예비 키 승격**. 사용자는 같은 토큰으로 계속 200을 받는다.

## 2. 라이브 데모

- 서비스 주소: **https://sio2.kr** (이전 주소 `hack.sio2.kr`는 301로 `sio2.kr`에 리다이렉트)
- 데모 서버에는 실제 AI 키를 넣지 않았다. Mock 공급자(가짜 응답) 모드로 동작한다.
- 관리자 화면·데모 제어에는 `X-Admin-Key`가 필요하다. **데모 서버 고정 키이며 발표자에게 문의.**

| 화면 | 경로 | 누가 보나 |
|---|---|---|
| 랜딩 | `/` | 처음 방문자 |
| 관리자 콘솔(홈·사용량·키·한도·접근 제어·사고·데모 제어) | `/ui/index.html?server=https://sio2.kr` | 대표(관리자) |
| 공격자(청중) 채팅 — 유출 키로 공급자 직접 호출 | `/ui/live.html` | 발표 청중 |
| 직원 채팅 — 게이트웨이 토큰으로 호출 | `/ui/employee.html` | 정상 사용자 |
| 기능 테스트 페이지(디자인 없음, 모든 API 버튼) | `/test` | 개발자 |
| 자동 테스트 페이지 | `/test/auto` | 개발자 |

> 교차 출처 POST는 리다이렉트를 따라가지 못할 수 있으므로 콘솔의 `?server=` 값은 반드시 `https://sio2.kr`로 둔다.

## 3. 데모 스크린샷

### 상태 흐름: NORMAL → SUSPECT → WARNING → BLOCKED → RECOVERED
| NORMAL | SUSPECT | WARNING |
|---|---|---|
| <img src="docs/screenshots/10-console-home-normal.png" alt="콘솔 홈 정상 상태" width="300"> | <img src="docs/screenshots/11-console-home-suspect.png" alt="콘솔 홈 의심 상태" width="300"> | <img src="docs/screenshots/12-console-home-warning.png" alt="콘솔 홈 경고 상태" width="300"> |
| 게이트웨이 기록과 공급자 보고가 겹친다. 차이 ≈ 0 | 공급자 선만 올라가며 "게이트웨이가 모르는 호출"이 생긴다 | 차이가 경고선을 넘어 경고 알림 |

| BLOCKED (자동 차단 팝업) | RECOVERED | 사고 타임라인 |
|---|---|---|
| <img src="docs/screenshots/13-console-home-blocked-popup.png" alt="자동 차단 팝업" width="300"> | <img src="docs/screenshots/14-console-home-recovered.png" alt="예비 키로 복구된 상태" width="300"> | <img src="docs/screenshots/15-console-incident-timeline.png" alt="사고 타임라인" width="300"> |
| 차단선 도달: 키 폐기 → 예비 키 승격 → 통보 → 검증 | 예비 키로 전환, 사용자 호출은 끊기지 않음 | 단계별 시각과 손실 호출 수(< 한도) |

### 관리자 콘솔
| | | |
|---|---|---|
| <img src="docs/screenshots/20-usage.png" alt="사용량 화면" width="260"><br>사용량·월말 예측 | <img src="docs/screenshots/21-limits.png" alt="한도 설정 화면" width="260"><br>판정 방식·한도·허용 오차 | <img src="docs/screenshots/22-keys.png" alt="키 관리 화면" width="260"><br>공급자 키(마스킹) |
| <img src="docs/screenshots/23-keys-issue-sheet.png" alt="공급자 키 등록 시트" width="260"><br>공급자 키 등록 | <img src="docs/screenshots/24-access.png" alt="접근 제어 화면" width="260"><br>허용 IP·토큰·허니키 | <img src="docs/screenshots/25-incidents.png" alt="사고 목록" width="260"><br>사고 목록 |
| <img src="docs/screenshots/26-demo-control.png" alt="데모 제어 패널" width="260"><br>데모 제어(공격 시작·리셋) | <img src="docs/screenshots/27-connection-sheet.png" alt="서버 연결 설정" width="260"><br>서버 연결 설정 | <img src="docs/screenshots/16-console-home-dark.png" alt="콘솔 홈 다크 모드" width="260"><br>다크 모드 |

### 공격자(청중) 화면과 직원 화면
| 공격자: 준비 | 공격자: 채팅 중 | 공격자: 차단됨 | 직원: 채팅 | 직원: 차단 이후 |
|---|---|---|---|---|
| <img src="docs/screenshots/30-attacker-ready.png" alt="공격자 화면 준비" width="170"> | <img src="docs/screenshots/31-attacker-chatting.png" alt="공격자 채팅 중" width="170"> | <img src="docs/screenshots/32-attacker-blocked.png" alt="공격자 차단됨" width="170"> | <img src="docs/screenshots/33-employee-chat.png" alt="직원 채팅" width="170"> | <img src="docs/screenshots/34-employee-after-block.png" alt="차단 이후에도 이어지는 직원 채팅" width="170"> |

유출 키는 "차단되었습니다"(401)로 막히고, 직원은 같은 게이트웨이 토큰으로 예비 키를 통해 계속 대화한다.

### 모바일과 테스트 페이지
| 모바일 홈 | 모바일 공격자 차단 | 모바일 직원 | 테스트 콘솔 `/test` | 자동 테스트 `/test/auto` |
|---|---|---|---|---|
| <img src="docs/screenshots/40-mobile-home.png" alt="모바일 콘솔 홈" width="150"> | <img src="docs/screenshots/41-mobile-attacker-blocked.png" alt="모바일 공격자 차단" width="150"> | <img src="docs/screenshots/42-mobile-employee.png" alt="모바일 직원 채팅" width="150"> | <img src="docs/screenshots/50-test-console.png" alt="기능 테스트 페이지" width="220"> | <img src="docs/screenshots/51-test-auto-pass.png" alt="자동 테스트 통과" width="220"> |

## 4. 동작 원리

### 4-1. 시나리오 8장면 (약 6분, 상세 `docs/demo-scenario.md`)
| # | 장면 | 내용 |
|---|---|---|
| 1 | 등록 | 주 키 K1, 예비 키 S1, WIF 크레덴셜 K2 등록(화면엔 마스킹만), 사용자 토큰 발급, 한도 설정 |
| 2 | 정상 운영 | 사용자 A·B 호출 200, 차이 ≈ 0, 허용 IP 밖 호출은 403 |
| 3 | 유출 | K1이 공개 저장소에 노출. 시스템은 아직 모른다 |
| 4 | 무단 사용 | 청중이 유출 키로 공급자에 직접 호출(또는 공격 시뮬레이터 초당 5건). SUSPECT → WARNING |
| 5 | 자동 차단 | 차단선 도달 → 폐기·승격·통보·검증, BLOCKED. 청중 화면 401 |
| 6 | 복구 | 사용자는 같은 토큰으로 끊김 없이 200, RECOVERED → NORMAL |
| 7 | 허니키(선택) | 심어 둔 가짜 토큰 사용 시 겉으론 평범한 401, 내부에선 전체 토큰 폐기·서비스 일시 정지 |
| 8 | 한계와 확장 | 공급자별 지연, 자동 대응은 피해를 줄이는 도구이지 완전 예방이 아님 |

Mock 공격(초당 5건) 실측: 약 6초 SUSPECT, 약 14초 WARNING, 약 18초 BLOCKED, 약 21초 RECOVERED.

### 4-2. 판정 방식 3가지 (`docs/contract.md` O절)
판정값 = `공급자 보고 호출 수 − (게이트웨이 기록 + 내부 호출) − 허용 오차` (음수는 0)

| 방식 | 기준 | 기본 임계 (의심 / 경고 / 차단) |
|---|---|---|
| 레거시 한도(기본) | 허용 무단 호출 수 한도(기본 100) × 비율 | Mock·vLLM 10% / 50% / 70%, OpenRouter·OpenAI 5% / 20% / 35% |
| 사용량 % (`usage_pct`) | 공급자 보고 중 게이트웨이가 모르는 호출 비율 | 5% / 10% / 20% |
| 건수 (`calls`) | 설명되지 않는 호출 건수 | 1 / 5 / 10건 |

- 차단선을 100%보다 낮게 두는 이유: 집계 지연·폴링 사이에 늘어나는 호출을 흡수해 최종 손실이 한도 아래에 머물게 하려고.
- 판정 상승은 즉시, 하강은 낮은 판정이 10초 유지될 때만(히스테리시스).
- 비용만 주는 공급자(OpenRouter)는 평균 호출 비용으로 호출 수를 추정한다(화면에 "추정" 표시).

### 4-3. 자동 대응 순서 (`docs/contract.md` D·N절)
1. **탐지**(DETECTED) → 사고 생성
2. **공급자에서 키 폐기** (실패 시 최대 3회 재시도)
3. **예비 키를 주 키로 승격** — 사용자 호출은 바로 예비 키로 이어짐
4. **사고 기록·관리자 통보**
5. **청중 경로 즉시 차단** — 공급자 폐기 결과와 무관하게 `/live/chat`은 즉시 401
6. **폐기 확인** — 유출 키로 1토큰 호출해 401인지 확인. 확인 전까지 BLOCKED 유지, 3~30초 간격 재폐기. 확인 후 30분간 사용량이 다시 늘면 `REVOKE_NOT_EFFECTIVE`로 재폐기

사고는 (예비 키 검증 성공) **그리고** (폐기 확인) 둘 다일 때만 RESOLVED가 된다.

### 4-4. 허용 오차와 폴링 (`docs/contract.md` P절)
- 허용 오차 `tolerance_calls`: 관리자 설정(0~100, 기본 0). 환경변수 `TOLERANCE_CALLS`는 설정이 없을 때의 기본값.
- 내부 호출(승격 키 검증, 폐기 확인 호출)과 진행 중 요청은 "설명된 호출"로 친다.
- 탐지 워커 주기 1초(`POLL_INTERVAL_S`), 속도 제한이 있는 공급자는 어댑터별 최소 간격(OpenRouter 3초, OpenAI 15초)으로만 조회.

### 4-5. 공급자별 지연 (실측)
| 공급자 | 사용량 반영 지연 | 키 폐기 반영 |
|---|---|---|
| Mock | 2초 | 즉시 |
| OpenRouter | 사용액 약 6초(호출 수 API는 당일 미지원, 비용으로 추정) | 비활성화 후 최대 약 20초 |
| OpenAI | 95~381초(실측 2회, 편차 큼. 분 단위 정확한 호출 수) | 서비스 계정 삭제 후 약 3초 |

공급자 지연만큼 탐지가 늦어지고, 그 사이 손실은 "호출 속도 × 지연"이다.

## 5. 시스템 아키텍처

| 컴포넌트 구성 | 탐지·대응 시퀀스 |
|---|---|
| <img src="docs/screenshots/60-arch-components.png" alt="컴포넌트 아키텍처" width="420"> | <img src="docs/screenshots/61-arch-detection-sequence.png" alt="탐지와 자동 대응 시퀀스" width="420"> |
| **개인 사용자 WIF (목표 구성)** | **공급자 키 보관 (봉투 암호화)** |
| <img src="docs/screenshots/62-arch-personal-wif.png" alt="개인 사용자 WIF 흐름" width="420"> | <img src="docs/screenshots/63-arch-key-storage.png" alt="공급자 키 보관 구조" width="420"> |

**구성 요약** (상세 `docs/architecture.md`)
- 진입점: Nginx Proxy Manager가 TLS(Let's Encrypt)를 종료하고 Proxmox VE의 LXC 컨테이너로 프록시. `/live*`만 같은 컨테이너의 Mock 공급자로.
- 게이트웨이 앱(FastAPI 한 프로세스): 중계·접근 통제(허니키 → 허용 IP → 토큰/WIF → 서비스 상태), 관리자 API, 탐지·대응 워커, 사용량 집계·예측, 공급자 어댑터(mock·openrouter·openai).
- 저장소: SQLite(운영 전환 시 PostgreSQL). 평문 공급자 키는 DB에 없다.

**보안 프로토콜 요지** (`docs/architecture.md` 5-1절)
- 인터넷 구간은 모두 HTTPS, 내부는 Proxmox 사설 LAN. `X-Forwarded-For`는 신뢰 프록시에서 온 것만 인정.
- 사용자: Bearer 게이트웨이 토큰(서버는 SHA-256 해시만 보관) 또는 RS256 JWT(서명·`exp`·`aud`·`iss` 검증, 신뢰 규칙 매핑).
- 게이트웨이 → AWS KMS: HTTPS + SigV4, 해당 CMK에 `GenerateDataKey`·`Decrypt`만 허용된 최소 권한 IAM 사용자, 암호화 컨텍스트 바인딩.

**공급자 키 보관** (`docs/key-security-design.md`, 설정 절차 `docs/kms-setup.md`)
| 구분 | 내용 |
|---|---|
| **적용됨** | **AWS KMS 봉투 암호화**(`KEY_BACKEND=kms`): 크레덴셜마다 DEK 생성, AES-256-GCM, KEK(CMK)는 KMS 밖으로 나가지 않음, DEK 메모리 캐시 10분, KMS 사용은 CloudTrail 기록. `provider_ref`는 해시만, DB·환경 파일 권한 600/700. 로컬 개발은 `fernet` 백엔드 |
| 향후 확장 | HashiCorp Vault(KV v2·Transit·AppRole), append-only 감사 로그, 테넌트별 KEK, 관리 키 별도 경로, IAM Roles Anywhere |

**기술 스택**
| 계층 | 기술 |
|---|---|
| 언어·런타임 | Python 3, Uvicorn, asyncio |
| 웹 | FastAPI, httpx |
| 암호·토큰 | cryptography(AES-256-GCM, Fernet, RS256), JWT, OIDC |
| 저장소 | SQLite(PoC) → PostgreSQL(운영) |
| 키 관리 | AWS KMS, boto3, AWS IAM, AWS CloudTrail (향후 HashiCorp Vault) |
| 진입점·인프라 | Nginx Proxy Manager, Let's Encrypt, Proxmox VE, LXC(Ubuntu 24.04), systemd |
| AI 공급자 | OpenAI API, OpenRouter API, Mock Provider(자체) |

## 6. 구현 현황과 한계

| 영역 | 구현됨 | 아직 없음 / 설계만 |
|---|---|---|
| 중계 | OpenAI 호환 `/v1/chat/completions`(스트리밍 포함), 게이트웨이 토큰, 허용 IP 대역, 서비스 정지·재개 | 장치 단위 인증(mTLS 등) |
| 탐지·대응 | 사용량 대조 판정 3방식, 자동 폐기·예비 키 승격, 폐기 확인·재폐기, 사고 타임라인, 허니키 | 외부 알림 채널(이메일·슬랙) |
| 공급자 | Mock, OpenRouter(라이브 모드), OpenAI(Usage API) 어댑터 | 그 밖의 공급자 |
| 신원 | 워크로드 WIF(로컬 데모 IdP의 RS256 JWT → 서비스 매핑) | **개인 사용자 SSO·Device Code·사람 단위 예산·그룹 정책은 설계만**, **외부 IdP(Google·Entra 등) 연동 안 함**, **실제 공급자 측 WIF 연동 안 함**(Mock STS로 대체) |
| 키 보관 | AWS KMS 봉투 암호화(서버), Fernet(로컬) | Vault, 앱 감사 로그, 테넌트별 KEK, 정기 교체 자동화 |
| 관리자 | 고정 관리자 키(`X-Admin-Key`) | SSO·역할 기반 접근 |
| 사용량 | 서비스별 집계, 단가표 예상 비용, 월말 선형 예측, 예산 | 사람 단위 귀속 |
| 테스트 | 판정 함수 assert, `scripts/rehearse.py`, `scripts/demo_guard.py`, `/test/auto` | 단위 테스트 스위트 |

**알려진 한계**
- 공격자가 공급자에 **직접** 부르는 호출은 공급자 폐기가 확인되기 전까지 막을 수 없다(청중 경로만 즉시 차단). 자동 대응은 사후 피해를 줄이는 도구다.
- 탐지는 공급자 사용량 반영 지연만큼 늦다(OpenAI는 수 분).
- 서버가 AWS 밖(Proxmox)에 있어 KMS 인증에 **장기 IAM 액세스 키**가 서버 환경 파일(권한 600)에 있다. 운영은 IAM Roles Anywhere 또는 AWS 안의 IAM 역할로 바꿔야 한다.
- 공급자 관리 키(OpenRouter Management·OpenAI Admin)가 환경변수로 중계와 같은 프로세스에 있다.
- 게이트웨이 자체가 침해되면 키 사용 권한까지 노출될 수 있다(허니키·사용량 대조로 탐지만 가능).
- 단일 프로세스·SQLite PoC다.

## 7. 빠른 시작

### 로컬 실행 (Windows)
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\scripts\dev.ps1          # Mock Provider(9000) + Gateway(8000) 기동
```
- 콘솔: `http://127.0.0.1:8000/ui/index.html?server=http://127.0.0.1:8000`, 테스트 페이지: `http://127.0.0.1:8000/test`, 청중 페이지(Mock): `http://127.0.0.1:9000/live`
- 데모 초기화: `POST /demo/reset` (토큰·허니키 새로 발급. WIF를 쓰려면 이어서 `POST /demo/wif/seed`)
- 공격 시작/중지: `POST /demo/attack/start`, `POST /demo/attack/stop`

### 점검 스크립트 (서버 기동 후)
```powershell
.\.venv\Scripts\python scripts\rehearse.py      # 리셋 → 정상 호출 → 공격 → 차단·복구를 자동 점검, PASS/FAIL
.\.venv\Scripts\python scripts\demo_guard.py --gw http://127.0.0.1:8000 --mock http://127.0.0.1:9000 --mode mock
                                                # 기준선(scripts/demo_guard_baseline.json) 대비 API 응답 형태 비교
```

### 환경변수 (주요)
| 이름 | 기본값 | 설명 |
|---|---|---|
| `PUBLIC_MODE` | `0` | `1`이면 인터넷 공개 모드: `/demo/*`·`/idp/token`도 관리자 키 필요, `/docs` 비활성 |
| `ADMIN_KEY` | 추측 가능한 고정 문자열 | `X-Admin-Key` 값. **공개 서버에서는 반드시 바꿀 것** |
| `TRUSTED_PROXIES` | (빈 값) | `X-Forwarded-For`를 믿을 직접 접속 IP(쉼표 구분) |
| `DEMO_MODE` | `1` | 로컬 데모 IdP, `X-Demo-Source-IP` 허용 |
| `DB_PATH` | `data/gateway.db` | SQLite 경로 |
| `KEY_BACKEND` | `fernet` | `kms`(AWS KMS 봉투 암호화, 서버) 또는 `fernet`(로컬) |
| `VAULT_KEY` | (없음) | fernet용 키. 로컬은 없으면 `data/.vault_key` 자동 생성, 공개 모드 fernet에선 필수 |
| `KMS_KEY_ID` | `alias/november-gateway-kek` | KMS KEK 별칭 또는 ARN |
| `AWS_REGION` | `ap-northeast-2` | KMS 리전 (AWS 자격 증명은 최소 권한 IAM 사용자) |
| `TOLERANCE_CALLS` | `0` | 허용 오차 기본값(관리자 설정이 우선) |
| `POLL_INTERVAL_S` | `1` | 탐지 워커 주기(초) |
| `DEMO_PROVIDER` | `mock` | `/demo/reset`이 시드할 공급자(예: `vllm`) |
| `MOCK_BASE_URL` | `http://127.0.0.1:9000` | Mock 공급자 주소 |
| `LEAKED_KEY` | 데모용 Mock 키 | 청중에게 "유출 키"로 공개되는 시드 K1 값 |
| `UPSTREAM_API_KEY` / `UPSTREAM_BASE_URL` | (없음) / OpenRouter | Mock이 실제 AI로 패스스루할 때 |
| `LIVE_MODEL` | `openai/gpt-4o-mini` | 청중 채팅 모델 |
| `OPENROUTER_MANAGEMENT_KEY` | (없음) | OpenRouter 라이브 모드(키 발급·사용량·폐기) |
| `OPENAI_ADMIN_KEY` / `OPENAI_PROJECT_ID` | (없음) | OpenAI 어댑터 |
| `VLLM_BASE_URL` / `VLLM_ADMIN_KEY` / `VLLM_MODEL` / `USAGE_LAG_S_VLLM` | 로컬 `http://127.0.0.1:8100` / (없음) / Gemma 모델 / `0` | vLLM 공급자(해커톤 데모 전용, 9절) |
| `IDP_ISSUER` | — | 로컬 데모 IdP issuer |

`.env`는 `app/contract.py`가 직접 읽는다(이미 설정된 환경변수 우선). `.env`는 저장소에 올리지 않는다.

### 실제 공급자 모드 (선택)
- OpenRouter 라이브: `POST /demo/reset-openrouter` → 정리 `POST /demo/cleanup-openrouter` (절차 `scripts/live.md`)
- OpenAI: `POST /demo/reset-openai` → 정리 `POST /demo/cleanup-openai`

### 서버 배포
```bash
bash scripts/deploy_test_server.sh <VMID>   # origin/main을 Proxmox LXC에 배포(멱등, 미커밋 변경 제외)
```
서버는 공개 모드 + KMS 백엔드로 systemd에서 실행되며, 실제 AI 키(`UPSTREAM_API_KEY`·`OPENROUTER_MANAGEMENT_KEY`·`OPENAI_ADMIN_KEY`)는 넣지 않는다.

## 8. 주요 API
상세 스키마와 에러 코드는 [`docs/contract.md`](docs/contract.md)가 원본이다.

| 구분 | 엔드포인트 | 인증 |
|---|---|---|
| 사용자 중계 | `POST /v1/chat/completions` (OpenAI 호환, 스트리밍) | `Authorization: Bearer <게이트웨이 토큰 또는 JWT>` |
| 관리자: 상태 | `GET /admin/dashboard`, `/admin/timeseries`, `/admin/events?since_id=`, `/admin/incidents[/{id}]` | `X-Admin-Key` |
| 관리자: 설정 | `GET/PUT /admin/settings` (한도·판정 방식·허용 오차) | `X-Admin-Key` |
| 관리자: 키 | `GET/POST/DELETE /admin/credentials`, `POST /admin/{openrouter\|openai\|vllm}/provision` | `X-Admin-Key` |
| 관리자: 접근 | `/admin/cidrs`, `/admin/services` (+`suspend`·`resume`), `/admin/tokens`, `/admin/access-denied`, `/admin/honeykeys`, `/admin/trusted-identities` | `X-Admin-Key` |
| 관리자: 사용량 | `GET /admin/usage/summary`, `/timeseries`, `/forecast`, `/budget` | `X-Admin-Key` |
| 데모 | `POST /demo/reset`, `/demo/reset-{provider}`, `/demo/cleanup-{provider}`, `/demo/attack/start`·`stop`, `/demo/honeypot/hit`, `/demo/wif/seed`, `/demo/wif/token` | 공개 모드에서 `X-Admin-Key` |
| 청중 라이브(Mock) | `GET /live`, `GET /live/config`, `POST /live/chat`, `GET /live/stats` | 없음(IP당 속도 제한) |
| WIF IdP(데모) | `GET /idp/.well-known/jwks.json`, `POST /idp/token` | 토큰 발급은 공개 모드에서 `X-Admin-Key` |

## 9. 보안 노트와 데모 전용 구성

**보안 노트**
- 공개 모드(`PUBLIC_MODE=1`)는 관리자·데모·토큰 발급 경로를 관리자 키로 막고 Swagger(`/docs`)를 끈다. 키 비교는 상수시간(`hmac.compare_digest`).
- **관리자 키 기본값은 추측 가능한 고정 문자열이다.** 공개 서버에서는 `ADMIN_KEY`를 바꾸고 프록시 단 접근 제한(IP·인증)을 함께 건다. 데모 서버는 발표 편의상 고정 키를 쓰며 이는 알려진 한계다.
- 저장소에는 비밀이 없다: `.env`, `data/`(DB·`.vault_key`)는 `.gitignore` 대상이고, 서버 환경 파일은 서버에만(권한 600) 있다. 공급자 키는 응답·로그에 마스킹(`sk-...a9f2`)으로만 나타난다.
- 데모 응답(`/demo/*`)에는 시연을 위해 유출 키·토큰 원문이 포함된다. 운영 구성이 아니다.

**해커톤 데모 전용 구성**
- **vLLM(자체 GPU, `gpu-serving/`)**: 비용 없이 청중 라이브·공격 시뮬레이터를 돌리기 위한 시연용 공급자다(`POST /demo/reset-vllm`, 인터페이스 `docs/vllm-gateway-spec.md`). **실제 시스템 아키텍처 그림에는 포함하지 않는다.**
- Mock Provider, 로컬 데모 IdP·Mock STS, 공격 시뮬레이터, 허니키 시연 버튼도 데모용이다.

## 10. 문서 목차 · 라이선스

| 문서 | 내용 |
|---|---|
| [`docs/contract.md`](docs/contract.md) | API 계약 원본(A~P절: 중계·관리자·탐지·청중·OpenRouter·OpenAI·WIF·공개 모드·vLLM·폐기 실패·판정 방식·허용 오차) |
| [`docs/architecture.md`](docs/architecture.md) | 컴포넌트·시퀀스·신뢰 경계·통신 구간별 보안·배포 구조·기술 스택 |
| [`docs/key-security-design.md`](docs/key-security-design.md) | 공급자 API 키 보관 설계(위협 모델, KMS 봉투 암호화, 격차·로드맵) |
| [`docs/kms-setup.md`](docs/kms-setup.md) | AWS KMS 설정 절차(CMK·최소 권한 IAM·`KEY_BACKEND=kms` 전환)와 운영 반영 기록 |
| [`docs/multi-key-management-design.md`](docs/multi-key-management-design.md) | 다중 키 분할 관리·테넌트별 KEK 설계(미구현, 목표 구성) |
| [`docs/demo-scenario.md`](docs/demo-scenario.md) | 발표 데모 시나리오 8장면과 준비 체크리스트 |
| [`docs/demo-page-spec.md`](docs/demo-page-spec.md) | 청중·사용자 채팅 페이지 명세 |
| [`docs/feature-spec.md`](docs/feature-spec.md) | 기능 명세(F1~F15)와 상태 머신 |
| [`docs/frontend-handoff.md`](docs/frontend-handoff.md) | 프론트·디자인 인계: 화면별 API 매핑, 에러 코드, 샘플 JSON |
| [`docs/erd.md`](docs/erd.md) | 데이터 모델(ERD) |
| [`docs/vllm-gateway-spec.md`](docs/vllm-gateway-spec.md) | vLLM 앞단 게이트웨이 계량·폐기 인터페이스(데모 전용) |
| [`docs/tech-stack-logos.md`](docs/tech-stack-logos.md) | 기술 스택 로고 출처와 사용 조건 |
| [`docs/CHANGELOG-docs.md`](docs/CHANGELOG-docs.md) | 문서 점검 기록 |
| [`app/static/ui/README.md`](app/static/ui/README.md) | 웹 화면(콘솔·청중·직원) 안내 |
| [`scripts/live.md`](scripts/live.md) | OpenRouter 라이브 모드 진행 절차 |

**라이선스·로고 표기**
- 별도 라이선스 파일이 없다. 해커톤 출품용 PoC다.
- 아키텍처 그림 등에 쓴 기술 로고는 **비상업 해커톤 발표용**으로만 사용하며, **각 로고는 해당 소유자의 상표**다. 후원·제휴·인증을 뜻하지 않는다. 출처와 사용 조건은 `docs/tech-stack-logos.md`.

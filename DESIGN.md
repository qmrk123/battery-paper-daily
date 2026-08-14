# 설계 노트 & 근거 (2026-08-14 조사 기반)

## 소스: OpenAlex(주) + arXiv(보조)

라이브 실측(2026-08-14)으로 확정한 제약:

| 필터/정렬 | 무료 티어 | 사용 |
|---|---|---|
| `from_publication_date` | ✅ 무료 | **채택** — 게재일 기준 최근 N일 |
| `sort=publication_date:desc` | ✅ 무료 | 채택 |
| `from_created_date` / `from_updated_date` | ❌ 유료(Premium) | 회피 |
| `sort=created_date:desc` | ❌ 유료 | 회피 |

- **API 키 불필요.** `mailto`(polite pool)만으로 하루 1회 수집엔 충분. 필요 시
  `OPENALEX_API_KEY` 환경변수를 지원(옵션).
- 초록은 **inverted index** 로 오므로 클라이언트에서 복원(`reconstruct_abstract`).
  일부 논문(closed)은 초록이 없어 요약 시 fallback 필요.
- "새 논문" 정의: 게재일-기준 롤링 윈도우 + `seen.json` 누적 중복제거.
  게재→색인 지연이 있어 며칠 늦게 잡히는 논문이 있으나, 윈도우가 이를 흡수.
- arXiv는 `cond-mat.mtrl-sci`/`physics.chem-ph` 만(자성 등 물리 오탐 축소),
  ToU 준수로 3초/요청 스로틀. OpenAlex에도 색인된 프리프린트는 DOI로 교차 중복제거.

## 정밀도: 3단 깔때기

키워드 검색만으론 오탐이 많음(실측: "high-nickel cathode" → 니켈 제련·Al 용접 혼입).
→ `include`/`exclude` 정규식 + 전기화학 `context` 게이트로 1차 정리, 최종은 LLM 게이트.
`context` 게이트가 "lithium-rich antiperovskite 자성" 같은 비전지 물리 논문을 제거함(검증됨).

## 그래픽 초록: 라이선스 우선(license-first) 베스트-에포트

깔끔한 per-DOI 이미지 API는 **어디에도 없음**. 핫링크는 출판사(Elsevier/Wiley/ACS/
Springer)가 referer·봇 차단 + 저작권 문제. 방침:

1. OA(CC-BY/CC-BY-SA/CC0) 논문 → 이미지 캐싱 + 출처·라이선스 표기(재호스팅 가능)
2. 그 외 → og:image 시도(검증기 통과 시), 실패 시 **소재색 플레이스홀더 + 원문 링크**
3. 공개 사이트에 비-OA 이미지를 재호스팅하지 않음(저작권 회색지대 회피)

→ 표시 안 되는 논문이 생기는 건 **정상**. ACS/RSC는 og:image 적중률 높고
Elsevier/Wiley/Springer는 대개 커버/로고라 플레이스홀더로 떨어짐.

## 한글 요약: Claude Haiku 4.5 (두 백엔드)

`summarize.py`가 백엔드를 자동 선택:
- **구독 CLI (기본, 추가 과금 없음):** 번들 `claude.exe`를 헤드리스(`-p
  --output-format json --model haiku`)로 호출 → 구독으로 과금. 헤드리스 인증은
  데스크톱 로그인과 분리돼 있어 **`claude setup-token` 1회 + `CLAUDE_CODE_OAUTH_TOKEN`**
  필요(실측 확인: 토큰 없으면 "Not logged in"). CLI 경로는 `%APPDATA%\Claude\
  claude-code\*\claude.exe` 최신 버전을 자동 탐색(`resolve_claude_cli`).
- **API 키 (종량제):** `ANTHROPIC_API_KEY` 있으면 SDK로 tool-forced JSON 사용.
  Haiku 4.5 $1/$5 per M tok, 하루 50편 ≈ $0.15, 월 $2~5(Batch 시 -50%).
- 요약과 **LLM 관련성 게이트를 한 번의 호출**로 묶어 비용·오탐 동시 해결.
  CLI는 tool 강제가 어려워 프롬프트로 "순수 JSON만" 지시 후 방어적 파싱.
- 초록 없는 closed 논문은 제목-기반 요약(품질 낮음, `(제목 기반 추정)` 표기).
- CI 구독 모드: 워크플로가 `npm i -g @anthropic-ai/claude-code`로 CLI 설치 후 토큰 사용.

## 자동화/호스팅: GitHub Actions → Pages

- 공개 repo면 Actions 분·Pages 무료. `actions/configure-pages` →
  `upload-pages-artifact` → `deploy-pages` (파생물 커밋 없이 OIDC 배포).
- 스케줄 크론은 **기본 브랜치에서만**, **60일 무활동 시 자동중지** 주의.
- 시크릿: `ANTHROPIC_API_KEY` (Settings → Secrets). Max 구독 OAuth 토큰
  (`claude setup-token`)도 가능하나 무인 대량 사용은 ToS 회색지대 → 계량 API 키 권장.

## 데이터 계약 (site/app.js 가 읽음 — 필드명 고정)

`Paper`: `id, source, title, url, published, topics[], doi, authors[], venue,
abstract_en, oa_status, oa_url, first_seen, image, summary_ko, relevant`.
일자 파일 `data/YYYY-MM-DD.json = {date, generated_at, count, papers[]}`,
`data/index.json = {dates[], topics[], updated_at}`, `data/seen.json = {id: first_seen}`.

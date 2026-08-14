# 전지 소재 논문 데일리 (battery-paper-daily)

리튬 금속 · 소듐 금속 음극 / High-Ni NCM · Li-rich 양극재 — 매일 새로 색인된 논문을
소재별 탭으로 모아 **제목 · 링크 · 그래픽 초록 · 한글 요약**과 함께 보여주는 정적 사이트.

## 빠르게 보기 (로컬)

```bat
run.bat
```

`.venv` 생성 → 의존성 설치 → 오늘자 논문 수집 → 사이트 조립 →
`http://localhost:8765` 로 브라우저를 엽니다.

## 구성

```
pipeline/        수집 파이프라인 (Python, 키 불필요)
  openalex.py    OpenAlex — 주 소스 (초록 복원, from_publication_date 무료 필터)
  arxiv.py       arXiv — cond-mat 프리프린트 보조
  config.py      topics.yaml 로드 + 정규식 필터
  fetch.py       소스 → 필터 → 중복제거(DOI 기준)
  summarize.py   (4단계) 한글 요약 + LLM 관련성 게이트 — Anthropic
  images.py      (3단계) 그래픽 초록 베스트-에포트
  store.py       data/seen.json · data/YYYY-MM-DD.json · data/index.json
  main.py        CLI
config/topics.yaml   4개 소재의 검색어·필터 (여기서 정밀도 조정)
site/            정적 프런트 (index.html · style.css · app.js)
scripts/         build_site.py(→public/) · snapshot.py(자체완결 1파일)
data/            수집 결과 (커밋됨 = 사이트 콘텐츠)
```

## 파이프라인 (정밀도 3단 깔때기)

1. **검색(recall)** — OpenAlex `title_and_abstract.search` + arXiv (소재별 검색어)
2. **정규식 후필터(precision)** — `include` / `exclude` + 전기화학 `context` 게이트
3. **LLM 관련성 게이트(final)** — 요약 단계에서 주제 무관 논문 제외 *(4단계)*

"새 논문"은 게재일 기준 최근 `window_days`(기본 7일)를 매일 조회하고
`data/seen.json` 으로 **누적 중복제거** → 오늘 처음 본 것만 그날 파일에 기록합니다.
(OpenAlex 무료 티어는 `from_created_date`/`from_updated_date`가 유료라 게재일 기준 사용.)

## 흔한 작업

```bat
REM 특정 소재만 미리보기 (아무것도 쓰지 않음)
.venv\Scripts\python -m pipeline.main --topic high-ni-ncm --dry-run --show-filtered

REM 수집만 (서버 없이) — 스케줄러/CI 용
update.bat

REM 자체완결 1파일 미리보기 만들기
.venv\Scripts\python scripts\snapshot.py preview.html
```

검색 정밀도가 아쉬우면 `config/topics.yaml` 의 `queries` / `include` / `exclude`
/ `context` 를 고치고 `--dry-run --show-filtered` 로 즉시 확인하세요.

## 자동화 & 배포 (GitHub Actions → Pages)

`.github/workflows/daily.yml` 이 매일 06:00 KST 에 수집→요약→이미지→커밋백→Pages 배포.
**한 번만 설정하면** 이후 자동입니다:

1. GitHub 저장소 생성(무료 Actions·Pages를 위해 **public 권장**) 후 push.
2. **Settings → Pages → Source: `GitHub Actions`**.
3. **Settings → Secrets and variables → Actions**
   - Secret `ANTHROPIC_API_KEY` (한글 요약용). 없으면 요약만 건너뜁니다.
   - (선택) Variable `CONTACT_EMAIL` — API polite-pool 연락처.
4. **Settings → Actions → General → Workflow permissions: Read and write**.
5. **Actions 탭 → daily-update → Run workflow** 로 첫 실행(시드).

주의: 스케줄은 **기본 브랜치에서만** 돌고, 저장소가 **60일간 활동 없으면 자동 중지**됩니다.
`data/`(특히 `seen.json`)는 매 실행 후 **저장소로 커밋백**되어 중복제거 원장이 유지됩니다.

- 로컬 자동화가 좋으면 Windows 작업 스케줄러로 `update.bat` 을 걸어도 됩니다(PC가 켜져 있어야 함).
- **한글 요약**: `ANTHROPIC_API_KEY` 필요 (Haiku 4.5, 하루 50편 ≈ 월 $2~5).

자세한 소스·이미지·비용 근거는 [DESIGN.md](DESIGN.md) 참고.

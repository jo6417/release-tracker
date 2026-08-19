# 출시 트래커

게임·영화·시리즈의 출시일을 자동 추적해 Notion에 기록하는 개인용 시스템.
서버 없음, 프론트엔드 없음. GitHub Actions가 매일 API를 조회하고 Notion이 UI를 담당한다.

문서: [PROJECT.md](PROJECT.md) 목적·범위 · [SCHEMA.md](SCHEMA.md) DB 정의 · [HANDOFF.md](HANDOFF.md) 현재 상태

## 구성

| 파일 | 하는 일 |
|---|---|
| `config.py` | 공통 설정, DB 스키마 정의, `.env` 로드 |
| `create_db.py` | Notion에 작품/일정 DB 생성 (최초 1회) |
| `sync_schema.py` | `config.py`의 스키마를 기존 DB에 반영 |
| `migrate.py` | 네이버 캘린더 .ics → Notion 이관 |
| `review_list.py` | 이관 대상 검토 목록(`이관검토.md`) 생성 |
| `apply_review.py` | 검토에서 `x` 표시한 항목을 제외 목록으로 |
| `match_tmdb.py` | 영화·시리즈를 TMDB와 대조 |
| `apply_tmdb.py` | 매칭 결과를 이관 데이터에 반영 |
| `match_igdb.py` | 게임을 IGDB와 대조 (스팀 appid 역조회 + 제목 검색) |
| `apply_igdb.py` | 매칭 결과를 노션에 반영 |
| `sync_steam.py` | 스팀 보유·플레이시간을 노션에 반영 |
| `add_steam_games.py` | 스팀에만 있는 게임을 작품으로 추가 |
| `sync_series.py` | 시리즈 방영·완결 상태를 TMDB로 갱신 (완결대기 알림·캘린더) |
| `track.py` | 스냅샷 대조로 변화 감지 → 알림 |
| `notify.py` | 노션 알림 DB에 카드 한 장 (@멘션 → 폰 푸시) |
| `describe.py` | 알림 문구 — 요약 한 줄과 "언제부터 보면 되나" 상세 |
| `briefing.py` | 저녁 19시 브리핑 — 지금 상태 한 장 (오늘·7일내·진행중·백로그) |
| `create_notify_db.py` | 알림 DB 생성 (최초 1회) |
| `rename_option.py` | select 옵션 이름 바꾸기 (노션 UI가 더 빠르다 — HANDOFF 참고) |
| `adapters/tmdb.py` | TMDB 조회 (소스별 격리) |
| `adapters/anilist.py` | AniList 조회 — 애니 회차별 방영일 (키 불필요) |
| `adapters/igdb.py` | IGDB 조회 |
| `adapters/steam.py` | 스팀 라이브러리 조회 |

## 대조표

API로 자동 매칭이 안 되는 부분은 표로 고정해 둔다. 한 번 만들면 계속 재사용된다.

| 파일 | 내용 |
|---|---|
| `steam_map.json` | 스팀 appid ↔ 작품 제목 (스팀은 영문, 노션은 한글) |
| `game_title_en.json` | 한글 게임 제목 → 영문 (IGDB 한글 검색 히트율 10%) |
| `tmdb_match.json` / `igdb_match.json` | 매칭 결과 캐시 |

## 사용

```bash
python migrate.py --dry              # 미리보기 (Notion 미변경)
python review_list.py                # 검토 목록 생성
python apply_review.py               # 검토 결과 반영
python match_tmdb.py                 # TMDB 매칭
python apply_tmdb.py                 # 매칭 결과 반영
python migrate.py                    # 실제 이관
python migrate.py --limit 5          # 소량 테스트
python migrate.py --only 타이탄폴,씨프  # 특정 작품만
```

이관은 `migrate_state.json`에 진행 상태를 남긴다. 중단되어도 다시 실행하면
이어서 진행하며 중복을 만들지 않는다.

## 설계 원칙

- 의존성 최소화 — Python 표준 라이브러리만 사용
- 소스 격리 — 데이터 소스마다 어댑터 1개. 하나가 깨져도 나머지는 동작
- 한글 우선 — Notion 속성명·알림 문구·작품명은 한글

## 키

`.env`에 넣는다 (gitignore됨). GitHub Actions는 리포 시크릿을 쓴다.

```
NOTION_TOKEN=
TMDB_API_KEY=
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
STEAM_API_KEY=
```

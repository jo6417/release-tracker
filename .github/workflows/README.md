# 워크플로

`daily.yml` — 매일 09:00 KST에 실행.

1. 스팀 라이브러리 동기화 (소유·플레이시간)
2. 노션을 읽어 스냅샷 저장 → 어제와 대조 → 변화가 있으면 알림
3. 스냅샷을 리포에 커밋 (원본 보존)

각 단계는 격리되어 있다. 스팀이 죽어도 추적은 돈다.

## 필요한 시크릿

`NOTION_TOKEN`, `STEAM_API_KEY`, `TMDB_API_KEY`,
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`

## 주의

`db_ids.json`과 `notify_ids.json`은 gitignore 되어 있어 Actions에서 읽을 수 없다.
둘 다 비밀이 아니므로 리포에 올리거나, 시크릿으로 넘겨야 한다. (아래 참조)

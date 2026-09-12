# -*- coding: utf-8 -*-
"""닌텐도 다이렉트·State of Play·Xbox 쇼케이스를 방송 전에 미리 잡는다.

`sync_events.py`(IGDB)는 쇼가 끝난 뒤에만 안다 — 미래 이벤트를 안 준다.
반면 이 셋은 방송을 예고할 때 유튜브에 **"예정된 라이브 스트림"** 페이지를
먼저 만들고, 그 안에 시작 시각이 이미 확정돼 있다. YouTube Data API의
`search?eventType=upcoming`으로 그 페이지를 잡는다.

두 가지 함정을 실측으로 확인했다 (2026-09-12).

1. **같은 방송의 언어별 중복.** Xbox TGS 2026 방송이 영어·일본어·수어·
   화면해설 4개로 따로 잡혔는데 전부 같은 시각이었다. 채널+시작시각으로
   묶어 하나만 남긴다.
2. **지나간 예약이 섞여 나온다.** `eventType=upcoming`인데도 2018년 예약
   스트림이 나왔다(다시는 안 열릴 방송). `scheduledStartTime`이 과거면
   버린다.

날짜는 유튜브가 준 값을 그대로 쓴다 — 게임쇼 사전 예고 전체에서 유일하게
기사 본문을 파싱하지 않아도 되는 경로다(구조화된 필드라 정확하다).

사용법:
    python sync_upcoming_shows.py --dry
    python sync_upcoming_shows.py
"""
import argparse
import datetime
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (.env 로드)
import notify
from config import API, headers

IDS_FILE = "db_ids.json"
YT_API = "https://www.googleapis.com/youtube/v3"
CHANNELS = {
    "Nintendo": "UCGIY_O-8vW4rfX98KlMkvRg",
    "PlayStation": "UC-2Y8dQb0S6DtpxNgAKoJKA",
    "Xbox": "UCjBp_7RuDBUYbd1LegWEJ8g",
}
# 예측에 쓸 플랫폼 키워드. 채널과 얼추 맞춰뒀다 — 정확한 매핑이 아니라
# "나올 법한 후보"를 추리는 참고용이라 대충 맞아도 손해가 없다.
PLATFORM_HINT = {
    "Nintendo": ("Switch", "Switch2"),
    "PlayStation": ("PS5", "PS4"),
    "Xbox": ("Xbox",),
}
MAX_예측 = 5


def _yt_get(path, params):
    params = dict(params, key=_key())
    url = f"{YT_API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def _key():
    import os
    k = os.environ.get("YOUTUBE_API_KEY", "")
    if not k:
        raise RuntimeError("YOUTUBE_API_KEY가 없습니다 (.env 확인)")
    return k


def query_all(dbid, flt=None):
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if flt:
            body["filter"] = flt
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(f"{API}/databases/{dbid}/query",
                                     data=json.dumps(body).encode(),
                                     headers=headers(), method="POST")
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read())
        out += d["results"]
        if not d.get("has_more"):
            return out
        cursor = d["next_cursor"]


def create_page(dbid, props):
    req = urllib.request.Request(
        f"{API}/pages",
        data=json.dumps({"parent": {"database_id": dbid},
                         "properties": props}).encode(),
        headers=headers(), method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def known_ids(schedule_db):
    out = set()
    for p in query_all(schedule_db):
        m = re.search(r"yt_upcoming:([\w-]+)", txt(p["properties"]["외부ID"]))
        if m:
            out.add(m.group(1))
    return out


def upcoming_for(channel_id):
    """채널의 예정된 라이브. (videoId, title, scheduledStartTime) 목록."""
    hits = _yt_get("search", {"part": "snippet", "channelId": channel_id,
                              "eventType": "upcoming", "type": "video",
                              "maxResults": 25})
    ids = [it["id"]["videoId"] for it in hits.get("items", [])]
    if not ids:
        return []
    details = _yt_get("videos", {"part": "snippet,liveStreamingDetails",
                                 "id": ",".join(ids)})
    out = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for it in details.get("items", []):
        st = (it.get("liveStreamingDetails") or {}).get("scheduledStartTime")
        if not st:
            continue
        when = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
        if when <= now:
            continue  # 지나간 예약 찌꺼기
        out.append((it["id"], it["snippet"]["title"], st))
    return out


def _lang_rank(title):
    """언어 변형 중 뭘 대표로 남길지 우선순위. 낮을수록 우선."""
    if not title.startswith("["):
        return 0        # 대괄호 없는 맨 제목이 최우선
    if "english" in title.lower():
        return 1        # 없으면 영어 표기 우선 (한국어 사용자 기준 가장 읽힘)
    return 2


def dedup(items):
    """같은 시각이면 하나만 남긴다 — 언어별 중복 방송이 그렇다."""
    by_time = {}
    for vid, title, st in items:
        cur = by_time.get(st)
        if not cur or _lang_rank(title) < _lang_rank(cur[1]):
            by_time[st] = (vid, title, st)
    return list(by_time.values())


def predict(work_db, channel_name):
    hints = PLATFORM_HINT.get(channel_name, ())
    if not hints:
        return []
    rows = query_all(work_db, {
        "and": [{"property": "종류", "select": {"equals": "게임"}},
                {"property": "날짜정밀도", "select": {
                    "does_not_equal": "확정"}}]})
    out = []
    for p in rows:
        pr = p["properties"]
        plats = [o["name"] for o in pr["플랫폼"]["multi_select"]]
        if any(h in plats for h in hints):
            out.append(txt(pr["제목"]))
        if len(out) >= MAX_예측:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    known = known_ids(ids["schedule_db"])
    alerts = []
    n_created = 0

    for name, cid in CHANNELS.items():
        try:
            items = dedup(upcoming_for(cid))
        except urllib.error.HTTPError as e:
            print(f"[{name}] 조회 실패 {e.code}: {e.read().decode()[:200]}",
                  file=sys.stderr)
            continue
        for vid, title, st in items:
            if vid in known:
                continue
            when = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
            kst = when.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            props = {
                "이름": {"title": [{"type": "text", "text": {"content": title[:1900]}}]},
                "종류": {"select": {"name": "발표"}},
                "날짜": {"date": {"start": kst.isoformat()}},
                "날짜정밀도": {"select": {"name": "확정"}},
                "캘린더노출": {"checkbox": True},
                "스트림": {"url": f"https://youtu.be/{vid}"},
                "외부ID": {"rich_text": [{"type": "text",
                                        "text": {"content": f"yt_upcoming:{vid}"}}]},
            }
            print(f"  [{name}] {title[:50]:50} {kst.strftime('%Y-%m-%d %H:%M')}")
            if not a.dry:
                create_page(ids["schedule_db"], props)
                time.sleep(0.34)
            n_created += 1
            guesses = predict(ids["work_db"], name)
            alerts.append((title, kst, guesses))

    print(f"\n{'기록 예정' if a.dry else '기록'} {n_created}건")

    if alerts and not a.dry:
        summary = ["[게임쇼 예고] " + ", ".join(
            f"{t} ({d.strftime('%m/%d %H:%M')})" for t, d, _ in alerts)]
        details = []
        for title, d, guesses in alerts:
            lines = [f"{d.strftime('%Y-%m-%d (%a) %H:%M')} 방송 예정"]
            if guesses:
                lines.append("나올 만한 것: " + ", ".join(guesses))
            details.append((f"[게임쇼 예고] {title}", lines))
        notify.send_card(f"게임쇼 예고 {len(alerts)}건", summary=summary,
                         details=details, kinds=["게임쇼예고"], count=len(alerts))
        if not notify.spooling():
            print("알림 카드 1장 발송 완료")

    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()

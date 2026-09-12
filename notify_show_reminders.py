# -*- coding: utf-8 -*-
"""임박한 게임쇼를 D-7·D-3·D-1·당일에 다시 알린다.

`sync_events.py`·`sync_upcoming_shows.py`는 **발견했을 때 한 번**만 일정
DB에 기록하고 끝난다. 그 뒤로 방송일이 다가와도 다시 알리는 로직이
없었다 — 2026-09-12에 사용자가 "TGS 임박하면 계속 포함돼야 하는 거
아니냐"고 지적해서 만들었다.

대상은 출처를 안 가린다. 일정 DB의 `종류=발표` · `캘린더노출=True` ·
미래 날짜인 행이면 전부 본다 — IGDB가 잡은 것이든, 유튜브 사전 감지든,
사용자가 채팅으로 직접 넣은 연례 대형 행사(TGS·gamescom 등)든 같은
경로로 리마인드된다.

**같은 문턱을 두 번 안 넘는다.** 하루씩 착실히 돌면 7일 전에 한 번,
3일 전에 한 번… 이렇게 넉 번 알린다. 실행이 하루 밀려 D-8에서 D-2로
건너뛰면(과거에 실제로 이런 일이 있었다) 그사이 안 쓴 문턱 중 **가장
가까운 것 하나만** 알린다 — 7일 전과 3일 전을 동시에 말하면 이상하다.

사용법:
    python notify_show_reminders.py --dry
    python notify_show_reminders.py
"""
import argparse
import datetime
import io
import json
import re
import urllib.request

import config  # noqa: F401  (.env 로드)
import notify
from config import API, headers

IDS_FILE = "db_ids.json"
STATE_FILE = "show_reminder_state.json"
MILESTONES = [7, 3, 1, 0]  # 며칠 전에 다시 알릴지. 필요하면 숫자만 바꾸면 된다.


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


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def load_state():
    try:
        with io.open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def save_state(state):
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def next_milestone(days_left, already_sent):
    """오늘 알릴 문턱 하나. 없으면 None.

    days_left 이상인 문턱 중 아직 안 보낸 것 중 가장 작은 값을 고른다 —
    가장 최근·가장 임박한 정보를 우선한다."""
    candidates = [m for m in MILESTONES if days_left <= m and m not in already_sent]
    return min(candidates) if candidates else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    today = now.date()

    rows = query_all(ids["schedule_db"], {
        "and": [{"property": "종류", "select": {"equals": "발표"}},
                {"property": "캘린더노출", "checkbox": {"equals": True}},
                {"property": "날짜", "date": {"on_or_after": today.isoformat()}}]})

    state = load_state()
    alerts = []
    for p in rows:
        pr = p["properties"]
        raw = pr["날짜"]["date"]["start"]
        if "T" in raw:
            when = datetime.datetime.fromisoformat(raw)
            when_local = when.astimezone(now.tzinfo)
            date_str = when_local.strftime("%m/%d(%a) %H:%M")
            days_left = (when_local.date() - today).days
        else:
            when_local = datetime.date.fromisoformat(raw)
            date_str = when_local.strftime("%m/%d(%a)")
            days_left = (when_local - today).days
        if days_left < 0:
            continue  # 이미 지남 — sync_events.py의 사후 감지 몫이다

        sent = state.get(p["id"], [])
        m = next_milestone(days_left, sent)
        if m is None:
            continue

        title = txt(pr["이름"])
        when_word = "오늘" if days_left == 0 else f"{days_left}일 후"
        alerts.append((p["id"], title, when_word, date_str, m))

    if not alerts:
        print("임박한 게임쇼 없음")
        return

    for pid, title, when_word, date_str, m in alerts:
        print(f"  {title[:44]:44} {when_word:6} ({date_str})")
        state.setdefault(pid, []).append(m)

    if not a.dry:
        save_state(state)
        summary = ["[게임쇼 임박] " + ", ".join(
            f"{t} ({w})" for _, t, w, _, _ in alerts)]
        details = [(f"[게임쇼 임박] {t}", [f"{w} · {d} 방송 예정"])
                  for _, t, w, d, _ in alerts]
        notify.send_card(f"임박한 게임쇼 {len(alerts)}건", summary=summary,
                         details=details, kinds=["게임쇼"], count=len(alerts))
        if not notify.spooling():
            print("알림 카드 1장 발송 완료")
    else:
        print("--dry 모드: 상태 미저장")


if __name__ == "__main__":
    main()

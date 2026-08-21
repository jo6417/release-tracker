# -*- coding: utf-8 -*-
"""새 영상(트레일러·게임플레이) 감지.

**없던 기능이다.** 2026-08-20 오전 11시(KST)에 블랙 미스: 종규의 15분
게임플레이 트레일러가 올라왔는데 그날 저녁 브리핑에 한 줄도 없었다.
이유가 두 개였고 둘 다 여기서 고친다.

1. 영상을 보는 코드가 아예 없었다
2. `블랙 미스: 종규`가 작품 DB에 없었다 — 있어도 못 잡았을 상황

## 소스를 셋 쓰는 이유는 하나다 — 속도

같은 트레일러라도 어디서 보느냐에 따라 며칠이 갈린다. 실측:

| 소스 | 지연 | 대상 |
|---|---|---|
| **유튜브 채널 RSS** | 몇 분 | 채널을 등록해둔 작품 |
| 스팀 `movies` | 같은 날 | 스팀 상점 페이지가 있는 게임 |
| TMDB `/videos` | 같은 날~하루 | 영화·시리즈 |
| IGDB `videos` | **몇 주** | 나머지 게임 |

종규가 딱 이 차이에 걸렸다. 그날 IGDB의 마지막 갱신은 7월 21일이었고 스팀
페이지는 아직 없다. **유튜브 RSS만 그날 안에 알 수 있었다.** 그래서 채널
등록이 이 스크립트에서 제일 값나가는 부분이다 (`youtube_watch.json`).

키가 필요 없다는 것도 이유다. RSS는 그냥 XML 한 장이라 토큰도 쿼터도 없다.

## 첫 실행은 조용하다

기존 작품 하나에 트레일러가 열몇 개씩 달려 있어서, 처음부터 알리면 수백 건이
한꺼번에 울린다. 처음 본 작품은 기준값만 잡고 넘어간다 (`sync_prices.py`와 같다).

사용법:
    python sync_media.py --dry
    python sync_media.py --add-channel @BlackMythGame "블랙 미스: 종규"
    python sync_media.py
"""
import argparse
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (.env 로드)
import describe
import notify
from adapters import igdb, tmdb
from config import API, headers

IDS_FILE = "db_ids.json"
STATE_FILE = "media_state.json"
WATCH_FILE = "youtube_watch.json"

# 영상을 볼 가치가 있는 진행도. 이미 한 작품의 트레일러는 소식이 아니다.
WAITING = ("출시 대기", "구매 대기", "공개 대기", "시청 안함")
# 나온 지 이만큼 지난 작품은 새 영상이 떠도 홍보물이지 소식이 아니다.
STALE_DAYS = 120
MAX_ALERT = 12          # 카드 한 장에 담을 상한
SEEN_MAX = 200          # 작품당 기억할 영상 id 개수. 상태 파일이 무한정 크지 않게
STEAM_APP = "https://store.steampowered.com/api/appdetails"
UA = {"User-Agent": "Mozilla/5.0 (release-tracker)"}


# ─────────────────────────────────────────────
# 노션
# ─────────────────────────────────────────────
def query_all(dbid):
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
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


def patch_page(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            print(f"[에러] {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            raise
    raise RuntimeError("재시도 초과")


def txt(prop):
    if not prop:
        return ""
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def sel(prop):
    return prop["select"]["name"] if prop and prop.get("select") else None


# ─────────────────────────────────────────────
# 상태 파일
# ─────────────────────────────────────────────
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


# ─────────────────────────────────────────────
# 소스별 조회 — 전부 [{id, 제목, url, 날짜}] 로 맞춰 돌려준다
# ─────────────────────────────────────────────
def youtube_channel_id(handle):
    """@핸들 → UC로 시작하는 채널 id. RSS 주소에는 id가 필요하다."""
    if handle.startswith("UC"):
        return handle
    url = "https://www.youtube.com/" + handle.lstrip("/")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=25) as r:
        page = r.read().decode("utf-8", "ignore")
    m = re.search(r"channel_id=(UC[\w-]{22})", page)
    return m.group(1) if m else None


def youtube_videos(channel_id):
    """채널의 최근 15개. RSS라 키도 쿼터도 필요 없다."""
    url = ("https://www.youtube.com/feeds/videos.xml?channel_id="
           + urllib.parse.quote(channel_id))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=25) as r:
            xml = r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[유튜브 실패] {channel_id}: {e}", file=sys.stderr)
        return []
    out = []
    for entry in re.findall(r"<entry>.*?</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", entry)
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        when = re.search(r"<published>(.*?)</published>", entry)
        if not vid:
            continue
        out.append({"id": "yt:" + vid.group(1),
                    "제목": (title.group(1) if title else "").strip(),
                    "url": f"https://youtu.be/{vid.group(1)}",
                    "날짜": (when.group(1)[:10] if when else None)})
    return out


def tmdb_videos(media_type, tid):
    out = []
    for lang in ("ko-KR", "en-US"):
        try:
            d = tmdb._get(f"/{media_type}/{tid}/videos", language=lang)
        except Exception:
            continue
        for v in d.get("results") or []:
            if v.get("site") != "YouTube":
                continue
            out.append({"id": "tmdb:" + v["id"],
                        "제목": f"{v.get('type', '영상')} · {v.get('name', '')}".strip(),
                        "url": f"https://youtu.be/{v['key']}",
                        "날짜": (v.get("published_at") or "")[:10] or None})
    return out


def steam_videos(appid):
    url = f"{STEAM_APP}?" + urllib.parse.urlencode(
        {"appids": appid, "l": "koreana", "cc": "kr"})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return []
    entry = d.get(str(appid)) or {}
    if not entry.get("success"):
        return []
    return [{"id": f"steam:{m['id']}", "제목": m.get("name", ""),
             "url": f"https://store.steampowered.com/app/{appid}/", "날짜": None}
            for m in (entry.get("data") or {}).get("movies", [])]


def igdb_videos(gids):
    """IGDB는 100건씩 묶어 물어볼 수 있다. 날짜 필드가 없어 id 순서로만 본다."""
    out = {}
    gids = list(gids)
    for i in range(0, len(gids), 100):
        chunk = gids[i:i + 100]
        try:
            rows = igdb.query("games",
                              "fields id,videos.name,videos.video_id; "
                              f"where id = ({','.join(map(str, chunk))}); limit 500;")
        except Exception as e:
            print(f"[IGDB 실패] {e}", file=sys.stderr)
            continue
        for g in rows:
            out[g["id"]] = [
                {"id": f"igdb:{v['id']}", "제목": v.get("name", "영상"),
                 "url": f"https://youtu.be/{v['video_id']}", "날짜": None}
                for v in (g.get("videos") or [])]
    return out


# ─────────────────────────────────────────────
def is_target(row, today):
    """새 영상이 소식이 되는 작품인가."""
    pr = row["properties"]
    stage = sel(pr.get("진행도(게임)")) or sel(pr.get("진행도(영상)"))
    if stage not in WAITING:
        return False
    av = (pr.get("이용 가능일") or {}).get("formula") or {}
    av = (av.get("date") or {}).get("start") if av.get("type") == "date" else None
    if not av:
        return True             # 날짜 미정 = 기대작. 트레일러가 제일 궁금한 부류다
    try:
        return (today - datetime.date.fromisoformat(av[:10])).days <= STALE_DAYS
    except ValueError:
        return True


def add_channel(handle, title):
    """유튜브 채널을 감시 목록에 넣는다. 키워드는 제목으로 거를 때 쓴다."""
    cid = youtube_channel_id(handle)
    if not cid:
        print(f"채널 id를 못 찾았습니다: {handle}", file=sys.stderr)
        return 1
    watch = load_json(WATCH_FILE, [])
    for w in watch:
        if w["채널"] == cid:
            print(f"이미 등록됨: {w['작품']} ({cid})")
            return 0
    watch.append({"작품": title, "채널": cid, "핸들": handle, "키워드": []})
    save_json(WATCH_FILE, watch)
    print(f"등록 — {title} · {handle} → {cid}")
    print(f"제목으로 더 좁히려면 {WATCH_FILE}의 '키워드'에 단어를 넣으세요")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="알림·기록 없이 결과만")
    ap.add_argument("--only", help="제목에 이 문자열이 든 작품만")
    ap.add_argument("--add-channel", nargs=2, metavar=("핸들", "작품"),
                    help="유튜브 채널 감시 등록 (@handle 또는 UC...)")
    a = ap.parse_args()

    if a.add_channel:
        sys.exit(add_channel(*a.add_channel))

    today = datetime.date.today()
    state = load_json(STATE_FILE, {})
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    # ── 대상 추리기
    rows, need_igdb = [], {}
    for p in query_all(ids["work_db"]):
        title = txt(p["properties"].get("제목"))
        if a.only and a.only not in title:
            continue
        if not is_target(p, today):
            continue
        rows.append((p, title))
    print(f"영상 확인 대상 {len(rows)}건")

    found = {}          # key -> (표시이름, [영상])
    for p, title in rows:
        ext = txt(p["properties"].get("외부ID"))
        vids = []
        m = re.search(r"tmdb:(movie|tv):(\d+)", ext)
        if m:
            vids += tmdb_videos(m.group(1), m.group(2))
        m = re.search(r"steam:(\d+)", ext)
        if m:
            vids += steam_videos(m.group(1))
        g = re.search(r"igdb:(\d+)", ext)
        if g:
            need_igdb.setdefault(int(g.group(1)), []).append(p["id"])
        if vids:
            found[p["id"]] = (title, vids)

    if need_igdb:
        for gid, vids in igdb_videos(need_igdb).items():
            for pid in need_igdb[gid]:
                cur = found.get(pid)
                found[pid] = (cur[0] if cur else pid,
                              (cur[1] if cur else []) + vids)

    page_of = {p["id"]: p for p, _ in rows}
    name_of = {p["id"]: t for p, t in rows}
    for pid in list(found):
        found[pid] = (name_of.get(pid, found[pid][0]), found[pid][1])

    # ── 유튜브 채널 감시. 작품 DB에 없는 것도 볼 수 있는 유일한 경로다
    by_title = {t: p for p, t in rows}
    for w in load_json(WATCH_FILE, []):
        vids = youtube_videos(w["채널"])
        words = [k.lower() for k in w.get("키워드") or []]
        if words:
            vids = [v for v in vids
                    if any(k in v["제목"].lower() for k in words)]
        if vids:
            key = "yt:" + w["채널"]
            cur = found.get(key)
            found[key] = (w["작품"], (cur[1] if cur else []) + vids)
            # 같은 이름의 행이 DB에 있으면 이어둔다. 소개 한 줄이 붙고
            # 레퍼런스에 최신 영상이 남는다 (채널 키는 페이지 id가 아니라서
            # 이걸 안 하면 트레일러만 덩그러니 뜬다)
            if w["작품"] in by_title:
                page_of[key] = by_title[w["작품"]]

    # ── 처음 본 것과 새로 생긴 것을 가른다
    alerts, first = [], 0
    for key, (title, vids) in found.items():
        seen = state.get(key, {}).get("본것")
        ids_now = [v["id"] for v in vids]
        if seen is None:
            # 첫 조회는 기준값만. 여기서 알리면 밀린 트레일러 수백 건이 터진다
            first += 1
        else:
            fresh = [v for v in vids if v["id"] not in set(seen)]
            if fresh:
                fresh.sort(key=lambda v: v.get("날짜") or "", reverse=True)
                alerts.append((key, title, fresh))
        if not a.dry:
            # 덮어쓰면 안 된다. 소스가 잠깐 적게 주는 날(장애·지역 제한)에
            # 목록이 줄고, 회복되면 사라졌던 영상이 전부 '새 영상'으로 울린다.
            # 합집합으로 두고 상한만 건다.
            merged = sorted(set(seen or []) | set(ids_now))
            state[key] = {"본것": merged[-SEEN_MAX:], "확인": today.isoformat()}

    print(f"기준값만 잡음 {first}건 · 새 영상 있는 작품 {len(alerts)}건")

    lines_by_work = []
    for key, title, fresh in alerts[:MAX_ALERT]:
        row = page_of.get(key)
        lines = []
        if row:
            snap = {"종류": sel(row["properties"].get("종류")),
                    "소개": txt(row["properties"].get("소개"))}
            intro = describe.intro_line(snap)
            if intro:
                lines.append(intro)
        for v in fresh[:3]:
            when = f"{v['날짜']} · " if v.get("날짜") else ""
            lines.append(f"{when}{v['제목'][:70]}")
            lines.append(f"  {v['url']}")
        lines.append("→ 공개 대기 중인 작품의 새 영상입니다")
        lines_by_work.append((f"[영상] {title}", lines))
        print(f"  {title[:24]:24} 새 영상 {len(fresh)}건 — {fresh[0]['제목'][:50]}")

        # 레퍼런스에 최신 영상 주소를 남겨둔다. 노션에서 바로 눌러 볼 수 있다
        if row and not a.dry:
            patch_page(row["id"], {"레퍼런스": {"url": fresh[0]["url"]}})
            time.sleep(0.34)

    if not a.dry:
        save_json(STATE_FILE, state)

    if alerts and not a.dry:
        notify.send_card(
            f"새 영상 {len(alerts)}건",
            summary=["[영상] " + ", ".join(t for _, t, _ in alerts)],
            details=lines_by_work, kinds=["영상"], count=len(alerts))
        if not notify.spooling():
            print("알림 카드 1장 발송 완료")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""시리즈 방영 상태를 TMDB에서 읽어 노션 작품 DB에 반영한다.

목적은 하나다. **완결되면 몰아보려고 대기 중인 작품을 손으로 확인하지 않는 것.**
나무위키 방영표를 매주 열어보는 대신, 완결된 순간에 알림이 오게 한다.

채우는 속성: 방영상태 / 방영진행 / 완결일
사람이 하는 건 **날짜정밀도를 `완결대기`로 두는 것** 하나뿐이다.

TMDB에 미래 회차 날짜가 없으면 완결 예정일이 '남은 화수 × 7일' 추정이 된다.
애니메이션이면 그 자리를 AniList 방영표로 메운다 (회차별 날짜가 다 있다).

판정은 **행이 가리키는 시즌 기준**이다. 노션 행은 시즌 단위("만달로리안 시즌2")인데
TMDB의 외부ID는 시리즈 단위라, 최신 시즌만 보면 시즌1 행이 시즌3 방영중 때문에
"아직 안 끝남"으로 보인다. 그래서 제목의 시즌 표기 → 출시일과 맞는 시즌 →
최신 시즌 순으로 대상 시즌을 정하고, 그 시즌의 화별 방영일을 받아 판정한다.

    남은 방영 예정 화 있음            → 방영중   (+ 주간공개면 완결예정일 추정)
    전 화 방영 끝 + 마지막 시즌 + Ended → 완결
    TMDB Canceled                    → 취소
    전 화 방영 끝 (다음 시즌 있음)     → 시즌완결
    남았는데 다음 날짜가 없음          → 휴방
    방영일이 하나도 없음               → 방영예정

사용법:
    python sync_series.py --dry    # 미리보기
    python sync_series.py
"""
import argparse
import datetime
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

import config  # noqa: F401  (.env 로드)
from adapters import anilist, tmdb
from config import API, headers

IDS_FILE = "db_ids.json"
WEEK = 7
# 이보다 오래된 완결일은 캘린더에 올리지 않는다 (make_ics.py의 6개월과 맞춘 값)
RECENT = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()


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
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def sel(prop):
    return prop["select"]["name"] if prop and prop["select"] else None


def plus_days(date_str, days):
    d = datetime.date.fromisoformat(date_str[:10]) + datetime.timedelta(days=days)
    return d.isoformat()


SEASON_RE = re.compile(r"(?:시즌|season)\s*([0-9]+)|([0-9]+)기", re.I)


def target_season(d, title, start_date):
    """이 노션 행이 가리키는 시즌 번호. (번호, 근거)"""
    nums = sorted(s["season_number"] for s in (d.get("seasons") or [])
                  if (s.get("season_number") or 0) > 0)
    if not nums:
        return 1, "기본"

    m = SEASON_RE.search(title or "")
    if m:
        n = int(m.group(1) or m.group(2))
        if n in nums:
            return n, "제목"

    if start_date:
        best = None
        for s in d.get("seasons") or []:
            if (s.get("season_number") or 0) <= 0 or not s.get("air_date"):
                continue
            try:
                gap = abs((datetime.date.fromisoformat(s["air_date"][:10])
                           - datetime.date.fromisoformat(start_date[:10])).days)
            except ValueError:
                continue
            if gap <= 31 and (best is None or gap < best[0]):
                best = (gap, s["season_number"])
        if best:
            return best[1], "출시일"

    # 시즌 표기가 없으면 최신 시즌으로 본다 (시즌1만 있는 작품이 대부분)
    return nums[-1], "최신"


def judge(d, snum, eps):
    """snum 시즌의 화별 방영일로 판정.

    반환: (방영상태, 방영진행, 완결일, 확정여부)
    완결일은 끝난 작품이면 실제 마지막 화 날짜,
    방영 중이면 마지막 화 예정일(또는 주간 공개 추정치)이다.
    """
    today = datetime.date.today().isoformat()
    nums = sorted(s["season_number"] for s in (d.get("seasons") or [])
                  if (s.get("season_number") or 0) > 0)
    multi = len(nums) > 1
    head = [f"시즌{snum}"] if multi else []
    status = d.get("status") or ""

    total = len(eps)
    dated = [e for e in eps if e.get("air_date")]
    aired = [e for e in dated if e["air_date"] <= today]
    todo = [e for e in dated if e["air_date"] > today]

    if not dated:
        first = d.get("first_air_date")
        tail = f"총 {total}화 · " if total else ""
        return ("방영예정",
                " · ".join(head + [tail + (f"첫 방영 {first}" if first
                                           else "방영일 미정")]),
                None, False)

    if todo:
        nxt = min(todo, key=lambda e: e["air_date"])
        bits = head + [f"{len(aired)}/{total}화", f"다음 {nxt['air_date']}"]
        # 마지막 화 날짜를 이미 알면 그게 곧 완결 예정일이다.
        last_dated = max(dated, key=lambda e: e["air_date"])
        finale, exact = None, False
        if last_dated.get("episode_number") == total:
            finale, exact = last_dated["air_date"], True
            bits.append(f"완결예정 {finale}")
        elif total > nxt["episode_number"]:
            # 주간 공개로 보고 남은 화수만큼 밀어 어림잡는다.
            # 특별편·결방으로 밀리는 추정치다. 캘린더에는 (추정)으로 나간다.
            left = total - nxt["episode_number"]
            finale = plus_days(nxt["air_date"], left * WEEK)
            bits.append(f"완결예정 ~{finale}")
        return "방영중", " · ".join(bits), finale, exact

    done = len(aired) >= total > 0
    ended_date = max(e["air_date"] for e in aired) if aired else None

    if status == "Canceled":
        state = "취소"
    elif done and snum == nums[-1] and status in ("Ended", "Canceled"):
        state = "완결"
    elif done:
        state = "시즌완결"
    else:
        state = "휴방"          # 남은 화가 있는데 다음 날짜가 안 잡힌 상태

    bits = head + [f"{len(aired)}/{total}화"]
    if ended_date:
        bits.append(f"{'마지막 방영' if state == '휴방' else '종영'} {ended_date}")
    # 휴방은 아직 끝난 게 아니므로 완결일을 남기지 않는다
    done_date = ended_date if state != "휴방" else None
    return state, " · ".join(bits), done_date, bool(done_date)


def finale_rows(sdb):
    """일정 DB의 '최종화' 행을 작품 페이지 id로 색인한다."""
    idx = {}
    for r in query_all(sdb, {"property": "종류", "select": {"equals": "최종화"}}):
        for rel in r["properties"]["작품"]["relation"]:
            idx[rel["id"]] = r
    return idx


def archive_finale(row, dry):
    """대기 표시를 지운 작품의 최종화 일정을 걷어낸다.

    이름 규칙이 맞는 것만 건드린다. 손으로 만든 일정을 지우면 안 된다.
    """
    name = "".join(x["plain_text"] for x in row["properties"]["이름"]["title"])
    if not (name.endswith("최종화") or name.endswith("최종화 (추정)")):
        return None
    if not dry:
        req = urllib.request.Request(f"{API}/pages/{row['id']}",
                                     data=json.dumps({"archived": True}).encode(),
                                     headers=headers(), method="PATCH")
        urllib.request.urlopen(req).read()
        time.sleep(0.34)
    return f"일정 정리 {name}"


def upsert_finale(sdb, idx, work_id, title, date, exact, dry):
    """완결(예정)일을 일정 DB의 '최종화' 행으로 만들어 캘린더에 띄운다.

    사용자가 예전에 캘린더에 손으로 적던 '○○ 마지막화'를 대신하는 자리다.
    추정치일 때는 날짜정밀도를 '월'로 둬서 확정 알림이 뜨지 않게 한다.
    """
    # 추정치는 제목에 티를 낸다. 캘린더에서 확정 일정처럼 보이면 곤란하다.
    name = f"{title} 최종화" + ("" if exact else " (추정)")
    props = {
        "이름": {"title": [{"type": "text", "text": {"content": name}}]},
        "종류": {"select": {"name": "최종화"}},
        "날짜": {"date": {"start": date}},
        "날짜정밀도": {"select": {"name": "확정" if exact else "월"}},
        "캘린더노출": {"checkbox": True},
        "작품": {"relation": [{"id": work_id}]},
    }
    row = idx.get(work_id)
    if row:
        cur = (row["properties"]["날짜"]["date"] or {}).get("start")
        cur_p = row["properties"]["날짜정밀도"]["select"]
        if cur == date and (cur_p or {}).get("name") == ("확정" if exact else "월"):
            return None
        if not dry:
            patch_page(row["id"], props)
            time.sleep(0.34)
        return f"일정 갱신 {name} {cur} → {date}"
    if not dry:
        req = urllib.request.Request(
            f"{API}/pages",
            data=json.dumps({"parent": {"database_id": sdb},
                             "properties": props}).encode(),
            headers=headers(), method="POST")
        urllib.request.urlopen(req).read()
        time.sleep(0.34)
    return f"일정 추가 {name} {date}{'' if exact else ' (추정)'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="노션에 쓰지 않고 결과만 출력")
    ap.add_argument("--only", help="제목에 이 문자열이 든 작품만 (테스트용)")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    rows = query_all(ids["work_db"],
                     {"property": "종류", "select": {"equals": "시리즈"}})
    fin_idx = finale_rows(ids["schedule_db"])
    print(f"시리즈 {len(rows)}행 · 기존 최종화 일정 {len(fin_idx)}건")

    stat = {"갱신": 0, "그대로": 0, "일정": 0, "ID없음": [], "실패": []}
    for p in rows:
        pr = p["properties"]
        title = txt(pr["제목"])
        if a.only and a.only not in title:
            continue
        ext = txt(pr.get("외부ID", {"type": "rich_text", "rich_text": []}))
        if not ext.startswith("tmdb:tv:"):
            stat["ID없음"].append(title)
            continue
        start = (pr.get("출시·개봉일") or {}).get("date")
        start = start["start"] if start else None
        try:
            tv_id = ext.split(":")[2]
            d = tmdb._get(f"/tv/{tv_id}", language="ko-KR")
            snum, why = target_season(d, title, start)
            eps = (tmdb._get(f"/tv/{tv_id}/season/{snum}",
                             language="ko-KR").get("episodes") or [])
        except Exception as e:                      # 한 편이 죽어도 나머지는 돈다
            stat["실패"].append(f"{title} ({e})")
            continue

        state, prog, finale, exact = judge(d, snum, eps)

        # TMDB가 마지막 화 날짜를 안 주면 애니는 AniList 방영표로 확정한다
        if state == "방영중" and not exact and pr["애니메이션"]["checkbox"]:
            orig = txt(pr.get("원제", {"type": "rich_text", "rich_text": []}))
            got = anilist.finale(orig or title, len(eps))
            if got:
                finale, exact = got, True
                prog = prog.split(" · 완결예정")[0] + f" · 완결예정 {got}"
        cur_state = sel(pr.get("방영상태"))
        cur_prog = txt(pr.get("방영진행", {"type": "rich_text", "rich_text": []}))
        cur_end = (pr.get("완결일") or {}).get("date")
        cur_end = cur_end["start"][:10] if cur_end else None

        # 캘린더에 올릴 대상. 완결대기로 찍어둔 것 + 완결일이 아직 지나지 않았거나
        # 얼마 안 지난 것. 시리즈는 시작일이 캘린더에서 빠지므로(make_ics.py)
        # 최종화 일정이 그 작품의 유일한 캘린더 항목이 된다.
        wait = (sel(pr.get("날짜정밀도")) == "완결대기"
                or (finale is not None and finale >= RECENT))

        # 대기 중인 작품은 완결(예정)일을 캘린더에도 올린다
        msg = None
        if wait and finale:
            msg = upsert_finale(ids["schedule_db"], fin_idx, p["id"],
                                title, finale, exact, a.dry)
        elif not wait and p["id"] in fin_idx:
            msg = archive_finale(fin_idx[p["id"]], a.dry)
        if msg:
            print(f"  [캘린더] {msg}")
            stat["일정"] += 1

        if (state, prog, finale) == (cur_state, cur_prog, cur_end):
            stat["그대로"] += 1
            continue

        props = {
            "방영상태": {"select": {"name": state}},
            "방영진행": {"rich_text": [{"type": "text", "text": {"content": prog}}]},
            "완결일": {"date": {"start": finale} if finale else None},
        }
        print(f"  {title:24} {cur_state or '-'} → {state:5} {prog}"
              f"{' ★대기중' if wait else ''}")
        if not a.dry:
            patch_page(p["id"], props)
            time.sleep(0.34)
        stat["갱신"] += 1

    print(f"\n갱신 {stat['갱신']}건 / 변화 없음 {stat['그대로']}건")
    if stat["ID없음"]:
        print(f"tmdb:tv 외부ID 없음 {len(stat['ID없음'])}건: {', '.join(stat['ID없음'][:10])}")
    if stat["실패"]:
        print("조회 실패:", "; ".join(stat["실패"]))
    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()

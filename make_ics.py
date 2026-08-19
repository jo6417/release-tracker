# -*- coding: utf-8 -*-
"""노션 → .ics 캘린더 파일 생성.

날짜정밀도가 '확정'인 작품과 캘린더노출된 일정을 iCalendar로 내보낸다.
GitHub Pages로 올리면 구글·네이버·폰 기본 캘린더에서 구독할 수 있다.

미정 작품은 넣지 않는다. 그게 이 프로젝트의 설계 원칙이다.
(캘린더에 넣을 수 없어서 잊어버리던 문제 → 확정되면 자동으로 등장)

사용법:
    python make_ics.py
"""
import argparse
import io
import json
import time
import urllib.request

BS = chr(92)   # 백슬래시

from config import API, headers

IDS_FILE = "db_ids.json"
OUT = "docs/releases.ics"
# 과거를 다 실으면 이벤트가 677개가 되어 캘린더 앱이 버거워하고
# 기존 일정과 뒤엉킨다. 최근 것과 미래만 내보낸다.
PAST_MONTHS = 6
# 시리즈는 시작일을 캘린더에 넣지 않는다. 몰아보는 사람에게 의미 있는 날짜는
# "언제 다 나오나"뿐이라, 완결일만 일정 DB의 `최종화` 행으로 나간다.
SKIP_WORK_KINDS = {"시리즈"}
SKIP_SCHED_KINDS = {"시즌시작"}
PRODID = "-//release-tracker//jo6417//KO"


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
    return "".join(x["plain_text"] for x in prop[prop["type"]])


def sel(prop):
    return prop["select"]["name"] if prop["select"] else None


def multi(prop):
    return [o["name"] for o in prop["multi_select"]]


def esc(s):
    """iCalendar 텍스트 이스케이프"""
    s = s.replace(BS, BS + BS)
    s = s.replace(';', BS + ';')
    s = s.replace(',', BS + ',')
    s = s.replace(chr(10), BS + 'n')
    return s


def fold(line):
    """75바이트 제한 — UTF-8 경계를 깨지 않고 접는다."""
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return [line]
    out, buf = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(buf) + len(b) > 73:
            out.append(buf.decode("utf-8"))
            buf = b" " + b
        else:
            buf += b
    if buf:
        out.append(buf.decode("utf-8"))
    return out


def day_after(d):
    t = time.strptime(d, "%Y-%m-%d")
    return time.strftime("%Y%m%d", time.localtime(time.mktime(t) + 86400))


def stamp_of(page, fallback):
    """DTSTAMP는 노션의 마지막 수정 시각을 쓴다.

    생성 시각을 쓰면 데이터가 그대로여도 파일이 매번 바뀐다.
    짧은 주기로 돌릴 때 의미 없는 커밋이 하루 수백 개씩 쌓이므로,
    내용이 같으면 파일도 바이트 단위로 같아야 한다.
    """
    t = page.get("last_edited_time") or ""
    if not t:
        return fallback
    return t.replace("-", "").replace(":", "")[:15] + "Z"


def event(uid, start, end, summary, desc, url, stamp):
    lines = ["BEGIN:VEVENT",
             f"UID:{uid}@release-tracker",
             f"DTSTAMP:{stamp}",
             f"DTSTART;VALUE=DATE:{start.replace('-', '')}",
             # 종료일은 하루 뒤 (iCalendar 종일 일정은 끝을 배타적으로 본다)
             f"DTEND;VALUE=DATE:{day_after(end or start)}",
             f"SUMMARY:{esc(summary)}"]
    if desc:
        lines.append(f"DESCRIPTION:{esc(desc)}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VEVENT")
    return lines


ICON = {"게임": "[게임]", "영화": "[영화]", "시리즈": "[시리즈]", "만화": "[만화]"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT, help="출력 파일")
    ap.add_argument("--limit", type=int, default=0, help="이벤트 N개만 (테스트용)")
    a = ap.parse_args()
    out_path = a.out

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    # 노션에 수정시각이 없는 경우에만 쓰는 예비값 (stamp_of 참고)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    cutoff = time.strftime("%Y-%m-%d",
                           time.localtime(time.time() - PAST_MONTHS * 30 * 86400))
    print(f"기준일: {cutoff} 이후만 내보냄")

    body = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
            "CALSCALE:GREGORIAN",
            "X-WR-CALNAME:출시 트래커", "X-WR-TIMEZONE:Asia/Seoul",
            "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    n_work = n_sched = 0

    # 작품 — 정밀도 '확정'인 것만
    for p in query_all(ids["work_db"], {
            "and": [{"property": "날짜정밀도", "select": {"equals": "확정"}},
                    {"property": "출시·개봉일", "date": {"is_not_empty": True}}]}):
        pr = p["properties"]
        d = pr["출시·개봉일"]["date"]
        if (d.get("end") or d["start"]) < cutoff:
            continue
        kind = sel(pr["종류"]) or ""
        if kind in SKIP_WORK_KINDS:
            continue
        title = txt(pr["제목"])
        bits = []
        if multi(pr["플랫폼"]):
            bits.append("/".join(multi(pr["플랫폼"])))
        if multi(pr["공개처"]):
            bits.append("/".join(multi(pr["공개처"])))
        if multi(pr["소유처"]):
            bits.append("보유: " + "/".join(multi(pr["소유처"])))
        body += event(p["id"].replace("-", ""), d["start"], d.get("end"),
                      f"{ICON.get(kind, '')} {title}".strip(),
                      " · ".join(bits), p.get("url"), stamp_of(p, stamp))
        n_work += 1
        if a.limit and n_work >= a.limit:
            break

    # 일정 — 캘린더노출된 부속 사건
    for p in query_all(ids["schedule_db"], {
            "and": [{"property": "캘린더노출", "checkbox": {"equals": True}},
                    {"property": "날짜", "date": {"is_not_empty": True}}]}):
        pr = p["properties"]
        d = pr["날짜"]["date"]
        if (d.get("end") or d["start"]) < cutoff:
            continue
        if (sel(pr["종류"]) or "") in SKIP_SCHED_KINDS:
            continue
        body += event(p["id"].replace("-", ""), d["start"], d.get("end"),
                      f"📌 {txt(pr['이름'])}", sel(pr["종류"]) or "",
                      p.get("url"), stamp_of(p, stamp))
        n_sched += 1

    body.append("END:VCALENDAR")

    folded = []
    for line in body:
        folded += fold(line)
    # newline=""로 열어야 우리가 넣은 CRLF가 CR CR LF로 이중 변환되지 않는다
    with io.open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(folded) + "\r\n")
    print(f"{out_path} — 작품 {n_work}건 + 일정 {n_sched}건 = {n_work + n_sched}개 이벤트")


if __name__ == "__main__":
    main()

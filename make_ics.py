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

from config import API, headers, CALENDAR_VISIBLE

IDS_FILE = "db_ids.json"
OUT = "docs/releases.ics"
# 과거를 다 실으면 이벤트가 677개가 되어 캘린더 앱이 버거워하고
# 기존 일정과 뒤엉킨다. 최근 것과 미래만 내보낸다.
PAST_MONTHS = 6
# 시리즈는 시작일을 캘린더에 넣지 않는다. 몰아보는 사람에게 의미 있는 날짜는
# "언제 다 나오나"뿐이라, 완결일만 일정 DB의 `최종화` 행으로 나간다.
SKIP_WORK_KINDS = {"시리즈"}
SKIP_SCHED_KINDS = {"시즌시작"}
# 대표 사건(정식출시·극장개봉·OTT공개…)은 작품 행에 흡수하는 것이 이 시스템의
# 규칙이다 — 부속 사건(베타·데모·DLC·최종화)만 일정 행으로 남긴다. 이관할 때는
# migrate.py가 그 규칙을 지켰지만, 그 뒤에 손으로(또는 MCP로) 작품 행과 일정
# 행을 둘 다 만들면 아무도 막지 않아서 캘린더에 같은 날 같은 제목이 두 줄로
# 뜬다(2026-09-04 귀무자, 2026-09-16 애니모). 여기서 한 번 더 거른다.
DEDUP_SCHED_KINDS = CALENDAR_VISIBLE
PRODID = "-//release-tracker//jo6417//KO"
TAB = chr(9)


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


def unfold(text):
    """접힌 줄을 되돌린다. 이어지는 줄은 공백이나 탭으로 시작한다(RFC 5545)."""
    out = []
    for line in text.splitlines():
        if line[:1] in (" ", TAB) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _events(lines):
    """UID -> (DTSTAMP 줄, 그 밖의 줄들). VEVENT 블록만 본다."""
    found, block = {}, None
    for line in lines:
        if line == "BEGIN:VEVENT":
            block = []
        elif line == "END:VEVENT" and block is not None:
            uid = next((l for l in block if l.startswith("UID:")), None)
            stamp = next((l for l in block if l.startswith("DTSTAMP:")), None)
            if uid:
                found[uid] = (stamp,
                              tuple(l for l in block
                                    if not l.startswith("DTSTAMP:")))
            block = None
        elif block is not None:
            block.append(line)
    return found


def keep_stamps(lines, path):
    """캘린더에 보이는 값이 그대로인 이벤트는 이전 DTSTAMP를 그대로 둔다.

    `stamp_of`가 주는 노션 수정시각은 캘린더와 상관없는 이유로도 움직인다.
    추적기가 그 행에 `소개`나 `현재가`를 써넣기만 해도 갱신되므로, 출시일이
    그대로인데 파일이 달라져 커밋과 Pages 재배포가 돈다 (2026-08-29 커밋은
    변경분이 DTSTAMP 한 줄뿐이었다).

    DTSTAMP의 뜻이 "이 이벤트 정보가 마지막으로 고쳐진 때"이므로, 보이는 값이
    같으면 옛 값을 두는 쪽이 규격에도 맞다.

    무엇이 바뀌었는지를 따로 표시해 두는 방법도 있지만, 노션에 쓰는 곳이
    여섯 군데(track·sync_prices·sync_gamepass·sync_media·apply_candidates·
    사람)라 한 곳만 빠뜨려도 캘린더가 조용히 멈춘다. 출력물끼리 대보면
    빠뜨릴 곳이 없다.
    """
    try:
        with io.open(path, encoding="utf-8", newline="") as f:
            old = _events(unfold(f.read()))
    except (IOError, OSError):
        return lines               # 첫 실행 — 비교할 것이 없다
    if not old:
        return lines

    out, block, kept = [], None, 0
    for line in lines:
        if line == "BEGIN:VEVENT":
            block = [line]
            continue
        if block is None:
            out.append(line)
            continue
        block.append(line)
        if line != "END:VEVENT":
            continue
        inner = block[1:-1]
        uid = next((l for l in inner if l.startswith("UID:")), None)
        rest = tuple(l for l in inner if not l.startswith("DTSTAMP:"))
        prev = old.get(uid)
        if prev and prev[0] and prev[1] == rest:
            block = [prev[0] if l.startswith("DTSTAMP:") else l for l in block]
            kept += 1
        out += block
        block = None
    if kept:
        print(f"  변경 없는 이벤트 {kept}건은 이전 DTSTAMP를 유지합니다")
    return out


def stamp_of(page, fallback):
    """DTSTAMP의 1차값 — 노션의 마지막 수정 시각.

    생성 시각을 쓰면 데이터가 그대로여도 파일이 매번 바뀌므로 그것보다는 낫다.
    다만 이 값도 캘린더와 무관하게 움직여서, 위 `keep_stamps`가 한 번 더 거른다.
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


# 캘린더에서는 한 줄에 여러 일정이 겹쳐 보이므로 앞 글자만으로 뭔지 알아야 한다.
# `[게임]` 같은 말머리는 자리를 많이 먹어 제목이 잘렸다. 아이콘 한 글자로 바꾼다.
#
# **아이콘은 5개까지만 늘린다.** 종류마다 다른 그림을 주면 20개가 되고, 그러면
# 아무도 외우지 못해서 아이콘이 그냥 앞에 붙은 장식이 된다. 매체 넷 + 완결 하나면
# 격자에서 구별하기에 충분하다. 나머지 정보(예약구매인지 DLC인지)는 일정 이름과
# DESCRIPTION에 이미 글자로 들어 있다.
#
# 고를 때 **주 색깔이 서로 겹치지 않는지**를 본다. 격자에서 14px로 줄면 그림이
# 아니라 색덩어리로 읽히기 때문이다. 🎬·📺·🎮는 셋 다 어두운 회색이라 구별이
# 안 됐고, 그래서 영화를 🍿(빨강), 만화를 📗(초록)로 바꿨다.
#   🎮 회색 · 🍿 빨강 · 📺 갈색 · 📗 초록 · 🏁 흑백
WORK_ICON = {
    "게임": "🎮",
    "영화": "🍿",
    "시리즈": "📺",
    "만화": "📗",
    "도서": "📗",      # `종류`에 아직 없다. 나중에 추가되면 그대로 붙는다
}
# 애니메이션 체크는 아이콘으로 나누지 않는다. 서양 애니·애니 극장판까지 묶는
# 축이라 흔히 쓰는 🍥가 절반은 틀린 그림이 된다.

# 일정도 같은 다섯 글자로 떨어뜨린다. 일정 종류는 곧 매체를 말해준다.
FINALE = "🏁"        # 최종화 — "이제 정주행 가능"이라 유일하게 따로 뺄 값어치가 있다
SCHED_ICON = {
    "발표": "🎮", "예약구매": "🎮", "알파테스트": "🎮", "베타테스트": "🎮",
    "데모": "🎮", "얼리액세스": "🎮", "정식출시": "🎮", "DLC": "🎮",
    "무료배포": "🎮",
    "극장개봉": "🍿", "국내개봉": "🍿",
    "OTT공개": "📺", "시즌시작": "📺", "파트공개": "📺",
    "최종화": FINALE,
    "회차": "📗",
}


def work_icon(kind):
    return WORK_ICON.get(kind, "📌")


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
    n_work = n_sched = n_dup = 0
    # 작품 루프가 실제로 내보낸 (작품 id, 시작일). 일정 루프에서 같은 것을
    # 또 내보내지 않으려고 모은다.
    #
    # "작품 DB에 확정 날짜가 있으면 일정 행을 뺀다"로 하면 안 된다. 작품 루프는
    # 확정 날짜가 있어도 이벤트를 안 내는 경우가 셋 있다 — 컷오프 이전,
    # `종류=시리즈`, `--limit`. 그때 일정 행까지 빼면 그 날짜가 캘린더에서
    # 통째로 사라진다. 실제로 낸 것만 기준으로 삼으면 셋 다 저절로 걸러진다.
    emitted = set()

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
                      f"{work_icon(kind)} {title}".strip(),
                      " · ".join(bits), p.get("url"), stamp_of(p, stamp))
        emitted.add((p["id"], d["start"]))
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
        skind = sel(pr["종류"]) or ""
        if skind in SKIP_SCHED_KINDS:
            continue
        # 작품 행이 이미 같은 날짜로 낸 대표 사건이면 그건 중복이다
        if skind in DEDUP_SCHED_KINDS and any(
                (r["id"], d["start"]) in emitted for r in pr["작품"]["relation"]):
            n_dup += 1
            continue
        body += event(p["id"].replace("-", ""), d["start"], d.get("end"),
                      f"{SCHED_ICON.get(skind, '📌')} {txt(pr['이름'])}", skind,
                      p.get("url"), stamp_of(p, stamp))
        n_sched += 1

    body.append("END:VCALENDAR")

    # 캘린더에 보이는 값이 그대로인 이벤트는 DTSTAMP도 그대로 둔다.
    # 이 한 줄이 없으면 출시일이 안 바뀐 날에도 커밋과 재배포가 돈다.
    body = keep_stamps(body, out_path)

    folded = []
    for line in body:
        folded += fold(line)
    # newline=""로 열어야 우리가 넣은 CRLF가 CR CR LF로 이중 변환되지 않는다
    with io.open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(folded) + "\r\n")
    print(f"{out_path} — 작품 {n_work}건 + 일정 {n_sched}건 = {n_work + n_sched}개 이벤트"
          + (f" (작품과 겹쳐 뺀 일정 {n_dup}건)" if n_dup else ""))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""게임패스 입점·이탈을 감지해 노션에 반영하고 알린다.

성공 기준 3번 - "등록해둔 작품이 내 구독 서비스에 들어오면 그날 알림이 온다".

매일 카탈로그의 상품 ID 목록만 어제와 비교한다. 입점·이탈 판정은 ID 기준이라
제목 매칭이 틀려도 흔들리지 않는다. 이름은 새 ID가 나타난 날에만 물어보고
gamepass_map.json에 쌓는다 (steam_map.json과 같은 구조다).

알림은 세 갈래다.
  - 등록해둔 작품의 이탈 -> 개별로 상세히. 시한이 있어 행동해야 하는 알림이다
  - 등록해둔 작품의 입점 -> 개별로. 사려던 것이 공짜가 된 것이다
  - 등록 안 한 작품의 입점 -> 요약은 목록 한 줄, 상세는 소개까지. 등록할
    만한 게 보이면 사용자가 말한다

이탈 예고는 Xbox Wire 태그 피드에서 온다. 카탈로그 비교로는 내려간 다음 날에야
알 수 있는데, 그때는 이미 늦다. 게임패스는 2주쯤 전에 공식 발표를 한다.

노션의 `소유처`에 '게임패스'를 넣고 뺀다. 사람이 찍은 다른 값(스팀 등)은
건드리지 않는다. 진행도와 달리 이 칸은 바깥 사실이라 소스가 더 정확하다.

사용법:
    python sync_gamepass.py --dry
    python sync_gamepass.py
"""
import argparse
import datetime
import io
import json
import re
import time
import urllib.request

import describe
import notify
from adapters import gamepass
from config import API, headers

IDS_FILE = "db_ids.json"
STATE = "gamepass_state.json"
MAP = "gamepass_map.json"
TAG = "게임패스"
LIST_MAX = 10          # 미등록 입점작을 이름으로 몇 개까지 적을지
DAYONE_DAYS = 7        # 이 안에 나온 게임이면 "출시 첫날 입점"으로 본다
NOTICE_DAYS = 21       # 이탈 예고를 이 기간 안의 것만 알린다


def load(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


def save(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def query_all(dbid):
    out, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        req = urllib.request.Request(f"{API}/databases/{dbid}/query",
                                     data=json.dumps(body).encode(),
                                     headers=headers(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        out += d["results"]
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def patch(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def norm(s):
    """제목 대조용 정규화.

    게임패스 제목에는 상표기호와 꼬리표가 겹겹이 붙는다 -
    '콜 오브 듀티(R): 블랙 옵스 6 - 스탠다드 에디션 (Windows)'.
    꼬리를 한 번만 떼면 괄호가 남아 에디션 표기가 안 걸리므로 더 떨어지지
    않을 때까지 반복해서 벗긴다. 노션 제목은 사람이 적은 것이라 띄어쓰기와
    구두점이 제각각이고, 양쪽에서 같은 것을 걷어내야 만난다.
    """
    s = (s or "").lower()
    s = re.sub(r"[®™℗]", "", s)
    for _ in range(4):
        before = s
        s = re.sub(r"\s*\((?:windows|pc|console|콘솔|cloud|클라우드)\)\s*$", "", s)
        s = re.sub(r"[-:–·,]?\s*(일반판|스탠다드 에디션|컴플리트 에디션|"
                   r"디럭스 에디션|얼티밋 에디션|리마스터드 에디션|세대 호환 번들|"
                   r"standard edition|complete edition|deluxe edition|ultimate edition|"
                   r"remastered edition|cross-gen bundle|"
                   r"game of the year edition|goty edition)\s*$", "", s)
        if s == before:
            break
    s = re.sub(r"\b(the|a|an)\b", "", s)      # 'The Wild Hunt'와 'Wild Hunt'
    s = re.sub(r"[^0-9a-z가-힣]", "", s)
    return s


def find(index, gmap, pid):
    """상품 ID로 작품을 찾는다. 한국어 제목이 먼저, 안 되면 영문."""
    e = gmap.get(pid) or {}
    if isinstance(e, str):                      # 옛 형식(문자열)도 읽는다
        e = {"ko": e, "en": ""}
    for key in (e.get("ko"), e.get("en")):
        row = index.get(norm(key)) if key else None
        if row:
            return row
    return None


def name_of(gmap, pid):
    e = gmap.get(pid) or {}
    if isinstance(e, str):
        return e
    return e.get("ko") or e.get("en") or ""


def loose(index, name):
    """정확히 안 맞을 때 한 번 더. 한쪽이 다른 쪽에 통째로 들어 있고 후보가
    하나뿐일 때만 인정한다. 이탈 예고의 제목이 카탈로그와 에디션 표기가 달라
    (Remastered Edition vs Complete Edition) 정확 대조로는 못 잡는다.
    """
    k = norm(name)
    if len(k) < 8:
        return None
    hit = [row for key, row in index.items()
           if len(key) >= 8 and (key in k or k in key)]
    uniq = {row["id"]: row for row in hit}
    return list(uniq.values())[0] if len(uniq) == 1 else None


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def parse_when(text, today):
    """'August 31' -> date. 해가 안 적혀 있어 지난 날짜면 내년으로 본다."""
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})", text.strip())
    if not m or m.group(1).lower() not in MONTHS:
        return None
    month, day = MONTHS[m.group(1).lower()], int(m.group(2))
    try:
        d = datetime.date(today.year, month, day)
    except ValueError:
        return None
    if (d - today).days < -30:
        d = datetime.date(today.year + 1, month, day)
    return d


def read_works(pages):
    """제목·원제로 찾을 수 있게 색인한다."""
    index, rows = {}, {}
    for p in pages:
        pr = p["properties"]
        title = "".join(x["plain_text"] for x in pr["제목"]["title"])
        if not title:
            continue
        kind = pr["종류"]["select"]
        if kind and kind["name"] != "게임":
            continue
        stage = pr.get("진행도(게임)", {}).get("select")
        row = {
            "id": p["id"], "제목": title,
            "진행도": stage["name"] if stage else None,
            "소유처": [x["name"] for x in pr["소유처"]["multi_select"]],
        }
        rows[p["id"]] = row
        for key in (title, "".join(x["plain_text"]
                                   for x in pr.get("원제", {}).get("rich_text", []))):
            k = norm(key)
            if k and k not in index:
                index[k] = row
    return index, rows


def set_tag(row, on, dry):
    """소유처에서 게임패스만 넣거나 뺀다. 나머지 값은 그대로 둔다."""
    now = list(row["소유처"])
    if on and TAG not in now:
        now.append(TAG)
    elif not on and TAG in now:
        now.remove(TAG)
    else:
        return False
    if not dry:
        patch(row["id"], {"소유처": {"multi_select": [{"name": x} for x in now]}})
        time.sleep(0.34)
    row["소유처"] = now
    return True


SUFFIX = re.compile(r"[,\s]+(pte\.?\s*ltd|co\.?\s*,?\s*ltd|ltd|llc|inc|"
                    r"corp|corporation|gmbh|plc)\.?\s*$", re.I)


def studio(name):
    """개발사 표기를 사람이 읽는 이름으로. 카탈로그는 법인명을 그대로 준다
    ("NETEASE INTERACTIVE ENTERTAINMENT PTE.LTD"). 꼬리를 떼고, 전부 대문자면
    낱말 첫 글자만 남긴다 - 알림에서 혼자 소리지르는 줄이 되면 안 된다.
    """
    n = SUFFIX.sub("", name.strip()).strip(" ,.")
    if n.isupper() and len(n) > 3:
        n = n.title()
    return n or name.strip()


def detail_new(meta, today):
    """미등록 입점작 한 건의 상세.

    작품 DB에 없어서 제목만으로는 뭔지 안 떠오르는 게임들이다. 소개 한 줄이
    이 알림의 전부라고 봐도 된다 - 없으면 "이게 뭐지"로 끝나고 넘어가게 된다.
    """
    dev = studio(meta.get("개발사") or "")
    lines = [f"게임 · {dev}" if dev else "게임"]

    intro = describe.intro_line({"소개": meta.get("소개") or ""})
    if intro:
        lines.append(intro)

    out = (meta.get("출시일") or "").strip()
    if out:
        try:
            days = (today - datetime.date.fromisoformat(out)).days
        except ValueError:
            days = None
        line = f"{out} 출시"
        if days is not None and -DAYONE_DAYS <= days <= DAYONE_DAYS:
            line += " · 출시 첫날부터 게임패스"
        lines.append(line)

    lines.append("작품 DB에 없는 게임입니다. 등록해두면 이후 변경도 추적합니다")
    return lines


def detail_leave(row, when, today):
    left = (when - today).days
    lines = [f"게임 · {row['진행도'] or '미확인'} · 게임패스",
             f"{when.strftime('%m월 %d일')}에 내려간다 · 남은 {left}일"]
    if row["진행도"] in ("졸업", "중단"):
        lines.append("이미 한 작품이다. 내려가도 상관없다")
    elif set(row["소유처"]) - {TAG}:
        lines.append(f"{', '.join(sorted(set(row['소유처']) - {TAG}))}에도 있다. 내려가도 할 수 있다")
    else:
        lines.append("지금 하거나, 놓치면 사야 한다")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    today = datetime.date.today()
    ids = load(IDS_FILE, {})
    state = load(STATE, {})
    gmap = load(MAP, {})

    now_ids = gamepass.catalog_ids()
    old_ids = set(state.get("ids") or [])
    print(f"카탈로그 {len(now_ids)}건 (어제 {len(old_ids)}건)")

    # 새로 보이는 ID의 이름만 물어본다. 나머지는 이미 map에 있다.
    unknown = [i for i in now_ids if i not in gmap]
    if unknown:
        ko = gamepass.titles(unknown)
        en = gamepass.titles(unknown, lang="en-us")
        for i in unknown:
            if i in ko or i in en:
                gmap[i] = {"ko": ko.get(i, ""), "en": en.get(i, "")}
        print(f"  이름 조회 {len(ko)}건 (영문 {len(en)}건)")

    index, rows = read_works(query_all(ids["work_db"]))
    first = not old_ids

    entered = sorted(now_ids - old_ids) if not first else []
    left = sorted(old_ids - now_ids) if not first else []

    # 카탈로그에 있는 것 전부를 소유처에 반영한다. 첫 실행이면 여기서 다 채워진다.
    matched = {}
    for i in now_ids:
        row = find(index, gmap, i)
        if row:
            matched[row["id"]] = row
    tagged = 0
    for row in matched.values():
        if set_tag(row, True, a.dry):
            tagged += 1
    for row in rows.values():
        if row["id"] not in matched and TAG in row["소유처"]:
            if set_tag(row, False, a.dry):
                tagged += 1
    print(f"  작품 DB 매칭 {len(matched)}건 / 소유처 갱신 {tagged}건")

    summary, details = [], []

    # 1) 이탈 예고 - 공식 발표. 카탈로그 비교보다 2주쯤 빠르다
    sent = state.setdefault("예고", {})
    for name, when_text in gamepass.leaving():
        when = parse_when(when_text, today)
        row = index.get(norm(name)) or loose(index, name)
        if not row or not when:
            continue
        if not 0 <= (when - today).days <= NOTICE_DAYS:
            continue
        key = f"{row['제목']}|{when.isoformat()}"
        if sent.get(key):
            continue
        sent[key] = True
        summary.append(f"[게임패스 이탈] {row['제목']} ({when.strftime('%m/%d')})")
        details.append((f"[게임패스 이탈] {row['제목']}", detail_leave(row, when, today)))

    # 2) 등록해둔 작품의 입점
    known_in = [r for r in (find(index, gmap, i) for i in entered) if r]
    for row in known_in:
        summary.append(f"[게임패스 입점] {row['제목']}")
        details.append((f"[게임패스 입점] {row['제목']}", [
            f"게임 · {row['진행도'] or '미확인'}",
            "오늘부터 게임패스로 이용할 수 있다",
            "사지 않아도 된다" if row["진행도"] == "구매 대기" else "구독 중이라 바로 할 수 있다",
        ]))

    # 3) 등록 안 한 작품의 입점 - 목록 한 줄. 등록할 만한 게 보이면 사용자가 말한다
    new_ids = [i for i in entered
               if i in gmap and name_of(gmap, i) and not find(index, gmap, i)]
    if new_ids:
        names = [name_of(gmap, i) for i in new_ids]
        head = ", ".join(names[:LIST_MAX])
        if len(names) > LIST_MAX:
            head += f" 외 {len(names) - LIST_MAX}건"
        summary.append(f"[게임패스 신규] {head}")

        # 요약의 이름만으로는 뭔지 알 수 없어 상세를 같이 만든다. 여기 쓰는
        # 소개·개발사는 작품 DB가 아니라 카탈로그에서 바로 온다.
        try:
            meta = gamepass.info(new_ids[:LIST_MAX])
        except Exception as e:
            # 상세를 못 받았다고 이탈 알림까지 죽일 수는 없다. 목록만 나간다
            meta = {}
            print(f"  [경고] 신규작 상세를 못 받았습니다 - 목록만 알립니다: {e}")
        for i in new_ids[:LIST_MAX]:
            details.append((f"[게임패스 신규] {name_of(gmap, i)}",
                            detail_new(meta.get(i) or {}, today)))

    # 4) 예고 없이 이미 내려간 것 - 사후 통보지만 소유처를 되돌려야 한다
    for i in left:
        row = find(index, gmap, i)
        if row:
            summary.append(f"[게임패스 이탈 완료] {row['제목']}")

    for s in summary:
        print("  " + s)
    if first:
        print("  첫 실행 - 입점·이탈 비교는 건너뛴다 (이탈 예고는 그대로 나간다)")

    state["직전건수"] = len(old_ids) or len(now_ids)
    state["ids"] = sorted(now_ids)
    state["갱신"] = today.isoformat()
    if not a.dry:
        save(STATE, state)
        save(MAP, gmap)
        if summary:
            notify.send_card(f"게임패스 변경 {len(summary)}건", summary=summary,
                             details=details, kinds=["게임패스"], count=len(summary))


if __name__ == "__main__":
    main()

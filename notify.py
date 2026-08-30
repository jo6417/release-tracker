# -*- coding: utf-8 -*-
"""알림 발송 — 노션 알림 DB에 카드 한 장.

알림 한 건 = DB 페이지 한 장이다. 예전에는 페이지 하나에 문단을 계속 쌓았는데,
쌓일수록 어제 것과 오늘 것이 뒤엉켜서 읽을 수가 없었다. 카드로 나누면 갤러리·
보드 뷰에서 날짜·종류로 걸러 볼 수 있고, 읽은 건 `확인함`으로 지울 수 있다.

카드 제목은 늘 같은 꼴이다 — `2026-08-29 (토) 09시 · 오늘의 브리핑`.
날짜·시각만 바뀌고 뒷문장은 고정이라, 목록을 훑을 때 눈이 날짜에 먼저 간다.
무엇이 들어 있는지는 `종류` 태그와 `건수`가 말한다. 본문은 항상 같은 순서다:
    멘션 한 줄(푸시용) → 요약(제목만) → 상세(날짜·할 일)
요약만 보고 넘길 수 있어야 하고, 궁금하면 그 아래에 답이 있어야 한다.

`notify_ids.json`에 `notify_db`가 없으면 예전 방식(페이지 본문에 줄 추가)으로
떨어진다. DB를 만들기 전에도 알림이 끊기지 않게 하기 위한 것이다.

## 스풀 — 하루에 카드 한 장

카드를 만드는 스크립트가 넷이다(`track`·`sync_prices`·`sync_media`·`briefing`,
그리고 월 1회 `heartbeat`). 각자 자기 일을 끝내면서 자기 카드를 보내니
아침에 3장, 저녁에 2장이 따로 왔다. 스크립트끼리는 서로를 몰라서 묶을 자리가
없었다 — 각 스크립트 안에서 N건을 1장으로 묶는 것까지가 한계였다.

`NOTIFY_SPOOL` 환경변수에 파일 경로가 있으면 `send_card()`는 보내지 않고 그
파일에 카드를 적어둔다. 워크플로 마지막에 `python notify.py --flush`가 모아서
한 장으로 보낸다. 환경변수가 없으면(로컬 실행) 예전처럼 그때그때 보낸다.
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "notify_ids.json"
MAX_BLOCKS = 95        # 노션은 한 번에 100 블록까지. 여유를 둔다
WEEKDAY = "월화수목금토일"
SPOOL_ENV = "NOTIFY_SPOOL"

# 합친 카드 안에서 부분이 놓이는 순서. 작을수록 앞이다.
# 저녁은 브리핑이 본편이고, 아침은 "무슨 일이 있었나"(변경)가 먼저다.
# 할인과 영상은 훑어보는 것이라 뒤로 보낸다.
PART_RANK = {"점검": 0, "브리핑": 1, "할인": 8, "영상": 9}
PART_RANK_DEFAULT = 5      # track이 만드는 변경류(신규·상태변경·날짜변경…)

# 합친 카드의 제목은 늘 이 한 문장이다. 예전에는 각 스크립트가 낸 라벨을 이어
# 붙여서 "오늘의 변경 8건, 목표가 도달 1건, 새 예고편 2건"처럼 매일 다른 문장이
# 됐다. 목록에서 훑을 때 정작 필요한 날짜·시각이 그 문장에 묻힌다.
# 무엇이 들어 있는지는 `종류` 태그와 `건수`, 그리고 본문이 말한다.
BRIEFING_LABEL = "오늘의 브리핑"

# 슬롯 이름 -> (제목에 적을 KST 시각, 예약이 걸린 UTC 시각)
SLOTS = {"아침": (9, 0), "저녁": (19, 10)}
# GitHub은 워크플로의 `name:`을 GITHUB_WORKFLOW에 넣어준다. 그래서 워크플로
# 파일을 고치지 않고도 어느 슬롯인지 알 수 있다. `name:`을 바꾸면 여기도 바꿀 것.
WORKFLOW_SLOT = {"매일 추적": "아침", "저녁 브리핑": "저녁"}


def _ids():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _post(path, payload):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(payload).encode(),
                                 headers=headers(), method="POST")
    return json.loads(urllib.request.urlopen(req).read())


def _text(content, bold=False):
    return {"type": "text", "text": {"content": content[:1900]},
            "annotations": {"bold": bold}}


def _para(rich):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}


def _rich(content):
    """줄 안의 주소를 눌러지는 링크로 만든다.

    유튜브 주소가 글자로만 박혀 있으면 폰에서 복사해 붙여넣어야 열린다.
    알림에서 영상 하나 보는 데 그 수고를 들일 사람은 없다.
    """
    out, pos = [], 0
    for m in re.finditer(r"https?://[^ ]+", content):
        if m.start() > pos:
            out.append(_text(content[pos:m.start()]))
        url = m.group(0).rstrip(").,")
        out.append({"type": "text",
                    "text": {"content": url[:1900], "link": {"url": url}},
                    "annotations": {"bold": False}})
        pos = m.start() + len(url)
    if pos < len(content):
        out.append(_text(content[pos:]))
    return out or [_text(content)]


def _bullet(content):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich(content)}}


def _heading(content):
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [_text(content)], "is_toggleable": False}}


def _divider():
    return {"object": "block", "type": "divider", "divider": {}}


def _body(user_id, title, summary, details):
    """카드 본문. 멘션은 첫 줄에 둔다 — 이게 폰 푸시를 띄운다."""
    blocks = [_para([{"type": "mention", "mention": {"user": {"id": user_id}}},
                     _text(" " + title, bold=True)])]
    if summary:
        blocks.append(_heading("요약"))
        blocks += [_bullet(s) for s in summary]
    if details:
        blocks.append(_divider())
        blocks.append(_heading("상세"))
        for headline, lines in details:
            blocks.append(_para([_text(headline, bold=True)]))
            blocks += [_bullet(x) for x in lines if x]
            if len(blocks) > MAX_BLOCKS:
                blocks = blocks[:MAX_BLOCKS]
                blocks.append(_para([_text("… 나머지는 작품 DB에서 확인")]))
                break
    return blocks


def _legacy(user_id, page_id, title, summary, details):
    """알림 DB가 없을 때. 예전처럼 페이지 본문에 줄을 붙인다."""
    lines = [title] + list(summary)
    for headline, dets in details:
        lines.append(headline)
        lines += ["  " + d for d in dets]
    rich = [{"type": "mention", "mention": {"user": {"id": user_id}}},
            _text(" " + "\n".join(lines))]
    req = urllib.request.Request(f"{API}/blocks/{page_id}/children",
                                 data=json.dumps({"children": [_para(rich)]}).encode(),
                                 headers=headers(), method="PATCH")
    urllib.request.urlopen(req).read()
    return True


def card_title(label, date=None, hour=None):
    """'2026-08-29 (토) 09시 · 오늘의 브리핑'.

    날짜가 맨 앞이라 카드 목록이 그대로 시간순으로 읽힌다. 뒤의 시각은 아침 것과
    저녁 것을 가른다. 이 값은 예약 시각이라 고정이다 -- 실행이 밀려 오후에 와도
    아침 카드는 09시로 적힌다. 도착 시각이 아니라 "무엇의 브리핑인지"가 제목에
    필요한 정보이기 때문이다.
    """
    import datetime
    d = datetime.date.fromisoformat(date) if date else datetime.date.today()
    head = f"{d.isoformat()} ({WEEKDAY[d.weekday()]})"
    if hour is not None:
        head = f"{head} {hour:02d}시"
    return f"{head} · {label}" if label else head


def slot(name=None):
    """(제목에 적을 시각, 예약된 UTC 시각). 슬롯을 모르면 None."""
    key = name or WORKFLOW_SLOT.get(os.environ.get("GITHUB_WORKFLOW", ""))
    return SLOTS.get(key or "")


def slot_date(utc_hour, now=None):
    """예약이 걸린 날. 실행이 늦어도 카드에 적히는 날짜가 흔들리지 않는다.

    예약이 밀려 저녁 브리핑이 22:00Z(다음날 07:00 KST)에 돌면 `today()`는
    하루 뒤를 가리킨다. 그 카드는 어제 19시 것이므로 여기서 되돌린다.
    """
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc)
    d = now.date()
    if now.hour < utc_hour:
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def _send_now(label, summary, details, kinds, count, date, hour=None):
    """실제로 노션에 카드 한 장을 만든다. 스풀을 거치지 않는다."""
    ids = _ids()
    summary, details = list(summary), list(details)
    title = card_title(label, date, hour)

    if not ids.get("notify_db"):
        return _legacy(ids["user_id"], ids["notify_page"], title, summary, details)

    props = {
        "제목": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "날짜": {"date": {"start": date}},
        "확인함": {"checkbox": False},
    }
    if kinds:
        props["종류"] = {"multi_select": [{"name": k} for k in dict.fromkeys(kinds)]}
    if count is not None:
        props["건수"] = {"number": count}

    _post("/pages", {"parent": {"database_id": ids["notify_db"]},
                     "icon": {"type": "emoji", "emoji": "🔔"},
                     "properties": props,
                     "children": _body(ids["user_id"], title, summary, details)})
    return True


def send_card(label, summary=(), details=(), kinds=(), count=None, date=None):
    """알림 카드 한 장. `NOTIFY_SPOOL`이 있으면 보내지 않고 모아둔다.

    label   — 카드 제목 뒷부분 ("오늘의 변경 4건"). 앞에는 날짜가 붙는다
    summary — 제목만 짧게 적은 줄들 (["[신규] 랜턴스, 더 펭귄", ...])
    details — [(헤드라인, [상세 줄, ...]), ...]
    kinds   — 카드에 붙일 종류 태그
    """
    import datetime
    date = date or datetime.date.today().isoformat()
    spool = os.environ.get(SPOOL_ENV)
    if not spool:
        return _send_now(label, summary, details, kinds, count, date)

    cards = _read_spool(spool)
    cards.append({"label": label, "summary": list(summary),
                  "details": [list(d) for d in details],
                  "kinds": list(kinds), "count": count, "date": date})
    with io.open(spool, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    print(f"알림을 모아둡니다 ({len(cards)}번째: {label}) — 마지막에 한 장으로 보냅니다")
    return True


def spooling():
    """지금 발송이 스풀로 미뤄지는가. 호출부의 로그 문구를 가르는 데 쓴다."""
    return bool(os.environ.get(SPOOL_ENV))


def pending(path=None):
    """모아둔 카드 수. "이번 실행에서 나갈 소식이 이미 있나"를 묻는 데 쓴다.

    `track.py`가 변경 0건인 아침에 그래도 한 장 보내려면, 앞 단계(가격·트레일러)가
    이미 모아둔 것이 있는지 알아야 한다. 있으면 그쪽이 소식이고, `변경 없음`은
    같은 카드에 붙어 앞뒤가 안 맞는 말이 된다.
    """
    path = path or os.environ.get(SPOOL_ENV)
    return len(_read_spool(path)) if path else 0


def _read_spool(path):
    if not os.path.exists(path):
        return []
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        # 스풀이 깨졌다고 알림 전체를 죽일 수는 없다. 버리고 새로 시작한다
        print(f"[경고] 스풀을 못 읽었습니다 — 새로 시작합니다: {e}")
        return []


def flush(path=None, dry=False, slot_name=None):
    """모아둔 카드를 한 장으로 합쳐 보낸다. 워크플로 마지막 스텝이 부른다."""
    import datetime
    path = path or os.environ.get(SPOOL_ENV)
    if not path:
        print(f"{SPOOL_ENV}가 없습니다 — 모아둔 알림이 없습니다.")
        return False
    cards = _read_spool(path)
    if not cards:
        print("모아둔 알림이 없습니다 — 보내지 않습니다.")
        return False

    cards.sort(key=lambda c: min([PART_RANK.get(k, PART_RANK_DEFAULT)
                                  for k in c["kinds"]] or [PART_RANK_DEFAULT]))
    label = BRIEFING_LABEL
    summary = [s for c in cards for s in c["summary"]]
    details = [(d[0], d[1]) for c in cards for d in c["details"]]
    kinds = list(dict.fromkeys(k for c in cards for k in c["kinds"]))
    counts = [c["count"] for c in cards if c["count"] is not None]
    hours = slot(slot_name)
    if hours:
        hour, utc_hour = hours
        date = slot_date(utc_hour)
    else:
        # 슬롯 밖에서 부른 경우(로컬 손실행 등). 예전처럼 스풀의 날짜를 쓴다.
        hour = None
        date = next((c["date"] for c in cards if c["date"]),
                    datetime.date.today().isoformat())

    print(f"{card_title(label, date, hour)}")
    print(f"  카드 {len(cards)}장을 한 장으로 합칩니다: "
          + " + ".join(c["label"] for c in cards))
    if dry:
        print("--dry 모드: 보내지 않습니다")
        return False

    _send_now(label, summary, details, kinds,
              sum(counts) if counts else None, date, hour)
    # 보내고 나면 비운다. 남겨두면 다음 실행에서 같은 알림이 또 나간다
    os.remove(path)
    print("알림 카드 1장 발송 완료")
    return True


def send(text):
    """한 줄짜리 알림 (헬스체크·하트비트용). 카드 한 장으로 나간다."""
    head, _, rest = text.partition("\n")
    return send_card(head, summary=[l for l in rest.split("\n") if l], kinds=["점검"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="모아둔 알림을 한 장으로 보낸다")
    ap.add_argument("--flush", action="store_true", help="스풀을 합쳐 발송")
    ap.add_argument("--spool", help="스풀 파일 경로 (기본: $NOTIFY_SPOOL)")
    ap.add_argument("--dry", action="store_true", help="보내지 않고 결과만 출력")
    ap.add_argument("--slot", choices=sorted(SLOTS),
                    help="아침/저녁 (기본: GITHUB_WORKFLOW로 자동 판별)")
    a = ap.parse_args()
    if not a.flush:
        ap.print_help()
        sys.exit(1)
    flush(a.spool, a.dry, a.slot)

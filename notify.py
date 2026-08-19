# -*- coding: utf-8 -*-
"""알림 발송 — 노션 알림 DB에 카드 한 장.

알림 한 건 = DB 페이지 한 장이다. 예전에는 페이지 하나에 문단을 계속 쌓았는데,
쌓일수록 어제 것과 오늘 것이 뒤엉켜서 읽을 수가 없었다. 카드로 나누면 갤러리·
보드 뷰에서 날짜·종류로 걸러 볼 수 있고, 읽은 건 `확인함`으로 지울 수 있다.

카드 제목은 날짜로 시작하고(`card_title`), 본문은 항상 같은 순서다:
    멘션 한 줄(푸시용) → 요약(제목만) → 상세(날짜·할 일)
요약만 보고 넘길 수 있어야 하고, 궁금하면 그 아래에 답이 있어야 한다.

`notify_ids.json`에 `notify_db`가 없으면 예전 방식(페이지 본문에 줄 추가)으로
떨어진다. DB를 만들기 전에도 알림이 끊기지 않게 하기 위한 것이다.
"""
import io
import json
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "notify_ids.json"
MAX_BLOCKS = 95        # 노션은 한 번에 100 블록까지. 여유를 둔다
WEEKDAY = "월화수목금토일"


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


def _bullet(content):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_text(content)]}}


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


def card_title(label, date=None):
    """'2026-08-19 (수) · 오늘의 변경 4건'.

    날짜가 맨 앞이라 카드 목록이 그대로 시간순으로 읽힌다. 날짜만 쓰면 하루에
    여러 장(변경·할인·브리핑) 나올 때 카드가 전부 같은 제목이 되므로 뒤에 라벨을
    붙인다.
    """
    import datetime
    d = datetime.date.fromisoformat(date) if date else datetime.date.today()
    head = f"{d.isoformat()} ({WEEKDAY[d.weekday()]})"
    return f"{head} · {label}" if label else head


def send_card(label, summary=(), details=(), kinds=(), count=None, date=None):
    """알림 카드 한 장.

    label   — 카드 제목 뒷부분 ("오늘의 변경 4건"). 앞에는 날짜가 붙는다
    summary — 제목만 짧게 적은 줄들 (["[신규] 랜턴스, 더 펭귄", ...])
    details — [(헤드라인, [상세 줄, ...]), ...]
    kinds   — 카드에 붙일 종류 태그
    """
    import datetime
    ids = _ids()
    summary, details = list(summary), list(details)
    date = date or datetime.date.today().isoformat()
    title = card_title(label, date)

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


def send(text):
    """한 줄짜리 알림 (헬스체크·하트비트용). 카드 한 장으로 나간다."""
    head, _, rest = text.partition("\n")
    return send_card(head, summary=[l for l in rest.split("\n") if l], kinds=["점검"])

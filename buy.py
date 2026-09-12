# -*- coding: utf-8 -*-
"""구매·배송 기록을 채팅 한 줄로 구매DB에 남긴다.

예약구매·해외구매는 결제와 수령 사이가 길어서 "이거 샀나?" 싶을 때 크롬
기록·카드 결제내역을 뒤지게 된다. 구매DB에 상태 하나만 찍어두면 작품 DB의
`주문상태` 롤업으로 바로 보인다 — 찾으러 갈 필요가 없어진다.

`apply_candidates.py`와 같은 자리다. 세션이 채팅에서 받은 결정을 노션에
반영하는 창구이고, 노션에 입력 UI를 따로 두지 않는다.

작품 연결은 **제목 완전일치**로 찾는다. 못 찾아도 구매 기록 자체는
만든다 — 게임과 무관한 주문이 더 많다(설계-구매추적.md).

사용법:
    python buy.py 주문 "amiibo 미넬 골렘 (티어스 오브 더 킹덤)" \\
        --구입처 사줘 --결제액 56212 --주문번호 26090964602 --도착예정 2026-09-17
    python buy.py 수령 미넬골렘
    python buy.py 갱신 미넬골렘 --상태 주문·배송중 --도착예정 2026-09-20
    python buy.py --목록
"""
import argparse
import io
import json
import time
import urllib.error
import urllib.request

from config import API, COURIER_CODE, headers

IDS_FILE = "db_ids.json"
DONE_STATES = {"구매 완료", "보류·취소"}


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
    return _send(req)


def patch_page(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    return _send(req)


def _send(req):
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[에러] {e.code}: {e.read().decode()[:500]}")
        raise


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def find_work_page(ids, title):
    """작품 DB에서 제목이 정확히 일치하는 행 하나. 없으면 None."""
    rows = query_all(ids["work_db"],
                     {"property": "제목", "title": {"equals": title}})
    return rows[0] if rows else None


def find_orders(ids, keyword):
    """구매DB에서 이름에 keyword가 들어간 미완료 주문. 여러 건일 수 있다.

    공백은 무시하고 비교한다 — 채팅으로 "미넬골렘"이라고 치는 경우가
    "미넬 골렘"만큼 흔해서, 띄어쓰기 하나로 못 찾으면 도구를 쓸 이유가 없다.
    """
    key = keyword.replace(" ", "")
    rows = query_all(ids["purchase_db"])
    out = []
    for p in rows:
        pr = p["properties"]
        name = txt(pr["이름"])
        state = (pr["상태"]["select"] or {}).get("name")
        if key in name.replace(" ", "") and state not in DONE_STATES:
            out.append((p, name, state))
    return out


def cmd_order(ids, a):
    props = {
        "이름": {"title": [{"type": "text", "text": {"content": a.제목[:1900]}}]},
        "상태": {"select": {"name": a.상태}},
        "카테고리": {"select": {"name": a.카테고리}},
    }
    if a.구입처:
        props["구입처"] = {"select": {"name": a.구입처}}
    if a.결제액 is not None:
        props["결제액"] = {"number": a.결제액}
    if a.주문번호:
        props["주문번호"] = {"rich_text": [{"type": "text", "text": {"content": a.주문번호}}]}
    if a.도착예정:
        props["도착예정"] = {"date": {"start": a.도착예정}}
    if a.메모:
        props["메모"] = {"rich_text": [{"type": "text", "text": {"content": a.메모}}]}
    if a.링크:
        props["링크"] = {"url": a.링크}

    work = find_work_page(ids, a.제목)
    if work:
        props["작품"] = {"relation": [{"id": work["id"]}]}
        print(f"작품 DB와 연결됨: {a.제목}")
    else:
        print(f"작품 DB에 '{a.제목}' 없음 — 구매 기록만 남깁니다")

    if a.dry:
        print("[dry] 생성 예정:", json.dumps(props, ensure_ascii=False)[:300])
        return
    page = create_page(ids["purchase_db"], props)
    print(f"등록됨: {a.제목} ({a.상태})  {page['id']}")


def cmd_receive(ids, a):
    matches = find_orders(ids, a.제목)
    if not matches:
        print(f"'{a.제목}'이(가) 들어간 미완료 주문이 없습니다")
        return
    if len(matches) > 1:
        print("여러 건이 걸립니다. 더 구체적으로 입력해주세요:")
        for _, name, state in matches:
            print(f"  {name} ({state})")
        return
    page, name, state = matches[0]
    today = time.strftime("%Y-%m-%d")
    props = {"상태": {"select": {"name": "구매 완료"}},
             "구매일": {"date": {"start": today}}}
    if a.dry:
        print(f"[dry] {name}: {state} → 구매 완료, 구매일 {today}")
        return
    patch_page(page["id"], props)
    print(f"수령 처리: {name} → 구매 완료 ({today})")


def cmd_update(ids, a):
    matches = find_orders(ids, a.제목)
    if not matches:
        print(f"'{a.제목}'이(가) 들어간 미완료 주문이 없습니다")
        return
    if len(matches) > 1:
        print("여러 건이 걸립니다. 더 구체적으로 입력해주세요:")
        for _, name, state in matches:
            print(f"  {name} ({state})")
        return
    page, name, state = matches[0]

    props = {}
    if a.상태:
        props["상태"] = {"select": {"name": a.상태}}
    if a.구입처:
        props["구입처"] = {"select": {"name": a.구입처}}
    if a.결제액 is not None:
        props["결제액"] = {"number": a.결제액}
    if a.주문번호:
        props["주문번호"] = {"rich_text": [{"type": "text", "text": {"content": a.주문번호}}]}
    if a.도착예정:
        props["도착예정"] = {"date": {"start": a.도착예정}}
    if a.메모:
        props["메모"] = {"rich_text": [{"type": "text", "text": {"content": a.메모}}]}
    if a.송장번호:
        props["송장번호"] = {"rich_text": [{"type": "text", "text": {"content": a.송장번호}}]}
    if a.택배사:
        code = COURIER_CODE.get(a.택배사, a.택배사)
        props["택배사"] = {"rich_text": [{"type": "text", "text": {"content": code}}]}
        # 송장번호가 생겼다는 건 국내 배송이 시작됐다는 뜻 — 아직 예약주문
        # 그대로면 여기서 한 단계 올려준다. 사람이 이미 다른 값으로
        # 바꿔뒀으면 손대지 않는다.
        if not a.상태 and state == "예약주문":
            props["상태"] = {"select": {"name": "주문·배송 중"}}

    if not props:
        print("바꿀 값이 없습니다 (--상태·--도착예정 등 하나 이상 주세요)")
        return
    if a.dry:
        print(f"[dry] {name}: {list(props)}")
        return
    patch_page(page["id"], props)
    print(f"갱신됨: {name} — {list(props)}")


def cmd_list(ids):
    rows = query_all(ids["purchase_db"])
    open_rows = []
    for p in rows:
        pr = p["properties"]
        state = (pr["상태"]["select"] or {}).get("name")
        if state in DONE_STATES:
            continue
        due = (pr.get("도착예정", {}).get("date") or {}).get("start") or "9999"
        open_rows.append((due, txt(pr["이름"]), state,
                          (pr["구입처"]["select"] or {}).get("name"),
                          pr["결제액"]["number"]))
    open_rows.sort()
    if not open_rows:
        print("진행 중인 주문이 없습니다")
        return
    for due, name, state, store, price in open_rows:
        d = due if due != "9999" else "-"
        p = f"{price:,.0f}원" if price is not None else "-"
        print(f"  {name:34} {state or '-':10} {store or '-':8} 도착예정 {d}  {p}")


def main():
    # --dry는 각 서브커맨드 뒤에도 오게 서브파서마다 따로 붙인다 — argparse는
    # 서브커맨드 토큰 뒤의 옵션을 그 서브파서 것만 인식한다.
    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument("--dry", action="store_true")

    ap = argparse.ArgumentParser(parents=[dry])
    ap.add_argument("--목록", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("주문", parents=[dry])
    p1.add_argument("제목")
    p1.add_argument("--구입처")
    p1.add_argument("--상태", default="예약주문")
    p1.add_argument("--카테고리", default="게임·하드웨어")
    p1.add_argument("--결제액", type=int)
    p1.add_argument("--주문번호")
    p1.add_argument("--도착예정", help="YYYY-MM-DD")
    p1.add_argument("--메모")
    p1.add_argument("--링크")

    p2 = sub.add_parser("수령", parents=[dry])
    p2.add_argument("제목")

    p3 = sub.add_parser("갱신", parents=[dry])
    p3.add_argument("제목")
    p3.add_argument("--상태")
    p3.add_argument("--구입처")
    p3.add_argument("--결제액", type=int)
    p3.add_argument("--주문번호")
    p3.add_argument("--도착예정")
    p3.add_argument("--메모")
    p3.add_argument("--송장번호")
    p3.add_argument("--택배사", help="회사명(CJ대한통운 등) 또는 코드")

    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    if a.목록 or not a.cmd:
        cmd_list(ids)
        return
    if a.cmd == "주문":
        cmd_order(ids, a)
    elif a.cmd == "수령":
        cmd_receive(ids, a)
    elif a.cmd == "갱신":
        cmd_update(ids, a)


if __name__ == "__main__":
    main()

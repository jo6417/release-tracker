# -*- coding: utf-8 -*-
"""알림 DB를 만든다. 한 번만 실행하면 되고, ID는 notify_ids.json에 저장된다.

알림을 페이지 한 장에 쌓지 않고 카드(= DB 행) 한 장씩 남기기 위한 DB다.
갤러리 뷰로 두면 카드가 되고, 날짜·종류로 거르거나 읽은 것을 지울 수 있다.

사용법:
    python create_notify_db.py
"""
import io
import json
import sys
import urllib.request

from config import API, PARENT_PAGE_ID, NOTION_TOKEN, headers

IDS_FILE = "notify_ids.json"

SCHEMA = {
    "제목": {"title": {}},
    "날짜": {"date": {}},
    "종류": {"multi_select": {"options": [
        {"name": "완결", "color": "purple"},
        {"name": "볼차례", "color": "green"},
        {"name": "날짜확정", "color": "blue"},
        {"name": "게임패스", "color": "green"},
        {"name": "할인", "color": "red"},
        {"name": "신규", "color": "yellow"},
        {"name": "날짜변경", "color": "orange"},
        {"name": "진행도", "color": "gray"},
        {"name": "방치", "color": "brown"},
        {"name": "브리핑", "color": "blue"},
        {"name": "취소", "color": "red"},
        {"name": "방영", "color": "pink"},
        {"name": "점검", "color": "default"},
    ]}},
    "건수": {"number": {"format": "number"}},
    "확인함": {"checkbox": {}},
}


def main():
    if not NOTION_TOKEN:
        print("NOTION_TOKEN이 없습니다.", file=sys.stderr)
        sys.exit(1)

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    if ids.get("notify_db"):
        print(f"이미 있습니다: {ids['notify_db']}  (중복 생성을 막기 위해 중단)")
        return

    req = urllib.request.Request(
        f"{API}/databases",
        data=json.dumps({
            "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
            "icon": {"type": "emoji", "emoji": "🔔"},
            "title": [{"type": "text", "text": {"content": "알림"}}],
            "properties": SCHEMA,
        }).encode(),
        headers=headers(), method="POST")
    res = json.loads(urllib.request.urlopen(req).read())

    ids["notify_db"] = res["id"]
    with io.open(IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=1)
    print(f"알림 DB 생성됨: {res['id']}")
    print(f"  {res['url']}")
    print("노션에서 기본 뷰를 갤러리로 바꾸면 카드로 보인다.")


if __name__ == "__main__":
    main()

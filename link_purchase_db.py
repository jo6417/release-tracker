# -*- coding: utf-8 -*-
"""작품 DB와 구매DB를 relation·rollup으로 잇는다 (1회성 설정 스크립트).

relation·rollup은 config.py의 단순 스키마 딕셔너리로 못 만든다 — relation은
상대 DB id가 필요하고, rollup은 그 relation이 먼저 있어야 참조할 수 있어서
두 단계로 나눠 PATCH해야 한다. `sync_schema.py`처럼 매번 돌리는 스크립트가
아니라, 처음 한 번 잇고 나면 다시 쓸 일이 없다.

다시 실행해도 안전하다 — 이미 있으면 만들지 않고 건너뛴다.

작품 DB에 생기는 것:
    구매    (relation → 구매DB)
    주문상태 (rollup, 구매.상태를 그대로 보여줌)

사용법:
    python link_purchase_db.py
"""
import io
import json
import sys
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"
RELATION_NAME = "구매"
ROLLUP_NAME = "주문상태"


def get_db(dbid):
    req = urllib.request.Request(f"{API}/databases/{dbid}", headers=headers())
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def patch_db(dbid, props):
    req = urllib.request.Request(f"{API}/databases/{dbid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[에러] {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        raise


def main():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    work_db, purchase_db = ids["work_db"], ids["purchase_db"]

    work = get_db(work_db)
    if RELATION_NAME in work["properties"]:
        print(f"'{RELATION_NAME}' 이미 있음 — relation 생성 건너뜀")
    else:
        patch_db(work_db, {
            RELATION_NAME: {"relation": {
                "database_id": purchase_db,
                "type": "dual_property",
                "dual_property": {},
            }}
        })
        print(f"relation '{RELATION_NAME}' 생성 (작품 DB → 구매DB)")

    work = get_db(work_db)  # 갱신된 스키마로 다시 읽는다
    if ROLLUP_NAME in work["properties"]:
        print(f"'{ROLLUP_NAME}' 이미 있음 — rollup 생성 건너뜀")
    else:
        patch_db(work_db, {
            ROLLUP_NAME: {"rollup": {
                "relation_property_name": RELATION_NAME,
                "rollup_property_name": "상태",
                "function": "show_original",
            }}
        })
        print(f"rollup '{ROLLUP_NAME}' 생성 (구매.상태를 그대로 표시)")

    print("\n완료. 구매DB 쪽에는 반대편 relation이 자동으로 생겼다 "
          "(이름은 노션이 임의로 붙인다 — 필요하면 노션에서 손으로 '작품'으로 바꿀 것).")


if __name__ == "__main__":
    main()

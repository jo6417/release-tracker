# -*- coding: utf-8 -*-
"""TMDB 매칭 결과를 노션 작품 DB에 반영한다 (영화·시리즈의 외부ID·원제).

`apply_tmdb.py`와 헷갈리기 쉬운데 그쪽은 이관용이다 - migrate_preview.json을
읽어 migrate_enriched.json을 쓰고 노션은 건드리지 않는다. 이관이 끝난 지금
새로 들어온 영화·시리즈에 외부ID를 붙일 경로가 없었고, 그래서 영상물만
계속 손으로 채우고 있었다. 이 스크립트가 게임 쪽 `apply_igdb.py`에 대응한다.

게임 쪽과 같은 규칙을 따른다.
  - 비어 있는 칸만 채운다. 사람이 넣은 값은 건드리지 않는다
  - 확신이 `낮음`이면 건너뛴다. 잘못된 정보를 넣느니 비워두는 게 낫다
  - 종류는 바꾸지 않는다. 이관 때와 달리 지금은 사람이 정한 값이다

사용법:
    python apply_tmdb_ids.py --dry
    python apply_tmdb_ids.py
"""
import argparse
import io
import json
import time
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"
MATCH = "tmdb_match.json"


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


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get("rich_text", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    with io.open(MATCH, encoding="utf-8") as f:
        match = json.load(f)

    changed, skip_low, already = 0, 0, 0
    for p in query_all(ids["work_db"]):
        pr = p["properties"]
        title = "".join(x["plain_text"] for x in pr["제목"]["title"])
        m = match.get(title)
        if not m:
            continue
        if m.get("확신") == "낮음":
            skip_low += 1
            continue

        props = {}
        if not txt(pr["외부ID"]).strip():
            props["외부ID"] = {"rich_text": [{"type": "text", "text": {
                "content": f"tmdb:{m['media_type']}:{m['tmdb_id']}"}}]}
        if "원제" in pr and not txt(pr["원제"]).strip() and m.get("원제"):
            props["원제"] = {"rich_text": [{"type": "text",
                                          "text": {"content": m["원제"][:1900]}}]}
        if not props:
            already += 1
            continue

        changed += 1
        print(f"  {title[:34]:34} {', '.join(props)}")
        if not a.dry:
            patch(p["id"], props)
            time.sleep(0.34)

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {changed}건 / "
          f"이미 채워짐 {already} / 확신 낮아 건너뜀 {skip_low}")


if __name__ == "__main__":
    main()

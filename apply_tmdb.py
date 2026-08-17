# -*- coding: utf-8 -*-
"""TMDB 매칭 결과를 이관 데이터에 반영한다.

migrate_preview.json의 영화/시리즈 작품에 종류·애니메이션·원제·외부ID를 채워
migrate_enriched.json으로 저장한다. migrate.py가 있으면 자동으로 읽는다.

확신이 '낮음'인 건은 건드리지 않는다 (잘못된 정보를 넣느니 비워두는 게 낫다).

사용법:
    python apply_tmdb.py
"""
import io
import json

PREVIEW = "migrate_preview.json"
MATCH = "tmdb_match.json"
OUT = "migrate_enriched.json"


def main():
    with io.open(PREVIEW, encoding="utf-8") as f:
        works = json.load(f)
    with io.open(MATCH, encoding="utf-8") as f:
        match = json.load(f)

    stat = {"종류변경": 0, "애니": 0, "외부ID": 0, "건너뜀": 0}
    for w in works:
        # TMDB 매칭은 영화·시리즈만 대상이다. 제목만으로 찾으면
        # 같은 이름의 게임(월드워Z, 샌드랜드)이 영화 정보를 덮어쓴다.
        if w["종류"] not in ("영화", "시리즈"):
            continue
        m = match.get(w["제목"])
        if not m:
            continue
        if m["확신"] == "낮음":
            stat["건너뜀"] += 1
            continue
        if w["종류"] != m["종류"]:
            w["종류"] = m["종류"]
            stat["종류변경"] += 1
        if m["애니메이션"]:
            w["애니메이션"] = True
            stat["애니"] += 1
        w["원제"] = m["원제"]
        w["외부ID"] = f"tmdb:{m['media_type']}:{m['tmdb_id']}"
        w["레퍼런스"] = (f"https://www.themoviedb.org/"
                      f"{m['media_type']}/{m['tmdb_id']}")
        if m.get("시즌번호"):
            w["시즌번호"] = m["시즌번호"]
        stat["외부ID"] += 1

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(works, f, ensure_ascii=False, indent=1)

    from collections import Counter
    print("반영 결과:", stat)
    print("종류:", dict(Counter(w["종류"] for w in works)))
    print("애니메이션:", sum(1 for w in works if w.get("애니메이션")))
    print(f"{OUT} 저장 — 작품 {len(works)}개")


if __name__ == "__main__":
    main()

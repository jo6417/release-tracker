# -*- coding: utf-8 -*-
"""영화·시리즈 작품을 TMDB와 대조해 종류/애니메이션/원제/개봉일을 채운다.

migrate_preview.json을 읽어 tmdb_match.json을 만든다.
확신이 낮은 건은 tmdb_확인필요.md로 따로 뽑아 사람이 보게 한다.

사용법:
    python match_tmdb.py
"""
import io
import json
import re
import sys

import config  # noqa: F401  (.env 로드)
from adapters import tmdb

# 띄어쓰기 추정이 실패하는 것들 — 검색어를 직접 지정
FORCE_QUERY = {
    "레디플레이어원": "레디 플레이어 원",
    "맨인더다크": "맨 인 더 다크",
    "인디펜던스데이:리써전스": "인디펜던스 데이: 리써전스",
    "나루토 최종 극장판": "더 라스트 나루토 더 무비",
    "퍼시잭슨과 괴물들의바다": "퍼시 잭슨과 괴물의 바다",
    "아이엠그루트": "아이 앰 그루트",
    "앤트맨앤와스프": "앤트맨과 와스프",
    "데드풀 & 울버린": "데드풀과 울버린",
}

# TMDB가 제목만으로 헷갈리는 것들 — 매체를 못 박는다
FORCE_MEDIA = {
    "엑시트": "movie",          # 2019 한국 영화 (동명 드라마가 있음)
    "닌자거북이": "movie",       # 2014 실사 영화
    "오징어 게임 2": "tv",
    "Backrooms": "movie",
}

PREVIEW = "migrate_preview.json"
OUT = "tmdb_match.json"
REVIEW = "tmdb_확인필요.md"


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def query_title(title):
    """검색용 제목 — 시즌/파트 표기를 떼어낸다."""
    t = re.sub(r"시즌[ ]*[0-9]+|[0-9]+기$|:[ ]*시즌[0-9]+", " ", title)
    t = re.sub(r"[ ]{2,}", " ", t).strip(" :·-,")
    return t or title


def season_no(title):
    m = re.search(r"시즌[ ]*([0-9]+)", title)
    return int(m.group(1)) if m else None


def score(cand, want_title, want_year, is_season=False):
    """제목 일치도 + 연도 근접도. 시즌물은 연도를 무시한다.

    '위쳐 시즌3'(2023)을 시리즈 첫 방영연도(2019)와 비교하면
    엉뚱한 스핀오프가 이기기 때문.
    """
    s = 0.0
    nt, nc = norm(want_title), norm(cand["제목"])
    if nt == nc:
        s += 3
    elif nt and (nt in nc or nc in nt):
        s += 1.5
    if is_season:
        s += 1.0 if cand["media_type"] == "tv" else -1.0
        want_year = None
    if want_year and cand["연도"]:
        diff = abs(int(cand["연도"]) - int(want_year))
        s += {0: 2, 1: 1.2, 2: 0.3}.get(diff, -1.0)
    s += min(cand["인기도"], 50) / 100.0
    return s


def main():
    with io.open(PREVIEW, encoding="utf-8") as f:
        works = json.load(f)
    targets = [w for w in works if w["종류"] in ("영화", "시리즈")]
    print(f"대상 {len(targets)}건 조회 시작...")

    out, low = {}, []
    for i, w in enumerate(targets, 1):
        q = FORCE_QUERY.get(w["제목"]) or query_title(w["제목"])
        year = (w["출시·개봉일"] or "")[:4]
        try:
            cands, used_q = tmdb.search_smart(q, year)
        except Exception as e:
            print(f"  [{w['제목']}] 조회 실패: {e}", file=sys.stderr)
            continue
        if not cands:
            low.append((w, None, "검색 결과 없음"))
            continue
        # 점수는 원래 제목 기준 (norm이 띄어쓰기를 무시하므로 그대로 비교됨)
        want_media = FORCE_MEDIA.get(w["제목"])
        if want_media:
            filtered = [c for c in cands if c["media_type"] == want_media]
            cands = filtered or cands
        season = season_no(w["제목"])
        best = max(cands, key=lambda c: score(c, q, year, season is not None))
        sc = score(best, q, year, season is not None)
        exact = norm(q) == norm(best["제목"])
        ydiff = (abs(int(best["연도"]) - int(year))
                 if year and best["연도"] else None)
        if season is not None:
            conf = "확실" if exact and best["media_type"] == "tv" else "보통"
        else:
            conf = ("확실" if exact and (ydiff is not None and ydiff <= 1)
                    else "보통" if exact or ydiff == 0 else "낮음")
        out[w["제목"]] = {
            "tmdb_id": best["id"],
            "media_type": best["media_type"],
            "종류": "시리즈" if best["media_type"] == "tv" else "영화",
            "TMDB제목": best["제목"],
            "원제": best["원제"],
            "TMDB날짜": best["날짜"],
            "애니메이션": best["애니메이션"],
            "시즌번호": season,
            "확신": conf,
            "점수": round(sc, 2),
            "질의어": used_q,
        }
        if conf == "낮음":
            low.append((w, best, f"점수 {sc:.2f}"))
        if i % 50 == 0:
            print(f"  {i}/{len(targets)}")

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    changed = [(t, d) for t, d in out.items() if d["종류"] == "시리즈"]
    anim = [(t, d) for t, d in out.items() if d["애니메이션"]]
    print(f"\n매칭 {len(out)}건 / 확인필요 {len(low)}건")
    print(f"  시리즈로 판정: {len(changed)}건")
    print(f"  애니메이션으로 판정: {len(anim)}건")

    lines = ["# TMDB 확인 필요 목록", "",
             "자동 매칭이 애매한 건들입니다. 틀린 게 있으면 알려주세요.", ""]
    for w, best, why in low:
        got = (f"{best['제목']} ({best['연도']}, {best['media_type']})"
               if best else "—")
        lines.append(f"- `{w['제목']}` ({w['출시·개봉일']}) → {got}   [{why}]")
    with io.open(REVIEW, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  {REVIEW} 저장")


if __name__ == "__main__":
    main()

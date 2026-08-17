# -*- coding: utf-8 -*-
"""이관 대상 검토 목록을 만든다 (이관검토.md).

migrate.py --dry 가 만든 migrate_preview.json을 읽어 사람이 훑을 수 있는
목록으로 뽑는다. 줄 앞에 x 를 붙여 저장하면 apply_review.py가 제외 목록으로 읽는다.
"""
import io
import json
from collections import defaultdict

PREVIEW = "migrate_preview.json"
OUT = "이관검토.md"


def main():
    with io.open(PREVIEW, encoding="utf-8") as f:
        works = json.load(f)

    by_year = defaultdict(list)
    for x in works:
        d = x["출시·개봉일"] or (x["일정"][0]["날짜"] if x["일정"] else "0000-00-00")
        by_year[d[:4]].append((d, x))

    n_sub = sum(len(x["일정"]) for x in works)
    out = [
        "# 이관 검토 목록", "",
        f"총 **{len(works)}작품** / 부속 일정 {n_sub}건", "",
        "- 빼고 싶은 줄 맨 앞에 `x` 를 붙이고 저장해주세요.",
        "- 작품 줄을 빼면 그 아래 `└` 부속 일정도 같이 빠집니다.",
        "- `└` 줄에만 붙이면 그 일정만 빠집니다.",
        "- 제목 뒤 `[PS4]` `[넷플릭스]`는 제목에서 떼어내 속성으로 옮긴 값입니다.", "",
        "---", "", "## 전체 목록 (연도순)", "",
    ]
    for y in sorted(by_year):
        rows = sorted(by_year[y], key=lambda r: r[0])
        out += ["", f"### {y if y != '0000' else '날짜없음'} — {len(rows)}건", ""]
        for _, x in rows:
            tags = "".join(f" [{v}]" for v in x["플랫폼"] + x["공개처"])
            date = x["출시·개봉일"] or "미정"
            if x.get("종료일"):
                date = f"{date}~{x['종료일']}"
            out.append(f"  {date} [{x['종류']}] {x['제목']}{tags}")
            for s in x["일정"]:
                nm = f'  "{s["이름"]}"' if s["이름"] != x["제목"] else ""
                out.append(f"      └ {s['날짜']} {s['종류']}{nm}")

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"{OUT} — {len(works)}작품 / 부속 {n_sub}건")


if __name__ == "__main__":
    main()

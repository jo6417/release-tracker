# -*- coding: utf-8 -*-
"""검토 목록(이관검토.md)에서 x 표시한 줄을 읽어 제외 목록으로 만든다.

작품 줄에 x → 그 작품과 딸린 부속 일정 전부 제외
`└` 줄에 x → 그 일정만 제외

결과는 review_exclude.json에 저장되고, migrate.py가 자동으로 읽는다.

사용법:
    python apply_review.py
"""
import io
import json
import re

REVIEW = "이관검토.md"
OUT = "review_exclude.json"

WORK_RE = re.compile(r"^(미정|\d{4}-\d{2}-\d{2})\s+\[(.+?)\]\s+(.+?)\s*$")
SUB_RE = re.compile(r"^└\s+(\d{4}-\d{2}-\d{2})\s+(\S+)")
TAG_RE = re.compile(r"(?:\s*\[[^\]]+\])+$")


def main():
    works, subs = [], []
    current = None
    with io.open(REVIEW, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            marked = bool(re.match(r"^\s*x\s", line, re.I))
            body = re.sub(r"^\s*x\s+", "", line, flags=re.I).strip()

            sub = SUB_RE.match(body)
            if sub:
                if marked and current:
                    subs.append([current, sub.group(1)])
                continue

            work = WORK_RE.match(body)
            if work:
                title = TAG_RE.sub("", work.group(3)).strip()
                current = title
                if marked:
                    works.append(title)

    data = {"works": sorted(set(works)), "schedules": subs}
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"제외할 작품 {len(data['works'])}개 / 부속 일정 {len(subs)}건 → {OUT}")
    for t in data["works"][:20]:
        print(f"   작품 제외: {t}")
    for t, d in subs[:20]:
        print(f"   일정 제외: {t} / {d}")


if __name__ == "__main__":
    main()

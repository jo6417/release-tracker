# -*- coding: utf-8 -*-
"""스마트택배로 국내 배송 상태를 매일 확인하고, 도착하면 자동으로 닫는다.

결제가 끝난 물건은 받을 때까지 매일 눈에 띄어야 한다 — 예정일까지 남은
날수와 상관없이. 그래야 "이거 왔나?"를 스마트택배 앱을 따로 열어 확인하는
일이 없어진다.

**국내 구간만 된다.** 사줘 같은 해외 구매대행은 국내 택배사가 송장을
찍기 전까지(현지 판매처→창고→국제배송) 추적 수단이 없다 — 지금 쓰는 것과
같은 앱(스마트택배)의 한계이기도 하다. `송장번호`·`택배사`가 채워진 뒤부터만
동작한다.

**프리 등급은 월 100건, 키는 1개월 만료.** 그래서 대상을 좁힌다 —
`상태`가 `주문·배송 중`인 것만 매일 조회하고(`예약주문`은 아직 물건이
없으니 조회할 이유가 없다), `구매 완료`가 되는 즉시 뺀다. 키 만료
임박(발급 후 25일)도 여기서 감지해 알린다.

사용법:
    python sync_delivery.py --dry
    python sync_delivery.py
"""
import argparse
import datetime
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (.env 로드)
import notify
from config import API, COURIER_CODE, headers

IDS_FILE = "db_ids.json"
TRACK_API = "https://info.sweettracker.co.kr/api/v1/trackingInfo"
KEY_FILE = "sweettracker_key.json"  # 발급일 기록 (만료 알림용)
DONE_LEVEL = 6


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


def patch_page(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def track(t_key, code, invoice):
    code = COURIER_CODE.get(code, code)
    q = urllib.parse.urlencode({"t_key": t_key, "t_code": code, "t_invoice": invoice})
    req = urllib.request.Request(f"{TRACK_API}?{q}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def check_key_age():
    """발급 후 25일이 지나면 재발급을 알린다. 발급일은 최초 실행 때 오늘로
    한 번 기록해두고(사용자가 실제 발급받은 날과 하루 이틀 어긋날 수 있지만,
    "슬슬 갱신할 때"라는 목적에는 그 정도 오차가 문제 되지 않는다)."""
    info = {}
    if os.path.exists(KEY_FILE):
        with io.open(KEY_FILE, encoding="utf-8") as f:
            info = json.load(f)
    issued = info.get("발급일")
    if not issued:
        issued = time.strftime("%Y-%m-%d")
        with io.open(KEY_FILE, "w", encoding="utf-8") as f:
            json.dump({"발급일": issued}, f, ensure_ascii=False, indent=1)
        return None
    age = (datetime.date.today() - datetime.date.fromisoformat(issued)).days
    if age >= 25:
        return age
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    key = os.environ.get("SWEETTRACKER_API_KEY")
    if not key:
        print("SWEETTRACKER_API_KEY가 없습니다 (.env 확인)", file=sys.stderr)
        return

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    rows = query_all(ids["purchase_db"],
                     {"property": "상태", "select": {"equals": "주문·배송 중"}})
    targets = []
    for p in rows:
        pr = p["properties"]
        invoice = txt(pr["송장번호"]).strip()
        courier = txt(pr["택배사"]).strip()
        if invoice and courier:
            targets.append((p, txt(pr["이름"]), courier, invoice))

    print(f"배송 중 {len(targets)}건 (송장 등록된 것만)")

    lines, done = [], []
    for p, name, courier, invoice in targets:
        try:
            d = track(key, courier, invoice)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  {name}: 조회 실패 {e.code} {body[:150]}", file=sys.stderr)
            continue
        if d.get("status") is False:
            print(f"  {name}: {d.get('msg')}")
            continue

        last = d.get("lastDetail") or {}
        stage = last.get("kind", "-")
        where = last.get("where", "")
        when = (last.get("timeString") or "")[:10]
        complete = d.get("completeYN") == "Y" or d.get("level", 0) >= DONE_LEVEL

        print(f"  {name:34} {stage:8} {where:12} {when}"
              f"{'  → 도착 완료' if complete else ''}")
        lines.append(f"{name} — {stage} ({where}, {when})")

        if complete:
            done.append((p["id"], name, when or time.strftime("%Y-%m-%d")))
        time.sleep(0.2)

    if not a.dry:
        for pid, name, when in done:
            patch_page(pid, {"상태": {"select": {"name": "구매 완료"}},
                             "구매일": {"date": {"start": when}}})
            print(f"자동 완료 처리: {name} ({when})")

    key_age = check_key_age()

    if not a.dry and (lines or key_age):
        summary_bits = []
        if lines:
            summary_bits.append(f"배송 중 {len(lines)}건")
        if key_age:
            summary_bits.append("스마트택배 키 재발급 필요")
        details = []
        if lines:
            details.append(("[배송]", lines))
        if key_age:
            details.append(("[스마트택배]", [
                f"키 발급 후 {key_age}일 지났습니다. 무료 키는 1개월만 유효합니다.",
                "tracking.sweettracker.co.kr에서 재발급 후 .env·리포 시크릿을 갱신해주세요."]))
        notify.send_card(" · ".join(summary_bits), summary=[" · ".join(summary_bits)],
                         details=details, kinds=["배송"], count=len(lines))
        if not notify.spooling():
            print("알림 카드 1장 발송 완료")

    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()

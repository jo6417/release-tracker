# -*- coding: utf-8 -*-
"""시스템이 살아있는지 밖에서 확인한다.

'커밋이 없다'는 죽음의 신호가 아니다. DTSTAMP를 노션 수정시각 기준으로
바꾼 뒤로는 신작이 없으면 커밋이 안 생기는 게 정상이기 때문이다.

정확한 신호는 워크플로 실행 이력이다. calendar.yml은 데이터가 그대로여도
계속 돌기 때문에, 실행이 한참 끊겼다면 그건 확실히 고장이다.

GitHub Actions API는 public 리포에 한해 인증 없이 읽을 수 있어
토큰이 필요 없다. 어디서 돌려도 된다.

사용법:
    python healthcheck.py          # 이상 있으면 종료코드 1
"""
import datetime
import json
import sys
import urllib.error
import urllib.request

REPO = "jo6417/release-tracker"
# calendar.yml의 예약은 "가능한 한 자주"지만 실제로는 한참 밀린다.
# 2026-08-22 실측(100건)에서 평균 33분, 최대 103분(=1.7시간)이었다.
# 기준을 2시간으로 두면 최대치와 겹쳐 멀쩡한데도 경보가 뜬다. 3시간으로 둔다.
CHECKS = [("calendar.yml", "캘린더 갱신", 3),
          ("daily.yml", "매일 추적", 36)]


def last_run(workflow):
    url = (f"https://api.github.com/repos/{REPO}/actions/workflows/"
           f"{workflow}/runs?per_page=1")
    req = urllib.request.Request(url, headers={"User-Agent": "release-tracker"})
    with urllib.request.urlopen(req, timeout=20) as r:
        runs = json.loads(r.read()).get("workflow_runs", [])
    if not runs:
        return None, None
    run = runs[0]
    t = datetime.datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    t = t.replace(tzinfo=datetime.timezone.utc)
    return t, run.get("conclusion") or run.get("status")


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    problems = []
    print(f"확인 시각(UTC): {now:%Y-%m-%d %H:%M}")

    for wf, label, limit_h in CHECKS:
        try:
            t, result = last_run(wf)
        except urllib.error.URLError as e:
            print(f"  {label}: 조회 실패 ({e}) — GitHub 장애일 수 있음")
            continue

        if t is None:
            problems.append(f"{label}: 실행 이력이 없습니다")
            print(f"  {label}: 실행 이력 없음  ← 이상")
            continue

        age_h = (now - t).total_seconds() / 3600
        mark = "정상" if age_h <= limit_h else "← 이상"
        print(f"  {label}: {age_h:.1f}시간 전 ({result}) {mark}")
        if age_h > limit_h:
            problems.append(
                f"{label}가 {age_h:.0f}시간째 실행되지 않았습니다 "
                f"(기준 {limit_h}시간)")
        elif result not in ("success", "in_progress", "queued"):
            problems.append(f"{label}의 마지막 실행이 {result}로 끝났습니다")

    if not problems:
        print("\n결과: 정상")
        return 0

    print("\n결과: 이상 발견")
    for p in problems:
        print(" -", p)
    print("\nGitHub은 60일간 활동이 없으면 예약 워크플로를 끕니다.")
    print(f"https://github.com/{REPO}/actions 에서 다시 켤 수 있습니다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

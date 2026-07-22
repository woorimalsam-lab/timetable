# -*- coding: utf-8 -*-
"""
컴시간 공개 시간표를 받아 docs/data/ 에 JSON으로 저장하는 스크립트.
GitHub Actions가 몇 시간마다 실행 → 커밋 → GitHub Pages로 서빙.
지난 주차 파일은 지우지 않으므로 '지난 기록'이 저절로 쌓인다.
"""
import os
import re
import json
import glob
from base64 import b64encode

import time

import requests
from bs4 import BeautifulSoup

# 서버 IP가 바뀌므로 도메인을 사용한다 (예전 하드코딩 IP 222.106.100.23 은 2026-07 사망)
COMCI_URL = "http://comci.net:4082"
SCHOOL_QUERY = "심원고"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "docs", "data")
os.makedirs(DATA_DIR, exist_ok=True)


def get(url, timeout=20, tries=5):
    """컴시간 서버가 간헐적으로 죽으므로 재시도한다."""
    last = None
    for i in range(tries):
        try:
            return requests.get(url, timeout=timeout)
        except Exception as e:
            last = e
            print(f"  요청 실패({i+1}/{tries}): {type(e).__name__} — 재시도")
            time.sleep(5 * (i + 1))
    raise last


def load_base():
    r = get(f"{COMCI_URL}/st", timeout=20)
    r.encoding = "EUC-KR"
    script = BeautifulSoup(r.text, "lxml").find_all("script")[1].contents[0]
    route = re.search(r"\./\d+\?\d+l", script).group(0)
    prefix = re.search(r"'\d+_'", script).group(0)[1:-1]
    return {
        "prefix": prefix,
        "baseurl": f"{COMCI_URL}{route[1:8]}",
        "searchurl": f"{COMCI_URL}{route[1:8]}{route[8:]}",
    }


def find_school(base):
    enc = "%".join(
        str(SCHOOL_QUERY.encode("EUC-KR")).upper()[2:-1].replace("\\X", "\\").split("\\")
    )
    resp = get(base["searchurl"] + enc, timeout=20)
    resp.encoding = "UTF-8"
    raw = json.loads(resp.text.replace("\0", ""))["학교검색"]
    return {"region": raw[0][1], "name": raw[0][2], "code": raw[0][3]}


def decode_cell(cell):
    s = str(cell)
    changed = False
    if s.startswith(">"):
        s = s[1:]
        changed = True
    if not s.isdigit() or len(s) < 3:
        return None
    return int(s[:-3]), int(s[-2:]), changed


def norm_start(label):
    m = re.match(r"\s*(\d{2})-(\d{2})-(\d{2})", label or "")
    return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def fetch_week(base, code, r):
    url = f"{base['baseurl']}?" + b64encode(f"{base['prefix']}{code}_0_{r}".encode()).decode()
    raw = json.loads(get(url, timeout=20).content.decode("utf-8", "ignore").replace("\0", ""))

    teachers = raw["자료446"]
    subjects = raw["자료492"]
    grid = raw["자료147"]
    period_times = []
    for p in raw.get("일과시간", []):
        m = re.search(r"\(([^)]+)\)", p)
        period_times.append(m.group(1) if m else "")

    max_period = 0
    per_teacher = {i: [] for i in range(len(teachers))}
    for gi in range(1, len(grid)):
        og = grid[gi]
        if not isinstance(og, list):
            continue
        for ci in range(1, len(og)):
            oc = og[ci]
            if not isinstance(oc, list):
                continue
            for di in range(1, min(6, len(oc))):
                day = oc[di]
                if not isinstance(day, list):
                    continue
                for pi in range(1, len(day)):
                    d = decode_cell(day[pi])
                    if not d:
                        continue
                    subj, th, ch = d
                    if th <= 0 or th >= len(teachers):
                        continue
                    sub = subjects[subj] if 0 < subj < len(subjects) else ""
                    per_teacher[th].append({
                        "day": di - 1, "period": pi - 1,
                        "cls": f"{gi}-{ci}", "sub": sub, "changed": ch,
                    })
                    max_period = max(max_period, pi - 1)

    weeks = []
    for row in raw.get("일자자료", []):
        if isinstance(row, list) and len(row) >= 2:
            weeks.append({"r": row[0], "label": row[1], "start": norm_start(row[1])})

    return {
        "teachers": [{"idx": i, "name": teachers[i]} for i in range(1, len(teachers)) if teachers[i]],
        "per_teacher": {str(k): v for k, v in per_teacher.items()},
        "period_times": period_times,
        "max_period": max_period,
        "week_start": norm_start(raw.get("시작일", "")) or raw.get("시작일", ""),
        "week_label": raw.get("시작일", ""),
        "live_weeks": weeks,
    }


def main():
    base = load_base()
    school = find_school(base)
    code = school["code"]
    print("학교:", school["name"], code)

    first = fetch_week(base, code, 1)
    current = first["week_start"]
    live = {w["start"]: w for w in first["live_weeks"] if w["start"]}
    print("라이브 주차:", list(live.keys()))

    # 라이브 주차 저장 (라벨 포함)
    datasets = {current: first}
    for st, w in live.items():
        if st not in datasets:
            datasets[st] = fetch_week(base, code, w["r"])
    for st, data in datasets.items():
        lab = live.get(st, {}).get("label") or st
        snap = {k: data[k] for k in
                ("teachers", "per_teacher", "period_times", "max_period", "week_start")}
        snap["week_label"] = lab
        with open(os.path.join(DATA_DIR, f"{code}_{st}.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        print("저장:", st, lab)

    # index.json 재구성 (기존 파일 = 지난 기록 유지)
    weeks = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, f"{code}_*.json")), reverse=True):
        m = re.search(rf"{code}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$", os.path.basename(path))
        if not m:
            continue
        st = m.group(1)
        try:
            with open(path, encoding="utf-8") as f:
                lab = json.load(f).get("week_label", st)
        except Exception:
            lab = st
        weeks.append({"start": st, "label": lab})

    index = {"school": school, "current": current, "weeks": weeks}
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print("index.json:", len(weeks), "주차")


if __name__ == "__main__":
    main()

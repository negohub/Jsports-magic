"""NPB公式の月別日程ページ（schedule_MM_detail.html）を読み、
セ・リーグ球団が絡む試合を data/latest.json に保存する。
試合内容に変化がなければファイルは書き換えない（無駄なコミットを防ぐ）。
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
MONTHS = range(3, 11)  # 3月〜10月
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")

# 表記 → コード（長い名前から先に照合する）
TEAMS = [
    ("ソフトバンク", "H"), ("日本ハム", "F"), ("オリックス", "B"),
    ("ヤクルト", "S"), ("DeNA", "DB"), ("ロッテ", "M"), ("西武", "L"),
    ("楽天", "E"), ("巨人", "G"), ("阪神", "T"), ("中日", "D"), ("広島", "C"),
]
CL = {"DB", "G", "T", "D", "S", "C"}
TEAM_RE = re.compile("|".join(re.escape(n) for n, _ in TEAMS))
CODE = dict(TEAMS)
DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})")


def norm(s):
    return unicodedata.normalize("NFKC", s or "").strip()


def parse_month(html, season):
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for table in soup.find_all("table"):
        cur = None
        for tr in table.find_all("tr"):
            cells = [norm(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            m = DATE_RE.match(cells[0])
            if m:
                cur = f"{season}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
                cells = cells[1:]
            if cur is None:
                continue
            idx = next((i for i, c in enumerate(cells) if len(TEAM_RE.findall(c)) >= 2), None)
            if idx is None:
                continue
            card = cells[idx]
            if "予備日" in card:
                continue
            found = list(TEAM_RE.finditer(card))
            home, away = CODE[found[0].group()], CODE[found[1].group()]
            if home not in CL and away not in CL:
                continue
            middle = card[found[0].end():found[1].start()]
            vcell = cells[idx + 1] if idx + 1 < len(cells) else ""
            tm = re.search(r"(\d{1,2}:\d{2})", vcell)
            venue = re.sub(r"\s*\d{1,2}:\d{2}.*$", "", vcell).replace(" ", "")
            pitch = " ".join(cells[idx + 2:])
            g = {"d": cur, "h": home, "a": away, "v": venue}
            if "中止" in card or "ノーゲーム" in card:
                g["st"] = "canc"
            else:
                sc = re.search(r"(\d+)\s*-\s*(\d+)", middle)
                if sc and re.search(r"(勝|分)\s*:", pitch):
                    g.update(st="final", hs=int(sc.group(1)), as_=int(sc.group(2)))
                elif sc:
                    g["st"] = "live"
                else:
                    g["st"] = "sched"
                    if tm:
                        g["t"] = tm.group(1)
            if "as_" in g:
                g["as"] = g.pop("as_")
            games.append(g)
    return games


def main():
    season = int(os.environ.get("SEASON") or datetime.now(JST).year)
    all_games = []
    for mo in MONTHS:
        url = f"https://npb.jp/games/{season}/schedule_{mo:02d}_detail.html"
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (jsports-magic)"})
        except requests.RequestException as e:
            print(f"[{mo}月] 取得失敗: {e}")
            continue
        if r.status_code != 200:
            print(f"[{mo}月] HTTP {r.status_code}")
            continue
        r.encoding = r.apparent_encoding or "utf-8"
        gs = parse_month(r.text, season)
        st = {k: sum(g["st"] == k for g in gs) for k in ("final", "sched", "live", "canc")}
        print(f"[{mo}月] {len(gs)}試合 {st}")
        all_games += gs

    if not all_games:
        print("試合が1件も取れませんでした。ページ構成が変わった可能性があります。")
        sys.exit(1)

    all_games.sort(key=lambda g: (g["d"], g["h"]))
    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            try:
                old = json.load(f)
            except json.JSONDecodeError:
                old = None
    if old and old.get("games") == all_games and old.get("season") == season:
        print("変化なし")
        return
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "season": season,
        "games": all_games,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存しました: {len(all_games)}試合")


if __name__ == "__main__":
    main()

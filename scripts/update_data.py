"""NPB公式の月別日程ページ（schedule_MM_detail.html）を読み、
セ・リーグ球団が絡む試合を data/latest.json に保存する。
・一度「試合終了」になった試合は、ページ側の反映遅れで「試合前」に戻さない
・読み取りに失敗した月は取り直し、それでもダメなら前回のデータを残す
・ページ上部の当日の「試合終了」欄からも結果を拾う
"""
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup, NavigableString

JST = timezone(timedelta(hours=9))
MONTHS = range(3, 11)  # 3月〜10月
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")

TEAMS = [
    ("ソフトバンク", "H"), ("日本ハム", "F"), ("オリックス", "B"),
    ("ヤクルト", "S"), ("DeNA", "DB"), ("ロッテ", "M"), ("西武", "L"),
    ("楽天", "E"), ("巨人", "G"), ("阪神", "T"), ("中日", "D"), ("広島", "C"),
]
CL = {"DB", "G", "T", "D", "S", "C"}
TEAM_RE = re.compile("|".join(re.escape(n) for n, _ in TEAMS))
CODE = dict(TEAMS)
URLCODE = {"g": "G", "db": "DB", "t": "T", "d": "D", "c": "C", "s": "S",
           "h": "H", "f": "F", "b": "B", "e": "E", "l": "L", "m": "M"}
DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})")
SCORE_HREF = re.compile(r"/scores/(\d{4})/(\d{2})(\d{2})/([a-z]+)-([a-z]+)-\d+")


def norm(s):
    return unicodedata.normalize("NFKC", s or "").strip()


def key(g):
    return (g["d"], g["h"], g["a"])


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
                    g.update(st="final", hs=int(sc.group(1)))
                    g["as"] = int(sc.group(2))
                elif sc:
                    g["st"] = "live"
                else:
                    g["st"] = "sched"
                    if tm:
                        g["t"] = tm.group(1)
            games.append(g)
    return games


def parse_ticker(html):
    """ページ上部の当日の試合欄から「試合終了」の結果を拾う。並びが判別できないものは使わない。"""
    soup = BeautifulSoup(html, "html.parser")
    res = {}
    for a in soup.find_all("a", href=True):
        m = SCORE_HREF.search(a["href"])
        if not m:
            continue
        parts = []
        for el in a.descendants:
            if isinstance(el, NavigableString):
                parts.append(str(el))
            elif getattr(el, "name", None) == "img" and el.get("alt"):
                parts.append(f" {el['alt']} ")
        text = norm(" ".join(parts))
        if "試合終了" not in text:
            continue
        home, away = URLCODE.get(m.group(4)), URLCODE.get(m.group(5))
        sc = re.search(r"(\d+)\s*-\s*(\d+)", text)
        names = [CODE[x.group()] for x in TEAM_RE.finditer(text)]
        if not (home and away and sc) or len(names) < 2 or {names[0], names[1]} != {home, away}:
            continue
        s1, s2 = int(sc.group(1)), int(sc.group(2))
        hs, as_ = (s1, s2) if names[0] == home else (s2, s1)
        d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        res[(d, home, away)] = (hs, as_)
    return res


def fetch(url):
    for i in range(3):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (jsports-magic)"})
            if r.status_code == 200:
                try:
                    return r.content.decode("utf-8")
                except UnicodeDecodeError:
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
            print(f"  HTTP {r.status_code}（{i + 1}回目）")
        except requests.RequestException as e:
            print(f"  取得失敗（{i + 1}回目）: {e}")
        time.sleep(3)
    return None


def main():
    season = int(os.environ.get("SEASON") or datetime.now(JST).year)
    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            try:
                old = json.load(f)
            except json.JSONDecodeError:
                old = None
    old_games = old["games"] if old and old.get("season") == season else []
    old_month = {}
    for g in old_games:
        old_month.setdefault(int(g["d"][5:7]), []).append(g)

    all_games, ticker = [], {}
    for mo in MONTHS:
        url = f"https://npb.jp/games/{season}/schedule_{mo:02d}_detail.html"
        gs = []
        for attempt in range(3):
            html = fetch(url)
            if html is None:
                break
            ticker.update(parse_ticker(html))
            gs = parse_month(html, season)
            if gs:
                break
            print(f"[{mo}月] 0試合でした。取り直します（{attempt + 1}回目）")
            time.sleep(5)
        prev = old_month.get(mo, [])
        if prev and len(gs) < len(prev) * 0.8:
            print(f"[{mo}月] 読み取り不足（{len(gs)}件）のため前回のデータ{len(prev)}件を使います")
            gs = prev
        st = {k: sum(g["st"] == k for g in gs) for k in ("final", "sched", "live", "canc")}
        print(f"[{mo}月] {len(gs)}試合 {st}")
        all_games += gs

    if not all_games:
        print("試合が1件も取れませんでした。ページ構成が変わった可能性があります。")
        sys.exit(1)

    # 当日の「試合終了」欄の結果を反映
    for g in all_games:
        k = key(g)
        if g["st"] in ("sched", "live") and k in ticker:
            g["st"], g["hs"], g["as"] = "final", ticker[k][0], ticker[k][1]
            g.pop("t", None)
            print(f"  速報から反映: {k} {g['hs']}-{g['as']}")
    # 一度確定した結果は巻き戻さない
    done = {key(g): g for g in old_games if g["st"] in ("final", "canc")}
    for i, g in enumerate(all_games):
        k = key(g)
        if g["st"] in ("sched", "live") and k in done:
            all_games[i] = done[k]
            print(f"  前回の確定結果を維持: {k}")

    all_games.sort(key=lambda g: (g["d"], g["h"]))
    if old and old_games == all_games:
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

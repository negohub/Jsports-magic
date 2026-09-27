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
    ("楽天", "E"), ("ジャイアンツ", "G"), ("巨人", "G"), ("阪神", "T"), ("中日", "D"), ("広島", "C"),
]
CL = {"DB", "G", "T", "D", "S", "C"}
TEAM_RE = re.compile("|".join(re.escape(n) for n, _ in TEAMS))
CODE = dict(TEAMS)
ABBR = {"神": "T", "巨": "G", "デ": "DB", "中": "D", "広": "C", "ヤ": "S"}

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


# ---------- 成績（スポーツナビ） ----------
YAHOO = "https://baseball.yahoo.co.jp/npb"
# スポナビの個人成績の全項目（type= の値 → 表の見出し）
BAT_CATS = {"avg": "打率", "g": "試合", "pa": "打席", "ab": "打数", "h": "安打", "h2b": "二塁打", "h3b": "三塁打", "hr": "本塁打",
            "tb": "塁打", "rbi": "打点", "r": "得点", "so": "三振", "bb": "四球", "hbp": "死球", "sh": "犠打", "sf": "犠飛",
            "sb": "盗塁", "cs": "盗塁死", "gidp": "併殺打", "obp": "出塁率", "slg": "長打率", "ops": "OPS", "risp": "得点圏", "e": "失策"}
PIT_CATS = {"era": "防御率", "g": "登板", "gs": "先発", "cg": "完投", "sho": "完封", "qs": "QS", "w": "勝利", "l": "敗戦",
            "hld": "ホールド", "hldp": "HP", "sv": "セーブ", "wpct": "勝率", "ip": "投球回", "h": "被安打", "hr": "被本塁打",
            "so": "奪三振", "k9": "奪三振率", "bb": "与四球", "hbp": "与死球", "wp": "暴投", "bk": "ボーク", "r": "失点",
            "er": "自責点", "avg": "被打率", "kbb": "K/BB", "qs_pct": "QS率", "whip": "WHIP"}
TEAM_KEYS = ["打率", "本塁打", "得点", "盗塁", "防御率", "失点", "失策"]
STAMP_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")


def clean(s):
    return re.sub(r"\s+", "", norm(s))


def stamp_of(html):
    m = STAMP_RE.search(norm(BeautifulSoup(html, "html.parser").get_text(" ")))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))) if m else None


def parse_yahoo_rank(html, label, limit=10):
    """部門別の個人成績ページ（最大30位）から上位を取る"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [clean(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if "選手名" not in head or label not in head:
            continue
        ni, vi = head.index("選手名"), head.index(label)
        res = []
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) != len(head) or not cells[0].isdigit():
                continue
            m = re.match(r"^(.+?)\s*\(\s*(.)\s*\)$", cells[ni])
            if not m or m.group(2) not in ABBR:
                continue
            r = int(cells[0])
            if r > limit:
                break
            res.append({"r": r, "n": m.group(1).strip(), "t": ABBR[m.group(2)], "v": cells[vi]})
        if res:
            return res
    return None


def parse_yahoo_team(html):
    """セ・リーグ順位表（詳細）からチーム成績を取る。「-」の項目は入れない"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [clean(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if "チーム名" not in head or "防御率" not in head:
            continue
        ti = head.index("チーム名")
        out = {}
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) != len(head):
                continue
            m = TEAM_RE.search(cells[ti])
            if not m or CODE[m.group()] not in CL:
                continue
            out[CODE[m.group()]] = {k: cells[head.index(k)] for k in TEAM_KEYS
                                    if k in head and re.search(r"\d", cells[head.index(k)])}
        if len(out) == 6:
            return out
    return None


def fetch_stats(season, old_stats):
    """チーム成績と個人ランキング。取れなかった部分は前回の値を残す"""
    st = json.loads(json.dumps(old_stats or {}))
    st["team"] = st.get("team") if isinstance(st.get("team"), dict) else {}
    st.setdefault("leaders", {})
    stamps = []
    html = fetch(f"{YAHOO}/standings/detail/1")
    tbl = parse_yahoo_team(html) if html else None
    if tbl:
        for t, row in tbl.items():
            cur = st["team"].get(t)
            cur = cur if isinstance(cur, dict) and "bat" not in cur else {}
            cur.update(row)
            st["team"][t] = cur
        stamps.append(stamp_of(html))
        print("[成績] チーム成績 OK")
    else:
        print("[成績] チーム成績 読み取れず（前回の値を使用）")
    # 個人ランキング：スポナビの成績の更新時刻が前回と同じなら、全項目の取り直しはしない（負担を減らす）
    probe = fetch(f"{YAHOO}/stats/batter?gameKindId=1&type=avg")
    probe_stamp = stamp_of(probe) if probe else None
    want = [f"b_{k}" for k in BAT_CATS] + [f"p_{k}" for k in PIT_CATS]
    have_all = all(k in st["leaders"] for k in want)
    if probe_stamp and have_all and st.get("rank_stamp") == list(probe_stamp):
        print("[成績] 個人ランキングは前回から更新なし（取り直さない）")
        stamps.append(probe_stamp)
    else:
        leaders = {k: v for k, v in st["leaders"].items() if k in want}
        for kind, pre, cats in (("batter", "b_", BAT_CATS), ("pitcher", "p_", PIT_CATS)):
            for key, label in cats.items():
                html = probe if (kind == "batter" and key == "avg") else fetch(f"{YAHOO}/stats/{kind}?gameKindId=1&type={key}")
                rows = parse_yahoo_rank(html, label) if html else None
                if rows:
                    leaders[pre + key] = rows
                    stamps.append(stamp_of(html))
                else:
                    print(f"[成績] ランキング {label} 読み取れず（前回の値を使用）")
                if not (kind == "batter" and key == "avg"):
                    time.sleep(1)
        st["leaders"] = leaders
        if probe_stamp:
            st["rank_stamp"] = list(probe_stamp)
    stamps = [x for x in stamps if x]
    if stamps:
        y, mo, d, h, mi = max(stamps)
        st["asof"] = f"{mo}/{d} {h}:{mi:02d}"
    st["src"] = "スポーツナビ"
    print(f"[成績] {st.get('asof')} 更新分 ランキング{len(st['leaders'])}部門")
    return st


def fetch_prev_order(season, old):
    """前年の最終順位（日程タブの球団の並びに使う）。一度取れたら次から取りに行かない"""
    prev = (old or {}).get("prev_order")
    if prev and prev.get("season") == season - 1 and len(prev.get("order", [])) == 6:
        return prev
    html = fetch(f"https://npb.jp/bis/{season - 1}/stats/std_c.html")
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            order = []
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                m = TEAM_RE.search(norm(cells[0].get_text(" ", strip=True))) if cells else None
                if m and CODE[m.group()] in CL and CODE[m.group()] not in order:
                    order.append(CODE[m.group()])
            if len(order) == 6:
                print(f"[前年順位] {season - 1}年: {order}")
                return {"season": season - 1, "order": order}
    print("[前年順位] 取得できず")
    return prev


ROSTER_CODE = {"T": "t", "G": "g", "DB": "db", "D": "d", "C": "c", "S": "s"}
POSITIONS = ("投手", "捕手", "内野手", "外野手")


def parse_roster(html):
    """NPBの選手一覧ページから、背番号・名前・ポジション・育成かどうかを取る"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        h = table.find_previous(["h3", "h4"])
        dev = bool(h and "育成" in h.get_text())
        pos = None
        for tr in table.find_all("tr"):
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            if cells[0] == "No.":
                pos = cells[1] if cells[1] in POSITIONS else None
                continue
            if pos and re.fullmatch(r"\d{1,3}", cells[0]) and cells[1]:
                out.append({"no": cells[0], "n": re.sub(r"\s+", " ", cells[1]), "p": pos, "dev": dev})
    return out


# 個別の応援歌がある選手を調べるページ（名前だけを照合し、歌詞は保存しない）
SONG_SOURCES = {
    "T": ["https://m.hanshintigers.jp/data/march/", "https://www.yakyu-ouen.net/tigers/"],
    "DB": ["https://sp.baystars.co.jp/player_songs/index", "https://www.yakyu-ouen.net/baystars/"],
    "G": ["https://giants-cheeringclub.com/cheeringsong/", "https://www.yakyu-ouen.net/giants/"],
    "D": ["https://www.yakyu-ouen.net/dragons/"],
    "C": ["https://www.carp.co.jp/team/songs", "https://www.yakyu-ouen.net/carp/"],
    "S": ["https://www.yakult-swallows.co.jp/players/song", "https://www.yakyu-ouen.net/swallows/"],
}
# 公式がPDFで配っている球団は、PDFに載っている選手名をここに書いておく（中日：cheersong2026.pdf）
SONG_EXTRA = {
    "D": ["岡林勇希", "田中幹也", "高橋周平", "カリステ", "村松開人", "福永裕基", "大島洋平", "石伊雄太", "大野雄大",
          "ボスラー", "石川昂弥", "根尾昂", "木下拓哉", "宇佐見真吾", "ブライト健太", "土田龍空", "阿部寿樹",
          "加藤匠馬", "上林誠知", "細川成也", "山本泰寛", "鵜飼航丞"],
}
SONG_REV = 2  # 判定のしかたを変えたら数字を上げる（上げると時期に関係なく1回やり直す）
# 背番号で並んでいるページ（ヤクルト公式）は背番号でも照合する
SONG_BY_NUMBER = {"https://www.yakult-swallows.co.jp/players/song"}
VARIANT = str.maketrans({"髙": "高", "﨑": "崎", "濵": "浜", "德": "徳", "瀨": "瀬", "邊": "辺", "邉": "辺", "塚": "塚", "・": "", "＝": "", "=": ""})


def squash(s):
    return re.sub(r"\s+", "", norm(s)).translate(VARIANT)


def mark_songs(t, rows):
    """応援歌ページに名前（または背番号）が出てくる選手に song=True を付ける。どのページも読めなければ None"""
    texts, numbers, ok = [squash(" ".join(SONG_EXTRA.get(t, [])))], set(), bool(SONG_EXTRA.get(t))
    for url in SONG_SOURCES.get(t, []):
        html = fetch(url)
        if not html:
            continue
        text = squash(BeautifulSoup(html, "html.parser").get_text(" "))
        if len(text) < 200:
            continue
        ok = True
        texts.append(text)
        if url in SONG_BY_NUMBER:
            numbers |= set(re.findall(r"背番号(\d{1,3})", text))
    if not ok:
        return None
    blob = "\n".join(texts)
    n = 0
    for r in rows:
        name = squash(r["n"])
        r["song"] = (len(name) >= 2 and name in blob) or (not r["dev"] and r["no"] in numbers)
        n += r["song"]
    return n


def fetch_rosters(old):
    """各球団の選手一覧（1日1回だけ取りに行く）"""
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    rosters = dict((old or {}).get("rosters") or {})
    have_all = (len(rosters) == 6 and all(any("song" in r for r in v) for v in rosters.values())
                and (old or {}).get("song_rev") == SONG_REV)
    # 更新は3月〜7月だけ（支配下登録の期限が7月末のため）。まだ全球団そろっていなければ時期に関係なく取る
    if have_all and not (3 <= now.month <= 7):
        return rosters, (old or {}).get("roster_date")
    # 取りに行くのは1日1回まで（応援歌ページが読めない球団があっても、何度も取りに行かない）
    if len(rosters) == 6 and (old or {}).get("roster_date") == today and (old or {}).get("song_rev") == SONG_REV:
        return rosters, today
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/teams/rst_{code}.html")
        rows = parse_roster(html) if html else []
        if len(rows) >= 20:
            n = mark_songs(t, rows)
            if n is None and t in rosters:  # 応援歌ページが読めなかったときは前回の判定を引き継ぐ
                prev = {(r["no"], r["n"]): r.get("song") for r in rosters[t]}
                for r in rows:
                    if prev.get((r["no"], r["n"])) is not None:
                        r["song"] = prev[(r["no"], r["n"])]
            print(f"[応援歌] {t}: 個別応援歌あり {n if n is not None else '判定できず'}人")
            rosters[t] = rows
        else:
            print(f"[選手一覧] {t} 読み取れず（前回の値を使用）")
    print(f"[選手一覧] {sum(len(v) for v in rosters.values())}人（{len(rosters)}球団）")
    return rosters, today


# ===== ポストシーズン（CS・日本シリーズ）：日程カレンダー用。戦況の計算には使わない =====
def parse_post_rows(html, season, stage_of):
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        text = norm(tr.get_text(" ", strip=True))
        m = re.search(r"(\d{1,2})/(\d{1,2})", text)
        if not m or "予備日" in text:
            continue
        st = stage_of(text)
        n = re.search(r"第(\d)戦", text)
        if not st or not n:
            continue
        g = {"d": f"{season}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", "stage": st, "no": int(n.group(1))}
        teams = [CODE[x] for x in TEAM_RE.findall(text)]
        if len(teams) >= 2:
            g["h"], g["a"] = teams[0], teams[1]
            sc = re.search(r"(\d+)\s*-\s*(\d+)", text.split(")")[-1]) if ")" in text else None
            if sc:
                g["hs"], g["as"], g["st"] = int(sc.group(1)), int(sc.group(2)), "final"
        if "中止" in text:
            g["st"] = "canc"
        tm = re.search(r"(\d{1,2}:\d{2})", text)
        if tm:
            g["t"] = tm.group(1)
        out.append(g)
    return out


def fetch_post(season, old):
    prev = [p for p in ((old or {}).get("post") or []) if p.get("d", "").startswith(str(season))]
    games = []
    html = fetch(f"https://npb.jp/games/{season}/schedule_climax_cl.html")
    if html:
        games += parse_post_rows(html, season, lambda t: "CSF" if "ファイナルステージ" in t else "CS1" if "ファーストステージ" in t else None)
    html = fetch(f"https://npb.jp/nippons/{season}/")
    if html:
        text = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        for m in re.finditer(r"【第(\d)戦】\s*(\d{1,2})月(\d{1,2})日[^【]*?(セ・リーグ|パ・リーグ)出場チーム本拠地", text):
            games.append({"d": f"{season}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "stage": "JS", "no": int(m.group(1)),
                          "home": "セ" if m.group(4).startswith("セ") else "パ"})
    if not games:
        print("[ポストシーズン] 読み取れず（前回の値を使用）")
        return prev
    # 前回に結果があって今回取れなかった試合は、結果を引き継ぐ
    old_by = {(p["d"], p["stage"], p["no"]): p for p in prev}
    for g in games:
        o = old_by.get((g["d"], g["stage"], g["no"]))
        if o and o.get("st") == "final" and g.get("st") != "final":
            for k in ("h", "a", "hs", "as", "st"):
                if k in o:
                    g[k] = o[k]
    print(f"[ポストシーズン] {len(games)}試合（CS {sum(g['stage'] != 'JS' for g in games)}・日本シリーズ {sum(g['stage'] == 'JS' for g in games)}）")
    return games


def write_json(data):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


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

    month = datetime.now(JST).strftime("%Y-%m")
    if not all_games:
        if old:
            # オフシーズン（新しい年の日程がまだ出ていない）→ 前のデータをそのまま残す
            print(f"{season}年の試合はまだありません。前のデータを残します")
            if old.get("checked") != month:
                old["checked"] = month  # 月1回ファイルを更新して、GitHubの自動実行が止まらないようにする
                write_json(old)
                print("月1回の生存確認を記録しました")
            return
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
    old_stats = old.get("stats") if old and old.get("season") == season else None
    stats = fetch_stats(season, old_stats)
    prev_order = fetch_prev_order(season, old)
    rosters, roster_date = fetch_rosters(old)
    post = fetch_post(season, old)
    if (old and old_games == all_games and old_stats == stats and old.get("prev_order") == prev_order
            and old.get("checked") == month and old.get("rosters") == rosters and old.get("song_rev") == SONG_REV
            and old.get("post") == post):
        print("変化なし")
        return
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00") if old_games != all_games or not old else old.get("updated"),
        "season": season,
        "games": all_games,
        "stats": stats,
        "prev_order": prev_order,
        "checked": month,
        "rosters": rosters,
        "roster_date": roster_date,
        "song_rev": SONG_REV,
        "post": post,
    }
    write_json(data)
    print(f"保存しました: {len(all_games)}試合")


if __name__ == "__main__":
    main()

"""NPB公式の月別日程ページ（schedule_MM_detail.html）を読み、
セ・リーグ球団が絡む試合を data/latest.json に保存する。
・一度「試合終了」になった試合は、ページ側の反映遅れで「試合前」に戻さない
・読み取りに失敗した月は取り直し、それでもダメなら前回のデータを残す
・ページ上部の当日の「試合終了」欄からも結果を拾う
"""
import json
import os
import re
from urllib.parse import quote
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
PL = {"H", "F", "B", "E", "L", "M"}
TEAM_RE = re.compile("|".join(re.escape(n) for n, _ in TEAMS))
CODE = dict(TEAMS)
ABBR = {"神": "T", "巨": "G", "デ": "DB", "中": "D", "広": "C", "ヤ": "S",
        "ソ": "H", "日": "F", "オ": "B", "楽": "E", "西": "L", "ロ": "M"}

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


def parse_yahoo_team(html, league=None):
    """リーグの順位表（詳細）からチーム成績を取る。「-」の項目は入れない"""
    league = league or CL
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
            if not m or CODE[m.group()] not in league:
                continue
            out[CODE[m.group()]] = {k: cells[head.index(k)] for k in TEAM_KEYS
                                    if k in head and re.search(r"\d", cells[head.index(k)])}
        if len(out) == 6:
            return out
    return None


def fetch_stats(season, old_stats, kind=1):
    """チーム成績と個人ランキング（kind=1 セ・リーグ、2 パ・リーグ）。取れなかった部分は前回の値を残す"""
    league, tag = (CL, "") if kind == 1 else (PL, "パ・")
    st = json.loads(json.dumps(old_stats or {}))
    st["team"] = st.get("team") if isinstance(st.get("team"), dict) else {}
    st.setdefault("leaders", {})
    stamps = []
    html = fetch(f"{YAHOO}/standings/detail/{kind}")
    tbl = parse_yahoo_team(html, league) if html else None
    if tbl:
        for t, row in tbl.items():
            cur = st["team"].get(t)
            cur = cur if isinstance(cur, dict) and "bat" not in cur else {}
            cur.update(row)
            st["team"][t] = cur
        stamps.append(stamp_of(html))
        print(f"[{tag}成績] チーム成績 OK")
    else:
        print(f"[{tag}成績] チーム成績 読み取れず（前回の値を使用）")
    # 個人ランキング：スポナビの成績の更新時刻が前回と同じなら、全項目の取り直しはしない（負担を減らす）
    probe = fetch(f"{YAHOO}/stats/batter?gameKindId={kind}&type=avg")
    probe_stamp = stamp_of(probe) if probe else None
    want = [f"b_{k}" for k in BAT_CATS] + [f"p_{k}" for k in PIT_CATS]
    have_all = all(k in st["leaders"] for k in want)
    if probe_stamp and have_all and st.get("rank_stamp") == list(probe_stamp):
        print(f"[{tag}成績] 個人ランキングは前回から更新なし（取り直さない）")
        stamps.append(probe_stamp)
    else:
        leaders = {k: v for k, v in st["leaders"].items() if k in want}
        for who, pre, cats in (("batter", "b_", BAT_CATS), ("pitcher", "p_", PIT_CATS)):
            for key, label in cats.items():
                html = probe if (who == "batter" and key == "avg") else fetch(f"{YAHOO}/stats/{who}?gameKindId={kind}&type={key}")
                rows = parse_yahoo_rank(html, label) if html else None
                if rows:
                    leaders[pre + key] = rows
                    stamps.append(stamp_of(html))
                else:
                    print(f"[{tag}成績] ランキング {label} 読み取れず（前回の値を使用）")
                if not (who == "batter" and key == "avg"):
                    time.sleep(1)
        st["leaders"] = leaders
        if probe_stamp:
            st["rank_stamp"] = list(probe_stamp)
    stamps = [x for x in stamps if x]
    if stamps:
        y, mo, d, h, mi = max(stamps)
        st["asof"] = f"{mo}/{d} {h}:{mi:02d}"
    st["src"] = "スポーツナビ"
    print(f"[{tag}成績] {st.get('asof')} 更新分 ランキング{len(st['leaders'])}部門")
    return st


def fetch_prev_order(season, old, lg="c"):
    """前年の最終順位（日程タブの球団の並びに使う）。一度取れたら次から取りに行かない"""
    league = CL if lg == "c" else PL
    prev = (old or {}).get("prev_order" if lg == "c" else "prev_order_p")
    if prev and prev.get("season") == season - 1 and len(prev.get("order", [])) == 6:
        return prev
    html = fetch(f"https://npb.jp/bis/{season - 1}/stats/std_{lg}.html")
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            order = []
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                m = TEAM_RE.search(norm(cells[0].get_text(" ", strip=True))) if cells else None
                if m and CODE[m.group()] in league and CODE[m.group()] not in order:
                    order.append(CODE[m.group()])
            if len(order) == 6:
                print(f"[前年順位] {season - 1}年 {'セ' if lg == 'c' else 'パ'}: {order}")
                return {"season": season - 1, "order": order}
    print("[前年順位] 取得できず")
    return prev


ROSTER_CODE = {"T": "t", "G": "g", "DB": "db", "D": "d", "C": "c", "S": "s",
               "H": "h", "F": "f", "B": "b", "E": "e", "L": "l", "M": "m"}
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


def parse_manager(html):
    """NPBの選手一覧ページから、監督の名前を取る（コーチは取らない）"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        role = None
        for tr in table.find_all("tr"):
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            if cells[0] == "No.":
                role = cells[1]
                continue
            if role == "監督" and re.fullmatch(r"\d{1,3}", cells[0]) and cells[1]:
                return {"no": cells[0], "n": re.sub(r"\s+", " ", cells[1])}
    return None


# 個別の応援歌がある選手を調べるページ（名前だけを照合し、歌詞は保存しない）
SONG_SOURCES = {
    "T": ["https://m.hanshintigers.jp/data/march/", "https://www.yakyu-ouen.net/tigers/"],
    "DB": ["https://sp.baystars.co.jp/player_songs/index", "https://www.yakyu-ouen.net/baystars/"],
    "G": ["https://giants-cheeringclub.com/cheeringsong/", "https://www.yakyu-ouen.net/giants/"],
    "D": ["https://www.yakyu-ouen.net/dragons/"],
    "C": ["https://www.carp.co.jp/team/songs", "https://www.yakyu-ouen.net/carp/"],
    "S": ["https://www.yakult-swallows.co.jp/players/song", "https://www.yakyu-ouen.net/swallows/"],
    # パ・リーグ：公式の応援歌ページ＋応援歌まとめサイト（西武は公式にページがないのでまとめサイトだけ）
    "H": ["https://www.softbankhawks.co.jp/team/song/", "https://www.yakyu-ouen.net/hawks/"],
    "F": ["https://www.fighters.co.jp/entertainment/cheer_player/", "https://www.yakyu-ouen.net/fighters/"],
    "B": ["https://www.buffaloes.co.jp/team/playersong.html", "https://www.yakyu-ouen.net/buffaloes/"],
    "E": ["https://www.rakuteneagles.jp/team/rooterssong/", "https://www.yakyu-ouen.net/eagles/"],
    "L": ["https://www.yakyu-ouen.net/lions/"],
    "M": ["https://www.marines.co.jp/fans/supportersong/", "https://www.yakyu-ouen.net/marines/"],
}
# 公式がPDFで配っている球団は、PDFに載っている選手名をここに書いておく（中日：cheersong2026.pdf）
SONG_EXTRA = {
    "D": ["岡林勇希", "田中幹也", "高橋周平", "カリステ", "村松開人", "福永裕基", "大島洋平", "石伊雄太", "大野雄大",
          "ボスラー", "石川昂弥", "根尾昂", "木下拓哉", "宇佐見真吾", "ブライト健太", "土田龍空", "阿部寿樹",
          "加藤匠馬", "上林誠知", "細川成也", "山本泰寛", "鵜飼航丞"],
}
# 公式に応援歌ページがない球団：まとめサイトの球団ページの表から、選手ごとのページ（なければ表の位置）を拾う
SONG_LINK_PAGE = {"L": "https://www.yakyu-ouen.net/lions/"}
SONG_REV = 4  # 判定のしかたを変えたら数字を上げる（上げると時期に関係なく1回やり直す）
# 背番号で並んでいるページ（ヤクルト公式）は背番号でも照合する
SONG_BY_NUMBER = {"https://www.yakult-swallows.co.jp/players/song"}
VARIANT = str.maketrans({"髙": "高", "﨑": "崎", "濵": "浜", "德": "徳", "瀨": "瀬", "邊": "辺", "邉": "辺", "塚": "塚", "・": "", "＝": "", "=": ""})


def squash(s):
    return re.sub(r"\s+", "", norm(s)).translate(VARIANT)


def song_links(url, html):
    """まとめサイトの球団ページの選手応援歌の表から [(背番号, 表の名前, 選手ページのURL or None)] を取る"""
    out = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue
        no = squash(tds[0].get_text())
        name = squash(tds[1].get_text())
        if not re.fullmatch(r"\d{1,3}", no) or len(name) < 2:
            continue
        a = tds[1].find("a", href=True)
        out.append((no, name, a["href"] if a and a["href"].startswith("https://") else None))
    return out


def mark_songs(t, rows):
    """応援歌ページに名前（または背番号）が出てくる選手に song=True を付ける。どのページも読めなければ None
    SONG_LINK_PAGE の球団は、応援歌がある選手に su（タップしたときに開くページ）も付ける"""
    texts, numbers, ok = [squash(" ".join(SONG_EXTRA.get(t, [])))], set(), bool(SONG_EXTRA.get(t))
    links = []
    for url in SONG_SOURCES.get(t, []):
        html = fetch(url)
        if not html:
            continue
        if SONG_LINK_PAGE.get(t) == url:
            links = song_links(url, html)
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
        if r["song"] and links:
            # 名前が含まれる行を優先、なければ背番号が同じ行（表の名前が短い「ネビン」など）
            hit = next((x for x in links if x[1] in name or name in x[1]), None) or next((x for x in links if x[0] == r["no"]), None)
            if hit:
                r["su"] = hit[2] or f"{SONG_LINK_PAGE[t]}#:~:text={quote(hit[1])}"
    return n


FPOS_GROUP = {"一塁手": "内", "二塁手": "内", "三塁手": "内", "遊撃手": "内", "外野手": "外", "捕手": "捕", "投手": "投"}


# ---------- オフの戦力外・引退（各球団の公式サイトの発表から） ----------
# 各球団のニュース一覧から「来季の選手契約について」「現役引退」などの発表を探し、
# 記事の中に出てくる名簿の選手名と照らし合わせる（記事の作りは球団ごとに違うので、名前の照合で拾う）
# パ・リーグ6球団は同じ作りのサイトで、ニュース一覧は /news/list/（チームのニュースは /news/list/0/00000001/）
# （/news/announce/retire/ は「公示 任意引退・自由契約」の去年までの一覧なので使わない）
_PA = lambda base: [base + "/news/list/0/00000001/", base + "/news/list/"]
OFF_LISTS = {
    "G": ["https://www.giants.jp/news/", "https://www.giants.jp/news/list/", "https://www.giants.jp/"],
    "T": ["https://hanshintigers.jp/news/topics/", "https://hanshintigers.jp/news/"],
    "DB": ["https://www.baystars.co.jp/news/"],
    "C": ["https://www.carp.co.jp/news", "https://www.hub.carp.co.jp/news/news26/index.html", "https://www.carp.co.jp/"],
    "S": ["https://www.yakult-swallows.co.jp/news/"],
    "D": ["https://dragons.jp/news/", "https://dragons.jp/"],
    "H": _PA("https://www.softbankhawks.co.jp"),
    "F": _PA("https://www.fighters.co.jp"),
    "B": _PA("https://www.buffaloes.co.jp"),
    "E": _PA("https://www.rakuteneagles.jp"),
    "L": _PA("https://www.seibulions.jp"),
    "M": _PA("https://www.marines.co.jp"),
}
# 見出しで拾う発表：「来季の選手契約について」「選手契約に関して」「○○選手 現役引退」「○○選手について」（阪神の引退の発表の見出し）など
OFF_TITLE = re.compile(r"契約|退団|戦力外|自由契約|選手について|選手に関して")
OFF_TITLE_MGR = re.compile(r"監督.{0,20}(辞任|退任|解任)|(辞任|退任|解任).{0,20}監督")   # 監督の辞任・退任
OFF_TITLE_NG = re.compile(r"更改|合意|締結|獲得|入団|加入|新外国人|育成選手契約を結ぶ|スポンサー|パートナー|協定|提携|ファンクラブ|チケット|グッズ|放送|配信|中継|販売|募集|キャンプ|約款|規約|観戦|公示|登録|抹消|出演|誕生日|登場曲|達成|記録|受賞|選出|手術|負傷|故障|けが|怪我|復帰|結婚|入籍|出産")
OFF_URL_NG = re.compile(r"/announce/|/stadium/|/ticket|/fanclub|/shop|/goods|/company/|/recruit")
# 本文の終わりの目印（ここから後ろは「関連ニュース」などなので見ない）
OFF_END = re.compile(r"関連ニュース|関連記事|一覧へ戻る|もっと見る|RELATEDNEWS|RelatedNews|おすすめ記事|最新ニュース|ニュース一覧")
# 今季より前に引退を表明していた選手など、ニュース一覧の最初のページに出てこない発表（見つけたら公式の発表で上書き・補う）
OFF_SEED = [
    {"t": "L", "n": "栗山 巧", "kind": "retire", "date": "2025-11-24"},
    {"t": "DB", "n": "ビシエド", "kind": "retire", "date": "2026-05-25"},
    {"t": "H", "n": "中村 晃", "kind": "retire", "date": "2026-07-03", "url": "https://www.softbankhawks.co.jp/news/detail/202601046312.html"},
    {"t": "M", "n": "角中 勝也", "kind": "retire", "date": "2026-07-20"},
    {"t": "B", "n": "平野 佳寿", "kind": "retire", "date": "2026-08-09"},
    {"t": "S", "n": "石川 雅規", "kind": "retire", "date": "2026-09-02"},
    {"t": "M", "n": "唐川 侑己", "kind": "retire", "date": "2026-09-04"},
    {"t": "F", "n": "中島 卓也", "kind": "retire", "date": "2026-09-11"},
    {"t": "E", "n": "辛島 航", "kind": "retire", "date": "2026-09-23", "url": "https://www.rakuteneagles.jp/news/detail/202601195444.html"},
    {"t": "B", "n": "山田 修義", "kind": "retire", "date": "2026-09-24"},
    {"t": "B", "n": "西野 真弘", "kind": "retire", "date": "2026-09-24"},
    {"t": "T", "n": "西 勇輝", "kind": "retire", "date": "2026-09-25", "url": "https://hanshintigers.jp/news/topics/info_11241.html"},
    {"t": "T", "n": "岩貞 祐太", "kind": "retire", "date": "2026-09-28"},
    {"t": "D", "n": "井上 一樹", "kind": "mgr", "role": "監督", "date": "2026-09-29"},
]
OFF_KEY = re.compile(r"結ばない|行わない|締結しない|更新しない|結ばず|行わず|戦力外|自由契約|退団|引退|辞任|退任|解任")
OFF_JUNK = re.compile(r"side|related|recommend|ranking|breadcrumb|pickup|banner|share|sns|pager|pagination|footer|header|menu|gnav|global|topics-list|news-list|other", re.I)
OFF_DATE = re.compile(r"(20\d\d)\s*[./年-]\s*(\d{1,2})\s*[./月-]\s*(\d{1,2})")
OFF_EVERY = 3 * 3600   # 球団サイトを見に行く間隔（秒）。15分ごとの自動更新のたびには見に行かない


def off_links(list_url, html):
    """ニュース一覧のページから、戦力外・引退の発表らしい記事のリンク（URL, 見出し）を取る"""
    from urllib.parse import urljoin, urlparse
    host = urlparse(list_url).netloc.replace("www.", "")
    out, seen = [], set()
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        title = re.sub(r"\s+", " ", norm(a.get_text(" "))).strip()
        if not title or len(title) > 120:
            continue
        retire = "引退" in title and not re.search(r"公示|グッズ|チケット|販売", title)
        mgr = bool(OFF_TITLE_MGR.search(title)) and not re.search(r"公示|グッズ|チケット|販売|二軍|ファーム", title)
        if not retire and not mgr and not (OFF_TITLE.search(title) and not OFF_TITLE_NG.search(title)):
            continue
        url = urljoin(list_url, a["href"]).split("#")[0]
        if urlparse(url).netloc.replace("www.", "") != host or url in seen or url.rstrip("/") == list_url.rstrip("/") or OFF_URL_NG.search(url):
            continue
        seen.add(url)
        out.append((url, title))
    return out


def off_article(t, url, list_title, html, roster, season, manager=None):
    """1つの発表記事から、戦力外（来季契約せず）・育成再契約の打診・現役引退の選手を取り出す"""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.find("h1") or soup.find("title")
    title = re.sub(r"\s+", " ", norm(h.get_text(" "))).strip() if h else ""
    title = title if len(title) >= 4 else list_title
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        cls = " ".join(tag.get("class") or []) + " " + str(tag.get("id") or "")
        if OFF_JUNK.search(cls) and tag.name not in ("body", "html", "main", "article"):
            tag.decompose()
    text = norm(soup.get_text("\n"))
    retire_page = "引退" in list_title or "引退" in title
    # 発表日：本文の先頭あたり（見出しの近く）の日付。今季の9月より前なら去年などの古い記事なので使わない
    # 見つからなければ空（見つけた日を使う）。本文の途中の日付（生年月日など）は見ない
    date = ""
    head = text[:max(400, text.find(title[:10]) + 400 if title[:10] and title[:10] in text else 400)]
    for m in OFF_DATE.finditer(head):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < season - 1:
            continue
        old = y == season - 1 or (y == season and mo < 9)
        if retire_page and old and (y == season or mo >= 10):
            old = False   # 引退の表明は今季の途中や去年の秋のこともある（今の名簿にいる選手だけ拾うので、去年の引退選手は出ない）
        if old:
            return [], "古い記事"
        date = f"{y:04d}-{mo:02d}-{d:02d}"
        break
    S = squash(text)
    k0 = OFF_KEY.search(S)
    if not k0:
        return [], "戦力外・引退の文言なし"
    # 本文の終わり（「関連ニュース」「一覧へ戻る」など）から後ろは見ない
    e = OFF_END.search(S, k0.end())
    if e:
        S = S[:e.start()]
    keys = [(m.start(), m.group(0)) for m in OFF_KEY.finditer(S)]
    mkeys = keys   # 監督用（辞任・退任・解任も含む）
    keys = [kk for kk in keys if kk[1] not in ("辞任", "退任", "解任")]   # 選手用
    # 名前を探す範囲：「結ばない」「引退」などの言葉の前後（記事の横の一覧などを拾わないように）
    wins = [(max(0, p - 700), p + 700) for p, _ in keys]
    if not keys:
        roster = []   # 監督の辞任などの発表だけで、選手の話はない
    sq_title = squash(title)
    # 「育成選手契約を打診」の文（改行・句点で区切った1文ずつ。名前の並びと混ざらないよう、詰める前の本文で区切る）
    offer_sents = [squash(x) for x in re.split(r"[。\n]", text) if "育成" in x and re.search(r"打診|再契約|提示|予定", x)]
    items = []
    for r in roster:
        k = squash(r.get("n", ""))
        if len(k) < 2:
            continue
        pos = [m.start() for m in re.finditer(re.escape(k), S)]
        if len(k) == 2:   # 2文字の名前は「○○投手」「○○選手」のように続くときだけ
            pos = [p for p in pos if re.match(r"(投手|捕手|内野手|外野手|選手)", S[p + 2:p + 6])]
        in_title = k in sq_title
        if not in_title and not any(a <= p <= b for p in pos for a, b in wins):
            continue
        # 種類：名前にいちばん近い言葉が「引退」なら引退、「結ばない・行わない・戦力外」などなら戦力外（見出しに引退があれば引退）
        near = min(keys, key=lambda kk: min((abs(kk[0] - p) for p in pos), default=10 ** 9))[1] if pos else ""
        if retire_page and near in ("", "引退"):
            kind = "retire"
        elif near == "引退":
            kind = "retire"
        elif any(k in x for x in offer_sents) or re.match(r".{0,12}育成.{0,12}(打診|再契約)", S[pos[0] + len(k):pos[0] + len(k) + 30] if pos else ""):
            kind = "offer"
        else:
            kind = "cut"
        items.append({"t": t, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": kind,
                      "date": date, "url": url, "title": title[:80]})
    # 監督：名前が「辞任・退任・解任」の近く（または見出し）に出てくれば、監督の退任（二軍監督・コーチの話は除く）
    if manager and manager.get("n"):
        k = squash(manager["n"])
        pos = [m.start() for m in re.finditer(re.escape(k), S)]
        near = [kk for p in pos for kk in mkeys if kk[1] in ("辞任", "退任", "解任") and abs(kk[0] - p) < 120]
        if (near or (k in sq_title and OFF_TITLE_MGR.search(title))) and not re.search(k + r".{0,4}(二軍|ファーム)", S):
            items.append({"t": t, "n": manager["n"], "no": manager.get("no", ""), "dev": False, "kind": "mgr", "role": "監督",
                          "date": date, "url": url, "title": title[:80]})
    return items, ""


def fetch_offseason(season, old, rosters, force=False, any_month=False):
    """各球団の公式サイトから、今オフの戦力外・引退の発表を集める（前回までに見つけたものは残す）"""
    prev = (old or {}).get("offseason") or {}
    if prev.get("season") != season:
        prev = {}
    now = datetime.now(JST)
    if now.month < 9 and not any_month:   # 9月〜12月だけ（手動で実行しても、この時期以外は見に行かない）
        return prev or None
    last = prev.get("checked_at")
    if last and not force:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < OFF_EVERY:
                return prev
        except ValueError:
            pass
    # 前回までに見つけたもの（「公示」の一覧など、今は使わないページから拾ったものは消す）
    items = {(x["t"], x["n"]): x for x in prev.get("items", []) if not OFF_URL_NG.search(x.get("url") or "")}
    # 各球団の監督の名前（NPBの選手一覧ページから、1日1回）
    managers = dict(prev.get("managers") or {})
    if prev.get("managers_date") != now.strftime("%Y-%m-%d") or not managers:
        for t, code in ROSTER_CODE.items():
            html = fetch(f"https://npb.jp/bis/teams/rst_{code}.html")
            m = parse_manager(html) if html else None
            if m:
                managers[t] = m
        print(f"[監督] {len(managers)}球団: " + " ".join(f"{t}:{m['n']}" for t, m in managers.items()))
    seen = {u: d for u, d in (prev.get("seen") or {}).items() if not OFF_URL_NG.search(u)}
    teams = {}
    today = now.strftime("%Y-%m-%d")
    for t, urls in OFF_LISTS.items():
        st = {"lists": {}, "links": 0, "new": 0, "found": 0, "err": ""}
        roster = rosters.get(t) or []
        cands, got = [], set()
        # 候補の一覧ページを全部見て、発表らしい記事をまとめる（球団ごとの状況も残す：開けたか・何件あったか）
        for u in urls:
            html = fetch(u)
            if not html:
                st["lists"][u] = "開けない"
                continue
            found_links = off_links(u, html)
            st["lists"][u] = len(found_links)
            for url, ltitle in found_links:
                if url not in got:
                    got.add(url)
                    cands.append((url, ltitle))
        st["links"] = len(cands)
        if not any(isinstance(v, int) for v in st["lists"].values()):
            st["err"] = "ニュース一覧を開けない"
        for url, ltitle in cands[:15]:
            if url in seen:
                continue
            html = fetch(url)
            time.sleep(1)
            if not html:
                continue
            found, why = off_article(t, url, ltitle, html, roster, season, managers.get(t))
            seen[url] = today
            st["new"] += 1
            for it in found:
                it["date"] = it["date"] or today
                old_it = items.get((t, it["n"]))
                # 同じ選手の発表が2つあるとき（戦力外→引退など）は、新しい方
                if not old_it or it["date"] >= old_it.get("date", ""):
                    items[(t, it["n"])] = it
                st["found"] += 1
            print(f"  [戦力外・引退 {t}] {ltitle[:40]} → {len(found)}人{('（' + why + '）') if why else ''}")
        teams[t] = st
    # 補う分：OFF_SEED と data/offseason_fix.json（{"exclude": [{"t","n"}], "add": [{...}]}）
    # すでに公式の発表から見つけている選手は、発表日・種類だけ補う（リンクは公式の発表のまま）
    def patch(x):
        if not (x.get("t") and x.get("n") and x.get("kind")):
            return
        key = (x["t"], x["n"])
        ro = next((r for r in rosters.get(x["t"]) or [] if squash(r.get("n", "")) == squash(x["n"])), None)
        if key in items:
            for f in ("date", "kind"):
                if x.get(f):
                    items[key][f] = x[f]
            if x.get("url") and not items[key].get("url"):
                items[key]["url"] = x["url"]
        else:
            items[key] = {"t": x["t"], "n": x["n"], "no": (ro or {}).get("no", "") or (managers.get(x["t"]) or {}).get("no", "") if x["kind"] == "mgr" else (ro or {}).get("no", ""),
                          "dev": bool((ro or {}).get("dev")), "kind": x["kind"], "date": x.get("date") or today, "url": x.get("url", ""), "title": x.get("title", "")}
            if x.get("role"):
                items[key]["role"] = x["role"]
    for x in OFF_SEED:
        patch(x)
    fix_path = os.path.join(os.path.dirname(OUT), "offseason_fix.json")
    if os.path.exists(fix_path):
        try:
            with open(fix_path, encoding="utf-8") as f:
                fix = json.load(f)
            for x in fix.get("exclude", []):
                items.pop((x.get("t"), x.get("n")), None)
            for x in fix.get("add", []):
                patch(x)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  offseason_fix.json を読めません: {e}")
    # 見た記事の記録は60日分だけ残す
    lim = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    seen = {u: d for u, d in seen.items() if d >= lim}
    out = sorted(items.values(), key=lambda x: (x["date"], x["t"], x["n"]), reverse=True)
    print(f"[戦力外・引退] {len(out)}人（" + " ".join(f"{t}:{v['found']}" for t, v in teams.items()) + "）")
    return {"season": season, "checked_at": now.isoformat(timespec="seconds"), "items": out, "teams": teams, "seen": seen,
            "managers": managers, "managers_date": now.strftime("%Y-%m-%d") if managers else prev.get("managers_date")}


def fetch_fpos(season, old):
    """各球団の個人守備成績から、選手ごとに今季守ったポジション（投・捕・内・外）と試合数を取る（1日1回）"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    prev = (old or {}).get("fpos") or {}
    if prev.get("date") == today and prev.get("season") == season and len(prev.get("teams", {})) == len(ROSTER_CODE):
        return prev
    teams = dict(prev.get("teams", {})) if prev.get("season") == season else {}
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/{season}/stats/idf1_{code}.html")
        if not html:
            print(f"[守備位置] {t} 読み取れず（前回の値を使用）")
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            h = table.find_previous(["h5", "h4", "h3"])
            grp = FPOS_GROUP.get(clean(h.get_text()) if h else "")
            if not grp:
                continue
            for tr in table.find_all("tr")[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < 2 or not c[1].isdigit():
                    continue
                name = re.sub(r"[\s*＊]", "", c[0])
                if not name:
                    continue
                d = out.setdefault(name, {})
                d[grp] = d.get(grp, 0) + int(c[1])
        if out:
            teams[t] = out
            time.sleep(1)
        else:
            print(f"[守備位置] {t} 表が見つからず（前回の値を使用）")
    print(f"[守備位置] {sum(len(v) for v in teams.values())}人（{len(teams)}球団）")
    # 投手の役割（先発・中継ぎ・抑え）：個人投手成績の「1登板あたりの投球回」と「セーブ・ホールド」から判定
    roles = dict(prev.get("roles", {})) if prev.get("season") == season else {}
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/{season}/stats/idp1_{code}.html")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            head = [clean(x.get_text()) for x in rows[0].find_all(["th", "td"])]
            if "登板" not in head or "投球回" not in head or "セーブ" not in head:
                continue
            ix = {k: head.index(k) for k in ("登板", "投球回", "セーブ", "ホールド") if k in head}
            for tr in rows[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < len(head):
                    continue
                name = re.sub(r"[\s*＊]", "", c[0])
                try:
                    g = int(c[ix["登板"]])
                    ipt = c[ix["投球回"]].split(".")
                    ip = int(ipt[0] or 0) + (int(ipt[1]) / 3 if len(ipt) > 1 and ipt[1] else 0)
                    sv = int(c[ix["セーブ"]] or 0)
                    hd = int(c[ix["ホールド"]] or 0) if "ホールド" in ix else 0
                except (ValueError, KeyError):
                    continue
                if not name or g <= 0:
                    continue
                per = ip / g
                if sv >= 10:
                    r = ["抑"] + (["中"] if hd >= 5 else [])
                elif per >= 4:
                    r = ["先"]
                elif per >= 3.25:
                    r = ["先", "中"]
                elif per >= 2.5:
                    r = ["中", "先"]
                else:
                    r = ["中"] + (["抑"] if sv >= 3 else [])
                out[name] = r
        if out:
            roles[t] = out
            time.sleep(1)
    # スポナビの球団別投手成績に「登板」「先発」があるので、取れた球団はそれで上書きする
    # （先発＝先発した試合数、中継ぎ＝登板−先発。セーブ10以上の投手の救援は「抑」）
    YID = {"T": 5, "G": 1, "DB": 3, "D": 4, "C": 6, "S": 2, "H": 12, "F": 8, "B": 11, "E": 376, "L": 7, "M": 9}
    for t, yid in YID.items():
        html = fetch(f"https://baseball.yahoo.co.jp/npb/teams/{yid}/pitchingstats")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            head = [re.sub(r"\s+", "", clean(x.get_text())) for x in rows[0].find_all(["th", "td"])]
            if "先発" not in head or "登板" not in head or "選手名" not in head:
                continue
            ix = {k: head.index(k) for k in ("選手名", "登板", "先発", "セーブ") if k in head}
            for tr in rows[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < len(head):
                    continue
                name = re.sub(r"[\s*＊]", "", c[ix["選手名"]])
                try:
                    g, gs = int(c[ix["登板"]]), int(c[ix["先発"]])
                    sv = int(c[ix["セーブ"]]) if "セーブ" in ix else 0
                except ValueError:
                    continue  # 一軍登板なし（「-」）
                if not name or g <= 0:
                    continue
                rel = g - gs
                d = {}
                if gs > 0:
                    d["先"] = gs
                if rel > 0:
                    d["抑" if sv >= 10 else "中"] = rel
                out[name] = d
            break
        if out:
            roles[t] = out
            time.sleep(1)
    print(f"[投手の役割] {sum(len(v) for v in roles.values())}人（{len(roles)}球団）")
    return {"season": season, "date": today, "teams": teams, "roles": roles}


def fetch_rosters(old):
    """各球団の選手一覧（1日1回だけ取りに行く）"""
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    rosters = dict((old or {}).get("rosters") or {})
    have_all = (len(rosters) == len(ROSTER_CODE) and all(any("song" in r for r in v) for v in rosters.values())
                and (old or {}).get("song_rev") == SONG_REV)
    # 更新は3月〜7月だけ（支配下登録の期限が7月末のため）。まだ全球団そろっていなければ時期に関係なく取る
    if have_all and not (3 <= now.month <= 7):
        return rosters, (old or {}).get("roster_date")
    # 取りに行くのは1日1回まで（応援歌ページが読めない球団があっても、何度も取りに行かない）
    if len(rosters) == len(ROSTER_CODE) and (old or {}).get("roster_date") == today and (old or {}).get("song_rev") == SONG_REV:
        return rosters, today
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/teams/rst_{code}.html")
        rows = parse_roster(html) if html else []
        if len(rows) >= 20:
            n = mark_songs(t, rows)
            if n is None and t in rosters:  # 応援歌ページが読めなかったときは前回の判定を引き継ぐ
                prev = {(r["no"], r["n"]): r for r in rosters[t]}
                for r in rows:
                    o = prev.get((r["no"], r["n"]))
                    if o and o.get("song") is not None:
                        r["song"] = o["song"]
                        if o.get("su"):
                            r["su"] = o["su"]
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
    stage = lambda t: "CSF" if "ファイナルステージ" in t else "CS1" if "ファーストステージ" in t else None
    html = fetch(f"https://npb.jp/games/{season}/schedule_climax_cl.html")
    if html:
        games += parse_post_rows(html, season, stage)
    html = fetch(f"https://npb.jp/games/{season}/schedule_climax_pl.html")
    if html:
        games += [dict(g, lg="P") for g in parse_post_rows(html, season, stage)]
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
    old_by = {(p["d"], p["stage"], p["no"], p.get("lg", "C")): p for p in prev}
    for g in games:
        o = old_by.get((g["d"], g["stage"], g["no"], g.get("lg", "C")))
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

    # 試合結果以外の取り込みは、1つが失敗しても前回の値を使って続ける（試合結果の更新まで止めない）
    def safe(label, fn, fallback):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] 取り込みでエラー（前回の値を使います）: {type(e).__name__}: {e}")
            return fallback

    stats = safe("成績", lambda: fetch_stats(season, old_stats), old_stats)
    old_stats_p = old.get("stats_p") if old and old.get("season") == season else None
    stats_p = safe("パ・成績", lambda: fetch_stats(season, old_stats_p, 2), old_stats_p)
    prev_order = safe("前年の順位", lambda: fetch_prev_order(season, old), (old or {}).get("prev_order"))
    prev_order_p = safe("パ・前年の順位", lambda: fetch_prev_order(season, old, "p"), (old or {}).get("prev_order_p"))
    rosters, roster_date = safe("選手一覧", lambda: fetch_rosters(old), ((old or {}).get("rosters") or {}, (old or {}).get("roster_date")))
    post = safe("ポストシーズン", lambda: fetch_post(season, old), (old or {}).get("post"))
    fpos = safe("守備位置", lambda: fetch_fpos(season, old), (old or {}).get("fpos"))
    # Actions の画面で「Run workflow」を押したとき（手動で実行したとき）は、3時間の間隔を待たずに球団サイトを見に行く
    off_force = os.environ.get("OFF_FORCE") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    offseason = safe("戦力外・引退", lambda: fetch_offseason(season, old, rosters, off_force, os.environ.get("OFF_FORCE") == "1"), (old or {}).get("offseason"))
    if (old and old_games == all_games and old_stats == stats and old.get("prev_order") == prev_order
            and old_stats_p == stats_p and old.get("prev_order_p") == prev_order_p
            and old.get("checked") == month and old.get("rosters") == rosters and old.get("song_rev") == SONG_REV
            and old.get("post") == post and old.get("fpos") == fpos
            and old.get("offseason") == offseason):
        print("変化なし")
        return
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00") if old_games != all_games or not old else old.get("updated"),
        "season": season,
        "games": all_games,
        "stats": stats,
        "stats_p": stats_p,
        "prev_order": prev_order,
        "prev_order_p": prev_order_p,
        "checked": month,
        "rosters": rosters,
        "roster_date": roster_date,
        "song_rev": SONG_REV,
        "post": post,
        "fpos": fpos,
        "offseason": offseason,
    }
    write_json(data)
    print(f"保存しました: {len(all_games)}試合")


if __name__ == "__main__":
    main()

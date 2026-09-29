"""
J SPORTS ペナントレース：サイト全体の自動検査

  python tests/site_check.py            # すべて検査（index.html を直接開く）

検査すること（テーマ2つ × 画面幅2つ × 状態いろいろ × 全タブ）
  1. 画面を開いたり操作したりしてエラーが出ないか
  2. 表や画面が横にはみ出していないか
  3. 文字が背景に溶けて読めなくなっていないか（半透明の背景も重ねて計算）
  4. 大事な部品がちゃんと表示されているか（順位表・マジック・スコア・一球速報など）
  5. データの差し替え（試合以外だけ変わったとき）が反映されるか

問題があれば一覧を出して、終了コード 1 で終わる（GitHub Actions で赤い×になる）。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = (ROOT / "index.html").as_uri()
LIVE = "https://live.example/"
PROBLEMS = []


def bad(msg):
    PROBLEMS.append(msg)
    print("  ✕", msg)
    # GitHub Actions で動いているときは、Actions の画面の「Annotations」にも出す（ログを開かなくても原因が見える）
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=サイト検査::" + str(msg).replace("\n", " ").replace("%", "%25"))


# ---------- 中継プログラムの代わり（試合中の一球速報・打順・投手成績など） ----------
DETAIL = {
    "line": {"innings": [str(i) for i in range(1, 10)],
             "away": {"name": "ヤクルト", "inn": ["0", "0", "1", "0", "0", "", "", "", ""], "r": "1", "h": "4", "e": "0"},
             "home": {"name": "巨人", "inn": ["2", "0", "0", "0", "0", "", "", "", ""], "r": "2", "h": "6", "e": "1"}},
    "flows": [{"half": "1回裏", "runs": 2, "box": True, "steps": [
        {"order": "1番", "name": "丸 佳浩", "sit": None, "res": "左安"},
        {"order": "2番", "name": "泉口 友汰", "sit": None, "res": "空三振"},
        {"order": "3番", "name": "吉川 尚輝", "sit": None, "res": "四球"},
        {"order": "4番", "name": "岡本 和真", "sit": None, "res": "左本"}]}],
    "plays": [{"half": "1回裏", "order": "", "name": "巨人の攻撃", "kind": "2点", "score": "ヤ 0-2 巨"}],
    "cur": {"half": "6回表", "steps": [{"order": "1番", "name": "丸山 和郁", "res": "四球"}, {"order": "2番", "name": "長岡 秀樹", "res": "右安"}]},
    "lineups": [[{"order": 1, "pos": "中", "starter": True, "name": "丸山 和郁", "avg": ".262", "results": ["二ゴロ", "中安", "四球"]},
                 {"order": 2, "pos": "遊", "starter": True, "name": "長岡 秀樹", "avg": ".281", "results": ["見三振", "右2", "投犠打", "遊失"]},
                 {"order": 2, "pos": "打", "starter": False, "name": "北村 恵吾", "avg": ".210", "results": []}],
                [{"order": 1, "pos": "中", "starter": True, "name": "丸 佳浩", "avg": ".270", "results": ["左安", "右中本"]}]],
    "pitchers": [[{"name": "高橋 奎二", "dec": "", "era": "3.12", "ip": "5", "np": "92", "h": "5", "hr": "1", "so": "6", "bb": "2", "r": "2", "er": "2"}],
                 [{"name": "山﨑 伊織", "dec": "勝", "era": "2.41", "ip": "5.1", "np": "88", "h": "4", "hr": "0", "so": "6", "bb": "2", "r": "1", "er": "1"}]],
    "over": False,
}
PITCH = {"half": "6回表", "attack": "ヤクルト", "b": 2, "s": 1, "o": 1, "bases": {}, "runners": {},
         "batter": {"name": "オスナ", "no": "13", "hand": "右打", "avg": ".271", "game": {"ab": "2", "hit": "1", "rbi": "1", "hr": "0", "bb": "0", "results": ["中安", "遊ゴロ"]}},
         "pitcher": {"name": "山﨑 伊織", "no": "19", "hand": "右投", "era": "2.41", "game": {"ip": "5.1", "np": "88", "bf": "24", "h": "4", "so": "6", "bb": "2", "r": "1"}},
         "next": "エンカーナシオン",  # 長い名前でも切れないかを見るため
         "pitches": [{"n": 1, "type": "ストレート", "speed": "148km/h", "res": "ボール"}, {"n": 2, "type": "フォーク", "speed": "136km/h", "res": "空振り"},
                     {"n": 3, "type": "スライダー", "speed": "131km/h", "res": "ファウル"}]}


GAME = {"d": "2026-09-27", "h": "G", "a": "S", "h2": "DB", "a2": "C"}


async def route_live(route):
    u = route.request.url
    if "pitch=" in u:
        body = PITCH
    elif "game=" in u:
        body = DETAIL
    elif "stats=team" in u:
        body = {}
    elif "rank=" in u:
        body = {}
    elif "pstats=" in u:
        body = {"asof": "9/26", "bat": {"丸山和郁": {"試合": "100", "打席": "350", "打率": ".262", "本塁打": "3", "打点": "25", "安打": "80", "出塁率": ".330", "長打率": ".350"}},
                "pit": {"山﨑伊織": {"登板": "24", "勝利": "10", "敗北": "5", "セーブ": "0", "投球回": "150.1", "三振": "120", "四球": "30", "安打": "120", "防御率": "2.41"}}}
    else:
        body = {"games": [{"d": GAME["d"], "h": GAME["h"], "a": GAME["a"], "st": "live", "hs": 2, "as": 1, "inn": "6回表"}]
                + ([{"d": GAME["d"], "h": GAME["h2"], "a": GAME["a2"], "st": "final", "hs": 3, "as": 5}] if GAME.get("h2") else [])}
    await route.fulfill(status=200, headers={"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}, body=json.dumps(body))


# ---------- 画面の中を調べる（ブラウザ側で動かす） ----------
CHECK_JS = r"""
(tab) => {
  const out = { overflow: [], contrast: [] };
  const view = document.getElementById('v-' + tab);
  // はみ出し：表・ページ
  document.querySelectorAll('#v-' + tab + ' .ywrap').forEach(wr => {
    const tb = wr.querySelector('table'); if (!tb || !wr.offsetParent) return;
    const a = wr.getBoundingClientRect(), c = tb.getBoundingClientRect();
    if (c.right > a.right + 1) out.overflow.push((tb.id || tb.className) + ' が ' + Math.round(c.right - a.right) + 'px はみ出し');
  });
  if (document.documentElement.scrollWidth > innerWidth + 1) out.overflow.push('ページ全体が横にはみ出し ' + document.documentElement.scrollWidth + 'px');
  // 文字の読みやすさ：背景を外側から重ねて実際の色を出し、文字色との明るさの比を見る
  const parse = c => {
    if (!c) return null;
    let m = c.match(/^color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/);
    if (m) return [m[1] * 255, m[2] * 255, m[3] * 255, m[4] === undefined ? 1 : +m[4]];
    m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
    return null;
  };
  const lin = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
  const L = c => .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]);
  const pawa = document.documentElement.classList.contains('theme-pawa');
  const base = pawa ? [205, 235, 255, 1] : [5, 5, 6, 1];
  const bgAt = el => {
    const chain = []; for (let e = el; e && e !== document.documentElement; e = e.parentElement) chain.unshift(e);
    let col = base.slice(0, 3);
    for (const e of chain) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') {
        // グラデーション：一番上に敷いた色を代表として使う（半透明なら重ねる）
        const m = cs.backgroundImage.match(/(rgba?\([^)]+\)|color\(srgb[^)]+\))/);
        const g = m ? parse(m[1]) : null;
        if (g && g[3] > .6) col = [0, 1, 2].map(i => g[i] * g[3] + col[i] * (1 - g[3]));
        else if (!g) return null;
      }
      const b = parse(cs.backgroundColor);
      if (b && b[3] > 0) col = [0, 1, 2].map(i => b[i] * b[3] + col[i] * (1 - b[3]));
      if (+cs.opacity < .99 && e !== el) {} // 親の半透明は文字にも効くので比較には影響しない
    }
    return col;
  };
  view.querySelectorAll('*').forEach(el => {
    if (!el.offsetParent) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.textShadow !== 'none') return;
    if (el.closest('.wm, .twm')) return; // 背番号の透かしはわざと薄くしている
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    if (op < .5) return; // 参考表示（広島など）はわざと薄くしている
    const fg = parse(cs.color); if (!fg) return;
    const bg = bgAt(el); if (!bg) return;
    const f = [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3]));
    const a = L(f), b = L(bg), ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    if (ratio < 1.6) out.contrast.push(`${el.tagName.toLowerCase()}.${el.className} 「${el.textContent.trim().slice(0, 10)}」 比${ratio.toFixed(2)}`);
  });
  out.contrast = [...new Set(out.contrast)].slice(0, 12);
  return out;
}
"""

TABS = ["magic", "game", "cal", "std", "stats", "song", "off"]   # off：戦力外・引退（オフだけ出るタブ）


async def open_page(browser, width, theme, me="S", touch=False):
    pg = await browser.new_page(viewport={"width": width, "height": 844}, has_touch=touch)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.add_init_script(f"localStorage.setItem('me','{me}'); localStorage.setItem('theme','{theme}'); localStorage.setItem('songTeam','T'); localStorage.setItem('league','C')")
    await pg.route(LIVE + "**", route_live)
    await pg.route("https://api.open-meteo.com/**", lambda r: r.fulfill(status=500, body="x"))
    await pg.goto(URL)
    await pg.wait_for_timeout(700)
    # 検査に使う日：データの中で、対象球団どうしの試合がある一番新しい日（来季以降も同じ検査が使えるように）
    pick = await pg.evaluate("""(() => {
      const T = g => TEAM[g.h] && TEAM[g.a];
      const days = [...new Set(DATA.games.filter(g => T(g) && g.st !== 'canc').map(g => g.d))].sort().reverse();
      for (const d of days) { const gs = DATA.games.filter(g => g.d === d && T(g) && g.st !== 'canc'); if (gs.length) return { d, gs: gs.map(g => [g.h, g.a]) }; }
      return null; })()""")
    if pick:
        GAME.update({"d": pick["d"], "h": pick["gs"][0][0], "a": pick["gs"][0][1]})
        if len(pick["gs"]) > 1:
            GAME.update({"h2": pick["gs"][1][0], "a2": pick["gs"][1][1]})
        else:
            GAME.pop("h2", None)
    y, m, d = GAME["d"].split("-")
    await pg.evaluate(f"CONFIG.liveApi='{LIVE}'; jst=()=>({{y:{int(y)},m:{int(m)},d:{int(d)},iso:'{GAME['d']}'}}); liveWanted=()=>true; autoGame=false;")
    return pg, errs


async def scan(pg, label):
    for t in TABS:
        await pg.evaluate(f"setTab('{t}'); window.scrollTo(0,0)")
        await pg.wait_for_timeout(120)
        r = await pg.evaluate(CHECK_JS, t)
        for o in r["overflow"]:
            bad(f"{label} {t}: {o}")
        for c in r["contrast"]:
            bad(f"{label} {t}: 文字が読みにくい {c}")


async def essentials(pg, label):
    """大事な部品が出ているか"""
    n = await pg.evaluate("document.querySelectorAll('#cards tbody tr').length")
    if n < 5:
        bad(f"{label}: 戦況の順位表の行が足りない（{n}行）")
    if not await pg.evaluate("!!document.querySelector('#alert').innerText.trim()"):
        bad(f"{label}: 戦況の帯（このままだと支払い）が空")
    await pg.evaluate("setTab('std')")
    if await pg.evaluate("document.querySelectorAll('#std tbody tr').length") < 6:
        bad(f"{label}: 順位タブの順位表が足りない")
    await pg.evaluate("setTab('stats')")
    if await pg.evaluate("document.querySelectorAll('#tmTbl tbody tr').length") < 6:
        bad(f"{label}: チーム成績の表が足りない")
    await pg.evaluate("setTab('song')")
    if await pg.evaluate("document.querySelectorAll('#songList .ptile').length") < 5:
        bad(f"{label}: 応援歌の選手タイルが足りない")
    # パワプロ風：どの表でも、球団名のマスにチームカラーのグラデーションが付いているか（偶数行で消えていた不具合の再発防止）
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        for tab, sel in (("magic", "#cards"), ("std", "#std"), ("stats", "#tmTbl")):
            await pg.evaluate(f"setTab('{tab}')")
            miss = await pg.evaluate(f"[...document.querySelectorAll('{sel} tbody tr:not(.ex) td.tnm')].filter(td => !getComputedStyle(td).backgroundImage.startsWith('linear')).map(td => td.innerText.split('\\n')[0])")
            if miss:
                bad(f"{label}: {tab} の表で球団名のグラデーションが消えている {miss}")


async def game_live(pg, label):
    """試合中：カードを開いて一球速報・打順・投手成績まで出るか"""
    await pg.evaluate("pollLive()")
    await pg.wait_for_timeout(500)
    await pg.evaluate("setTab('game'); window.scrollTo(0,0)")
    await pg.wait_for_timeout(200)
    card = await pg.query_selector(".tg.islive .tgx")
    if not card:
        bad(f"{label}: 試合中のカードが出ない")
        return
    await card.click()
    await pg.wait_for_timeout(900)
    for sel, name in [(".tg.islive .ls", "スコア表"), (".tg.islive .pbox", "一球速報"), (".tg.islive .lu", "打順"), (".tg.islive .pu", "投手成績"), (".tg.islive .bug", "スコアバグ")]:
        if not await pg.query_selector(sel):
            bad(f"{label}: 試合中の{name}が出ない")
    # 一球速報の投手・打者・次の打者の名前もタップできる形になっているか
    for sel, nm in ((".tg.islive .pc3 .pn3[data-pl]", "一球速報の投手・打者"), (".tg.islive .pnx3 b[data-pl]", "次の打者")):
        if not await pg.query_selector(sel):
            bad(f"{label}: {nm}の名前がタップできる形になっていない")
    # 打順の選手名をタップ → 選手の成績の画面が出るか
    name = await pg.query_selector(".tg.islive .lnm[data-pl]")
    if not name:
        bad(f"{label}: 打順の選手名がタップできる形になっていない")
    else:
        await name.click()
        await pg.wait_for_timeout(600)
        txt = await pg.evaluate("document.getElementById('songSheet').hidden ? '' : document.getElementById('songPick').innerText")
        if "打撃成績" not in txt and "投手成績" not in txt and "出場記録" not in txt:
            bad(f"{label}: 選手名をタップしても成績が出ない")
        r = await pg.evaluate(CHECK_JS, "game")
        await pg.evaluate("document.getElementById('songSheet').classList.remove('open'); document.getElementById('songSheet').hidden = true")
    clipped = await pg.evaluate("""(() => { const out = [];
      for (const sel of ['.tg.islive .pnx3 b', '.tg.islive .pr3 > *', '.tg.islive .pn3 b']) document.querySelectorAll(sel).forEach(e => {
        const box = e.closest('.pc3') || e.parentElement, r = e.getBoundingClientRect(), c = box.getBoundingClientRect();
        if (r.right > c.right + 1 || e.scrollWidth > e.clientWidth + 1) out.push(e.textContent.trim()); });
      return out; })()""")
    for c in clipped:
        bad(f"{label}: 試合中の一球速報で「{c}」が切れている")
    runners = await pg.evaluate("document.querySelectorAll('.tg.islive .fld .bs.on').length")
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        r = await pg.evaluate("[document.querySelectorAll('.tg.islive .rtile').length, document.querySelectorAll('.tg.islive .fld g[data-pl]').length]")
        if r[0] < 1 or r[1] > 0:
            bad(f"{label}: パワプロ風でランナーが選手タイルになっていない（タイル{r[0]}・黒い札{r[1]}）")
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        tiles = await pg.evaluate("document.querySelectorAll('.tg.islive .fldw .rtile').length")
        if tiles != runners:
            bad(f"{label}: パワプロ風のランナーの札が選手タイルになっていない（札{tiles}／ランナー{runners}）")
    if runners != 2:
        bad(f"{label}: ランナーの塁の数が想定と違う（{runners}）")
    r = await pg.evaluate(CHECK_JS, "game")
    for c in r["contrast"]:
        bad(f"{label} 試合中: 文字が読みにくい {c}")
    for o in r["overflow"]:
        bad(f"{label} 試合中: {o}")


async def cal_weather(pg, label):
    """雨の確率が高い日があっても、日程の7列が同じ幅のままか（名前のぶつかりで列が広がった不具合の再発防止）"""
    widths = await pg.evaluate("""(() => {
      const time = [], code = [], temp = [], pop = [];
      const d0 = new Date(jst().iso + 'T00:00:00');
      for (let k = -3; k < 10; k++) { const d = new Date(d0.getTime() + k * 864e5); const ds = d.toISOString().slice(0, 10);
        for (let h = 0; h < 24; h++) { time.push(ds + 'T' + String(h).padStart(2, '0') + ':00'); code.push(61); temp.push(22); pop.push(90); } }
      Object.keys(VENUE).forEach(k => (WX[k] = { at: Date.now(), h: { time, weather_code: code, temperature_2m: temp, precipitation_probability: pop } }));
      const out = [];
      for (const t of Object.keys(CONFIG.owners)) { S.calTeam = t; S.calMonth = null; setTab('cal'); renderCal();
        const w = [...document.querySelectorAll('.cal .wd')].slice(0, 7).map(e => Math.round(e.getBoundingClientRect().width));
        if (Math.max(...w) - Math.min(...w) > 2) out.push(t + ':' + w.join(',')); }
      return out; })()""")
    for w in widths:
        bad(f"{label}: 雨の日があると日程の列の幅がそろわない {w}")


async def starters_check(pg, label):
    """予告先発が分かっている試合前の試合で、日程の詳細に予告先発が出るか"""
    r = await pg.evaluate(r"""(() => {
      const g = DATA.games.find(x => x.st === 'sched' && TEAM[x.h] && TEAM[x.a]);
      if (!g) return 'skip';
      YK = { [`${g.d}|${g.h}|${g.a}`]: { h: 'テスト太郎', a: null } };
      const t = Object.keys(CONFIG.owners).includes(g.h) ? g.h : g.a;
      S.calTeam = t; S.calMonth = +g.d.slice(5, 7); S.calSel = g.d; setTab('cal'); renderCal();
      const e = document.querySelector('#detail .yk');
      const out = e ? e.innerText.replace(/\s+/g, ' ') : '';
      YK = {};
      return out; })()""")
    if r != "skip" and ("テスト太郎" not in r or "未発表" not in r):
        bad(f"{label}: 日程の詳細に予告先発が出ない（{r}）")


async def season_end(pg, label):
    await pg.evaluate("DATA.games.forEach(g=>{ if(g.st==='sched'||g.st==='live'||g.st==='canc'){ g.st='final'; g.hs=3; g.as=2; } }); renderAll(); setTab('magic')")
    await pg.wait_for_timeout(200)
    t = await pg.evaluate("document.getElementById('ttl').textContent")
    if "最終結果" not in t:
        bad(f"{label}: シーズン終了後の表示に切り替わらない（{t}）")
    if await pg.evaluate("document.getElementById('raceBox').innerHTML.length") > 0:
        bad(f"{label}: 優勝が決まったあとも優勝ラインが出ている")
    await scan(pg, label + " シーズン終了後")


async def sheets(pg, label):
    await pg.evaluate("openSheet()")
    await pg.wait_for_timeout(350)
    await pg.evaluate("closeSheet(); openMePick()")
    await pg.wait_for_timeout(350)
    await pg.evaluate("closeMePick(); setTab('song')")
    await pg.wait_for_timeout(300)
    tile = await pg.query_selector("#songList .ptile")
    if tile:
        await tile.click()
        await pg.wait_for_timeout(350)
        if await pg.evaluate("document.getElementById('songSheet').hidden"):
            bad(f"{label}: 応援歌の選手をタップしても画面が出ない")


async def data_refresh(browser):
    """試合以外（守備位置）だけ変わった最新データも反映されるか"""
    d = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
    old = dict(d); old.pop("fpos", None)
    new = dict(d); new["fpos"] = {"season": 2026, "teams": {"T": {"佐藤輝明": {"内": 118, "外": 18}}}, "roles": {}}
    served = {"body": json.dumps(old)}
    import http.server, threading, functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        await pg.add_init_script("localStorage.setItem('me','S'); localStorage.setItem('theme','')")
        await pg.route("**/data/latest.json*", lambda r: r.fulfill(status=200, headers={"Content-Type": "application/json"}, body=served["body"]))
        await pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        await pg.wait_for_timeout(900)
        served["body"] = json.dumps(new)
        await pg.reload()
        await pg.wait_for_timeout(1200)
        ok = await pg.evaluate("!!(DATA.fpos && DATA.fpos.teams && DATA.fpos.teams.T)")
        if not ok:
            bad("データ差し替え: 試合以外だけ変わった最新データが反映されない")
        await pg.close()
    finally:
        srv.shutdown()


async def calc_cache(browser):
    """計算結果の使い回し：元の計算と同じ答えになるか・試合が変わったら計算し直すか・書き換えても混ざらないか（両リーグ）"""
    for lg in ["C", "P"]:
        pg, errs = await open_page(browser, 390, "")
        if lg == "P":
            await pg.evaluate("switchLeague('P')")
            await pg.wait_for_timeout(200)
        r = await pg.evaluate("""() => {
          const strip = a => { const c = JSON.parse(JSON.stringify(a)); if (!c.exact) c.rows.forEach(r => { delete r.rate; delete r.avoid; delete r.self; }); return JSON.stringify(c); };
          const ng = [];
          for (const p of CONFIG.periods) {
            if (strip(analyze(DATA.games, p, CONFIG)) !== strip(analyzeRaw(DATA.games, p, CONFIG))) ng.push("一致しない " + p.id);
            if (JSON.stringify(analyze(DATA.games, p, CONFIG)) !== JSON.stringify(analyze(DATA.games, p, CONFIG))) ng.push("毎回ちがう " + p.id);
          }
          const g = DATA.games.find(g => g.st === "final" && CL.includes(g.h) && g.hs !== g.as);
          const p = CONFIG.periods.find(p => p.months.includes(+g.d.slice(5, 7)));
          const key = () => JSON.stringify(analyze(DATA.games, p, CONFIG).rows.map(r => [r.t, r.w, r.l]));
          const before = key(); const hs = g.hs, as = g.as; g.hs = as; g.as = hs;
          if (key() === before) ng.push("試合が変わっても計算し直さない");
          g.hs = hs; g.as = as;
          if (key() !== before) ng.push("元に戻しても答えが戻らない");
          // 使い回した結果の残り試合が、画面の試合データそのものを指しているか（「勝ったら」の計算で使う）
          for (const q of CONFIG.periods) {
            analyze(DATA.games, q, CONFIG); const y1 = analyze(DATA.games, q, CONFIG);   // 2回目は使い回し
            if (y1.remaining.some(g => !DATA.games.includes(g))) ng.push(`使い回した結果の残り試合が、試合データそのものを指していない ${q.id}`);
            if (y1.rows.some(r => (r.left || []).some(g => !DATA.games.includes(g)))) ng.push(`使い回した結果の球団ごとの残り試合が、試合データそのものを指していない ${q.id}`);
          }
          const x = analyze(DATA.games, p, CONFIG); x.rows[0].w = 999;
          if (analyze(DATA.games, p, CONFIG).rows[0].w === 999) ng.push("返した結果の書き換えが使い回しに混ざる");
          return ng;
        }""")
        for m in r:
            bad(f"[計算の使い回し {lg}] {m}")
        for e in errs:
            bad(f"[計算の使い回し {lg}]: 画面のエラー {e}")
        await pg.close()


async def song_link_check(browser):
    """選手ごとの応援歌ページ（su）：あればそれを開く・おかしなURLは使わない"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const ng = [], L = (DATA.rosters || {}).L || [], a = L.find(x => x.song), b = L.filter(x => x.song)[1];
      if (!a || !b) return ng;
      const oa = a.su, ob = b.su;
      a.su = "https://www.yakyu-ouen.net/test-player/"; b.su = "javascript:alert(1)";
      if (songLink("L", a) !== a.su) ng.push("選手ごとのページが使われない");
      if (/^javascript/.test(songLink("L", b) || "")) ng.push("おかしなURLがそのまま使われる");
      a.su = oa; b.su = ob;
      return ng;
    }""")
    for m in r:
        bad(f"[応援歌のリンク] {m}")
    for e in errs:
        bad(f"[応援歌のリンク]: 画面のエラー {e}")
    await pg.close()


async def team_in_check(browser):
    """チーム内の成績：選手が多くても表がはみ出さない・全員表示・並べ替え（両テーマ×幅390/320×両リーグ）"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[チーム内の成績 {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                r = await pg.evaluate("""() => {
                  const ng = [];
                  setTab("stats");
                  for (const t of CL) {
                    const bat = {}, pit = {};
                    (DATA.rosters[t] || []).forEach((x, i) => {
                      const k = x.n.replace(/\\s+/g, "");
                      if (x.p === "投手") pit[k] = { 登板: "52", 投球回: "160.1", 防御率: "12.34", 勝利: "12", 敗北: "10", 三振: "188" };
                      else bat[k] = { 試合: "143", 打席: String(600 - i), 打率: ".333", 本塁打: "44", 打点: "123", 出塁率: ".444", 長打率: ".666" };
                    });
                    // とても長い名前の選手も混ぜる（省略せずに収まるか）
                    bat["ダーウィンゾンヘルナンデスジュニア"] = { 試合: "143", 打席: "700", 打率: ".333", 本塁打: "44", 打点: "123", 出塁率: ".444", 長打率: ".666" };
                    pit["クリストファーアレクサンダー"] = { 登板: "52", 投球回: "200.2", 防御率: "12.34", 勝利: "12", 敗北: "10", 三振: "188" };
                    PST[t] = { at: Date.now(), d: { bat, pit, asof: "9/28" } };
                  }
                  const widths = {};
                  const clipped = () => [...document.querySelectorAll("#ptTbl td, #ptTbl th")].filter(c => c.scrollWidth > c.clientWidth + 1).length;
                  const over = () => { const tb = document.getElementById("ptTbl"); return tb && tb.scrollWidth > tb.parentElement.clientWidth + 1 ? tb.scrollWidth - tb.parentElement.clientWidth : 0; };
                  for (const t of CL) {
                    S.ptTeam = t;
                    for (const k of ["bat", "pit"]) {
                      S.ptKind = k; S.ptAll = false; renderTeamIn();
                      const n = document.querySelectorAll("#ptTbl tbody tr").length;
                      if (!n) ng.push(`${t} ${k}: 表が出ない`);
                      if (over()) ng.push(`${t} ${k}: 表が ${over()}px はみ出し`);
                      if (clipped()) ng.push(`${t} ${k}: 文字がマスからはみ出したセルが ${clipped()} 個`);
                      widths[k] = widths[k] || new Set(); widths[k].add(Math.round(document.getElementById("ptTbl").getBoundingClientRect().width) + "/" + [...document.querySelectorAll("#ptTbl thead th")].map(th => Math.round(th.getBoundingClientRect().width)).join(","));
                      const more = document.getElementById("ptMore");
                      if (more) { more.click(); if (document.querySelectorAll("#ptTbl tbody tr").length <= n) ng.push(`${t} ${k}: 全員を表示が効かない`); if (over()) ng.push(`${t} ${k}: 全員表示で ${over()}px はみ出し`); }
                      const th = document.querySelector("#ptTbl th.srt[data-col='2']"); if (th) { th.click(); if (!document.querySelector("#ptTbl th.srt.on")) ng.push(`${t} ${k}: 並べ替えが効かない`); }
                    }
                  }
                  for (const k in widths) if (widths[k].size > 1) ng.push(`${k}: 球団によって表の幅・列の幅が違う（${[...widths[k]].join(" | ")}）`);
                  return ng;
                }""")
                for m in r[:5]:
                    bad(f"{label} {m}")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()


async def starter_order_check(browser):
    """予告先発：試合タブ・日程の詳細とも、ホームの投手が左（先）に来るか"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const g = { d: "2099-01-01", h: "T", a: "S", st: "sched" };
      YK[`${g.d}|${g.h}|${g.a}`] = { h: "才木 浩人", a: "奥川 恭伸" };
      const box = document.createElement("div");
      const ng = [];
      for (const html of [ykHTML(g), ykHTML(g, true)]) {
        box.innerHTML = html;
        const first = box.querySelector(".ykp");
        if (!first || !first.dataset.pl.startsWith("T|")) ng.push("ホームの投手が左になっていない");
      }
      delete YK[`${g.d}|${g.h}|${g.a}`];
      return ng;
    }""")
    for m in r:
        bad(f"[予告先発の並び] {m}")
    for e in errs:
        bad(f"[予告先発の並び]: 画面のエラー {e}")
    await pg.close()


async def wording_check(browser):
    """説明文と実際の見た目・リーグが合っているか
    1) パ・リーグ表示で、セ・リーグだけの言葉（支払・広島・担当など）が出ていないか（逆も）
    2) 「白枠」「青い枠」「黄色」「オレンジ」「白」「金色」などの色の説明が、そのテーマの実際の色と合っているか"""
    NG = {"P": ["支払", "広島", "担当", "セ・リーグ", "5球団", "神の行", "巨の列"], "C": ["パ・リーグ", "6球団", "ソの行"]}
    OK_P = "担当者・支払いはセ・リーグだけの遊びです"
    for theme in ["", "pawa"]:
        for lg in ["C", "P"]:
            label = f"[説明文 {'パワプロ風' if theme else 'スタイリッシュ'} {lg}]"
            pg, errs = await open_page(browser, 390, theme)
            if lg == "P":
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(200)
            for tab in ["magic", "game", "cal", "std", "stats", "song", "off"]:
                txt = await pg.evaluate("""(tab) => { setTab(tab); const v = document.getElementById('v-' + tab); v.querySelectorAll('details').forEach(d => d.open = true); return v.innerText; }""", tab)
                for line in txt.split("\n"):
                    if lg == "P" and OK_P in line:
                        continue
                    for w in NG[lg]:
                        if w in line:
                            bad(f"{label} [{tab}] リーグに合わない言葉「{w}」: {line.strip()[:80]}")
                            break
            # 色の説明と実際の色
            r = await pg.evaluate("""() => {
              const ng = [], rgb = s => (s.match(/\\d+(\\.\\d+)?/g) || []).map(Number);
              const vis = el => el && el.offsetParent !== null;
              // 画面に見えている文字（テーマで隠れている言葉は含まない）から、書いてある色を読む
              const said = (root, words) => { const t = root.innerText; return words.find(w => t.includes(w)) || null; };
              const isWhite = c => { const [r, g, b] = rgb(c); return r > 230 && g > 230 && b > 230; };
              const isBlue = c => { const [r, g, b] = rgb(c); return b > 90 && b > r + 40; };
              setTab('magic');
              const lab = [...document.querySelectorAll('#condBody .lab')].find(l => l.textContent.includes('直接対決'));
              const vs = document.querySelector('#condBody .left span.vs'), nv = document.querySelector('#condBody .left span:not(.vs)');
              if (lab && vs) {
                const w = said(lab, ['白枠', '青い枠']), bc = getComputedStyle(vs).borderTopColor;
                if (!w) ng.push('直接対決の説明に色が書かれていない');
                if (w === '白枠' && !isWhite(bc)) ng.push(`直接対決は「白枠」と書いているのに枠の色が ${bc}`);
                if (w === '青い枠' && !isBlue(bc)) ng.push(`直接対決は「青い枠」と書いているのに枠の色が ${bc}`);
                if (nv && getComputedStyle(nv).borderTopColor === bc) ng.push('直接対決とほかの試合の枠の色が同じ');
              }
              setTab('stats');
              const best = document.querySelector('#tmTbl td.best'), li = [...document.querySelectorAll('#v-stats .howto li')].find(l => l.textContent.includes('リーグトップ'));
              if (best && li) {
                const w = said(li, ['黄色', 'オレンジ']), c = rgb(getComputedStyle(best).color);
                if (w === '黄色' && !(c[0] > 200 && c[1] > 170 && c[2] < 120)) ng.push(`チーム成績のトップは「黄色」と書いているのに ${c}`);
                if (w === 'オレンジ' && !(c[0] > 190 && c[1] > 60 && c[1] < 170 && c[2] < 80)) ng.push(`チーム成績のトップは「オレンジ」と書いているのに ${c}`);
              }
              setTab('std');
              const first = document.querySelector('.ytbl td.yc2.first b'), li2 = [...document.querySelectorAll('#v-std .howto li')].find(l => l.textContent.includes('年間成績'));
              if (first && li2 && vis(li2)) {
                const w = said(li2, ['白＝', '金色＝']), cs = getComputedStyle(first);
                if (w === '白＝' && !isWhite(cs.backgroundColor)) ng.push(`年間成績の1位は「白」と書いているのに ${cs.backgroundColor}`);
                if (w === '金色＝' && !/255, 230, 128|242, 182, 0/.test(cs.backgroundImage + cs.backgroundColor)) ng.push(`年間成績の1位は「金色」と書いているのに ${cs.backgroundImage}`);
              }
              return ng;
            }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


# 指の操作をまねる（touchstart→touchmove→touchend）
SWIPE_JS = """([sel, dx, dy]) => {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) return "no-element";
  el.scrollIntoView({ block: "center" });
  const b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + Math.min(b.height / 2, 40);
  const mk = (cx, cy) => new Touch({ identifier: 1, target: el, clientX: cx, clientY: cy });
  const fire = (type, cx, cy) => { const t = mk(cx, cy); el.dispatchEvent(new TouchEvent(type, { touches: type === "touchend" ? [] : [t], targetTouches: type === "touchend" ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
  fire("touchstart", x, y);
  for (let i = 1; i <= 8; i++) fire("touchmove", x + dx * i / 8, y + dy * i / 8);
  fire("touchend", x + dx, y + dy);
  return "ok";
}"""


async def tap_target_check(browser):
    """指で押す部品が縦横44px以上あるか（表の球団名はマス全体が押せるか）。両テーマ×幅390/320×両リーグ・全タブ"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[押しやすさ {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                for tab in ["magic", "game", "cal", "std", "stats", "song", "off"]:
                    r = await pg.evaluate("""(tab) => {
                      setTab(tab);
                      const v = document.getElementById('v-' + tab), ng = new Set();
                      v.querySelectorAll('a[href], button, summary, select, th.srt').forEach(e => {
                        if (!e.offsetParent || e.closest('.ptile, .howto li, .foot, p')) return;   // 文章の中のリンク・選手名の札は対象外
                        let b = e.getBoundingClientRect();
                        const td = e.matches('td.tnm a') ? e.closest('td') : null;
                        if (td) {
                          // マス全体が押せるか：リンクの見えない面（a::before）がマスいっぱいに広がっているか、
                          // さらにマスの左端・右端（文字のない所）を押したときにこのリンクに当たるか
                          const pb = getComputedStyle(e, '::before');
                          if (getComputedStyle(td).position !== 'relative' || pb.position !== 'absolute' || pb.top !== '0px' || pb.left !== '0px' || pb.right !== '0px' || pb.bottom !== '0px') {
                            ng.add(`球団名のマス全体が押せる作りになっていない：${e.textContent.trim().slice(0, 10)}`); return;
                          }
                          e.scrollIntoView({ block: 'center' }); b = td.getBoundingClientRect();
                          const y = b.top + b.height / 2;
                          for (const x of [b.left + 4, b.right - 4]) {
                            const hit = document.elementFromPoint(x, y);
                            if (!hit || !(hit === e || e.contains(hit) || hit.closest('a') === e)) {
                              const d = hit ? `${hit.tagName.toLowerCase()}${hit.id ? '#' + hit.id : ''}.${String(hit.className).split(' ')[0]}` : 'なし';
                              ng.add(`球団名のマスの端を押してもリンクにならない：${e.textContent.trim().slice(0, 10)}（押された所：${d}、位置 ${Math.round(x)},${Math.round(y)}、画面の高さ ${innerHeight}）`); return;
                            }
                          }
                        }
                        if (b.width < 1) return;
                        // 表の見出し（並べ替え）と日程のカレンダーの日付（7列）は、列の幅が画面幅で決まるので高さだけ確かめる
                        if (b.height < 43.5 || (b.width < 43.5 && !e.matches('th.srt, #cal button.day'))) ng.add(`${e.tagName.toLowerCase()}「${e.textContent.trim().slice(0, 10)}」${Math.round(b.width)}×${Math.round(b.height)}`);
                      });
                      return [...ng].slice(0, 6);
                    }""", tab)
                    for m in r:
                        bad(f"{label} [{tab}] 押す部品が小さい：{m}")
                r = await pg.evaluate("(() => { window.scrollTo(0, 0); const b = document.getElementById('gearBtn').getBoundingClientRect(); return b.width >= 43.5 && b.height >= 43.5 ? '' : `設定ボタンが小さい（${Math.round(b.width)}×${Math.round(b.height)}）`; })()")
                if r:
                    bad(f"{label} {r}")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()


async def swipe_check(browser):
    """戦況の順位表のあたりを左右にスワイプすると月度が変わる・それ以外の場所ではタブが変わる"""
    for theme in ["", "pawa"]:
        label = f"[スワイプ {'パワプロ風' if theme else 'スタイリッシュ'}]"
        pg, errs = await open_page(browser, 390, theme, touch=True)
        await pg.evaluate("setTab('magic'); window.scrollTo(0, 0)")
        before = await pg.evaluate("S.period.id")
        list_ = await pg.evaluate("periods().map(p => p.id)")
        i = list_.index(before)
        await pg.evaluate(SWIPE_JS, ["#cards", 160, 0])      # 右へ：前の月度
        await pg.wait_for_timeout(800)
        after = await pg.evaluate("[S.tab, S.period.id]")
        if i > 0 and (after[0] != "magic" or after[1] != list_[i - 1]):
            bad(f"{label} 順位表を右へスワイプしても前の月度にならない（{before}→{after}）")
        await pg.evaluate(SWIPE_JS, ["#cards", -160, 0])     # 左へ：元の月度
        await pg.wait_for_timeout(800)
        after = await pg.evaluate("[S.tab, S.period.id]")
        if after != ["magic", before]:
            bad(f"{label} 順位表を左へスワイプしても元の月度に戻らない（{after}）")
        await pg.evaluate(SWIPE_JS, ["#cards", 30, 0])       # 少しだけ：変わらない
        await pg.wait_for_timeout(800)
        if await pg.evaluate("S.period.id") != before:
            bad(f"{label} 少し動かしただけで月度が変わる")
        await pg.evaluate(SWIPE_JS, ["#formBlk", -160, 0])   # 順位表以外：タブが変わる
        await pg.wait_for_timeout(800)
        if await pg.evaluate("S.tab") != "game":
            bad(f"{label} 順位表以外の場所を左へスワイプしてもタブが変わらない")
        # 動き終わったあと、画面がずれたり透明のまま残ったりしていないか
        left = await pg.evaluate("[...document.querySelectorAll('.view, #alert, #meCard, #cards')].filter(el => el.style.transform || el.style.opacity).map(el => el.id)")
        if left:
            bad(f"{label} スワイプのあと、画面の位置や透明度が元に戻っていない：{left}")
        for e in errs:
            bad(f"{label}: 画面のエラー {e}")
        await pg.close()


async def pull_refresh_check(browser):
    """引っぱって更新：ホーム画面から開いたときだけ動き、更新の処理が呼ばれる"""
    pg, errs = await open_page(browser, 390, "", touch=True)
    r = await pg.evaluate("""async () => {
      const ng = []; let called = 0;
      window.scrollTo(0, 0);
      // ブラウザで開いているとき：動かない
      const sw = async () => { const el = document.querySelector('#alert'); const mk = y => new Touch({ identifier: 2, target: el, clientX: 200, clientY: y });
        const f = (type, y) => { const t = mk(y); el.dispatchEvent(new TouchEvent(type, { touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
        f('touchstart', 150); for (let i = 1; i <= 8; i++) f('touchmove', 150 + 16 * i); f('touchend', 278); await new Promise(r => setTimeout(r, 50)); };
      await sw();
      if (document.getElementById('ptrTxt').textContent !== '引っぱって更新' || getComputedStyle(document.getElementById('ptr')).opacity !== '0') ng.push('ブラウザで開いているのに引っぱって更新が動く');
      window.__ptrForce = true;
      const t0 = document.getElementById('ptrTxt').textContent;
      await sw();
      const txt = document.getElementById('ptrTxt').textContent;
      if (!/更新中|最新です|更新しました|更新できません/.test(txt)) ng.push(`ホーム画面から開いたときに引っぱって更新が動かない（表示：${txt}）`);
      await new Promise(r => setTimeout(r, 1500));
      window.__ptrForce = false;
      return ng;
    }""")
    for m in r:
        bad(f"[引っぱって更新] {m}")
    for e in errs:
        bad(f"[引っぱって更新]: 画面のエラー {e}")
    await pg.close()


async def memory_check(browser):
    """成績タブの打者/投手・項目・並べ替えを、開き直しても覚えているか
    （file:// で開くと、開き直したときに端末への保存が消えることがある検査環境の癖があるため、手元のサーバー経由で開く）"""
    import http.server, threading, functools

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/index.html"
    try:
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.add_init_script("localStorage.setItem('me','S'); localStorage.setItem('theme',''); localStorage.setItem('league','C')")
        await pg.route("https://**", lambda r: r.abort())
        await pg.goto(url)
        await pg.wait_for_timeout(700)
        await pg.evaluate("""() => {
          setTab('stats');
          document.querySelector('#tmSeg button[data-k="pit"]').click();
          document.querySelector('#tmTbl th.srt[data-col="1"]').click();
          document.querySelector('#rkSeg button[data-k="pit"]').click();
          S.cat = CATS.pit[2][0]; renderStats();
          document.querySelector('#ptSeg button[data-k="pit"]').click();
        }""")
        want = await pg.evaluate("[S.tmKind, JSON.stringify(S.tmSort), S.rkKind, S.cat, S.ptKind]")
        await pg.wait_for_timeout(300)
        await pg.reload()
        await pg.wait_for_timeout(700)
        got = await pg.evaluate("[S.tmKind, JSON.stringify(S.tmSort), S.rkKind, S.cat, S.ptKind]")
        if got != want:
            bad(f"[状態の記憶] 開き直すと成績タブの状態が戻る（前：{want} → 後：{got}）")
        # 壊れた値が入っていても画面が壊れない
        await pg.evaluate("v => localStorage.setItem('statsUI', v)", json.dumps({"tmKind": "xx", "cat": "nope", "tmSort": {"kind": "bat", "col": "a"}}))
        await pg.reload()
        await pg.wait_for_timeout(700)
        ok = await pg.evaluate("setTab('stats'), [S.tmKind, CATS[S.rkKind].some(c => c[0] === S.cat), !!document.querySelector('#tmTbl tbody tr')]")
        if ok != ["bat", True, True]:
            bad(f"[状態の記憶] 壊れた値が入っていると成績タブがおかしくなる（{ok}）")
        for e in errs:
            bad(f"[状態の記憶]: 画面のエラー {e}")
        await pg.close()
    finally:
        srv.shutdown()


async def loser_wording_check(browser):
    """負けた側のマジックが減る場面では「負けても自力脱出が残る」と書き、「M5 → M4」とは書かない"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      // 今の月度の残り試合から、負けた側のマジックが減る場面を探す
      const p = S.period, base = analyze(DATA.games, p, CONFIG), by = {}; base.rows.forEach(x => by[x.t] = x);
      for (const g of base.remaining) {
        for (const hw of [true, false]) {
          const g2 = DATA.games.map(x => x === g ? { ...x, st: 'final', hs: hw ? 1 : 0, as: hw ? 0 : 1 } : x);
          const b = analyze(g2, p, CONFIG), lo = hw ? g.a : g.h, B = by[lo], R = b.rows.find(x => x.t === lo);
          if (B && R && !B.safe && !B.eliminated && !R.safe && !R.eliminated && B.self != null && R.self != null && R.self < B.self) {
            // その試合を今日の試合にして描く
            const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today });
            setTab('game'); renderGame();
            const box = [...document.querySelectorAll('#today .tgo')].find(el => el.querySelector('b').textContent.startsWith(fn(hw ? g.h : g.a) + 'が勝ったら'));
            if (!box) return ['場面は見つかったが、「勝ったら」の欄が出ない'];
            const t = box.textContent, ng = [];
            if (!t.includes(fn(lo) + 'は負けても自力脱出が残る')) ng.push(`「${fn(lo)}は負けても自力脱出が残る」と書かれていない：${t.slice(0, 80)}`);
            if (t.includes(fn(lo) + 'のマジック M' + B.self + ' → M' + R.self)) ng.push('負けた側のマジックが「M○ → M○」と減るように書かれている');
            return ng;
          }
        }
      }
      return [];
    }""")
    for m in r:
        bad(f"[負けた側の書き方] {m}")
    for e in errs:
        bad(f"[負けた側の書き方]: 画面のエラー {e}")
    await pg.close()


async def home_screen_check(browser):
    """ホーム画面に置いたときにアプリのように開く設定があるか"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => [!!document.querySelector('link[rel=manifest]'), (document.querySelector('meta[name=apple-mobile-web-app-capable]') || {}).content]""")
    if r != [True, "yes"]:
        bad(f"[ホーム画面] アプリとして開く設定が足りない（{r}）")
    mf = ROOT / "manifest.json"
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("display") != "standalone" or not m.get("icons"):
            bad("[ホーム画面] manifest.json の display／icons がおかしい")
    except Exception as e:
        bad(f"[ホーム画面] manifest.json が読めない（{e}）")
    await pg.close()


async def next_day_check(browser):
    """試合のない日に出る「次の試合」の「勝ったら」が、その日当日に見たときと同じ内容か（計算の使い回しで試合を見失わないか）"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const p = S.period, a = analyze(DATA.games, p, CONFIG);
      if (!a.remaining.length) return [];
      // 残り試合の期間の中で、試合のない日（D）と、その次に試合がある日（first）を探す
      const ds = [...new Set(a.remaining.map(g => g.d))].sort();
      let D = null, first = null;
      for (let t = new Date(ds[0] + 'T00:00:00Z'); t.toISOString().slice(0, 10) < ds[ds.length - 1]; t.setUTCDate(t.getUTCDate() + 1)) {
        const iso = t.toISOString().slice(0, 10);
        const mine = g => a.rows.some(r => r.t === g.h || r.t === g.a);   // 表示しているリーグの対象球団の試合
        if (!DATA.games.some(g => g.d === iso && g.st !== 'canc' && mine(g))) { D = iso; first = ds.find(x => x > iso); break; }
      }
      if (!D || !first) return [];
      const read = iso => { jst = () => ({ y: +iso.slice(0, 4), m: +iso.slice(5, 7), d: +iso.slice(8), iso }); setTab('game'); renderGame();
        return [...document.querySelectorAll('#today .tgo')].map(e => e.innerText.replace(/\\s+/g, ' ')).sort().join(' | '); };
      const nextView = read(D), dayView = read(first);
      return nextView === dayView ? [] : [`前の日に見た「次の試合」と当日の内容が違う：${nextView.slice(0, 120)} ／ ${dayView.slice(0, 120)}`];
    }""")
    for m in r:
        bad(f"[次の試合] {m}")
    for e in errs:
        bad(f"[次の試合]: 画面のエラー {e}")
    await pg.close()


async def name_center_check(browser):
    """パワプロ風の選手名のタイル（チーム内の成績）で、名前がタイルの真ん中にあるか（字間の分がずれていないか）"""
    for width in [390, 320]:
        pg, errs = await open_page(browser, width, "pawa")
        r = await pg.evaluate("""() => {
          setTab('stats'); const t = CL[0], bat = {}, pit = {};
          (DATA.rosters[t] || []).forEach((x, i) => { const k = x.n.replace(/\\s+/g, ''); if (x.p === '投手') pit[k] = { 登板: '9', 投球回: '9', 防御率: '1.00', 勝利: '1', 敗北: '1', 三振: '9' }; else bat[k] = { 試合: '9', 打席: String(600 - i), 打率: '.300', 本塁打: '9', 打点: '9', 出塁率: '.4', 長打率: '.5' }; });
          PST[t] = { at: Date.now(), d: { bat, pit, asof: '9/28' } }; S.ptTeam = t; S.ptAll = true;
          const ng = [];
          for (const k of ['bat', 'pit']) {
            S.ptKind = k; renderTeamIn();
            document.querySelectorAll('#ptTbl .ptile').forEach(tl => {
              const b = tl.querySelector('b'), r = document.createRange(); r.selectNodeContents(b);
              const ls = parseFloat(getComputedStyle(b).letterSpacing) || 0, tr = tl.getBoundingClientRect(), c = (tr.left + tr.right) / 2;
              for (const rc of r.getClientRects()) { const off = (rc.left + rc.right - ls) / 2 - c; if (Math.abs(off) > 1.5) { ng.push(`${b.textContent} が ${off.toFixed(1)}px ずれている`); break; } }
            });
          }
          return ng.slice(0, 5);
        }""")
        for m in r:
            bad(f"[名前の位置 パワプロ風 幅{width}] {m}")
        for e in errs:
            bad(f"[名前の位置 パワプロ風 幅{width}]: 画面のエラー {e}")
        await pg.close()


async def tabbar_check(browser):
    """下のタブバー：指でスクロールしている間は下へでも上へでも小さく、止まるといちばん上の近くでは元の大きさ"""
    pg, errs = await open_page(browser, 390, "", touch=True)
    r = await pg.evaluate("""async () => {
      const bar = document.querySelector('.tabbar'), P = () => parseFloat(bar.style.getPropertyValue('--p') || '0'), ng = [];
      setTab('magic');
      const el = document.getElementById('formBlk'), wait = ms => new Promise(r => setTimeout(r, ms));
      const f = (type, y) => { const t = new Touch({ identifier: 7, target: el, clientX: 200, clientY: y }); el.dispatchEvent(new TouchEvent(type, { touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
      window.scrollTo(0, 1200); await wait(900);
      f('touchstart', 300); for (let i = 1; i <= 8; i++) { f('touchmove', 300 - i * 15); window.scrollBy(0, 15); await wait(30); }
      if (P() < .99) ng.push(`下へ読み進めてもタブバーが小さくならない（${P()}）`);
      f('touchend', 180); await wait(1000);
      if (P() > .01) ng.push(`スクロールが止まってもタブバーが元の大きさに戻らない（${P()}）`);
      f('touchstart', 300); for (let i = 1; i <= 8; i++) { f('touchmove', 300 + i * 15); window.scrollBy(0, -15); await wait(30); }
      if (P() < .99) ng.push(`上へ戻るときにタブバーが小さくならない（${P()}）`);
      f('touchend', 420);
      window.scrollTo(0, 20); f('touchstart', 300); f('touchmove', 310); window.scrollTo(0, 10); await wait(80);
      if (P() > .01) ng.push(`いちばん上の近くでタブバーが元の大きさにならない（${P()}）`);
      f('touchend', 310);
      return ng;
    }""")
    for m in r:
        bad(f"[タブバー] {m}")
    for e in errs:
        bad(f"[タブバー]: 画面のエラー {e}")
    await pg.close()


async def offseason_check(browser):
    """今オフの戦力外・引退：一覧・札が出るか、はみ出さないか、押しやすいか（両テーマ×幅390/320×両リーグ）。
    あわせて、データ更新側の読み取り（球団の発表ページから選手を拾う処理）を試験用のページで確かめる"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[戦力外・引退 {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                r = await pg.evaluate("""() => {
                  const ng = [], items = [];
                  // 表示中のリーグの各球団から、名簿の選手を2人ずつ（長い名前の選手も入れる）
                  CL.forEach((t, i) => {
                    const ro = (DATA.rosters[t] || []).slice().sort((a, b) => b.n.length - a.n.length);
                    if (ro[0]) items.push({ t, n: ro[0].n, no: ro[0].no, dev: false, kind: 'offer', date: '2026-09-29', url: 'https://example.com/a', title: '' });
                    if (ro[1] && i % 2 === 0) items.push({ t, n: ro[1].n, no: ro[1].no, dev: false, kind: 'retire', date: '2026-09-23', url: 'https://example.com/b', title: '' });
                  });
                  DATA.offseason = { season: 2026, checked_at: '2026-09-29T15:00:00+09:00', items, teams: {}, seen: {} };
                  jst = () => ({ y: 2026, m: 10, d: 1, iso: '2026-10-01' });
                  renderAll(); setTab('off');
                  const tb = document.querySelector('.tabbar button[data-tab="off"]'), blk = document.getElementById('v-off');
                  if (tb.hidden || blk.hidden) { ng.push('戦力外のタブが出ない'); return ng; }
                  if (getComputedStyle(document.querySelector('.tabbar nav')).gridTemplateColumns.split(' ').length !== 7) ng.push('タブが7つ並んでいない');
                  const tbb = [...document.querySelectorAll('.tabbar button:not([hidden])')].map(b => b.getBoundingClientRect());
                  if (tbb.some((b, i) => i && b.left < tbb[i - 1].right - 1)) ng.push('タブのボタンが重なっている');
                  if (tbb.some(b => b.right > innerWidth)) ng.push('タブバーが画面からはみ出している');
                  const rows = blk.querySelectorAll('.ofr');
                  if (rows.length !== items.length) ng.push(`一覧の人数が違う（${rows.length}／${items.length}）`);
                  // パワプロ風は、名前が守備位置の色のタイルになっているか
                  if (isPawa() && [...rows].some(r => !r.querySelector('.onm .ptile'))) ng.push('パワプロ風なのに、名前がタイルになっていない');
                  const W = blk.getBoundingClientRect().right + 1;
                  blk.querySelectorAll('.ofr, .ofr *').forEach(e => { const b = e.getBoundingClientRect(); if (b.width && b.right > W) ng.push(`一覧が横にはみ出し：${e.className}`); });
                  blk.querySelectorAll('.oln, .onm').forEach(e => { const b = e.getBoundingClientRect(); if (e.matches('.oln') && (b.height < 43.5 || b.width < 43.5)) ng.push(`「発表」が小さい ${Math.round(b.width)}×${Math.round(b.height)}`); });
                  // 札：チーム内の成績・応援歌・選手の成績画面
                  const it = items[0], k = it.n.replace(/\\s+/g, '');
                  PST[it.t] = { at: Date.now(), d: { bat: { [k]: { 試合: '1', 打席: '9', 打率: '.1', 本塁打: '0', 打点: '0', 出塁率: '.1', 長打率: '.1' } }, pit: { [k]: { 登板: '1', 投球回: '1', 防御率: '1.00', 勝利: '0', 敗北: '0', 三振: '1' } }, asof: '9/28' } };
                  setTab('stats'); S.ptTeam = it.t; renderTeamIn();
                  if (!document.querySelector('#ptTbl .offtag')) ng.push('チーム内の成績に札が出ない');
                  if (document.getElementById('offList').closest('#v-stats')) ng.push('一覧が成績タブに残っている');
                  const over = [...document.querySelectorAll('#ptTbl td, #ptTbl th')].filter(c => c.scrollWidth > c.clientWidth + 1).length;
                  if (over) ng.push(`札を付けたチーム内の成績で、文字がマスからはみ出したセルが ${over} 個`);
                  return ng;
                }""")
                for m in r[:6]:
                    bad(f"{label} {m}")
                # オフでない時期（発表もない）はタブが消えて6つに戻り、戦力外のタブを見ていたら戦況に戻る
                r2 = await pg.evaluate("""() => {
                  const keep = DATA.offseason, keepJst = jst;
                  setTab('off'); DATA.offseason = null; jst = () => ({ y: 2026, m: 6, d: 1, iso: '2026-06-01' }); renderAll();
                  const ng = [];
                  if (!document.querySelector('.tabbar button[data-tab="off"]').hidden) ng.push('オフでないのに戦力外のタブが出ている');
                  if (getComputedStyle(document.querySelector('.tabbar nav')).gridTemplateColumns.split(' ').length !== 6) ng.push('タブが6つに戻らない');
                  if (S.tab !== 'magic') ng.push('戦力外のタブが消えたのに、その画面のまま');
                  if (tabOrder().includes('off')) ng.push('消えたタブにスワイプで行ける');
                  DATA.offseason = keep; jst = keepJst; renderAll();   // 元に戻す
                  return ng;
                }""")
                for m in r2:
                    bad(f"{label} {m}")
                # 選手の成績画面の札
                ok = await pg.evaluate("""async () => { const it = DATA.offseason.items[0]; await openPlayer(it.t, it.n); return !!document.querySelector('#songPick .sp-h .offtag'); }""")
                if not ok:
                    bad(f"{label} 選手の成績画面に札が出ない")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()
    # データ更新側：発表ページの読み取り（requests・BeautifulSoup が入っている環境だけ）
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "scripts"))
        import update_data as ud
    except ImportError:
        print("  （データ更新側の読み取りの試験は、requests・BeautifulSoup がないため省略）")
        return
    roster = [{"n": "酒居 知史", "no": "28"}, {"n": "林 優樹", "no": "64"}, {"n": "今野 龍太", "no": "66"}, {"n": "伊藤 樹", "no": "20"}, {"n": "辛島 航", "no": "58"}, {"n": "松田 啄磨", "no": "061", "dev": True}]
    lst = '<ul class="news-list"><li><a href="/news/1.html">2026/09/28 来季の選手契約について</a></li><li><a href="/news/2.html">辛島 航選手 現役引退に関して</a></li><li><a href="/news/3.html">伊藤 樹選手がプロ初勝利</a></li><li><a href="/news/4.html">契約更改について</a></li></ul>'
    art = ('<header>伊藤 樹選手</header><article><h1>来季の選手契約について</h1><p>2026/09/28</p><p>以下の選手と2027シーズンの選手契約を行わないことを通知しました。</p>'
           '<p>投手 酒居 知史<br>投手 林 優樹<br>投手 今野 龍太<br>【育成】投手 松田 啄磨</p><p>なお、酒居 知史投手、林 優樹投手には育成選手契約を打診しております。</p></article>'
           '<aside class="side">伊藤 樹選手がプロ初勝利</aside>')
    links = [t for _, t in ud.off_links("https://www.example.jp/news/", lst)]
    if len(links) != 2 or not any("契約" in t for t in links) or not any("引退" in t for t in links):
        bad(f"[戦力外・引退の読み取り] ニュース一覧から発表を正しく選べない：{links}")
    got, _ = ud.off_article("E", "u", "来季の選手契約について", art, roster, 2026)
    want = {"酒居 知史": "offer", "林 優樹": "offer", "今野 龍太": "cut", "松田 啄磨": "cut"}
    if {x["n"]: x["kind"] for x in got} != want:
        bad(f"[戦力外・引退の読み取り] 発表から選手を正しく拾えない：{[(x['n'], x['kind']) for x in got]}")
    old, why = ud.off_article("E", "u", "来季の選手契約について", "<h1>来季の選手契約について</h1><p>2025/10/05</p><p>酒居 知史投手と来季の契約を結ばない</p>", roster, 2026)
    if old:
        bad("[戦力外・引退の読み取り] 去年の発表を今年のものとして拾っている")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for theme in ["", "pawa"]:
            for width in [390, 320]:
                label = f"[{'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}]"
                print(label)
                pg, errs = await open_page(browser, width, theme)
                await scan(pg, label)
                await essentials(pg, label)
                await game_live(pg, label)
                await sheets(pg, label)
                await cal_weather(pg, label)
                await starters_check(pg, label)
                await season_end(pg, label)
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()
                # パ・リーグに切り替えて、同じように全タブを検査する
                plabel = label.replace("]", " パ・リーグ]")
                pg, errs = await open_page(browser, width, theme)
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(300)
                if await pg.evaluate("LEAGUE") != "P" or await pg.evaluate("CL.join(',')") != "H,F,B,E,L,M":
                    bad(f"{plabel}: パ・リーグに切り替わらない")
                await scan(pg, plabel)
                shown = await pg.evaluate("[...document.querySelectorAll('.cl-only')].filter(e => e.offsetParent).length")
                if shown:
                    bad(f"{plabel}: セ・リーグだけの部分（支払いなど）が {shown} か所出ている")
                if await pg.evaluate("document.getElementById('stdTtl').textContent") != "パ・リーグ順位表":
                    bad(f"{plabel}: 順位表の見出しがパ・リーグになっていない")
                await pg.evaluate("switchLeague('C')")
                await pg.wait_for_timeout(200)
                if await pg.evaluate("CL.join(',')") != "DB,G,T,D,S,C":
                    bad(f"{plabel}: セ・リーグに戻らない")
                for e in errs:
                    bad(f"{plabel}: 画面のエラー {e}")
                await pg.close()
        # 担当を選んでいない人・選ぶ前の人
        for me in ["none", ""]:
            pg, errs = await open_page(browser, 390, "", me=me) if me else await open_page(browser, 390, "", me="")
            await scan(pg, f"[担当{me or 'まだ選んでいない'}]")
            for e in errs:
                bad(f"[担当{me}]: 画面のエラー {e}")
            await pg.close()
        await data_refresh(browser)
        await calc_cache(browser)
        await song_link_check(browser)
        await team_in_check(browser)
        await starter_order_check(browser)
        await wording_check(browser)
        await tap_target_check(browser)
        await swipe_check(browser)
        await pull_refresh_check(browser)
        await memory_check(browser)
        await loser_wording_check(browser)
        await next_day_check(browser)
        await home_screen_check(browser)
        await name_center_check(browser)
        await tabbar_check(browser)
        await offseason_check(browser)
        await browser.close()
    print()
    # Actions の実行結果のページ（Summary）にも一覧を書く
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## サイト検査\n\n" + ("すべて問題なし\n" if not PROBLEMS else f"問題 {len(PROBLEMS)} 件\n\n" + "\n".join(f"- {m}" for m in PROBLEMS[:100]) + "\n"))
    if PROBLEMS:
        print(f"問題 {len(PROBLEMS)} 件")
        sys.exit(1)
    print("すべて問題なし")


if __name__ == "__main__":
    asyncio.run(main())

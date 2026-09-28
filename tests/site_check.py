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

TABS = ["magic", "game", "cal", "std", "stats", "song"]


async def open_page(browser, width, theme, me="S"):
    pg = await browser.new_page(viewport={"width": width, "height": 844})
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
          const x = analyze(DATA.games, p, CONFIG); x.rows[0].w = 999;
          if (analyze(DATA.games, p, CONFIG).rows[0].w === 999) ng.push("返した結果の書き換えが使い回しに混ざる");
          return ng;
        }""")
        for m in r:
            bad(f"[計算の使い回し {lg}] {m}")
        for e in errs:
            bad(f"[計算の使い回し {lg}]: 画面のエラー {e}")
        await pg.close()


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
        await browser.close()
    print()
    if PROBLEMS:
        print(f"問題 {len(PROBLEMS)} 件")
        sys.exit(1)
    print("すべて問題なし")


if __name__ == "__main__":
    asyncio.run(main())

const fs = require("fs"), vm = require("vm");
// マジック計算の検証：残り試合の勝ち負けを全通り試して、analyze の結果（回避確定・最下位確定・M）と一致するか
// 使い方：node tests/magic_bruteforce.js （core.js は scripts/core.js、データは data/latest.json を使う）
const path = require("path"), ROOT = path.join(__dirname, "..");
const code = fs.readFileSync(path.join(ROOT, "scripts", "core.js"), "utf8");
const ctx = { console, structuredClone };
vm.createContext(ctx);
vm.runInContext(code + "\n;globalThis.__api={analyzeRaw, useLeague, CL, cmp, isPL};", ctx);
const A = ctx.__api;
const data = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "latest.json"), "utf8"));
const periods = [[3,4],[5],[6],[7],[8],[9,10]].map((m,i)=>({id:String(i),months:m}));
const mo = d => +d.slice(5,7);
let checked = 0, bad = 0;
for (const lg of ["C","P"]) {
  A.useLeague(lg);
  const excluded = lg === "C" ? ["C"] : [];
  const prevOrder = lg === "C" ? (data.prev_order||{}).order : (data.prev_order_p||{}).order;
  const cfg = { excluded, tieIsSafe: false, prevOrder };
  const targets = A.CL.filter(t => !excluded.includes(t));
  for (const p of periods) {
    const inP = data.games.filter(g => p.months.includes(mo(g.d)) && g.st !== "canc");
    const days = [...new Set(inP.map(g => g.d))].sort();
    for (const D of days) {
      // D の朝の状態：D 以降の試合は未消化に戻す
      const games = data.games.map(g => (p.months.includes(mo(g.d)) && g.d >= D && g.st !== "canc") ? { ...g, st: "sched", hs: undefined, as: undefined } : g);
      const rem = games.filter(g => p.months.includes(mo(g.d)) && g.st === "sched" && (targets.includes(g.h) || targets.includes(g.a)));
      if (rem.length > 18 || rem.length === 0) continue;
      const a = A.analyzeRaw(games, p, cfg);
      // 総当たり
      const base = {}; targets.forEach(t => base[t] = { w: 0, l: 0 });
      for (const g of games) { if (!p.months.includes(mo(g.d)) || g.st !== "final") continue;
        if (g.hs === g.as) continue;
        const W = g.hs > g.as ? g.h : g.a, L = g.hs > g.as ? g.a : g.h;
        if (base[W]) base[W].w++; if (base[L]) base[L].l++; }
      const n = rem.length, N = 1 << n;
      const res = {}; targets.forEach(t => res[t] = { canLastOrTie: false, alwaysStrictLast: true, minK: new Array(n + 2).fill(true), mine: rem.filter(g => g.h === t || g.a === t).length });
      // okAtK[t][k] = すべての「t の勝ちが k 以上」の結果で t が誰かより真に上
      const ok = {}; targets.forEach(t => ok[t] = new Array(n + 2).fill(true));
      for (let m = 0; m < N; m++) {
        const w = {}, l = {}, xw = {};
        targets.forEach(t => { w[t] = base[t].w; l[t] = base[t].l; xw[t] = 0; });
        for (let i = 0; i < n; i++) { const g = rem[i], hw = (m >> i) & 1; const W = hw ? g.h : g.a, L = hw ? g.a : g.h;
          if (W in w) { w[W]++; xw[W]++; } if (L in l) l[L]++; }
        for (const t of targets) {
          const above = targets.some(y => y !== t && A.cmp(w[t], l[t], w[y], l[y]) > 0);
          const strictLast = targets.every(y => y === t || A.cmp(w[t], l[t], w[y], l[y]) < 0);
          if (!above) res[t].canLastOrTie = true;
          if (!strictLast) res[t].alwaysStrictLast = false;
          if (!above) for (let k = 0; k <= xw[t]; k++) ok[t][k] = false;
        }
      }
      for (const r of a.rows) {
        const t = r.t, R = res[t];
        const safeB = !R.canLastOrTie, elimB = R.alwaysStrictLast;
        let selfB = null; for (let k = 0; k <= R.mine; k++) if (ok[t][k]) { selfB = k; break; }
        const selfA = r.safe ? null : r.self;
        const selfBB = safeB ? null : selfB;
        checked++;
        if (safeB !== r.safe || elimB !== r.eliminated || (!r.safe && !r.eliminated && selfA !== selfBB)) {
          bad++; if (bad <= 15) console.log(`NG ${lg} ${p.months} ${D} ${t}: safe ${r.safe}/${safeB} elim ${r.eliminated}/${elimB} self ${selfA}/${selfBB} rem ${n}`);
        }
      }
    }
  }
}
console.log(`検証 ${checked} 件・不一致 ${bad} 件`);
if (bad) process.exit(1);

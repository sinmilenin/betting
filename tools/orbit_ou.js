// Orbit Over/Under line reader - information-only add-on to the scan READER (Valentino's ruling of 08.11.2026).
// It does NOT replace or change the READER block: it is sent as its own javascript_tool call in the same Orbit tab,
// after the READER has run, and uses the same two Orbit data sources the READER uses
// (/customer/api/event/details/<event> and the multiple-market-prices websocket). Read-only.
//
// Usage (one javascript_tool call: this whole file, then the call line):
//   await window.orbitOU(['36167142', '36183266'])
// Output: one block per event with Match Odds and every Over/Under goal line Orbit offers
// (best back(size) / best lay(size) for Over and Under, and the line's matched amount),
// plus a JSON line "OUJSON <event> {...}" in the input format of tools/fit_ou.py (mo + ou),
// so it can be pasted straight into a prices file.
window.orbitOU = async function (events) {
  const csrf = () => (document.cookie.split('; ').find(c => c.startsWith('CSRF-TOKEN=')) || '').split('=')[1] || '';
  const H = () => ({ 'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf() });
  const n0 = x => Math.round(+x || 0);
  const lv = a => (a || []).filter(x => x && x.odds).sort((p, q) => p.index - q.index);
  const prices = subs => new Promise(resolve => {
    const got = {}, chunks = []; for (let i = 0; i < subs.length; i += 40) chunks.push(subs.slice(i, i + 40));
    if (!chunks.length) return resolve(got);
    const ws = new WebSocket(`wss://${location.host}/customer/ws/multiple-market-prices/${String(Math.floor(Math.random() * 1000)).padStart(3, '0')}/${Math.random().toString(36).slice(2, 10)}/websocket`);
    let i = -1, t = null, done = false;
    const fin = () => { if (done) return; done = true; clearTimeout(t); clearTimeout(hard); try { ws.close(); } catch (e) { } resolve(got); };
    const hard = setTimeout(fin, 8000 + chunks.length * 5000);
    const next = () => { clearTimeout(t); if (++i >= chunks.length) return fin(); ws.send(JSON.stringify([JSON.stringify(chunks[i].map(s => ({ marketId: s.id, eventId: s.ev, applicationType: 'WEB' })))])); t = setTimeout(next, 5000); };
    ws.onmessage = e => {
      const d = String(e.data);
      if (d[0] === 'o') { ws.send(JSON.stringify([JSON.stringify([{ applicationType: 'WEB' }])])); return next(); }
      if (d[0] !== 'a') return;
      for (const s of JSON.parse(d.slice(1))) { let m; try { m = JSON.parse(s); } catch (x) { continue; } for (const x of [].concat(m)) if (x && x.id && x.rc) got[x.id] = x; }
      if (i >= 0 && i < chunks.length && chunks[i].every(s => got[s.id])) next();
    };
    ws.onerror = fin; ws.onclose = fin;
  });
  const out = [], subs = [], meta = {}, bad = [];
  for (const ev of events.map(String)) {
    try {
      const r = await fetch('/customer/api/event/details/' + ev, { credentials: 'include', headers: H() });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const mk = [];
      for (const c of (await r.json()).marketCatalogues || []) {
        const t = c.description.marketType, m = t.match(/^OVER_UNDER_(\d)(\d)$/);
        if (t === 'MATCH_ODDS' || m) {
          mk.push({ id: c.marketId, type: t, line: m ? +(m[1] + '.' + m[2]) : null, names: Object.fromEntries(c.runners.map(x => [x.selectionId, x.runnerName])), order: c.runners.map(x => x.runnerName) });
          subs.push({ id: c.marketId, ev });
        }
      }
      meta[ev] = mk;
    } catch (e) { bad.push(ev + ': ' + e.message); }
  }
  let px = await prices(subs);
  const miss = subs.filter(s => !px[s.id]);
  if (miss.length) Object.assign(px, await prices(miss));
  const pair = r => [r && r.b[0] ? r.b[0].odds : null, r && r.l[0] ? r.l[0].odds : null];
  const txt = r => r ? `back ${r.b[0] ? r.b[0].odds + '(' + n0(r.b[0].amount) + ')' : '-'} lay ${r.l[0] ? r.l[0].odds + '(' + n0(r.l[0].amount) + ')' : '-'}` : '-';
  for (const ev of Object.keys(meta)) {
    const js = { mo: {}, ou: {} }, lines = [`== event ${ev}`];
    for (const m of meta[ev].sort((a, b) => (a.line || 0) - (b.line || 0))) {
      const s = px[m.id];
      if (!s) { lines.push(`${m.type}: no prices`); continue; }
      const rs = (s.rc || []).map(r => ({ name: m.names[r.id] || String(r.id), b: lv(r.bdatb), l: lv(r.bdatl) }));
      if (m.type === 'MATCH_ODDS') {
        const teams = m.order.filter(n => !/draw/i.test(n)), h = rs.find(r => r.name === teams[0]), a = rs.find(r => r.name === teams[1]), d = rs.find(r => /draw/i.test(r.name));
        js.mo = { home: pair(h), draw: pair(d), away: pair(a) };
        lines.push(`MO (matched ${n0(s.tv)}): ` + rs.map(r => r.name + ' ' + txt(r)).join(' | '));
      } else {
        const o = rs.find(r => /over/i.test(r.name)), u = rs.find(r => /under/i.test(r.name));
        js.ou[m.line.toFixed(1)] = { over: pair(o), under: pair(u), matched: n0(s.tv) };
        lines.push(`O/U ${m.line} (matched ${n0(s.tv)}): Over ${txt(o)} | Under ${txt(u)}`);
      }
    }
    lines.push('OUJSON ' + ev + ' ' + JSON.stringify(js));
    out.push(lines.join('\n'));
  }
  return out.join('\n\n') + (bad.length ? '\nPROBLEMS: ' + bad.join('; ') : '\nPROBLEMS: none');
};

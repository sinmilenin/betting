#!/usr/bin/env python3
"""results.py - final scores (after 90 minutes) for Lay Ledger rows, from API-Football.

Input : results/pending.json - a JSON list of {"id", "date" (YYYY-MM-DD), "ko" (HH:MM),
        "match" ("Home v Away"), "cell"} in Europe/Ljubljana time. Extra fields are ignored.
Output: results/results.json - one object per input row:
          id, state ("final" | "not_finished" | "void" | "not_found" | "ambiguous"),
          score   - 90-minute score written in the ROW's order (row home first), "" if none,
          result  - "won" / "lost" for the lay when state is final, else "",
          api     - "Home v Away" as API-Football names them, status, kick-off, fixture id,
          swapped - true when API-Football lists the teams the other way round,
          conf    - name-match confidence 0..1, note.
        results/_last.txt - a short readable summary.

Requests: one /fixtures?date= call per distinct date (plus the next day for kick-offs
after 22:00). The API key comes from the APISPORTS_KEY environment variable.
"""
import datetime
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import form  # noqa: E402  (reuses its api() helper, key loading and SSL context)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IN = os.path.join(ROOT, "results", "pending.json")
OUT = os.path.join(ROOT, "results", "results.json")
TXT = os.path.join(ROOT, "results", "_last.txt")
TZ = "Europe/Ljubljana"

FINAL = {"FT", "AET", "PEN"}
VOID = {"PST", "CANC", "ABD", "AWD", "WO"}

STOP = {"fc", "cf", "cd", "sc", "ac", "afc", "ca", "club", "de", "del", "la", "el", "the", "ud", "sd",
        "fk", "nk", "sk", "if", "bk", "ssc", "us", "as", "ss", "cs", "rc", "sv", "vfl", "vfb", "tsv",
        "fsv", "spvgg", "1", "calcio", "futebol", "clube", "deportivo", "sporting", "atletico", "real",
        "city", "united", "town", "football", "and", "y", "e", "do", "da", "di", "mfk", "gfa", "cda",
        "ssd", "aj", "ad", "sad", "cp", "ce", "pfc", "ofk", "hnk", "gnk", "kv", "kaa", "krc", "rsc"}
WOMEN = {"w", "women", "womens", "ladies", "fem", "femenino", "feminino", "femminile", "frauen", "dames"}
RESERVE = {"b", "ii", "2", "reserves", "reserve", "u23", "u21", "u19", "u20", "u18", "u17", "promesas",
           "atletico_b"}


def fold(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[()'.\-/]", " ", s)
    return s.split()


def profile(name):
    toks = fold(name)
    women = any(t in WOMEN for t in toks)
    reserve = sorted(t for t in toks if t in RESERVE and t not in {"2"} or re.fullmatch(r"u\d\d", t))
    core = [t for t in toks if t not in STOP and t not in WOMEN and t not in RESERVE and len(t) > 1]
    return {"core": core or toks, "women": women, "reserve": bool(reserve)}


def tok_sim(a, b):
    """Similarity of two token lists: exact or prefix matches (Kalba ~ Kalbaa, Sp ~ Sport)."""
    if not a or not b:
        return 0.0
    hit = 0
    for x in a:
        if any(x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)))
               for y in b):
            hit += 1
    return hit / max(len(a), len(b)) if hit else 0.0


def name_sim(orbit, api):
    p, q = profile(orbit), profile(api)
    s = tok_sim(p["core"], q["core"])
    a, b = "".join(p["core"]), "".join(q["core"])
    if a and b and (a in b or b in a) and min(len(a), len(b)) >= 4:
        s = max(s, 0.85)
    if p["women"] != q["women"]:
        s *= 0.3
    if p["reserve"] != q["reserve"]:
        s *= 0.5
    return s


def lay_result(cell, h, a):
    """A lay wins unless the 90-minute score lands in the cell."""
    if cell == "AOHW":
        hit = h > a and h >= 4
    elif cell == "AOAW":
        hit = a > h and a >= 4
    elif cell == "AOD":
        hit = h == a and h >= 4
    else:
        ch, ca = (int(x) for x in cell.split("-"))
        hit = (h, a) == (ch, ca)
    return "lost" if hit else "won"


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def main():
    form.KEY = form.load_key()
    form.CTX = form.make_context()
    rows = json.load(open(IN, encoding="utf-8")) if os.path.exists(IN) else []
    dates = set()
    for r in rows:
        dates.add(r["date"])
        if minutes(r.get("ko", "12:00")) >= 22 * 60:
            d = datetime.date.fromisoformat(r["date"]) + datetime.timedelta(days=1)
            dates.add(d.isoformat())
    fixtures, errors = {}, []
    for d in sorted(dates):
        try:
            fixtures[d] = form.api("/fixtures", date=d, timezone=TZ)
        except form.ApiError as e:
            fixtures[d] = []
            errors.append("%s: %s" % (d, e))
    out, lines = [], []
    for r in rows:
        home, away = [x.strip() for x in re.split(r"\s+v\s+", r["match"], maxsplit=1)]
        ko = minutes(r.get("ko", "12:00"))
        cands = []
        for d, lst in fixtures.items():
            for f in lst:
                fdate = f["fixture"]["date"][:10]
                if fdate != r["date"]:
                    continue
                fko = minutes(f["fixture"]["date"][11:16])
                gap = abs(fko - ko)
                if gap > 90:
                    continue
                th, ta = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
                straight = min(name_sim(home, th), name_sim(away, ta)), \
                    (name_sim(home, th) + name_sim(away, ta)) / 2
                swapped = min(name_sim(home, ta), name_sim(away, th)), \
                    (name_sim(home, ta) + name_sim(away, th)) / 2
                for (lo, avg), sw in ((straight, False), (swapped, True)):
                    # both names must fit reasonably, or one fits strongly and kick-off agrees
                    if lo >= 0.5 or (avg >= 0.6 and gap <= 15 and lo >= 0.25):
                        conf = avg - (0.1 if gap > 15 else 0) - (0.05 if sw else 0)
                        cands.append((conf, sw, gap, f))
        cands.sort(key=lambda c: -c[0])
        rec = {"id": r["id"], "match": r["match"], "cell": r.get("cell", ""), "state": "not_found",
               "score": "", "result": "", "api": "", "status": "", "api_ko": "", "fixture": None,
               "swapped": False, "conf": 0, "note": ""}
        if not cands:
            rec["note"] = "no API-Football fixture on %s within 90 min of %s with matching names" % (
                r["date"], r.get("ko"))
        else:
            conf, sw, gap, f = cands[0]
            if len(cands) > 1 and cands[1][3]["fixture"]["id"] != f["fixture"]["id"] \
                    and cands[1][0] >= conf - 0.1:
                rec["state"] = "ambiguous"
                rec["note"] = "two fixtures fit: %s | %s" % (
                    " v ".join([f["teams"]["home"]["name"], f["teams"]["away"]["name"]]),
                    " v ".join([cands[1][3]["teams"]["home"]["name"], cands[1][3]["teams"]["away"]["name"]]))
            st = f["fixture"]["status"]["short"]
            rec.update(api="%s v %s" % (f["teams"]["home"]["name"], f["teams"]["away"]["name"]),
                       status=st, api_ko=f["fixture"]["date"][11:16], fixture=f["fixture"]["id"],
                       swapped=sw, conf=round(conf, 2),
                       league="%s, %s" % (f["league"].get("country"), f["league"].get("name")))
            if rec["state"] != "ambiguous":
                ft = (f.get("score") or {}).get("fulltime") or {}
                if st in FINAL and ft.get("home") is not None:
                    h, a = ft["home"], ft["away"]
                    if sw:
                        h, a = a, h
                    rec["state"] = "final"
                    rec["score"] = "%d-%d" % (h, a)
                    if rec["cell"]:
                        rec["result"] = lay_result(rec["cell"], h, a)
                    if st != "FT":
                        rec["note"] = "went to %s; score is after 90 minutes" % st
                elif st in VOID:
                    rec["state"] = "void"
                    rec["note"] = "status %s (%s)" % (st, f["fixture"]["status"].get("long"))
                else:
                    rec["state"] = "not_finished"
                    rec["note"] = "status %s" % st
        out.append(rec)
        lines.append("%-12s %-5s %-50s %-5s %-4s -> %s%s  [%s %s, conf %.2f%s] %s" % (
            rec["state"], r.get("ko", ""), r["match"][:50], rec["cell"], rec["score"] or "-",
            rec["result"] or "-", "", rec["api"], rec["status"], rec["conf"],
            ", swapped" if rec["swapped"] else "", rec["note"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    head = "RESULTS %s UTC | %d rows | dates %s | API-Football requests %d%s" % (
        datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), len(rows), ", ".join(sorted(dates)),
        form.CALLS, (" | ERRORS: " + "; ".join(errors)) if errors else "")
    open(TXT, "w", encoding="utf-8").write(head + "\n" + "\n".join(lines) + "\n")
    print(head)
    print("\n".join(lines))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""run.py - fetch form (API-Football) and prices (Matchbook) for each match, one text file per match.

Matches come from matches.txt next to this script, one per line:

    Italy v Turkey
    id:768 v id:777                  team ids when a name is ambiguous in API-Football
    Italy v Turkey | mb=123456789    force a Matchbook event id
    Italy v Turkey | mb=Italy v Turkiye   different spelling on Matchbook
    Italy v Turkey | n=20 | xg=10    games per team, xG lookups per team

Lines starting with # are ignored. If matches.txt has no matches, the script asks for one.
Results go to the out folder.
"""
import argparse
import contextlib
import datetime
import io
import os
import re
import sys
import traceback

import form
try:
    import matchbook
except ImportError:
    matchbook = None

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = r"\s+(?:vs\.?|v)\s+"


class Tee(io.TextIOBase):
    """Write to several streams at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def parse_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.split("|")]
    teams = re.split(SPLIT, parts[0], maxsplit=1, flags=re.I)
    if len(teams) != 2 or not teams[0].strip() or not teams[1].strip():
        return {"error": 'cannot read "%s" (expected: Home v Away)' % line}
    m = {"home": teams[0].strip(), "away": teams[1].strip(), "mb": None, "n": None, "xg": None}
    for opt in parts[1:]:
        if "=" not in opt:
            continue
        k, v = [x.strip() for x in opt.split("=", 1)]
        k = k.lower()
        if k == "mb":
            m["mb"] = v
        elif k in ("n", "xg") and v.isdigit():
            m[k] = int(v)
    return m


def read_matches():
    path = os.path.join(HERE, "matches.txt")
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                m = parse_line(line)
                if m:
                    out.append(m)
    return out


def ask_match():
    print("matches.txt has no matches, so type one now.")
    home = input("Home team: ").strip()
    away = input("Away team: ").strip()
    if not home or not away:
        return []
    return [{"home": home, "away": away, "mb": None, "n": None, "xg": None}]


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "team"


def form_block(m, n, xg):
    """API-Football section as text; never raises."""
    buf = io.StringIO()
    err = Tee(sys.stderr, buf)
    before = form.CALLS
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        form.header(m.get("title") or m["home"], "" if m.get("title") else m["away"])
        countries = {}
        failed = []

        def one(team, other_country=None):
            try:
                countries[team] = form.report(team, n, xg, other_country)
                return True
            except SystemExit as e:
                return str(e.code)
            except form.ApiError as e:
                return "API-Football error: %s" % e
            except Exception:
                return "unexpected error\n%s" % traceback.format_exc()

        for team, other in ((m["home"], m["away"]), (m["away"], m["home"])):
            r = one(team)
            if r is not True:
                failed.append((team, other, r))
        for team, other, r in failed:
            if "Ambiguous" in r and countries.get(other):
                print("(retrying %s using the opponent's country, %s)" % (team, countries[other]))
                r2 = one(team, countries[other])
                if r2 is True:
                    continue
                r = r2
            print("!! %s: %s\n" % (team, r))
        try:
            form.print_league_tables()
        except Exception:
            print("!! league table: unexpected error\n%s" % traceback.format_exc())
        print("(%d API-Football requests used)" % (form.CALLS - before))
    return buf.getvalue()


def matchbook_block(m, state):
    """Matchbook section as text; never raises."""
    if state.get("off"):
        return "MATCHBOOK: skipped (%s)" % state["off"]
    try:
        mb = m.get("mb")
        if mb and mb.isdigit():
            return matchbook.book_text(event_id=mb)
        home, away = m["home"], m["away"]
        if mb:
            alt = re.split(SPLIT, mb, maxsplit=1, flags=re.I)
            if len(alt) == 2:
                home, away = alt[0].strip(), alt[1].strip()
        if home.lower().startswith("id:") or away.lower().startswith("id:"):
            return ("MATCHBOOK: skipped, the teams are given as API-Football ids. "
                    "Add | mb=<Matchbook event id or names> to the line.")
        if "events" not in state:
            state["events"] = matchbook.list_events(state["hours"], hours_back=3)
        return matchbook.book_text(home, away, events=state["events"], hours=state["hours"])
    except matchbook.NotConfigured as e:
        state["off"] = str(e)
        return "MATCHBOOK: skipped (%s)" % e
    except matchbook.MBError as e:
        if e.code in (0, 400, 401, 403):
            state["off"] = "earlier error: %s" % e
        return "MATCHBOOK: error: %s" % e
    except Exception:
        return "MATCHBOOK: unexpected error\n%s" % traceback.format_exc()


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Form and prices for the matches in matches.txt.")
    p.add_argument("--n", type=int, default=20, help="games per team (default 20)")
    p.add_argument("--xg", type=int, default=10, help="xG lookups per team (default 10)")
    p.add_argument("--hours", type=int, default=96,
                   help="how far ahead to look for the match on Matchbook (default 96)")
    p.add_argument("--no-matchbook", action="store_true")
    p.add_argument("--no-form", action="store_true")
    p.add_argument("--slate", action="store_true",
                   help="when matches.txt has no matches, run every upcoming fixture "
                        "in the competitions listed in slate_leagues.txt")
    p.add_argument("--slate-hours", type=int, default=30)
    a = p.parse_args()

    matches = read_matches()
    slate_index = None
    if not matches and a.slate:
        form.setup()
        rules = form.load_slate_leagues(os.path.join(HERE, "slate_leagues.txt"))
        if not rules:
            sys.exit("slate_leagues.txt is missing or empty")
        fx = form.slate(rules, a.slate_hours)
        slate_index = ["SLATE %s local | %d fixtures in the next %d hours" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), len(fx), a.slate_hours)]
        for m in fx:
            local = m["kickoff"].astimezone().strftime("%a %d.%m %H:%M")
            slate_index.append("%s | %s v %s | %s (%s) | ids %s v %s" % (
                local, m["home"]["name"], m["away"]["name"], m["league"]["name"],
                m["league"].get("country"), m["home"]["id"], m["away"]["id"]))
            matches.append({"home": "id:%s" % m["home"]["id"], "away": "id:%s" % m["away"]["id"],
                            "title": "%s v %s" % (m["home"]["name"], m["away"]["name"]),
                            "mb": None, "n": None, "xg": None})
        print("\n".join(slate_index))
    if not matches and sys.stdin and sys.stdin.isatty():
        matches = ask_match()
    if not matches:
        sys.exit("No matches to run. Put them in matches.txt, one per line: Home v Away")

    out_dir = os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    log = ["RUN %s local | %d match(es)" % (stamp, len(matches))]
    state = {"hours": a.hours}
    if a.no_matchbook or matchbook is None:
        state["off"] = "switched off for this run"
    form_ok = not a.no_form
    if form_ok:
        try:
            form.setup()
        except SystemExit as e:
            form_ok = False
            log.append("API-Football off: %s" % e.code)
            print("API-Football off: %s" % e.code)

    for i, m in enumerate(matches, 1):
        if "error" in m:
            log.append("line skipped: %s" % m["error"])
            print("Skipped: %s" % m["error"])
            continue
        title = m.get("title") or "%s v %s" % (m["home"], m["away"])
        print("[%d/%d] %s" % (i, len(matches), title))
        blocks = ["MATCH %s | fetched %s local" % (title, stamp), ""]
        if form_ok:
            print("    form and xG from API-Football ...")
            blocks.append(form_block(m, m["n"] or a.n, a.xg if m["xg"] is None else m["xg"]))
        else:
            blocks.append("API-FOOTBALL: skipped")
        blocks.append("-" * 70)
        print("    prices from Matchbook ...")
        blocks.append(matchbook_block(m, state))
        text = "\n".join(blocks)
        name = "%s.txt" % safe_name(title)
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        problems = [ln for ln in text.splitlines()
                    if ln.startswith("!!") or (ln.startswith("MATCHBOOK: ")
                                               and "switched off" not in ln)]
        log.append("%s -> out/%s%s" % (title, name,
                                        "".join("\n    " + x for x in problems)))
        print("    saved out\\%s%s" % (name, " (with problems, see file)" if problems else ""))

    mb_calls = matchbook.CALLS if matchbook else 0
    log.append("Requests used: API-Football %d, Matchbook %d" % (form.CALLS, mb_calls))
    if slate_index:
        with open(os.path.join(out_dir, "_slate.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(slate_index) + "\n")
    with open(os.path.join(out_dir, "_last_run.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    print()
    print("Finished. Requests used: API-Football %d, Matchbook %d." % (form.CALLS, mb_calls))
    print("Now tell Claude the run is done.")


if __name__ == "__main__":
    main()

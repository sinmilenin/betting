#!/usr/bin/env python3
"""form.py - recent results (and xG where the provider has it) for two teams, from API-Football.

Usage:
    python form.py "South Korea" "Uzbekistan"
    python form.py "South Korea" "Uzbekistan" --n 20 --xg 10
    python form.py id:17 id:1568            # use team ids when a name is ambiguous

Key: put your API-Football key in a file called apikey.txt next to this script
     (or set the environment variable APISPORTS_KEY).

Requests used per run: 4 + (2 x --xg). With --n 20 --xg 10 that is 24.
"""
import argparse
import datetime
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}
CALLS = 0
KEY = None
CTX = None


class ApiError(Exception):
    pass


def load_key():
    key = os.environ.get("APISPORTS_KEY", "").strip()
    if key:
        return key
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "apikey.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            key = f.read().strip()
        if key:
            return key
    sys.exit("No API key found. Save it in apikey.txt next to form.py.")


def make_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def api(path, **params):
    """One GET to API-Football. Returns the 'response' list or raises ApiError."""
    global CALLS
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        req = urllib.request.Request(url, headers={
            "x-apisports-key": KEY, "Accept": "application/json",
            "User-Agent": "betfeed/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise ApiError("HTTP %s from API-Football" % e.code)
        except urllib.error.URLError as e:
            raise ApiError("cannot reach API-Football (%s)" % e.reason)
        CALLS += 1
        errs = data.get("errors")
        if errs:
            msg = json.dumps(errs)
            if attempt == 0 and ("rateLimit" in msg or "Too many" in msg):
                print("  (API-Football rate limit reached, waiting 61 s)", file=sys.stderr)
                time.sleep(61)
                continue
            raise ApiError(msg)
        return data.get("response", [])
    raise ApiError("rate limit")


LAST_COUNTRY = {}
KNOWN_NAMES = {}         # team id -> name, filled by the slate so no lookup is needed
NOT_STARTED = {"NS", "TBD"}


EXCLUDE = []             # substrings of league names to skip (filled from slate_leagues.txt)


def load_slate_leagues(path):
    """Read 'country | league name' lines; '*' as country means any country.
    A line 'exclude | word' skips every league whose name contains that word."""
    rules = []
    if not os.path.exists(path):
        return rules
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or "|" not in line:
                continue
            country, name = [x.strip().lower() for x in line.split("|", 1)]
            if country == "exclude":
                EXCLUDE.append(name)
            else:
                rules.append((country, name))
    return rules


def league_wanted(fixture, rules):
    lg = fixture["league"]
    country = (lg.get("country") or "").lower()
    name = (lg.get("name") or "").lower()
    if any(x in name for x in EXCLUDE):
        return False
    for c, n in rules:
        if (c == "*" or c == country) and n in name:
            return True
    return False


def slate(rules, hours=30):
    """Fixtures not yet started in the wanted competitions, kicking off within `hours`."""
    now = datetime.datetime.now(datetime.timezone.utc)
    out, seen = [], set()
    for d in (0, 1, 2):
        day = (now + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            fx = api("/fixtures", date=day)
        except ApiError as e:
            print("  (fixtures for %s not available: %s)" % (day, e), file=sys.stderr)
            continue
        for f in fx:
            fid = f["fixture"]["id"]
            if fid in seen or f["fixture"]["status"]["short"] not in NOT_STARTED:
                continue
            seen.add(fid)
            try:
                ko = datetime.datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (now <= ko <= now + datetime.timedelta(hours=hours)):
                continue
            if not league_wanted(f, rules):
                continue
            h, a = f["teams"]["home"], f["teams"]["away"]
            KNOWN_NAMES[h["id"]] = h["name"]
            KNOWN_NAMES[a["id"]] = a["name"]
            out.append({"kickoff": ko, "home": h, "away": a, "league": f["league"]})
    out.sort(key=lambda m: m["kickoff"])
    return out
LEAGUE_CACHE = {}        # (league id, season) -> text block or None
LEAGUE_TEAMS = {}        # (league id, season) -> ids of the teams in that table
LEAGUES_SEEN = {}        # team id -> [team name, [(league id, season, name), ...]] for the current match
SKIP_TABLE = ("friendl",)


def league_table(league_id, season, name):
    """Standings with home/away splits and league averages, as text; None if no table."""
    key = (league_id, season)
    if key in LEAGUE_CACHE:
        return LEAGUE_CACHE[key]
    text = None
    try:
        res = api("/standings", league=league_id, season=season)
    except ApiError as e:
        LEAGUE_CACHE[key] = None
        print("  (standings for %s %s not available: %s)" % (name, season, e), file=sys.stderr)
        return None
    groups = res[0]["league"].get("standings") if res else None
    if groups:
        rows = [r for g in groups for r in g]
        LEAGUE_TEAMS[key] = set(r["team"]["id"] for r in rows)
        def z(v):
            return v or 0          # some tables (cup groups, split seasons) carry empty cells
        hp = sum(z(r["home"]["played"]) for r in rows)
        hgf = sum(z(r["home"]["goals"]["for"]) for r in rows)
        hga = sum(z(r["home"]["goals"]["against"]) for r in rows)
        lines = ["LEAGUE TABLE %s %s | %d teams | %d games played" % (name, season, len(rows), hp)]
        if hp:
            lines.append("League averages per game: home %.2f, away %.2f, total %.2f" % (
                hgf / hp, hga / hp, (hgf + hga) / hp))
        lines.append("%-3s %-22s %3s | %-13s | %-13s | %s" % (
            "#", "team", "Pts", "home P GF-GA", "away P GF-GA", "all P GF-GA"))
        for r in rows:
            h, a, t = r["home"], r["away"], r["all"]
            grp = (" [%s]" % r["group"]) if len(groups) > 1 and r.get("group") else ""
            lines.append("%-3s %-22s %3s | %2d %3d-%-3d     | %2d %3d-%-3d     | %2d %3d-%d%s" % (
                z(r["rank"]), r["team"]["name"][:22], z(r["points"]),
                z(h["played"]), z(h["goals"]["for"]), z(h["goals"]["against"]),
                z(a["played"]), z(a["goals"]["for"]), z(a["goals"]["against"]),
                z(t["played"]), z(t["goals"]["for"]), z(t["goals"]["against"]), grp))
        text = "\n".join(lines)
    LEAGUE_CACHE[key] = text
    return text


def note_leagues(fixtures, team_id, team_name):
    """Remember the competitions of a team's recent fixtures (newest first) for the table lookup."""
    seen = LEAGUES_SEEN.setdefault(team_id, [team_name, []])[1]
    for f in fixtures[:12]:
        lg = f["league"]
        if any(w in (lg.get("name") or "").lower() for w in SKIP_TABLE):
            continue
        key = (lg["id"], lg["season"], lg["name"])
        if key not in seen:
            seen.append(key)


def print_league_tables():
    """Print each team's own league table: the newest competition whose table lists that team.

    Teams from different divisions (cup ties) get one table each, so both sides have
    league averages; teams in the same table get it once.
    """
    printed = {}
    for tid, (tname, leagues) in list(LEAGUES_SEEN.items()):
        found = None
        for lid, season, name in leagues:
            text = league_table(lid, season, name)
            if text and tid in LEAGUE_TEAMS.get((lid, season), ()):
                found = ((lid, season), name, text)
                break
        if not found:
            print("(no league table available for %s: tried %s)" % (tname, ", ".join(
                "%s %s" % (n, s) for _, s, n in leagues[:4]) or "no competitions"))
            print()
            continue
        key, name, text = found
        if key in printed:
            print("(%s: same table as %s, shown above)" % (tname, printed[key]))
            print()
            continue
        printed[key] = tname
        print("TABLE FOR %s" % tname)
        print(text)
        print()
    LEAGUES_SEEN.clear()


def find_team(query, prefer_country=None):
    """Return (id, name). Accepts 'id:123' or a name of 3+ characters."""
    if query.lower().startswith("id:"):
        tid = int(query[3:])
        if tid in KNOWN_NAMES:
            return tid, KNOWN_NAMES[tid]
        res = api("/teams", id=tid)
        name = res[0]["team"]["name"] if res else str(tid)
        LAST_COUNTRY[tid] = res[0]["team"].get("country") if res else None
        return tid, name
    res = api("/teams", search=query)
    teams = [r["team"] for r in res]
    if not teams:
        sys.exit('No team found for "%s".' % query)
    exact = [t for t in teams if t["name"].lower() == query.lower()]
    pool = exact or teams
    for t in teams:
        LAST_COUNTRY[t["id"]] = t.get("country")
    if prefer_country and len(pool) > 1:
        same = [t for t in pool if (t.get("country") or "").lower() == prefer_country.lower()]
        if len(same) == 1:
            print('NOTE: "%s" matched %d teams; picked %s (%s) because the opponent is from %s. '
                  'Others: %s' % (query, len(pool), same[0]["name"], same[0].get("country"),
                                  prefer_country, ", ".join("%s (%s)" % (t["name"], t.get("country"))
                                                             for t in pool if t is not same[0])))
            return same[0]["id"], same[0]["name"]
    if len(pool) == 1:
        return pool[0]["id"], pool[0]["name"]
    national = [t for t in pool if t.get("national")]
    if exact and len(national) == 1:
        return national[0]["id"], national[0]["name"]
    if exact and not national and len(exact) == 1:
        return exact[0]["id"], exact[0]["name"]
    print('"%s" matches several teams. Use id:<number> instead of the name:' % query)
    for t in teams[:25]:
        print("  id:%-6s %s (%s)%s" % (t["id"], t["name"], t.get("country"),
                                        " national" if t.get("national") else ""))
    sys.exit('Ambiguous team name "%s".' % query)


def get_fixtures(team_id, n):
    """Last n finished fixtures, newest first."""
    try:
        fx = api("/fixtures", team=team_id, last=min(n + 5, 99))
    except ApiError as first:
        # Some plans do not allow 'last'; fall back to whole seasons.
        print("  ('last' lookup refused: %s; trying whole seasons)" % first, file=sys.stderr)
        year = datetime.date.today().year
        fx = []
        for season in (year, year - 1, year - 2):
            try:
                fx += api("/fixtures", team=team_id, season=season)
            except ApiError as e:
                print("  (season %s not available: %s)" % (season, e), file=sys.stderr)
    seen, out = set(), []
    for f in fx:
        fid = f["fixture"]["id"]
        if fid in seen or f["fixture"]["status"]["short"] not in FINISHED:
            continue
        seen.add(fid)
        out.append(f)
    out.sort(key=lambda f: f["fixture"]["date"], reverse=True)
    return out[:n]


def get_xg(fixture_id):
    """{team_id: xG float} for one fixture; empty when the provider has none."""
    out = {}
    try:
        res = api("/fixtures/statistics", fixture=fixture_id)
    except ApiError:
        return out
    for side in res:
        for s in side.get("statistics", []):
            if s.get("type") == "expected_goals" and s.get("value") not in (None, ""):
                try:
                    out[side["team"]["id"]] = float(s["value"])
                except (TypeError, ValueError):
                    pass
    return out


def row(f, team_id, xg):
    home, away = f["teams"]["home"], f["teams"]["away"]
    is_home = home["id"] == team_id
    opp = away if is_home else home
    ft = f["score"].get("fulltime") or {}
    gh = ft.get("home") if ft.get("home") is not None else f["goals"]["home"]
    ga = ft.get("away") if ft.get("away") is not None else f["goals"]["away"]
    gf, gc = (gh, ga) if is_home else (ga, gh)
    status = f["fixture"]["status"]["short"]
    flag = "" if status == "FT" else " [%s, 90-min score shown]" % status
    city = (f["fixture"].get("venue") or {}).get("city") or "?"
    x = ""
    if xg:
        xf, xa = xg.get(team_id), xg.get(opp["id"])
        if xf is not None and xa is not None:
            x = " | xG %.2f-%.2f" % (xf, xa)
    line = "%s %s %d-%d v %s | %s | %s%s%s" % (
        f["fixture"]["date"][:10], "H" if is_home else "A", gf, gc, opp["name"],
        f["league"]["name"], city, x, flag)
    return line, is_home, gf, gc


def summarise(label, games):
    if not games:
        return "%s: no games" % label
    n = len(games)
    gf = sum(g[0] for g in games)
    gc = sum(g[1] for g in games)
    return "%s: %d games, scored %d (%.2f), conceded %d (%.2f), blanks %d, clean sheets %d" % (
        label, n, gf, gf / n, gc, gc / n,
        sum(1 for g in games if g[0] == 0), sum(1 for g in games if g[1] == 0))


def report(query, n, n_xg, prefer_country=None):
    """Print the form block; return the team's country (used to resolve the opponent)."""
    tid, name = find_team(query, prefer_country)
    fixtures = get_fixtures(tid, n)
    note_leagues(fixtures, tid, name)
    lines, home, away = [], [], []
    for i, f in enumerate(fixtures):
        xg = get_xg(f["fixture"]["id"]) if i < n_xg else {}
        line, is_home, gf, gc = row(f, tid, xg)
        lines.append(line)
        (home if is_home else away).append((gf, gc))
    print("=== %s (id %s): last %d finished games ===" % (name, tid, len(fixtures)))
    for line in lines:
        print(line)
    print(summarise("Listed home", home))
    print(summarise("Listed away", away))
    print(summarise("All", home + away))
    print()
    return LAST_COUNTRY.get(tid)


def header(home, away):
    now = datetime.datetime.now(datetime.timezone.utc)
    print("FORM DATA %s | source API-Football | %s UTC" % (
        ("%s v %s" % (home, away)) if away else home, now.strftime("%Y-%m-%d %H:%M")))
    print("H/A = listed home/away (city shown so neutral venues can be spotted). "
          "Scores are after 90 minutes, team's goals first.")
    print()


def setup():
    global KEY, CTX
    KEY, CTX = load_key(), make_context()


def main():
    p = argparse.ArgumentParser(description="Recent results and xG for two teams.")
    p.add_argument("home")
    p.add_argument("away")
    p.add_argument("--n", type=int, default=20, help="games per team (default 20)")
    p.add_argument("--xg", type=int, default=10,
                   help="look up xG for the latest N games per team (default 10, 0 = off)")
    a = p.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    setup()
    header(a.home, a.away)
    try:
        report(a.home, a.n, a.xg)
        report(a.away, a.n, a.xg)
        print_league_tables()
    except ApiError as e:
        sys.exit("API-Football error: %s" % e)
    print("(%d API requests used)" % CALLS)


if __name__ == "__main__":
    main()

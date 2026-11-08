#!/usr/bin/env python3
"""Market goal-rate fit for the correct-score lay system (pure Python, no packages needed).

Two fits from the same exchange prices:
  fit2 - Match Odds + Over/Under 2.5 only, rho fixed at -0.10 (the current fallback grid).
  fit3 - Match Odds + every Over/Under line with enough money on it (1.5, 2.5, 3.5, 4.5 ...),
         with rho fitted as well (bounded -0.25 .. +0.15). Information-only from 09.11.2026.

Input: a JSON file (or stdin) like
{
  "mo":  {"home": [back, lay], "draw": [back, lay], "away": [back, lay]},
  "ou":  {"1.5": {"over": [back, lay], "under": [back, lay], "matched": 4100},
          "2.5": {"over": [2.06, 2.10], "under": [1.85, 1.88], "matched": 9000},
          "3.5": {"over": [3.1, 3.3], "under": [1.42, 1.48], "matched": 800}},
  "lays": {"2-2": 16.5, "1-0": 9.4}          (optional: fill-for-3 lay prices to price)
}
A price pair may be [back, null] or [null, lay]; the mid is used when both sides exist, otherwise
the one side shown. Each market is normalised to remove the margin.

Weights: Match Odds outcomes 1 each, O/U 2.5 weight 1, any other line
min(1, matched / 1000), and a line with under 200 matched or a missing side is left out.

Output (JSON on stdout): fit2 and fit3 (lambdaH, lambdaA, rho, fit error, lines used),
the probability of every cell (0-0..3-3, AOHW, AOAW, AOD) under both fits, and for each lay
in "lays" the market-only EV (4% commission) under both fits.

Usage: python3 tools/fit_ou.py prices.json
"""
import json
import math
import sys

COMM = 0.96
CELLS = [f"{i}-{j}" for i in range(4) for j in range(4)] + ["AOHW", "AOAW", "AOD"]


def mid(pair):
    if pair is None:
        return None
    b, l = (pair + [None, None])[:2] if isinstance(pair, list) else (pair, None)
    if b and l:
        return (b + l) / 2
    return b or l


def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def grid(a, b, rho, n=11):
    g = {}
    for i in range(n):
        for j in range(n):
            p = pois(i, a) * pois(j, b)
            if i == 0 and j == 0:
                p *= 1 - a * b * rho
            elif i == 0 and j == 1:
                p *= 1 + a * rho
            elif i == 1 and j == 0:
                p *= 1 + b * rho
            elif i == 1 and j == 1:
                p *= 1 - rho
            g[(i, j)] = max(p, 0.0)
    s = sum(g.values())
    return {k: v / s for k, v in g.items()}


def summary(g):
    h = sum(v for (i, j), v in g.items() if i > j)
    d = sum(v for (i, j), v in g.items() if i == j)
    return h, d, 1 - h - d


def p_over(g, line):
    return sum(v for (i, j), v in g.items() if i + j > line)


def cells(g):
    out = {}
    for i in range(4):
        for j in range(4):
            out[f"{i}-{j}"] = g[(i, j)]
    out["AOHW"] = sum(v for (i, j), v in g.items() if i > j and i >= 4)
    out["AOAW"] = sum(v for (i, j), v in g.items() if j > i and j >= 4)
    out["AOD"] = sum(v for (i, j), v in g.items() if i == j and i >= 4)
    return out


def nelder_mead(f, x0, step, iters=600, tol=1e-12):
    n = len(x0)
    pts = [list(x0)] + [[x0[k] + (step[k] if k == i else 0) for k in range(n)] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        c = [sum(p[k] for p in pts[:-1]) / n for k in range(n)]
        xr = [c[k] + (c[k] - pts[-1][k]) for k in range(n)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c[k] + 2 * (c[k] - pts[-1][k]) for k in range(n)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [c[k] + 0.5 * (pts[-1][k] - c[k]) for k in range(n)]
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = [pts[0][k] + 0.5 * (pts[i][k] - pts[0][k]) for k in range(n)]
                    vals[i] = f(pts[i])
    i = min(range(n + 1), key=lambda i: vals[i])
    return pts[i], vals[i]


def market_targets(data):
    mo = data["mo"]
    raw = [1 / mid(mo[k]) for k in ("home", "draw", "away")]
    s = sum(raw)
    mo_p = [r / s for r in raw]
    lines = []
    for key, v in (data.get("ou") or {}).items():
        line = float(key)
        o, u = mid(v.get("over")), mid(v.get("under"))
        if not o or not u:
            continue
        matched = v.get("matched") or 0
        if abs(line - 2.5) < 1e-9:
            w = 1.0
        elif matched < 200:
            continue
        else:
            w = min(1.0, matched / 1000)
        po = (1 / o) / (1 / o + 1 / u)
        lines.append({"line": line, "p_over": po, "w": w, "matched": matched})
    return mo_p, sorted(lines, key=lambda x: x["line"])


def loss(params, mo_p, lines, fixed_rho=None):
    a, b = params[0], params[1]
    rho = fixed_rho if fixed_rho is not None else params[2]
    if a <= 0.05 or b <= 0.05 or a > 6 or b > 6 or rho < -0.25 or rho > 0.15 or 1 - a * b * rho <= 0:
        return 1e9
    g = grid(a, b, rho)
    h, d, aw = summary(g)
    e = (h - mo_p[0]) ** 2 + (d - mo_p[1]) ** 2 + (aw - mo_p[2]) ** 2
    for ln in lines:
        e += ln["w"] * (p_over(g, ln["line"]) - ln["p_over"]) ** 2
    return e


def fit(mo_p, lines, free_rho):
    best = None
    for a0, b0 in ((1.3, 1.1), (1.0, 1.4), (1.8, 0.8), (0.8, 1.8)):
        if free_rho:
            x, v = nelder_mead(lambda p: loss(p, mo_p, lines), [a0, b0, -0.10], [0.2, 0.2, 0.05])
            res = (x[0], x[1], x[2], v)
        else:
            x, v = nelder_mead(lambda p: loss(p, mo_p, lines, -0.10), [a0, b0], [0.2, 0.2])
            res = (x[0], x[1], -0.10, v)
        if best is None or res[3] < best[3]:
            best = res
    return best


def ev(p, lay):
    return (1 - p) * COMM - p * (lay - 1)


def main():
    src = open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin
    data = json.load(src)
    mo_p, lines = market_targets(data)
    l25 = [ln for ln in lines if abs(ln["line"] - 2.5) < 1e-9]
    if not l25:
        print(json.dumps({"error": "O/U 2.5 has no two-sided price; fit2 not possible"}))
        return
    a2, b2, r2, e2 = fit(mo_p, l25, free_rho=False)
    extra = [ln for ln in lines if abs(ln["line"] - 2.5) > 1e-9]
    if extra:
        a3, b3, r3, e3 = fit(mo_p, lines, free_rho=True)
        note3 = "lines " + ", ".join(f"{ln['line']:g}" for ln in lines)
    else:
        a3, b3, r3, e3 = a2, b2, r2, e2
        note3 = "no extra O/U line with 200+ matched on both sides; fit3 = fit2"
    g2, g3 = grid(a2, b2, r2), grid(a3, b3, r3)
    c2, c3 = cells(g2), cells(g3)
    out = {
        "market": {"mo": [round(x, 4) for x in mo_p],
                   "ou": [{"line": ln["line"], "p_over": round(ln["p_over"], 4), "w": round(ln["w"], 2),
                           "matched": ln["matched"],
                           "fit2_p_over": round(p_over(g2, ln["line"]), 4),
                           "fit3_p_over": round(p_over(g3, ln["line"]), 4)} for ln in lines]},
        "fit2": {"lambdaH": round(a2, 3), "lambdaA": round(b2, 3), "rho": r2, "err": round(e2, 7)},
        "fit3": {"lambdaH": round(a3, 3), "lambdaA": round(b3, 3), "rho": round(r3, 3), "err": round(e3, 7),
                 "note": note3},
        "cells": {c: {"fit2": round(c2[c], 4), "fit3": round(c3[c], 4)} for c in CELLS},
    }
    if data.get("lays"):
        out["lays"] = {c: {"lay": L, "p_fit2": round(c2[c], 4), "p_fit3": round(c3[c], 4),
                           "ev_fit2_pct": round(100 * ev(c2[c], L), 1),
                           "ev_fit3_pct": round(100 * ev(c3[c], L), 1)}
                       for c, L in data["lays"].items() if c in c2}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

# Betting data feed

- `matches.txt` – one match per line, `Home v Away`. Edit it on GitHub, then run the workflow (Actions → form-and-xg → Run workflow). Nothing runs on its own.
- `out/` – one text file per match with form, xG and each team's own league table (two tables when the teams are in different divisions). Claude reads these.
- The API-Football key is stored as the repository secret `APISPORTS_KEY`, never in a file.
- `tools/orbit_ou.js` – add-on to the Orbit scan reader (from 09.11.2026): reads Match Odds and every Over/Under goal line (1.5, 2.5, 3.5, 4.5 …) for given events, read-only. Prints an `OUJSON` line per event.
- `tools/fit_ou.py` – fits the market's goal rates two ways: fit2 = Match Odds + O/U 2.5 (rho −0.10, today's fallback grid) and fit3 = Match Odds + all O/U lines with 200+ matched (rho fitted). Gives each correct-score cell's probability and the market-only lay EV under both. Information-only until the end-of-November review.

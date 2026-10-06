# Betting data feed

- `matches.txt` – one match per line, `Home v Away`. Edit it on GitHub, then run the workflow (Actions → form-and-xg → Run workflow). Nothing runs on its own.
- `out/` – one text file per match with form, xG and the league table. Claude reads these.
- The API-Football key is stored as the repository secret `APISPORTS_KEY`, never in a file.

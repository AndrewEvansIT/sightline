# Sightline — working rules

Sightline is a desk panel for Macs that run local AI: what the Mac is running, what it's talking to, and whether it's healthy. Open source (MIT). It grew out of Andrew's private Homelab Panel (`~/Developer/homelab-panel`), which stays separate and personal.

## The one rule: works with no setup

Every widget must work the moment the app opens, with no accounts, no Shortcuts, no config and no admin rights. If a widget can't find its data on this Mac, `available()` returns False and the card hides. Anything that needs extra setup is opt-in and says so in plain words.

## Architecture

- `sightline/` — Python package, **stdlib only, Python 3.9** (the `/usr/bin/python3` that ships with the Command Line Tools). No pip installs, no `match`, no `X | Y` type unions.
  - `core.py` helpers (`run`, `run_json`, `http_json`, `which`, `footprints`, `processes`, `process_args`, `State`, `helper`).
  - `widgets/__init__.py` — the **Widget contract** (read it first). One file per widget group; any `Widget` subclass with an `id` is auto-discovered.
  - `app.py` (runners, status), `server.py` (HTTP + JSON API on 127.0.0.1:47800), `history.py` (SQLite), `alerts.py`, `config.py`.
- `web/` — the page. `app.js` runtime + generic renderer, `settings.js`, `cards.js`. A widget with a richer look ships `web/widgets/<id>.js` calling `Sightline.register(id, {render(data, ctx)})`. Buttons need no wiring: `data-action`, `data-params`, `data-confirm`.
- `helpers/` — small Swift command-line tools (`smc`, `power`, …) built by `helpers/build.sh` into `helpers/build/`; Python finds them with `core.helper(name)`.
- `app/` — the Mac app shell (Swift): window layouts, menu bar item, notifications; runs `python3 -m sightline` as a child.

## Rules for widgets

- Read-only by default. Actions only touch the user's own processes (SIGTERM, never SIGKILL first), move files to the Trash (never delete), and ask for a second click (`"confirm": true`) when they stop or remove something.
- No network calls except to localhost, plus the few documented ones (ping/trace to 1.1.1.1, Hugging Face model metadata), each behind a setting.
- No personal paths or names in code. Detect, don't hardcode.
- Plain words in the UI: say what something is and what a button does. No jargon where a plain word works.
- Parsers are pure functions with tests in `tests/` against saved command output in `tests/fixtures/`. Run `python3 -m unittest discover tests`.
- Keep `collect()` fast; anything slow runs on a longer interval or in a background scan with a cache.

## Git

Commit with explicit paths, check `git status --short` after. Pushing to GitHub needs Andrew's OK.

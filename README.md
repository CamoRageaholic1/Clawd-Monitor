# Clawd Monitor

A Claude Code usage monitor for macOS — terminal widget, menu bar, and a full
historical breakdown, in one pure-Python file.

Inspired by [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter), an
ESP32 desk dashboard. `clawd` is the software port: same idea, no hardware.

---

## What it does

clawd reads your Claude Code **subscription** OAuth token, makes a tiny
throwaway Haiku call to `api.anthropic.com/v1/messages`, and scrapes the
account-wide rate-limit headers off the response. Every poll is logged
locally, which builds a historical trend of your usage over time.

The figure it shows is **account-wide** — it already includes every Claude
surface on your subscription (Claude Code, claude.ai web, Desktop, Cowork, the
extensions). The throwaway call runs on your subscription, so it costs **$0** —
no metered API billing.

It pairs with [Codeburn](https://codeburn.app): Codeburn answers *"where did my
tokens go?"*, clawd answers *"how close am I to the wall right now?"* — and
clawd surfaces a Codeburn snapshot in its breakdown screen.

## Three faces, one file

| Mode | Command | What you get |
|---|---|---|
| Terminal widget | `python3 clawd.py --widget` | Live compact gauge — 5h/7d bars, burn rate, 24h trend sparkline |
| Menu bar | via SwiftBar | Reactive icon + %, dropdown with bars, burn, reset timers |
| Show all | `python3 clawd.py --showall` | Full breakdown — live gauges + 24h/7d/30d/all-time history + Codeburn |

Run with no flag and it auto-detects: a Terminal gives you the widget,
SwiftBar gives you the menu bar.

```
  ◆ CLAWD - full breakdown   all Claude surfaces - 14:44:10

  ● LIVE   (o_o) COOKING
  5h  #######################...........  67.3%  resets 2h 13m
  7d  ##############....................  41.8%  resets 4d 03h
  burn v 22%/h        status ● allowed

  HISTORY  (logged locally - fills in as clawd runs)
  24h  peak 99%  avg 31%  7d-peak 70%  288 samples  23 over 80%
       ▂▃▄▄▅▅▆▇▇▃▂▂▁▂▂▂▂▃▂▂▁▁▁▂▂▂▂▂▂▃▃▁▁▁▁▂▂▄▆▆▇▆▂▂▃▃▄▅▅▆▆▇
```

## Install

```bash
git clone https://github.com/CamoRageaholic1/Clawd-Monitor.git
cd Clawd-Monitor
bash install.sh
```

The installer sets up `~/.clawd/`, installs the script, makes a double-click
`.command` launcher, and — if [SwiftBar](https://swiftbar.app) is installed —
links the menu bar plugin (`brew install --cask swiftbar`).

## Configure

Top of `clawd.py`, one block:

- `THEME` — `claude` (Anthropic clay/coral), `camo`, `matrix`, `mono`, `neon`
- `MENUBAR_ICON` — `dot`, `gauge`, `emoji`, `image`
- `POLL_SECONDS` — poll interval (300 = 5 min default)

## Honest limits

- The % is account-wide but **cannot be split by product** — no data source
  exposes "Cowork vs Desktop vs Code." The breakdown is by time window, not by
  surface.
- History has **no backfill** — charts start empty and fill from first run,
  since the utilization % was never stored anywhere.
- The OAuth-token → API handshake is the one fragile dependency; see
  [`ARCHITECTURE.md`](ARCHITECTURE.md) section 5 if a real run returns `401`.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design and data map.

## Status

`test_clawd.py` covers history aggregation, sparklines, burn-rate math, color
conversion, mode detection, and all three renderers — 34 checks. Pure Python 3
standard library; no dependencies.

## Credits

Concept and inspiration: [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter)
by Hermann Björgvin. clawd reproduces none of its code or artwork — the ASCII
UI here is original.

## License

MIT — see [`LICENSE`](LICENSE).

# Clawd Monitor — Architecture

A Claude Code usage monitor for macOS. Three faces, one file: a live terminal
widget, a SwiftBar menu bar plugin, and a full historical breakdown screen.

Inspired by [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) (an
ESP32 hardware project) — clawd is the software port: same idea, no hardware.

---

## 1. What it does

clawd reads your Claude Code **subscription** OAuth token, makes a tiny
throwaway Haiku call to `api.anthropic.com/v1/messages`, and scrapes the
account-wide rate-limit headers off the response. It logs every poll to a
local file, which builds a historical trend of your usage over time.

It runs alongside [Codeburn](https://codeburn.app), not on top of it. Codeburn
answers *"where did my tokens go?"* (cost by model/project/task, Claude Code
only). clawd answers *"how close am I to the wall right now?"* (live 5h/7d
utilization, account-wide). They are complementary — clawd surfaces a Codeburn
snapshot in its breakdown screen so both live in one place.

---

## 2. The data reality (read this first)

This is the single most important section, because it bounds what the tool
can honestly show.

**The good news.** The `anthropic-ratelimit-unified-*` figure is
**account-wide**. Every Claude surface on your subscription draws from the
same pool — Claude Code, claude.ai web, Claude Desktop, Cowork, the Chrome and
Excel extensions. The percentage clawd shows already includes all of them.
"Summary of all usage" is satisfied by that one number.

**The hard limit.** That percentage is a *single aggregate*. There is no
per-product header — nothing that says "Cowork 15%, Desktop 8%, Code 40%."
The server does not expose it, and it cannot be computed reliably (utilization
% is not linear in tokens). The only local history that exists,
`~/.claude/projects/`, is **Claude Code only** — Desktop and Cowork write
nothing to disk that any tool can read. So:

- The **% breakdown** is by *time window* (24h / 7d / 30d / all-time) and
  *metric* (peak, average, burn) — **not** by product.
- The **per-product token detail** exists for **Claude Code only**, via
  Codeburn. There is no Desktop/Cowork equivalent and there cannot be.

**No backfill.** The utilization % is header-only and ephemeral — Anthropic
never stored it. clawd's history therefore starts accumulating *the day you
first run it*. Day one, the 30d/all-time charts are sparse; they fill in over
time.

---

## 3. Data sources

| Data | Source | Scope | Cost |
|---|---|---|---|
| 5h / 7d utilization %, status | API response headers `anthropic-ratelimit-unified-*` | Account-wide (all surfaces) | $0 — subscription |
| Reset countdowns | Same headers (`*-5h-reset`, `*-7d-reset`, Unix epochs) | Account-wide | $0 |
| % history & trend charts | `~/.clawd/history.jsonl` — clawd logs one sample per poll | Account-wide | $0 |
| Burn rate & time-to-cap | Derived from the last ~30 min of history | Account-wide | $0 |
| Token cost by model/project/task | `codeburn` (reads `~/.claude/projects/`) | **Claude Code only** | $0 — local parse |

Everything is free. The throwaway poll runs on your Max/Pro subscription, not
metered API billing — no invoice. It consumes a microscopic sliver of your own
5h/7d quota (a 1-token Haiku call ≈ 25 tokens; a full day of 5-minute polling
is less than a single "edit this file" Code task).

---

## 4. Components

Single file: `clawd.py`. Pure Python 3 standard library — no `pip install`.
It detects which mode to run, or you force it with a flag.

```
                      ┌───────────────────────────┐
                      │   api.anthropic.com        │
                      │   /v1/messages  (Haiku)    │
                      └─────────────┬─────────────-┘
                       throwaway call│ headers back
                      ┌─────────────▼─────────────┐
                      │  poll_once()              │
                      │  token → POST → parse     │
                      └──────┬───────────┬────────┘
                  cache_write│           │hist_append
                   last.json │           │ history.jsonl
                      ┌──────▼───────────▼────────┐
            ┌─────────┤        renderers          ├─────────┐
            │         └───────────────────────────┘         │
   ┌────────▼────────┐  ┌──────────────────┐  ┌──────────────▼───┐
   │ render_terminal │  │ render_menubar   │  │ render_showall   │
   │ live TUI widget │  │ SwiftBar plugin  │  │ 24h/7d/30d/all   │
   └─────────────────┘  └──────────────────┘  └──────────────────┘
                                                   │ snapshot
                                            ┌──────▼──────┐
                                            │  codeburn   │
                                            └─────────────┘
```

### Modes

| Mode | Flag | Trigger | Behaviour |
|---|---|---|---|
| Terminal widget | `--widget` | run from a Terminal (tty) | Live compact gauge: 5h/7d bars, burn, 24h sparkline. Refreshes each second; polls every `POLL_SECONDS`. |
| Menu bar | `--menubar` | `SWIFTBAR` env var present | Prints one SwiftBar block and exits. Title = reactive icon + %; dropdown = bars, burn, reset timers, actions. |
| Show all | `--showall` | explicit flag | Full breakdown: live gauges + 24h/7d/30d/all-time history with sparklines + a Codeburn snapshot. Launched from the menu bar's "Show all" item. |

`detect_mode()` resolves: explicit flag → SwiftBar env → tty → menu bar.

---

## 5. The OAuth handshake (the one fragile dependency)

A subscription OAuth token only works against `/v1/messages` when the request
carries **both**:

1. `anthropic-beta: oauth-2025-04-20`
2. a `system` prompt whose first line identifies as Claude Code

If a real token returns `401`/`403`, this is the part to fix. The authoritative
working reference is Clawdmeter's `daemon/` source — mirror exactly what it
sends. The relevant constants in `clawd.py` are `OAUTH_BETA` and `CC_IDENTITY`.

A metered **API key** (console.anthropic.com) is the wrong tool: it costs money
*and* returns the API platform's own separate tier limits, not the
`unified-5h/7d` subscription headers. clawd must use the subscription token.

`MODEL` defaults to `claude-haiku-4-5`. If that 404s, switch to
`claude-3-5-haiku-20241022` — the call is a throwaway, the model only needs to
return headers.

---

## 6. Files & on-disk format

```
~/.clawd/
  history.jsonl   append-only log, one JSON object per poll
  last.json       most recent good reading (menu bar fallback)
  icon.png        optional custom menu bar icon (MENUBAR_ICON = "image")
```

**History record** (one line of `history.jsonl`):

```json
{"t": 1747500000, "s5": 67.3, "s7": 41.8, "st": "allowed"}
```

`t` = Unix epoch, `s5` = 5h utilization %, `s7` = 7d utilization %, `st` =
status. ~60 bytes/line; 5-minute polling ≈ 0.5 MB/month. `hist_prune()` keeps
the last 400 days. A 30s dedupe guard stops the widget and the menu bar plugin
double-logging when both run.

---

## 7. Configuration

Top of `clawd.py`, one block:

| Setting | Values | Notes |
|---|---|---|
| `THEME` | `claude` `camo` `matrix` `mono` `neon` | Defined in hex, auto-converted to 256-color for Terminal.app safety |
| `MENUBAR_ICON` | `dot` `gauge` `emoji` `image` | `dot` = reactive ● + %; `image` uses your own PNG |
| `POLL_SECONDS` | integer | 300 (5 min) default; 60 also fine |
| `HOME_DIR` | path | where history + cache live |

---

## 8. Install / menu bar

`install.sh` creates `~/.clawd/`, copies `clawd.py`, makes it executable, and —
if SwiftBar is installed — symlinks it into the SwiftBar plugin folder as
`clawd.1m.py` (refreshes every minute). SwiftBar itself: `brew install --cask
swiftbar`. The symlink keeps one source of truth — editing the config updates
all three modes.

---

## 9. Known limitations

- **No per-product split.** Desktop/Cowork usage is *included* in the % but
  cannot be isolated. No data source exposes it.
- **No history backfill.** Charts start empty and fill from first run.
- **OAuth handshake** may need adjustment per Claude Code version (§5).
- **Codeburn JSON schema** is parsed best-effort; the panel degrades to a hint
  if the schema differs from what's expected. Codeburn is Claude Code only.
- **SwiftBar `gauge` icon** uses an SF Symbol whose name can vary by macOS
  version; `dot` is the safe default.

---

## 10. Test status

`test_clawd.py` — 34 checks, all passing: history load/aggregate/prune,
windowing, sparklines, burn-rate math, color conversion, countdown formatting,
Codeburn graceful-absence, mode detection, all three renderers, and one full
mocked widget loop. The real API plumbing was probed against
`api.anthropic.com` (clean `401` on a bogus token; error handling confirmed).

The **only** path not coverable in a sandbox is the live OAuth call with a real
token — that needs your credentials on your Mac. First real run confirms it in
seconds: bars = working, `HTTP 401` = handshake needs the §5 fix.

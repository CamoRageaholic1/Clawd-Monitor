#!/usr/bin/env python3
"""
clawd.py - Claude Code usage monitor for macOS.

Three faces, one file (auto-detected, or forced with a flag):
  * TERMINAL WIDGET  - live TUI gauge for a small pinned Terminal window  [--widget]
  * MENU BAR         - SwiftBar plugin for the macOS menu bar             [--menubar]
  * SHOW ALL         - full breakdown: 24h / 7d / 30d / all-time          [--showall]

It reads your Claude Code *subscription* OAuth token, makes a tiny throwaway
Haiku call to /v1/messages, and scrapes the account-wide rate-limit headers
off the response. The call runs on your subscription -- $0, no metered billing,
and the figure already includes every Claude surface (Code, web, Desktop,
Cowork). Every poll is logged to ~/.clawd/history.jsonl, which is what builds
the historical % trend over time.

Inspired by Clawdmeter (github.com/HermannBjorgvin/Clawdmeter).
Pure Python 3 standard library. No pip installs.

  Terminal widget:  python3 clawd.py            (Ctrl-C to quit)
  Full breakdown:   python3 clawd.py --showall
  Menu bar:         see ARCHITECTURE.md / install.sh (SwiftBar)
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# ============================ USER CONFIG ===================================
THEME        = "claude"   # claude | camo | matrix | mono | neon
MENUBAR_ICON = "dot"      # dot | gauge | emoji | image
EMOJI        = "\U0001F99E"               # lobster, for MENUBAR_ICON = "emoji"
IMAGE_PATH   = "~/.clawd/icon.png"        # your PNG, for MENUBAR_ICON = "image"
POLL_SECONDS = 300        # how often to poll (300 = 5 min; 60 also fine)
HOME_DIR     = "~/.clawd" # where history + cache live
# =============================================================================

# Each theme: accent (titles/icon), track (empty bar), live (the "LIVE" /
# allowed-status green), and a 4-stop severity ramp `levels` (calm -> maxed).
THEMES = {
    # claude - Anthropic's clay/coral brand color; warm, on-brand severity ramp
    "claude": {"accent": "#D97757", "track": "#3F3F3F", "live": "#5FB87A",
               "levels": ["#5FB87A", "#E0A23E", "#D97757", "#BE4A3A"]},
    "camo":   {"accent": "#A3B07A", "track": "#33372C", "live": "#8FBF5F",
               "levels": ["#7C8B4E", "#B5A642", "#9C6B30", "#6E4B3A"]},
    "matrix": {"accent": "#39FF14", "track": "#0C2A0C", "live": "#7CFF4F",
               "levels": ["#1FBF1F", "#7CFF4F", "#C8FF2E", "#FF5555"]},
    "mono":   {"accent": "#E6E6E6", "track": "#3A3A3A", "live": "#BDBDBD",
               "levels": ["#9E9E9E", "#BDBDBD", "#DADADA", "#FFFFFF"]},
    "neon":   {"accent": "#00E5FF", "track": "#2A1A3A", "live": "#00FF9C",
               "levels": ["#00E5FF", "#B14FFF", "#FF4FD8", "#FF3860"]},
}
T = THEMES.get(THEME, THEMES["claude"])
LIVE_HEX = T.get("live", T["levels"][0])   # green for LIVE / allowed status

# ----------------------------------------------------------- API / token ----
API_URL      = "https://api.anthropic.com/v1/messages"
MODEL        = "claude-haiku-4-5"          # throwaway call; if 404 ->
                                           # "claude-3-5-haiku-20241022"
KEYCHAIN_SVC = "Claude Code-credentials"
CRED_FILE    = os.path.expanduser("~/.claude/.credentials.json")

# Claude Code OAuth handshake: a subscription token only works against
# /v1/messages with the oauth beta header AND a Claude-Code system prompt.
# If you get a 401/403, this is the part to fix -- mirror Clawdmeter's daemon/.
OAUTH_BETA   = "oauth-2025-04-20"
CC_IDENTITY  = "You are Claude Code, Anthropic's official CLI for Claude."

EMPTY_STATE  = {"session_pct": 0.0, "weekly_pct": 0.0, "session_reset": 0.0,
                "weekly_reset": 0.0, "status": "unknown", "claim": ""}

# ----------------------------------------------------------------- color ----
ESC, RESET, BOLD, DIM = "\033[", "\033[0m", "\033[1m", "\033[2m"
CLEAR = ESC + "2J" + ESC + "H"
HIDE, SHOW = ESC + "?25l", ESC + "?25h"
SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
SPIN  = ["|", "/", "-", "\\"]


def hex_to_256(h):
    """Approximate a #rrggbb color with an xterm-256 index (Terminal.app safe)."""
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    if abs(r - g) < 12 and abs(g - b) < 12 and abs(r - b) < 12:
        grey = (r + g + b) // 3
        if grey < 8:
            return 16
        if grey > 248:
            return 231
        return 232 + round((grey - 8) / 247 * 24)
    steps = [0, 95, 135, 175, 215, 255]
    q = lambda v: min(range(6), key=lambda i: abs(steps[i] - v))   # noqa: E731
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def fg(hex_color):
    return f"{ESC}38;5;{hex_to_256(hex_color)}m"


def level_index(pct):
    return 3 if pct >= 95 else 2 if pct >= 80 else 1 if pct >= 50 else 0


def level_hex(pct):
    return T["levels"][level_index(pct)]


# --------------------------------------------------------------- token ------
def get_token():
    """Claude Code OAuth access token, or None. Re-read every poll so a
    background token refresh by Claude Code is picked up automatically."""
    raw = None
    if os.path.exists(CRED_FILE):
        try:
            with open(CRED_FILE) as fh:
                raw = fh.read()
        except OSError:
            raw = None
    if not raw:
        try:
            raw = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SVC, "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            raw = None
    if not raw:
        return None
    try:
        return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return raw if raw.startswith("sk-ant-") else None


# ----------------------------------------------------------------- poll -----
def _lc(headers):
    return {k.lower(): v for k, v in dict(headers).items()}


def poll_usage(token):
    """POST a minimal request; return (headers_dict, error_or_None).
    Headers arrive even on 429 -- that's exactly when usage matters."""
    body = json.dumps({
        "model": MODEL, "max_tokens": 1, "system": CC_IDENTITY,
        "messages": [{"role": "user", "content": "."}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("anthropic-beta", OAUTH_BETA)
    req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _lc(resp.headers), None
    except urllib.error.HTTPError as exc:
        hdrs = _lc(exc.headers) if exc.headers else {}
        if hdrs.get("anthropic-ratelimit-unified-status"):
            return hdrs, None
        return hdrs, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return {}, str(exc)


def parse(hdrs):
    def num(name):
        try:
            return float(hdrs.get(name, 0.0))
        except (TypeError, ValueError):
            return 0.0
    return {
        "session_pct":   num("anthropic-ratelimit-unified-5h-utilization") * 100,
        "weekly_pct":    num("anthropic-ratelimit-unified-7d-utilization") * 100,
        "session_reset": num("anthropic-ratelimit-unified-5h-reset"),
        "weekly_reset":  num("anthropic-ratelimit-unified-7d-reset"),
        "status":        hdrs.get("anthropic-ratelimit-unified-status", "unknown"),
        "claim":         hdrs.get("anthropic-ratelimit-unified-representative-claim", ""),
    }


def poll_once():
    """Full poll cycle: get token, hit API, persist. Returns (state, error)."""
    token = get_token()
    if not token:
        return None, "no Claude Code token (file + keychain both empty)"
    hdrs, err = poll_usage(token)
    if hdrs.get("anthropic-ratelimit-unified-status") or \
            hdrs.get("anthropic-ratelimit-unified-5h-utilization"):
        state = parse(hdrs)
        cache_write(state)
        hist_append(state)
        return state, None
    return None, err or "no rate-limit headers in response"


# ----------------------------------------------------------- persistence ----
def _clawd_dir():
    d = os.path.expanduser(HOME_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def hist_file():
    return os.path.join(_clawd_dir(), "history.jsonl")


def cache_file():
    return os.path.join(_clawd_dir(), "last.json")


def cache_write(state):
    try:
        with open(cache_file(), "w") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def cache_read():
    try:
        with open(cache_file()) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _hist_last_ts(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 250))
            tail = fh.read().decode("utf-8", "ignore").strip().splitlines()
            if tail:
                return json.loads(tail[-1]).get("t")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def hist_append(state):
    """Append one compact sample. Skips if a sample was logged < 30s ago,
    so the widget and the menu bar plugin don't double-log."""
    path = hist_file()
    now = int(time.time())
    last = _hist_last_ts(path)
    if last and now - last < 30:
        return
    entry = {"t": now,
             "s5": round(state.get("session_pct", 0.0), 2),
             "s7": round(state.get("weekly_pct", 0.0), 2),
             "st": state.get("status", "unknown")}
    try:
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def hist_load():
    out = []
    try:
        with open(hist_file()) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def hist_prune(days=400):
    entries = hist_load()
    if not entries:
        return
    cutoff = time.time() - days * 86400
    kept = [e for e in entries if e.get("t", 0) >= cutoff]
    if len(kept) != len(entries):
        try:
            with open(hist_file(), "w") as fh:
                for e in kept:
                    fh.write(json.dumps(e) + "\n")
        except OSError:
            pass


def hist_window(entries, hours):
    cutoff = time.time() - hours * 3600
    rows = [e for e in entries if e.get("t", 0) >= cutoff]
    if not rows:
        return {"samples": 0, "peak5": 0.0, "peak7": 0.0,
                "avg5": 0.0, "series5": [], "over80": 0}
    s5 = [float(e.get("s5", 0)) for e in rows]
    s7 = [float(e.get("s7", 0)) for e in rows]
    return {"samples": len(rows), "peak5": max(s5), "peak7": max(s7),
            "avg5": sum(s5) / len(s5), "series5": s5,
            "over80": sum(1 for v in s5 if v >= 80)}


def burn_rate(entries):
    """(pct_per_hour, eta_minutes_to_5h_cap). Uses the last ~30 min of samples."""
    recent = [e for e in entries if e.get("t", 0) >= time.time() - 1800]
    if len(recent) < 2:
        return 0.0, None
    span = recent[-1]["t"] - recent[0]["t"]
    if span <= 0:
        return 0.0, None
    rate = (float(recent[-1]["s5"]) - float(recent[0]["s5"])) / (span / 3600.0)
    eta = None
    if rate > 0.5:
        eta = (100.0 - float(recent[-1]["s5"])) / rate * 60.0
    return rate, eta


# --------------------------------------------------------------- codeburn ---
def cb_available():
    return shutil.which("codeburn") is not None


def cb_fetch():
    """Best-effort Codeburn snapshot. Returns (dict_or_None, note)."""
    if not cb_available():
        return None, "not installed"
    try:
        res = subprocess.run(["codeburn", "status", "--format", "json"],
                             capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None, "codeburn failed to run"
    if res.returncode != 0:
        return None, "codeburn returned an error"
    try:
        return json.loads(res.stdout), None
    except json.JSONDecodeError:
        return None, "codeburn output was not JSON"


def _find_cost(obj):
    """Walk a small dict for the first cost-like number."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(t in k.lower() for t in ("cost", "spend", "usd", "total")) \
                    and isinstance(v, (int, float)):
                return float(v)
        for v in obj.values():
            found = _find_cost(v)
            if found is not None:
                return found
    return None


def cb_lines(cb, note):
    """Return a few display lines for the Codeburn panel (best-effort)."""
    if cb is None:
        if note == "not installed":
            return ["Codeburn not installed - `npm i -g codeburn` for "
                    "Claude Code token-cost detail"]
        return [f"Codeburn unavailable ({note})"]
    out = []
    for label in ("today", "week", "month"):
        sub = cb.get(label) if isinstance(cb, dict) else None
        cost = _find_cost(sub) if sub is not None else None
        if cost is not None:
            out.append(f"{label:<7}${cost:,.2f}")
    if not out:
        total = _find_cost(cb)
        if total is not None:
            out.append(f"total  ${total:,.2f}")
        else:
            out.append("Codeburn data present - run `codeburn status` for detail")
    return out


# --------------------------------------------------------------- helpers ----
def countdown(epoch):
    if not epoch:
        return "--"
    secs = int(epoch - time.time())
    if secs <= 0:
        return "now"
    d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def human(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(int(n))


def mood(pct, climbing=False):
    if pct >= 95:
        face, word = "(x_x)", "MAXED"
    elif pct >= 80:
        face, word = "(>_<)", "SWEATING"
    elif pct >= 50:
        face, word = "(o_o)", "COOKING"
    elif pct >= 20:
        face, word = "(-_o)", "WARMING UP"
    else:
        face, word = "(-_-)", "IDLE"
    if climbing and pct < 95:
        word += " ^"
    return face, word


def term_size():
    try:
        ts = os.get_terminal_size()
        return max(48, ts.columns), max(16, ts.lines)
    except OSError:
        return 80, 24


def bar(pct, width):
    pct = max(0.0, min(100.0, pct))
    fill = int(round(width * pct / 100.0))
    return fg(level_hex(pct)) + "#" * fill + fg(T["track"]) + "." * (width - fill) + RESET


def sparkline(values, width):
    if not values:
        return DIM + "\u00b7" * width + RESET
    vals = list(values)
    if len(vals) > width:                       # bucket-average down to width
        step = len(vals) / width
        out = []
        for i in range(width):
            chunk = values[int(i * step):int((i + 1) * step)]
            out.append(sum(chunk) / len(chunk) if chunk else 0.0)
        vals = out
    chars = "".join(SPARK[int(max(0, min(7, round(v / 100 * 7))))] for v in vals)
    peak = max(values) if values else 0
    body = fg(level_hex(peak)) + chars + RESET
    if len(vals) < width:
        body = DIM + "\u00b7" * (width - len(vals)) + RESET + body
    return body


def burn_text(rate, eta):
    if rate is None or abs(rate) < 0.5:
        return f"{DIM}burn  -  steady{RESET}"
    arrow = "^" if rate > 0 else "v"
    col = fg(level_hex(min(100, 50 + rate)))
    txt = f"{col}burn {arrow} {abs(rate):.0f}%/h{RESET}"
    if eta is not None and rate > 0:
        h, m = int(eta) // 60, int(eta) % 60
        eta_s = f"{h}h {m:02d}m" if h else f"{m}m"
        txt += f"{DIM} - ~{eta_s} to 5h cap{RESET}"
    return txt


# --------------------------------------------------------------- renders ----
def gauge_lines(state, width):
    s, wk = state["session_pct"], state["weekly_pct"]
    return [
        f"  {DIM}5h{RESET}  {bar(s, width)} {BOLD}{s:5.1f}%{RESET}"
        f"  {DIM}resets {countdown(state['session_reset'])}{RESET}",
        f"  {DIM}7d{RESET}  {bar(wk, width)} {BOLD}{wk:5.1f}%{RESET}"
        f"  {DIM}resets {countdown(state['weekly_reset'])}{RESET}",
    ]


def render_terminal(state, frame, err):
    cols, _ = term_size()
    w = max(10, min(30, cols - 26))
    s, wk = state["session_pct"], state["weekly_pct"]
    face, word = mood(max(s, wk))
    accent = fg(T["accent"])

    cache = render_terminal.__dict__
    if time.time() - cache.get("ts", 0) > 20:
        ents = hist_load()
        cache["w24"] = hist_window(ents, 24)
        cache["burn"] = burn_rate(ents)
        cache["ts"] = time.time()
    w24, (rate, eta) = cache["w24"], cache["burn"]

    out = [CLEAR,
           f"{BOLD}{accent}  \u25c6 CLAWD{RESET} {DIM}- Claude Code usage{RESET}", ""]
    out.append(f"  {BOLD}{face}{RESET}  {fg(level_hex(max(s, wk)))}{word}{RESET}")
    out.append("")
    out += gauge_lines(state, w)
    out.append(f"  {burn_text(rate, eta)}")
    out.append("")
    if w24["samples"]:
        out.append(f"  {DIM}24h trend (5h%){RESET}")
        out.append(f"  {sparkline(w24['series5'], min(cols - 6, 40))}")
        out.append("")
    st = state["status"]
    st_col = fg(LIVE_HEX) if st == "allowed" else \
        fg(T["levels"][3]) if st == "rate_limited" else DIM
    out.append(f"  status {st_col}\u25cf{RESET} {st}   "
               f"{DIM}{SPIN[frame % 4]} {datetime.now():%H:%M:%S}{RESET}")
    if err:
        out.append(f"  {fg('#FF5555')}! {err}{RESET} {DIM}(last good read){RESET}")
    out.append(f"  {DIM}--showall for full breakdown - Ctrl-C to quit{RESET}")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def render_showall(state, frame, err):
    cols, _ = term_size()
    w = max(14, min(34, cols - 30))
    spw = min(cols - 10, 52)
    accent = fg(T["accent"])

    cache = render_showall.__dict__
    if time.time() - cache.get("ts", 0) > 20:
        ents = hist_load()
        cache["win"] = {h: hist_window(ents, h) for h in (24, 168, 720, 999999)}
        cache["burn"] = burn_rate(ents)
        cache["first"] = ents[0]["t"] if ents else None
        cache["ts"] = time.time()
    win, (rate, eta), first = cache["win"], cache["burn"], cache["first"]

    s, wk = state["session_pct"], state["weekly_pct"]
    face, word = mood(max(s, wk))
    out = [CLEAR,
           f"{BOLD}{accent}  \u25c6 CLAWD - full breakdown{RESET}"
           f"   {DIM}all Claude surfaces - {datetime.now():%H:%M:%S}{RESET}", ""]

    out.append(f"  {fg(LIVE_HEX)}\u25cf{RESET} {BOLD}{fg(LIVE_HEX)}LIVE{RESET}"
               f"   {face} {fg(level_hex(max(s, wk)))}{word}{RESET}")
    out += gauge_lines(state, w)
    st = state["status"]
    st_col = fg(LIVE_HEX) if st == "allowed" else \
        fg(T["levels"][3]) if st == "rate_limited" else DIM
    out.append(f"  {burn_text(rate, eta)}        "
               f"{DIM}status{RESET} {st_col}\u25cf{RESET} {st}")
    out.append("")

    out.append(f"  {BOLD}HISTORY{RESET}  {DIM}(logged locally - fills in as "
               f"clawd runs){RESET}")
    for name, hrs in [("24h", 24), ("7d", 168), ("30d", 720), ("all", 999999)]:
        d = win[hrs]
        if not d["samples"]:
            out.append(f"  {BOLD}{name:<5}{RESET}{DIM}no samples yet{RESET}")
            continue
        line = (f"  {BOLD}{name:<5}{RESET}{DIM}peak{RESET} "
                f"{fg(level_hex(d['peak5']))}{d['peak5']:.0f}%{RESET}  "
                f"{DIM}avg{RESET} {d['avg5']:.0f}%  "
                f"{DIM}7d-peak{RESET} {d['peak7']:.0f}%  "
                f"{DIM}{human(d['samples'])} samples{RESET}")
        if d["over80"]:
            line += f"  {fg('#E0A82E')}{human(d['over80'])} over 80%{RESET}"
        out.append(line)
        out.append(f"       {sparkline(d['series5'], spw)}")
    if first:
        out.append(f"  {DIM}history since "
                   f"{datetime.fromtimestamp(first):%b %d, %Y}{RESET}")
    out.append("")

    out.append(f"  {BOLD}CLAUDE CODE TOKENS{RESET}  {DIM}(via codeburn - "
               f"snapshot at open){RESET}")
    for line in _CB_SNAPSHOT:
        out.append(f"  {line}")
    out.append("")
    if err:
        out.append(f"  {fg('#FF5555')}! {err}{RESET} {DIM}(showing last good "
                   f"read){RESET}")
    out.append(f"  {DIM}refreshes every {POLL_SECONDS // 60 or 1}m - "
               f"Ctrl-C to quit{RESET}")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def _img_b64(path):
    try:
        with open(os.path.expanduser(path), "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    except OSError:
        return None


def _menu_glyph(pct):
    fill = int(round(14 * min(100.0, pct) / 100.0))
    block, empty = "\u2588" * fill, "\u2591" * (14 - fill)
    return f"{block}{empty} | color={level_hex(pct)} font=Menlo"


def render_menubar(state, err, stale=False):
    lines = []
    if state is None:
        lines += [f"Clawd !  | color={T['levels'][3]}", "---",
                  f"{err or 'unavailable'} | color=#FF5555"]
    else:
        s, wk = state["session_pct"], state["weekly_pct"]
        hexc = level_hex(max(s, wk))
        if MENUBAR_ICON == "emoji":
            lines.append(f"{EMOJI} {round(s)}% | color={hexc}")
        elif MENUBAR_ICON == "gauge":
            lines.append(f"{round(s)}% | sfimage=gauge.medium "
                         f"sfcolor={hexc} color={hexc}")
        elif MENUBAR_ICON == "image" and _img_b64(IMAGE_PATH):
            lines.append(f"{round(s)}% | image={_img_b64(IMAGE_PATH)} "
                         f"color={hexc}")
        else:
            lines.append(f"\u25cf {round(s)}% | color={hexc}")
        face, word = mood(max(s, wk))
        rate, _eta = burn_rate(hist_load())
        burn = "steady" if abs(rate) < 0.5 else \
            f"{'+' if rate > 0 else ''}{rate:.0f}%/h"
        lines += ["---",
                  f"{face}  {word} | color={hexc}",
                  "---",
                  f"Session 5h   {round(s)}% | color={level_hex(s)}",
                  "--" + _menu_glyph(s),
                  f"--resets in {countdown(state['session_reset'])} | "
                  f"color=#999999",
                  f"Weekly 7d   {round(wk)}% | color={level_hex(wk)}",
                  "--" + _menu_glyph(wk),
                  f"--resets in {countdown(state['weekly_reset'])} | "
                  f"color=#999999",
                  "---",
                  f"burn  {burn} | color=#999999",
                  f"\u25cf  {state['status']} | color="
                  + (LIVE_HEX if state["status"] == "allowed"
                     else T["levels"][3] if state["status"] == "rate_limited"
                     else "#999999")]
        if stale:
            lines.append(f"stale read - {err or 'no response'} | color=#E0A82E")
    py, script = sys.executable, os.path.abspath(__file__)
    lines += ["---",
              f"Show all (24h/7d/30d/all) | bash={py} param1={script} "
              f"param2=--showall terminal=true refresh=false",
              f"Open live widget | bash={py} param1={script} "
              f"param2=--widget terminal=true refresh=false"]
    if cb_available():
        lines.append("Open Codeburn | bash=codeburn terminal=true refresh=false")
    lines.append("Refresh now | refresh=true")
    print("\n".join(lines))


# ----------------------------------------------------------------- run ------
_CB_SNAPSHOT = []


def _loop(render_fn):
    sys.stdout.write(HIDE)
    hist_prune()
    state = cache_read() or dict(EMPTY_STATE)
    err, frame, last_poll = None, 0, 0.0
    try:
        while True:
            if time.time() - last_poll >= POLL_SECONDS:
                fresh, perr = poll_once()
                if fresh:
                    state, err = fresh, None
                else:
                    err = perr
                last_poll = time.time()
            render_fn(state, frame, err)
            frame += 1
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW + RESET + "\n")


def run_widget():
    _loop(render_terminal)


def run_showall():
    global _CB_SNAPSHOT
    print("Loading Codeburn snapshot...", flush=True)
    cb, note = cb_fetch()
    _CB_SNAPSHOT = cb_lines(cb, note)
    _loop(render_showall)


def run_menubar():
    state, err = poll_once()
    stale = False
    if state is None:
        cached = cache_read()
        if cached:
            state, stale = cached, True
    render_menubar(state, err, stale)


def detect_mode(argv):
    if "--showall" in argv:
        return "showall"
    if "--menubar" in argv:
        return "menubar"
    if "--widget" in argv:
        return "widget"
    if os.environ.get("SWIFTBAR") or os.environ.get("SWIFTBAR_PLUGIN_PATH"):
        return "menubar"
    return "widget" if sys.stdout.isatty() else "menubar"


def main():
    mode = detect_mode(sys.argv[1:])
    if mode == "menubar":
        run_menubar()
    elif mode == "showall":
        run_showall()
    else:
        run_widget()


if __name__ == "__main__":
    main()

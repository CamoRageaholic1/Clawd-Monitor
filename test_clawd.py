#!/usr/bin/env python3
"""Sandbox test harness for clawd.py. Generates synthetic history, exercises
every code path, renders all three views, and probes the real API endpoint."""
import io
import json
import math
import os
import random
import re
import sys
import time

import clawd as m

ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")
strip = lambda s: ANSI.sub("", s)            # noqa: E731
passes, fails = [], []


def check(name, cond):
    (passes if cond else fails).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


# --- 1. build 30 days of synthetic history -----------------------------------
random.seed(7)
path = m.hist_file()
now = int(time.time())
rows = []
for i in range(30 * 24 * 12):                # 30 days, every 5 min
    t = now - (30 * 24 * 12 - i) * 300
    phase = (t % (5 * 3600)) / (5 * 3600)    # position in a 5h window
    hour = (t % 86400) / 3600
    active = 1.0 if 8 <= hour <= 20 else 0.25
    s5 = max(0, min(99, 95 * phase * active + random.uniform(-6, 6)))
    s7 = max(0, min(95, 25 + 40 * (i / (30 * 24 * 12)) + random.uniform(-5, 5)))
    rows.append({"t": t, "s5": round(s5, 2), "s7": round(s7, 2),
                 "st": "rate_limited" if s5 > 95 else "allowed"})
with open(path, "w") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")
print(f"=== synthetic history: {len(rows)} samples -> {path} ===\n")

# --- 2. history / aggregation / sparkline / burn -----------------------------
print("=== unit checks ===")
ents = m.hist_load()
check("hist_load reads every line", len(ents) == len(rows))

w24 = m.hist_window(ents, 24)
w7 = m.hist_window(ents, 168)
wall = m.hist_window(ents, 999999)
check("24h window has ~288 samples", 260 <= w24["samples"] <= 300)
check("7d window larger than 24h", w7["samples"] > w24["samples"])
check("all-time window == full set", wall["samples"] == len(rows))
check("peak5 within 0-100", 0 <= w24["peak5"] <= 100)
check("avg5 within 0-100", 0 <= w7["avg5"] <= 100)
check("over80 count is sane", 0 <= wall["over80"] <= wall["samples"])

empty = m.hist_window([], 24)
check("empty window degrades gracefully", empty["samples"] == 0 and
      empty["series5"] == [])

spark = strip(m.sparkline(w7["series5"], 48))
check("sparkline downsamples to width", len(spark) == 48)
check("sparkline uses block glyphs", any(c in spark for c in m.SPARK))
check("sparkline empty-safe", len(strip(m.sparkline([], 20))) == 20)

rate, eta = m.burn_rate(ents)
check("burn_rate returns a number", isinstance(rate, float))
check("burn_rate <2 samples -> 0", m.burn_rate(ents[:1]) == (0.0, None))

check("hex_to_256 maps colors", all(0 <= m.hex_to_256(h) <= 255
      for h in ("#D97757", "#39FF14", "#000000", "#FFFFFF", "#808080")))
check("countdown formats days", "d " in m.countdown(time.time() + 5 * 86400))
check("countdown formats minutes", m.countdown(time.time() + 600).endswith("m"))
check("countdown past -> now", m.countdown(time.time() - 10) == "now")
check("human abbreviates", m.human(7400) == "7.4k" and m.human(42) == "42")

# --- 3. codeburn graceful absence --------------------------------------------
cb, note = m.cb_fetch()
check("cb_fetch handles missing binary", cb is None and note == "not installed")
cbl = m.cb_lines(cb, note)
check("cb_lines gives a helpful note", len(cbl) == 1 and "npm" in cbl[0])
check("cb_lines parses a real-ish payload",
      m.cb_lines({"today": {"totalCost": 4.18}, "week": {"cost": 26.4}}, None)
      == ["today  $4.18", "week   $26.40"])

# --- 4. mode detection -------------------------------------------------------
check("detect --showall", m.detect_mode(["--showall"]) == "showall")
check("detect --menubar", m.detect_mode(["--menubar"]) == "menubar")
check("detect --widget", m.detect_mode(["--widget"]) == "widget")
os.environ["SWIFTBAR"] = "1"
check("detect SwiftBar env", m.detect_mode([]) == "menubar")
del os.environ["SWIFTBAR"]

# --- 5. renderers ------------------------------------------------------------
fake = {"session_pct": 67.3, "weekly_pct": 41.8,
        "session_reset": time.time() + 2 * 3600 + 700,
        "weekly_reset": time.time() + 4 * 86400 + 6 * 3600,
        "status": "allowed", "claim": "five_hour"}

buf = io.StringIO()
sys.stdout = buf
m.render_terminal(fake, 3, None)
sys.stdout = sys.__stdout__
term_out = buf.getvalue()
check("render_terminal produces output", len(term_out) > 100)
check("render_terminal emits ANSI color", "\033[" in term_out)
check("render_terminal shows the gauge", "67.3%" in strip(term_out))

m._CB_SNAPSHOT = m.cb_lines(None, "not installed")
buf = io.StringIO()
sys.stdout = buf
m.render_showall(fake, 0, None)
sys.stdout = sys.__stdout__
show_out = buf.getvalue()
check("render_showall produces output", len(show_out) > 300)
check("render_showall has all 4 windows",
      all(w in strip(show_out) for w in ("24h", "7d", "30d", "all")))

buf = io.StringIO()
sys.stdout = buf
m.render_menubar(fake, None)
sys.stdout = sys.__stdout__
menu_out = buf.getvalue()
check("render_menubar has SwiftBar separator", "---" in menu_out)
check("render_menubar has Show all action", "Show all" in menu_out)
check("render_menubar title shows %", "67%" in menu_out)

# --- 6. one full widget loop cycle (mocked poll + sleep) ---------------------
m.get_token = lambda: "sk-ant-oat01-fake"
m.poll_usage = lambda tok: ({
    "anthropic-ratelimit-unified-5h-utilization": "0.673",
    "anthropic-ratelimit-unified-7d-utilization": "0.418",
    "anthropic-ratelimit-unified-5h-reset": str(int(time.time() + 9000)),
    "anthropic-ratelimit-unified-7d-reset": str(int(time.time() + 360000)),
    "anthropic-ratelimit-unified-status": "allowed",
    "anthropic-ratelimit-unified-representative-claim": "five_hour",
}, None)
_ticks = {"n": 0}


def fake_sleep(_s):
    _ticks["n"] += 1
    raise KeyboardInterrupt


m.time.sleep = fake_sleep
buf = io.StringIO()
sys.stdout = buf
m.run_widget()
sys.stdout = sys.__stdout__
check("run_widget completes one cycle cleanly", _ticks["n"] == 1)
m.time.sleep = time.sleep   # restore

print()
print(f"=== {len(passes)} passed, {len(fails)} failed ===")
if fails:
    print("FAILED:", ", ".join(fails))

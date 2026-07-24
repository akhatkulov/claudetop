# claudetop

[![QA](https://github.com/akhatkulov/claudetop/actions/workflows/qa.yml/badge.svg)](https://github.com/akhatkulov/claudetop/actions/workflows/qa.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**`htop` for Claude Code** — a live terminal dashboard for your Claude Code usage,
limits, cost and analytics. Reads your local `~/.claude` data — no API calls, no
network, no secrets leave your machine.

```
╭────────────────────────────────────────────────────────────────────────────╮
│  claudetop  ·  live usage & limits                             Max · Max 5×│
│  2026-07-24 14:55:33 +05                                                   │
├────────────────────────────────────────────────────────────────────────────┤
│  LIMIT OYNALARI  (sarf + reset gacha vaqt)                                 │
│  5-soat sarf ██████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   54%│
│               228.20M tk · $191.18  (heuristik limit)                      │
│  5-soat vaqt █████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   23%│
│               reset 19:00 · 3h 52m qoldi  · limitga ~57m                   │
│  7-kun  sarf ███████████████████████████████████████████░░░░░░░░░░░░░   76%│
│               1.49B tk · $1,113.67  (heuristik limit)                      │
│  7-kun  vaqt █████████████████████████████████████░░░░░░░░░░░░░░░░░░░   66%│
│               reset Du 00:00 · 2k 8s qoldi                                 │
├────────────────────────────────────────────────────────────────────────────┤
│  SARF (token · API-ekvivalent qiymat)                                      │
│  Bugun      473.76M       $357.60                                          │
│  7 kun        1.43B     $1,066.42                                          │
│  Jami         6.41B     $4,427.48                                          │
│  kesh     6.28B keshdan o'qildi · ~100% keshlangan                         │
├────────────────────────────────────────────────────────────────────────────┤
│  MODEL BO'YICHA · LOYIHA BO'YICHA · OXIRGI 7 KUN ▁▃█▂▆ …                    │
╰────────────────────────────────────────────────────────────────────────────╯
  [1]Umumiy [2]Sessiya [3]Faollik [4]Trend [5]Fikrlar  q=chiqish
```

> **Note:** the UI text is in **Uzbek**. Quick glossary: *5-soat* = 5-hour window,
> *7-kun* = 7-day window, *sarf* = spend, *loyiha* = project, *faollik* = activity,
> *vositalar* = tools, *fikrlar* = insights, *qoldi* = remaining.

## Why

Claude Code (Max / Pro / Team) has rolling **5-hour** and **weekly** usage limits, but
no easy way to see how close you are, how fast you're burning, or where your tokens
go. `claudetop` turns your local session transcripts into a live, responsive
dashboard — like `htop`, but for Claude Code.

## Features

- **Live limit windows** — for both the **5-hour** and **weekly** windows you get
  **two separate bars**: one for **usage** (% + tokens + cost) and one for **time to
  reset** (% elapsed + exact reset time + countdown) — 4 clear gauges. Plus
  **ETA-to-limit** ("~1h 1m at this rate"), burn rate, and a warning banner at 75%/90%.
- **5 interactive screens** (switch with number keys or `←`/`→`):
  - **Overview** — limits, spend, models, projects, last 7 days
  - **Sessions** — **live running** Claude Code processes (busy/idle + uptime) plus
    recent sessions with their AI-generated titles, model, tokens, cost
  - **Activity** — an hour × weekday **heatmap** + effort distribution + **tool usage**
    (Bash / Edit / Read …)
  - **Trends** — 30-day chart + monthly / yearly projection + comparisons & records
  - **Insights** — auto-generated observations (today's pace, weekly-limit trajectory,
    peak time, busiest project/branch, cache verdict, model-cost savings estimate)
- **Session drill-down** — `--session <name>` shows one session's full card, including
  the tools *that session* used.
- **Fully responsive** — fills the whole terminal: wide terminals get **2–3 column**
  layouts; tall terminals fill vertically; narrow/short ones degrade gracefully to a
  one-line summary.
- **5 color themes** — `default` · `mono` · `ocean` · `matrix` · `amber`.
- **Cost** shown as **API-equivalent value** (on a subscription you don't pay per
  token — this is what those tokens *would* cost on the API).
- **Exports** — Markdown report (`--report`), daily CSV (`--csv`), raw JSON (`--json`),
  one-line compact (`--compact`) for tmux/statusline.
- **Fast** — file-level `mtime`+`size` cache; warm refresh in ~0.2s even over hundreds
  of session files.
- **Zero dependencies** — just `bash` + `python3` (standard library only).

## Install

```bash
git clone https://github.com/akhatkulov/claudetop.git
cd claudetop
./install.sh          # symlinks `claudetop` into ~/.local/bin
```

Or run in place: `./claudetop`. Requires `bash` and `python3` (nothing else).

## Usage

```bash
claudetop                    # live dashboard (auto in an interactive terminal)
claudetop --once             # one-shot snapshot
claudetop --view insights    # start on a specific screen
claudetop --session "i18n"   # drill into one session (by name / ai-title / id)
claudetop --theme matrix     # color theme
claudetop --report > out.md  # shareable Markdown report
claudetop --csv > usage.csv  # daily usage for spreadsheets / BI
claudetop --compact          # one line, for tmux / statusline
claudetop -h                 # full help
```

**Keys (live mode):** `1`–`5` or `←`/`→` (`h`/`l`) switch screens · `r` refresh ·
`?` help · `q` / `Ctrl+C` quit.

## Real limits (optional)

By default the limit percentage is heuristic (relative to your busiest historical
window) because the exact token cap isn't stored locally. Read your real numbers from
`/status` inside Claude Code and set them for a true percentage:

```bash
claudetop --set-limit session=880000000 --set-limit weekly=2000000000
# or env: CLAUDETOP_SESSION_LIMIT=... CLAUDETOP_WEEKLY_LIMIT=...
# or config: ~/.config/claudetop/config.json
```

The **time-to-reset** bars are always exact. The 5-hour reset is derived from the
active window; the weekly reset defaults to **Monday 00:00 local** and is configurable
(check `/status` for your real reset):

```bash
claudetop --set-limit reset_weekday=3 --set-limit reset_hour=9   # e.g. Thursday 09:00
# weekday: 0=Mon … 6=Sun
```

## tmux / statusline

Use the compact output in your statusline:

```
⏳1h 27m  5h 39%  7d 73%  bugun 474M $357
```

A ready-made Claude Code statusline wrapper is in `statusline/`.

## How it works

- **Data source:** `~/.claude/projects/**/*.jsonl` (session transcripts — the `usage`
  block on each assistant message), `~/.claude/sessions/*.json` (live process state),
  and `~/.claude/.credentials.json` (subscription tier only — **secrets are never read
  or printed**).
- **Tokens** are de-duplicated by message + request id; synthetic messages are skipped.
- **Cost** uses public per-model API pricing (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5,
  Fable $10/$50 per 1M; cache-write 1.25×/2×, cache-read 0.1×).
- **Privacy:** everything is computed locally. No network requests are made and no
  message content is read for analytics.

## Development

```bash
python3 .qa/verify.py        # QA: alignment (1/2/3 columns), no-overflow, themes, exports
```

Architecture: `claudetop` (bash frontend — args, live loop, terminal handling) +
`lib/engine.py` (parsing, aggregation, rendering, exports).

## License

[MIT](LICENSE) © Mekhroj Akhatkulov

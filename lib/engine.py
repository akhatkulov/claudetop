#!/usr/bin/env python3
"""claudetop engine — shu qurilmadagi Claude Code hisobining token/xarajat/limit
o'lchovlarini ~/.claude/projects/**/*.jsonl transkriptlaridan hisoblaydi.

Chiqish: --json bo'lsa xom agregat JSON, aks holda ANSI bilan bo'yalgan dashboard.
Bash frontend (claudetop) buni live loop ichida chaqiradi.
"""
import os
import sys
import json
import glob
import time
import argparse
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CREDS_FILE = os.path.join(HOME, ".claude", ".credentials.json")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")),
                         "claudetop")
CACHE_FILE = os.path.join(CACHE_DIR, "rows-cache.json")
CACHE_VERSION = 5
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")),
                          "claudetop")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_limits():
    """Foydalanuvchi sozlagan haqiqiy token limitlari (0 = heuristik ishlatiladi).
    Ustuvorlik: env > config.json. Claude Code'da /status buni ko'rsatadi."""
    lim = {"session": 0, "weekly": 0}
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        lim["session"] = int(cfg.get("session_token_limit", 0) or 0)
        lim["weekly"] = int(cfg.get("weekly_token_limit", 0) or 0)
    except Exception:
        pass
    try:
        if os.environ.get("CLAUDETOP_SESSION_LIMIT"):
            lim["session"] = int(os.environ["CLAUDETOP_SESSION_LIMIT"])
        if os.environ.get("CLAUDETOP_WEEKLY_LIMIT"):
            lim["weekly"] = int(os.environ["CLAUDETOP_WEEKLY_LIMIT"])
    except ValueError:
        pass
    return lim

# 5 soatlik rolling sessiya oynasi (Claude usage limiti shu oynaga bog'liq)
BLOCK_HOURS = 5
BLOCK_SECONDS = BLOCK_HOURS * 3600

# API narxlari ($ / 1M token). Max/Pro obunada token uchun to'lanmaydi —
# bu "API ekvivalenti" qiymati (siz obuna evaziga qancha qiymat olayotganingiz).
# input, output, cache_write_5m, cache_write_1h, cache_read
PRICING = {
    "opus":   {"in": 5.0,  "out": 25.0},
    "sonnet": {"in": 3.0,  "out": 15.0},
    "haiku":  {"in": 1.0,  "out": 5.0},
    "fable":  {"in": 10.0, "out": 50.0},
    "mythos": {"in": 10.0, "out": 50.0},
}
DEFAULT_PRICE = {"in": 5.0, "out": 25.0}


def price_for(model):
    if not model:
        return DEFAULT_PRICE
    m = model.lower()
    for key, p in PRICING.items():
        if key in m:
            return p
    return DEFAULT_PRICE


def cost_of(usage, model):
    p = price_for(model)
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    cc = usage.get("cache_creation", {}) or {}
    c5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
    c1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
    # cache_creation_input_tokens agar ajratilgan bo'lmasa, hammasini 5m deb ol
    if not cc:
        c5 = usage.get("cache_creation_input_tokens", 0) or 0
    unit_in = p["in"] / 1_000_000
    unit_out = p["out"] / 1_000_000
    return (
        inp * unit_in
        + out * unit_out
        + c5 * unit_in * 1.25
        + c1 * unit_in * 2.0
        + cr * unit_in * 0.1
    )


def load_credentials():
    info = {"plan": "unknown", "tier": "unknown"}
    try:
        with open(CREDS_FILE) as f:
            oauth = json.load(f).get("claudeAiOauth", {})
        info["plan"] = oauth.get("subscriptionType", "unknown")
        info["tier"] = oauth.get("rateLimitTier", "unknown")
    except Exception:
        pass
    return info


def project_name(fpath):
    """~/.claude/projects/<kodlangan-yo'l>/[subagents|wf_.../]xxx.jsonl → qulay nom.
    Ichki subagent/workflow transkriptlari yuqori-darajadagi loyihaga bog'lanadi.
    Kodlash '/' → '-' (lossy), shuning uchun 'best effort'."""
    try:
        rel = os.path.relpath(fpath, PROJECTS_DIR)
        top = rel.split(os.sep)[0]
    except Exception:
        top = os.path.basename(os.path.dirname(fpath))
    raw = top
    d = top.lstrip("-")
    parts = d.split("-")
    if len(parts) >= 2 and parts[0] == "home":
        parts = parts[2:]  # 'home', '<user>' ni tashlab
    name = "-".join(parts) if parts else ""
    return name or "(home)"


def parse_file(fpath):
    """Bitta jsonl faylni ixcham ma'lumotga aylantiradi.
    Qaytadi: {rows, titles, eff, branch, ver, entry}
      rows: [ts, model, in, out, cr, c5, c1, msg_id, req_id, session]
      titles: {sessionId: aiTitle}, eff: {level:count}, branch: {sid:branch},
      ver: {version:count}, entry: {entrypoint:count}"""
    rows = []
    titles, eff, branch, ver, entry, tools, tools_sid = {}, {}, {}, {}, {}, {}, {}
    try:
        with open(fpath, "r", errors="replace") as fh:
            for line in fh:
                has_usage = '"usage"' in line
                if not (has_usage or '"ai-title"' in line):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = d.get("type")
                if t == "ai-title":
                    sid = d.get("sessionId")
                    if sid and d.get("aiTitle"):
                        titles[sid] = d["aiTitle"]
                    continue
                if t != "assistant":
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                model = msg.get("model") or "unknown"
                if model == "<synthetic>":
                    continue  # Claude Code'ning ichki sintetik xabari — API chaqiruvi emas
                ts = d.get("timestamp")
                if not ts:
                    continue
                cc = usage.get("cache_creation") or {}
                c5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
                c1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
                if not cc:
                    c5 = usage.get("cache_creation_input_tokens", 0) or 0
                sid = d.get("sessionId")
                rows.append([
                    ts, model,
                    usage.get("input_tokens", 0) or 0,
                    usage.get("output_tokens", 0) or 0,
                    usage.get("cache_read_input_tokens", 0) or 0,
                    c5, c1,
                    msg.get("id"), d.get("requestId"), sid,
                ])
                e = d.get("effort")
                if e:
                    eff[e] = eff.get(e, 0) + 1
                b = d.get("gitBranch")
                if b and sid:
                    branch[sid] = b
                v = d.get("version")
                if v:
                    ver[v] = ver.get(v, 0) + 1
                ep = d.get("entrypoint")
                if ep:
                    entry[ep] = entry.get(ep, 0) + 1
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            nm = blk.get("name")
                            if nm:
                                tools[nm] = tools.get(nm, 0) + 1
                                if sid:
                                    st = tools_sid.setdefault(sid, {})
                                    st[nm] = st.get(nm, 0) + 1
    except OSError:
        pass
    return {"rows": rows, "titles": titles, "eff": eff, "branch": branch,
            "ver": ver, "entry": entry, "tools": tools, "tools_sid": tools_sid}


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if data.get("v") != CACHE_VERSION:
            return {}
        return data.get("files", {})
    except Exception:
        return {}


def save_cache(cache):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"v": CACHE_VERSION, "files": cache}, f)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def parse_ts(ts):
    # ISO 8601, "...Z" yoki offsetli
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def tokens_total(usage):
    return (
        (usage.get("input_tokens", 0) or 0)
        + (usage.get("output_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0)
        + (usage.get("cache_creation_input_tokens", 0) or 0)
    )


def collect(use_cache=True):
    """Barcha yozuvlarni yig'ib, dedup qilib, vaqt bo'yicha tartiblangan ro'yxat qaytaradi.
    mtime+size asosidagi fayl-kesh bilan: o'zgarmagan fayllar qayta o'qilmaydi."""
    files = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    cache = load_cache() if use_cache else {}
    new_cache = {}
    dirty = False

    all_raw = []  # (project, session, row) uchtaliklari
    titles, eff, branch, ver, entry, tools, tools_sid = {}, {}, {}, {}, {}, {}, {}
    for fpath in files:
        try:
            st = os.stat(fpath)
        except OSError:
            continue
        sig = [int(st.st_mtime), st.st_size]
        cached = cache.get(fpath)
        if cached and cached.get("sig") == sig and "data" in cached:
            data = cached["data"]
        else:
            data = parse_file(fpath)
            dirty = True
        new_cache[fpath] = {"sig": sig, "data": data}
        proj = project_name(fpath)
        for r in data["rows"]:
            all_raw.append((proj, r))
        titles.update(data.get("titles", {}))
        branch.update(data.get("branch", {}))
        for k, v in data.get("eff", {}).items():
            eff[k] = eff.get(k, 0) + v
        for k, v in data.get("ver", {}).items():
            ver[k] = ver.get(k, 0) + v
        for k, v in data.get("entry", {}).items():
            entry[k] = entry.get(k, 0) + v
        for k, v in data.get("tools", {}).items():
            tools[k] = tools.get(k, 0) + v
        for sid, tc in data.get("tools_sid", {}).items():
            dst = tools_sid.setdefault(sid, {})
            for k, v in tc.items():
                dst[k] = dst.get(k, 0) + v

    if use_cache and (dirty or len(new_cache) != len(cache)):
        save_cache(new_cache)

    aux = {"titles": titles, "eff": eff, "branch": branch, "ver": ver,
           "entry": entry, "tools": tools, "tools_sid": tools_sid}
    seen = set()
    rows = []
    for proj, r in all_raw:
        ts, model, inp, out, cr, c5, c1, msg_id, req_id, session = r
        if msg_id and req_id:
            key = (msg_id, req_id)
            if key in seen:
                continue
            seen.add(key)
        dt = parse_ts(ts)
        if dt is None:
            continue
        usage = {
            "input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cr,
            "cache_creation": {"ephemeral_5m_input_tokens": c5,
                               "ephemeral_1h_input_tokens": c1},
        }
        rows.append({
            "dt": dt.astimezone(),
            "utc": dt.astimezone(timezone.utc),
            "model": model,
            "in": inp, "out": out, "cr": cr, "cw": c5 + c1,
            "tokens": inp + out + cr + c5 + c1,
            "cost": cost_of(usage, model),
            "session": session,
            "project": proj,
        })
    rows.sort(key=lambda r: r["utc"])
    return rows, aux


def load_live_sessions():
    """~/.claude/sessions/*.json — hozir ishlab turgan Claude Code protsesslari."""
    out = []
    sdir = os.path.join(HOME, ".claude", "sessions")
    try:
        files = glob.glob(os.path.join(sdir, "*.json"))
    except Exception:
        return out
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            continue
        out.append({
            "pid": d.get("pid"),
            "sid": d.get("sessionId"),
            "name": d.get("name") or "",
            "cwd": d.get("cwd") or "",
            "status": d.get("status") or "?",
            "kind": d.get("kind") or "",
            "version": d.get("version") or "",
            "startedAt": d.get("startedAt") or 0,
            "updatedAt": d.get("updatedAt") or 0,
        })
    out.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
    return out


def build_blocks(rows):
    """5-soatlik rolling bloklarga bo'lish (Claude limit oynasi mantiqiga yaqin)."""
    blocks = []
    cur = None
    for r in rows:
        t = r["utc"]
        if cur is None:
            start = t.replace(minute=0, second=0, microsecond=0)
            cur = {"start": start, "end": start + timedelta(hours=BLOCK_HOURS),
                   "last": t, "rows": [r]}
            continue
        gap = (t - cur["last"]).total_seconds()
        if t >= cur["end"] or gap >= BLOCK_SECONDS:
            blocks.append(cur)
            start = t.replace(minute=0, second=0, microsecond=0)
            cur = {"start": start, "end": start + timedelta(hours=BLOCK_HOURS),
                   "last": t, "rows": [r]}
        else:
            cur["last"] = t
            cur["rows"].append(r)
    if cur is not None:
        blocks.append(cur)
    for b in blocks:
        b["tokens"] = sum(x["tokens"] for x in b["rows"])
        b["cost"] = sum(x["cost"] for x in b["rows"])
        b["in"] = sum(x["in"] for x in b["rows"])
        b["out"] = sum(x["out"] for x in b["rows"])
    return blocks


def summarize(rows, aux=None):
    aux = aux or {"titles": {}, "eff": {}, "branch": {}, "ver": {}, "entry": {}}
    now = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    today = now_local.date()
    week_ago = now - timedelta(days=7)

    by_model = {}
    by_project = {}
    today_tok = today_cost = 0
    week_tok = week_cost = 0
    all_tok = all_cost = 0
    day_series = {}       # kun -> tokens (oxirgi 7 kun)
    all_day = {}          # kun -> tokens (butun tarix)
    all_day_cost = {}     # kun -> cost
    all_day_req = {}      # kun -> so'rovlar soni
    cr_tot = in_tot = cw_tot = out_tot = 0
    hourly = [[0] * 24 for _ in range(7)]  # [weekday(0=Du)][soat] -> tokens
    sess = {}

    for r in rows:
        all_tok += r["tokens"]; all_cost += r["cost"]
        cr_tot += r["cr"]; in_tot += r["in"]; cw_tot += r["cw"]; out_tot += r["out"]
        m = r["model"]
        bm = by_model.setdefault(m, {"tokens": 0, "cost": 0, "in": 0, "out": 0})
        bm["tokens"] += r["tokens"]; bm["cost"] += r["cost"]
        bm["in"] += r["in"]; bm["out"] += r["out"]
        p = r.get("project") or "?"
        bp = by_project.setdefault(p, {"tokens": 0, "cost": 0})
        bp["tokens"] += r["tokens"]; bp["cost"] += r["cost"]
        dloc = r["dt"]
        dk = dloc.date().isoformat()
        all_day[dk] = all_day.get(dk, 0) + r["tokens"]
        all_day_cost[dk] = all_day_cost.get(dk, 0) + r["cost"]
        all_day_req[dk] = all_day_req.get(dk, 0) + 1
        hourly[dloc.weekday()][dloc.hour] += r["tokens"]
        if dloc.date() == today:
            today_tok += r["tokens"]; today_cost += r["cost"]
        if r["utc"] >= week_ago:
            week_tok += r["tokens"]; week_cost += r["cost"]
            day_series[dk] = day_series.get(dk, 0) + r["tokens"]
        # sessiya agregatsiyasi
        s = r["session"] or "?"
        sd = sess.get(s)
        if sd is None:
            sd = sess[s] = {"tokens": 0, "cost": 0, "first": r["utc"], "last": r["utc"],
                            "count": 0, "models": set(), "project": p}
        sd["tokens"] += r["tokens"]; sd["cost"] += r["cost"]; sd["count"] += 1
        sd["models"].add(m)
        if r["utc"] > sd["last"]:
            sd["last"] = r["utc"]
        if r["utc"] < sd["first"]:
            sd["first"] = r["utc"]

    blocks = build_blocks(rows)
    baseline = max((b["tokens"] for b in blocks), default=0)

    # haftalik baseline = eng gavjum 7-kalendar-kunlik oyna (butun tarix)
    weekly_baseline = 0
    if all_day:
        dates = sorted(datetime.fromisoformat(d).date() for d in all_day.keys())
        d0, d1 = dates[0], dates[-1]
        arr, cur = [], d0
        while cur <= d1:
            arr.append(all_day.get(cur.isoformat(), 0)); cur += timedelta(days=1)
        window = 0
        for i, v in enumerate(arr):
            window += v
            if i >= 7:
                window -= arr[i - 7]
            weekly_baseline = max(weekly_baseline, window)

    cache_hit = (cr_tot / (cr_tot + in_tot)) if (cr_tot + in_tot) else 0.0

    active = None
    if blocks:
        last = blocks[-1]
        if now < last["end"]:
            active = last

    # sessiya nomlari (ai-title) va git branch qo'shamiz
    titles = aux.get("titles", {})
    branches = aux.get("branch", {})
    tools_sid = aux.get("tools_sid", {})
    by_branch = {}
    for sid, sd in sess.items():
        sd["title"] = titles.get(sid, "")
        sd["branch"] = branches.get(sid, "")
        sd["tools"] = tools_sid.get(sid, {})
        b = sd["branch"] or ""
        bb = by_branch.setdefault(b, {"tokens": 0, "cost": 0, "sessions": 0})
        bb["tokens"] += sd["tokens"]; bb["cost"] += sd["cost"]; bb["sessions"] += 1
    recent_sessions = sorted(sess.items(), key=lambda kv: kv[1]["last"], reverse=True)

    # 30-kunlik seriya (bugun oxirida)
    series30 = []
    for i in range(29, -1, -1):
        dd = (today - timedelta(days=i))
        k = dd.isoformat()
        series30.append({"date": dd, "tokens": all_day.get(k, 0),
                         "cost": all_day_cost.get(k, 0)})

    return {
        "now": now, "now_local": now_local,
        "by_model": by_model, "by_project": by_project, "by_branch": by_branch,
        "today": {"tokens": today_tok, "cost": today_cost},
        "week": {"tokens": week_tok, "cost": week_cost},
        "all": {"tokens": all_tok, "cost": all_cost},
        "blocks": blocks, "active": active, "baseline": baseline,
        "weekly_baseline": weekly_baseline,
        "cache": {"read": cr_tot, "fresh_in": in_tot, "write": cw_tot,
                  "out": out_tot, "hit": cache_hit},
        "day_series": day_series, "series30": series30,
        "all_day": all_day, "all_day_cost": all_day_cost, "all_day_req": all_day_req,
        "hourly": hourly,
        "recent_sessions": recent_sessions,
        "sessions_total": len(sess),
        "effort": aux.get("eff", {}), "versions": aux.get("ver", {}),
        "entry_pts": aux.get("entry", {}), "tools": aux.get("tools", {}),
        "entry_count": len(rows),
    }


# ---------- Rendering ----------
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "mag": "\033[35m", "cyan": "\033[36m", "white": "\033[37m",
    "bred": "\033[91m", "bgreen": "\033[92m", "byellow": "\033[93m",
    "bblue": "\033[94m", "bmag": "\033[95m", "bcyan": "\033[96m",
    "gray": "\033[90m",
}
NOCOLOR = {k: "" for k in C}

# rang temalari — accent kalitlarni qayta xaritalaydi (semantika saqlanadi:
# gray/bred/bold alohida qoladi, faqat asosiy hue o'zgaradi)
_W = "\033[97m"
THEMES = {
    "default": {},
    "mono": {k: _W for k in ("bcyan", "bmag", "bgreen", "bblue", "blue",
             "byellow", "cyan", "green", "mag", "yellow", "red", "bred")},
    "ocean": {"bcyan": "\033[96m", "bmag": "\033[94m", "bgreen": "\033[96m",
              "bblue": "\033[94m", "blue": "\033[34m", "byellow": "\033[96m",
              "cyan": "\033[36m", "green": "\033[36m", "mag": "\033[34m"},
    "matrix": {"bcyan": "\033[92m", "bmag": "\033[92m", "bgreen": "\033[92m",
               "bblue": "\033[32m", "blue": "\033[32m", "byellow": "\033[92m",
               "cyan": "\033[32m", "green": "\033[32m", "mag": "\033[32m"},
    "amber": {"bcyan": "\033[93m", "bmag": "\033[33m", "bgreen": "\033[93m",
              "bblue": "\033[33m", "blue": "\033[33m", "byellow": "\033[93m",
              "cyan": "\033[33m", "green": "\033[33m", "mag": "\033[33m"},
}


def theme_palette(color, name):
    """color=False → NOCOLOR; aks holda tanlangan tema qo'llanilgan palitra."""
    if not color:
        return NOCOLOR
    ov = THEMES.get(name or "default")
    if not ov:
        return C
    c2 = dict(C)
    c2.update(ov)
    return c2


def human_tokens(n):
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{int(n)}"


def money(x):
    return f"${x:,.2f}"


def delta_str(cur, prev, c):
    """Ikki qiymat farqini rangli strelka bilan: ▲+23% / ▼-12% / =."""
    if prev <= 0:
        return c["gray"] + "—" + c["reset"]
    d = (cur - prev) / prev * 100
    if d > 2:
        return c["byellow"] + f"▲{d:+.0f}%" + c["reset"]
    if d < -2:
        return c["bcyan"] + f"▼{d:+.0f}%" + c["reset"]
    return c["gray"] + f"={d:+.0f}%" + c["reset"]


def dur(seconds):
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def bar(frac, width, col, c):
    frac = max(0.0, min(1.5, frac))
    filled = int(round(min(1.0, frac) * width))
    over = frac > 1.0
    ch = "█"
    b = ch * filled + c["gray"] + "░" * (width - filled) + c["reset"]
    color = c["bred"] if over or frac >= 0.9 else (c["byellow"] if frac >= 0.7 else col)
    return color + ch * filled + c["reset"] + c["gray"] + "░" * (width - filled) + c["reset"]


import re
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vis_len(s):
    return len(_ANSI_RE.sub("", s))


def model_short(m):
    ml = m.lower()
    ver = ""
    mm = re.search(r"(\d+)[-.](\d+)", ml)
    if mm:
        ver = f"{mm.group(1)}.{mm.group(2)}"
    else:
        mm = re.search(r"-(\d+)(?!\d)", ml)
        if mm:
            ver = mm.group(1)
    if "opus" in ml: base = "Opus"
    elif "sonnet" in ml: base = "Sonnet"
    elif "haiku" in ml: base = "Haiku"
    elif "fable" in ml: base = "Fable"
    elif "mythos" in ml: base = "Mythos"
    else: return m[:10]
    return (base + " " + ver).strip()[:10]


def tool_short(n):
    if n.startswith("mcp__"):
        parts = [p for p in n.split("__") if p]
        return ("mcp:" + parts[-1]) if len(parts) > 1 else n[:14]
    return n[:14]


def term_width(width=None):
    if width and width > 0:
        cols = int(width)
    else:
        try:
            import shutil
            cols = shutil.get_terminal_size(fallback=(80, 24)).columns
        except Exception:
            cols = 80
    # quti ichki kengligi = cols - 2 (chegaralar). Keng terminalni to'liq egallaydi;
    # juda keng bo'lsa ko'p-ustunli joylashuv qo'llanadi (render ichida).
    W = cols - 2
    if W > 300:
        W = 300
    if W < 20:
        W = max(16, cols - 2)
    return W


def term_height(height=None):
    """Terminal balandligi (qatorlar). 0/None → cheklovsiz (butun dashboard)."""
    if height and height > 0:
        return int(height)
    return 0  # avtomatik aniqlash faqat --height orqali (watch rejimda beriladi)


def render(summ, creds, limits=None, color=True, live_hint=True, width=None, height=None,
           view="overview", live_sessions=None, theme="default", session_query=None):
    limits = limits or {"session": 0, "weekly": 0}
    live_sessions = live_sessions or []
    c = theme_palette(color, theme)
    WBOX = term_width(width)   # quti ichki to'liq kengligi
    H = term_height(height)
    # ma'lumot yo'q — do'stona xabar
    if summ["entry_count"] == 0:
        msg = " Hali Claude Code foydalanish ma'lumoti yo'q "
        pad = max(0, WBOX - vis_len(msg))
        return (c["bcyan"] + "╭" + "─" * WBOX + "╮\n│" + c["reset"] + msg + " " * pad
                + c["bcyan"] + "│\n╰" + "─" * WBOX + "╯" + c["reset"])
    # juda past terminal — quti sig'maydi, bir qatorli compact ko'rinishga o'tamiz
    if 0 < H < 13:
        return render_compact(summ, limits, color=color, theme=theme)
    B = c["bcyan"]

    # ---- ustunlar sonini aniqlash (keng terminalda yonma-yon bo'limlar) ----
    GAP = 3  # ustunlar orasi " │ "
    if session_query is not None:
        ncols = 1                       # sessiya kartasi doim 1 ustun
    elif WBOX >= 184:
        ncols = 3
    elif WBOX >= 108:
        ncols = 2
    else:
        ncols = 1
    if ncols == 1:
        colw = [WBOX]
        CW = WBOX
    else:
        CW = (WBOX - 1 - (ncols - 1) * GAP) // ncols
        if CW < 42:                     # ustun juda tor — bittaga kamaytiramiz
            ncols -= 1
            if ncols <= 1:
                ncols, colw, CW = 1, [WBOX], WBOX
            else:
                CW = (WBOX - 1 - (ncols - 1) * GAP) // ncols
        if ncols > 1:
            colw = [CW] * ncols
            colw[-1] += (WBOX - 1 - (ncols - 1) * GAP) - CW * ncols  # qoldiq oxirgiga

    # W — builderlar ishlatadigan JORIY kontent kengligi (header uchun WBOX,
    # tana bo'limlari uchun ustun kengligi). Python closure joriy qiymatni o'qiydi.
    W = WBOX

    def clip(s, w):
        """ANSI-xavfsiz kesish: ko'rinadigan w belgigacha."""
        if vis_len(s) <= w:
            return s
        res, seen, i = [], 0, 0
        while i < len(s) and seen < w:
            if s[i] == "\033":
                m = _ANSI_RE.match(s, i)
                if m:
                    res.append(m.group(0)); i = m.end(); continue
            res.append(s[i]); seen += 1; i += 1
        return "".join(res) + c["reset"]

    def padcell(s, w):
        """Raw kontent qatorini w kenglikka to'ldiradi (yoki kesadi)."""
        vis = vis_len(s)
        if vis > w:
            return clip(s, w)
        return s + " " * (w - vis)

    def boxln(s=""):
        content = " " + s
        vis = vis_len(content)
        if vis > WBOX:
            content = clip(content, WBOX); vis = WBOX
        return B + "│" + c["reset"] + content + " " * (WBOX - vis) + B + "│" + c["reset"]

    def fillbar(prefix, suffix, frac, col):
        iw = W - 1
        bw = max(3, iw - vis_len(prefix) - vis_len(suffix))
        return prefix + bar(frac, bw, col, c) + suffix

    def lr(left, right):
        iw = W - 1
        rv = vis_len(right)
        left = clip(left, max(1, iw - rv - 1))
        pad = max(1, iw - vis_len(left) - rv)
        return left + " " * pad + right

    plan_name = {"team": "Team", "max": "Max", "pro": "Pro", "free": "Free"}.get(
        creds["plan"], creds["plan"].title())
    if "max_20" in creds["tier"]:
        tier_disp = "Max 20×"
    elif "max_5" in creds["tier"]:
        tier_disp = "Max 5×"
    elif creds["tier"] == "unknown":
        tier_disp = ""
    else:
        tier_disp = creds["tier"].replace("default_", "").replace("_", " ").title()

    now = summ["now_local"]

    # ---- limit hisob-kitoblari (banner + LIMIT bo'limi uchun oldindan) ----
    active = summ["active"]
    frac_lim = 0.0
    if active:
        remaining = (active["end"] - summ["now"]).total_seconds()
        elapsed = (summ["now"] - active["start"]).total_seconds()
        tok = active["tokens"]
        sess_lim = limits["session"] if limits["session"] > 0 else summ["baseline"]
        frac_lim = (tok / sess_lim) if sess_lim else 0
        rate = tok / elapsed if elapsed > 0 else 0
        projected = tok + rate * remaining
    wk = summ["week"]["tokens"]
    wk_lim = limits["weekly"] if limits["weekly"] > 0 else summ["weekly_baseline"]
    frac_wk = (wk / wk_lim) if wk_lim else 0
    worst = max(frac_lim, frac_wk) * 100

    # ---- bo'limlarni qatorlar ro'yxati sifatida quramiz (muhimlik tartibida) ----
    badge = f"{plan_name}" + (f" · {tier_disp}" if tier_disp else "")
    hdr = [
        lr(c["bold"] + c["bcyan"] + " claudetop" + c["reset"]
           + c["gray"] + "  ·  live usage & limits" + c["reset"],
           c["cyan"] + badge + c["reset"]),
        c["gray"] + " " + now.strftime("%Y-%m-%d %H:%M:%S %Z") + c["reset"],
    ]
    if worst >= 90:
        hdr.append(c["bold"] + c["bred"]
                   + f" ⚠  LIMIT DIQQAT — {worst:.0f}% ishlatildi, sekinlashtiring" + c["reset"])
    elif worst >= 75:
        hdr.append(c["byellow"] + f" ⚠  limit oynasining {worst:.0f}% i ishlatildi" + c["reset"])

    # ================= EKRAN 1: UMUMIY =================
    def mk_overview():
        lim = [c["bold"] + " LIMIT OYNALARI" + c["reset"], ""]
        if active:
            basis = "sozlangan limit" if limits["session"] > 0 else "eng gavjum oynaga nisbatan"
            if sess_lim and rate > 0 and projected >= sess_lim:
                eta = (sess_lim - tok) / rate
                ecol = c["bred"] if eta <= remaining else c["byellow"]
                proj = ecol + f"~{dur(eta)} da limitga yetadi" + c["reset"]
            else:
                proj = c["gray"] + f"~{human_tokens(projected)} proyeksiya" + c["reset"]
            lim += [
                fillbar(" 5-soat ", f" {frac_lim*100:4.0f}%", frac_lim, c["bgreen"]),
                c["gray"] + lr(f"        {human_tokens(tok)} tk · {money(active['cost'])}",
                               f"{dur(remaining)} qoldi ") + c["reset"],
                c["gray"] + f"        {human_tokens(rate*60)}/daq · " + proj
                + c["gray"] + f"  ({basis})" + c["reset"],
            ]
        else:
            lim.append(c["gray"] + " 5-soat  hozir aktiv oyna yo'q" + c["reset"])
        wbasis = "sozlangan limit" if limits["weekly"] > 0 else "eng gavjum 7-kunga nisbatan"
        lim += [
            "",
            fillbar(" 7-kun  ", f" {frac_wk*100:4.0f}%", frac_wk, c["bmag"]),
            c["gray"] + f"        {human_tokens(wk)} tk · {money(summ['week']['cost'])}"
            + f"  ({wbasis})" + c["reset"],
        ]

        sarf = [c["bold"] + " SARF " + c["reset"] + c["gray"] + "(token · API-ekvivalent qiymat)"
                + c["reset"], ""]
        for label, d in (("Bugun", summ["today"]), ("7 kun", summ["week"]), ("Jami", summ["all"])):
            sarf.append(c["cyan"] + f" {label:<8}" + c["reset"]
                        + f"{human_tokens(d['tokens']):>10}   "
                        + c["bgreen"] + f"{money(d['cost']):>11}" + c["reset"])
        sarf.append(c["gray"] + f" kesh     {human_tokens(summ['cache']['read'])} keshdan o'qildi"
                    + f" · ~{summ['cache']['hit']*100:.0f}% keshlangan" + c["reset"])

        model = [c["bold"] + " MODEL BO'YICHA " + c["reset"] + c["gray"] + "(jami)" + c["reset"], ""]
        bm = sorted(summ["by_model"].items(), key=lambda kv: kv[1]["cost"], reverse=True)
        total_cost = summ["all"]["cost"] or 1
        for m, d in bm[:5]:
            if d["cost"] <= 0 and d["tokens"] <= 0:
                continue
            prefix = " " + c["byellow"] + f"{model_short(m):<10}" + c["reset"]
            suffix = (f" {human_tokens(d['tokens']):>8}" + c["green"]
                      + f"{money(d['cost']):>10}" + c["reset"] + " ")
            model.append(fillbar(prefix, suffix, d["cost"] / total_cost, c["bmag"]))
        if len(model) == 2:
            model.append(c["gray"] + " ma'lumot yo'q" + c["reset"])

        loyiha = [c["bold"] + " LOYIHA BO'YICHA " + c["reset"] + c["gray"] + "(jami)" + c["reset"], ""]
        bp = sorted(summ["by_project"].items(), key=lambda kv: kv[1]["tokens"], reverse=True)
        total_tok = summ["all"]["tokens"] or 1
        namew = max(10, min(24, W // 3))
        for p, d in bp[:4]:
            if d["tokens"] <= 0:
                continue
            name = p if len(p) <= namew else "…" + p[-(namew - 1):]
            prefix = " " + c["bcyan"] + f"{name:<{namew}}" + c["reset"]
            suffix = f" {human_tokens(d['tokens']):>8} "
            loyiha.append(fillbar(prefix, suffix, d["tokens"] / total_tok, c["blue"]))
        if len(loyiha) == 2:
            loyiha.append(c["gray"] + " ma'lumot yo'q" + c["reset"])

        spk = [c["bold"] + " OXIRGI 7 KUN" + c["reset"], ""]
        spark = "▁▂▃▄▅▆▇█"
        days = [((now.date() - timedelta(days=i)),
                 summ["day_series"].get((now.date() - timedelta(days=i)).isoformat(), 0))
                for i in range(6, -1, -1)]
        mx = max((v for _, v in days), default=0) or 1
        ann = f"  max {human_tokens(mx)}/kun"
        sbw = max(1, min(7, (W - len(ann) - 2) // 7))
        srow = " " + "".join(c["bcyan"] + spark[int((v / mx) * (len(spark) - 1)) if v else 0] * sbw
                             + c["reset"] + " " for _, v in days)
        spk.append(srow + c["gray"] + ann + c["reset"])
        spk.append(c["gray"] + " " + " ".join(d.strftime("%a")[:sbw].ljust(sbw)
                   for d, _ in days) + c["reset"])
        return [lim, sarf, model, loyiha, spk]

    # ================= EKRAN 2: SESSIYALAR =================
    def mk_sessions():
        now_ms = summ["now"].timestamp() * 1000
        live = [s for s in live_sessions
                if s.get("updatedAt") and (now_ms - s["updatedAt"]) < 15 * 60 * 1000]
        L = [c["bold"] + " JONLI SESSIYALAR " + c["reset"]
             + c["gray"] + f"({len(live)} faol)" + c["reset"], ""]
        if live:
            for s in live[:7]:
                busy = s["status"] == "busy"
                dot = (c["bgreen"] + "●" if busy else c["bcyan"] + "○") + c["reset"]
                up = (now_ms - s["startedAt"]) / 1000 if s.get("startedAt") else 0
                nm = s["name"] or (s["sid"] or "?")[:8]
                st = (c["bgreen"] + "ishlayapti" if busy else c["cyan"] + "kutmoqda") + c["reset"]
                L.append(lr(" " + dot + " " + c["white"] + nm + c["reset"],
                            st + c["gray"] + f"  {dur(up)}" + c["reset"]))
        else:
            L.append(c["gray"] + " hozir ishlab turgan sessiya yo'q" + c["reset"])
        R = [c["bold"] + " SO'NGGI SESSIYALAR " + c["reset"]
             + c["gray"] + f"(jami {summ['sessions_total']})" + c["reset"], ""]
        for sid, sd in summ["recent_sessions"][:8]:
            title = sd.get("title") or "(nomsiz)"
            R.append(lr(" " + c["cyan"] + title + c["reset"],
                        c["byellow"] + model_short(next(iter(sd["models"]))) + c["reset"]
                        + c["gray"] + f"  {human_tokens(sd['tokens'])} · {money(sd['cost'])}"
                        + c["reset"]))
        if len(R) == 2:
            R.append(c["gray"] + " ma'lumot yo'q" + c["reset"])
        BR = [c["bold"] + " BRANCH BO'YICHA " + c["reset"] + c["gray"] + "(jami)" + c["reset"], ""]
        bb = sorted(((k, v) for k, v in summ["by_branch"].items() if k),
                    key=lambda kv: kv[1]["tokens"], reverse=True)
        tt = summ["all"]["tokens"] or 1
        nw = max(10, min(28, W // 3))
        for b, d in bb[:4]:
            nm = b if len(b) <= nw else "…" + b[-(nw - 1):]
            BR.append(fillbar(" " + c["bcyan"] + f"{nm:<{nw}}" + c["reset"],
                              f" {human_tokens(d['tokens']):>8} ", d["tokens"] / tt, c["blue"]))
        if len(BR) == 2:
            BR.append(c["gray"] + " ma'lumot yo'q" + c["reset"])
        return [L, R, BR]

    # ================= EKRAN 3: FAOLLIK =================
    def mk_activity():
        hm = summ["hourly"]
        mxc = max((max(r) for r in hm), default=0) or 1
        shades = " ░▒▓█"
        dab = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
        A = [c["bold"] + " FAOLLIK " + c["reset"] + c["gray"] + "(soat × hafta kuni)" + c["reset"], ""]
        A.append(c["gray"] + "    " + "".join(("│" if h % 6 == 0 else " ") for h in range(24))
                 + c["reset"])
        for wd in range(7):
            cells = ""
            for h in range(24):
                v = hm[wd][h]
                idx = min(4, int((v / mxc) * 5)) if v else 0
                if idx <= 0:
                    cells += c["gray"] + "·" + c["reset"]
                else:
                    col = c["bcyan"] if idx <= 1 else (c["bblue"] if idx == 2
                          else (c["byellow"] if idx == 3 else c["bred"]))
                    cells += col + shades[idx] + c["reset"]
            A.append(" " + c["cyan"] + dab[wd] + c["reset"] + " " + cells)
        A.append(c["gray"] + "    0     6     12    18  23" + c["reset"])
        peak = max(((hm[wd][h], wd, h) for wd in range(7) for h in range(24)), default=(0, 0, 0))
        if peak[0] > 0:
            A += ["", c["gray"] + f" eng faol: {dab[peak[1]]} · soat {peak[2]:02d}:00" + c["reset"]]

        E = [c["bold"] + " EFFORT " + c["reset"] + c["gray"] + "(so'rovlar soni)" + c["reset"], ""]
        eff = summ.get("effort", {})
        tot = sum(eff.values()) or 1
        order = [k for k in ("max", "xhigh", "high", "medium", "low") if eff.get(k)]
        order += [k for k in sorted(eff, key=lambda x: -eff[x]) if k not in order]
        for lv in order:
            E.append(fillbar(" " + c["byellow"] + f"{lv:<7}" + c["reset"],
                             f" {eff[lv]:>7}", eff[lv] / tot, c["bmag"]))
        if len(E) == 2:
            E.append(c["gray"] + " ma'lumot yo'q" + c["reset"])

        TL = [c["bold"] + " VOSITALAR " + c["reset"] + c["gray"] + "(chaqiruvlar)" + c["reset"], ""]
        tls = sorted(summ.get("tools", {}).items(), key=lambda kv: kv[1], reverse=True)
        ttot = sum(v for _, v in tls) or 1
        for n, cnt in tls[:6]:
            TL.append(fillbar(" " + c["bcyan"] + f"{tool_short(n):<14}" + c["reset"],
                              f" {cnt:>7}", cnt / ttot, c["bblue"]))
        if len(TL) == 2:
            TL.append(c["gray"] + " ma'lumot yo'q" + c["reset"])
        return [A, E, TL]

    # ================= EKRAN 4: TREND =================
    def mk_trends():
        s30 = summ["series30"]
        toks = [d["tokens"] for d in s30]
        mx = max(toks, default=0) or 1
        spark = "▁▂▃▄▅▆▇█"
        cw = max(1, min(3, (W - 4) // 30))
        T = [c["bold"] + " 30 KUNLIK TREND " + c["reset"] + c["gray"] + "(token/kun)" + c["reset"], ""]
        T.append(" " + "".join(
            c["bcyan"] + (spark[int(t / mx * (len(spark) - 1))] if t else "·") * cw + c["reset"]
            for t in toks))
        T.append(c["gray"] + f" {s30[0]['date'].strftime('%m-%d')} … "
                 f"{s30[-1]['date'].strftime('%m-%d')}   max {human_tokens(mx)}/kun" + c["reset"])
        tot_tok = sum(toks)
        tot_cost = sum(d["cost"] for d in s30)
        avg_cost = tot_cost / 30
        # oldingi 30 kun (solishtirish uchun)
        adc = summ["all_day_cost"]
        td = now.date()
        prev30 = sum(adc.get((td - timedelta(days=i)).isoformat(), 0) for i in range(30, 60))
        best = max(s30, key=lambda d: d["cost"]) if s30 else None
        S = [c["bold"] + " PROGNOZ " + c["reset"] + c["gray"] + "(API-ekvivalent)" + c["reset"], ""]
        S.append(lr(c["cyan"] + " 30-kun jami" + c["reset"],
                    c["bgreen"] + f"{human_tokens(tot_tok)} · {money(tot_cost)}  " + c["reset"]
                    + delta_str(tot_cost, prev30, c) + " "))
        if best and best["cost"] > 0:
            S.append(lr(c["cyan"] + " eng gavjum kun" + c["reset"],
                        c["gray"] + f"{best['date'].strftime('%m-%d')} · {money(best['cost'])}" + c["reset"]))
        S.append(lr(c["cyan"] + " kunlik o'rtacha" + c["reset"],
                    c["gray"] + f"{human_tokens(tot_tok / 30)} · {money(avg_cost)}" + c["reset"]))
        S.append(lr(c["cyan"] + " oylik proyeksiya" + c["reset"],
                    c["byellow"] + f"~{money(avg_cost * 30)}" + c["reset"]))
        S.append(lr(c["cyan"] + " yillik proyeksiya" + c["reset"],
                    c["byellow"] + f"~{money(avg_cost * 365)}" + c["reset"]))
        return [T, S]

    # ================= EKRAN 5: FIKRLAR (insights) =================
    def mk_insights():
        dab = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
        s30 = summ["series30"]
        avg_tok = sum(d["tokens"] for d in s30) / 30
        today_tok = summ["today"]["tokens"]
        # bugungi sur'at — HAQIQIY soatlik profil bo'yicha (naive kun-ulushi emas):
        # odatda shu vaqtga qadar kunlik sarfning qancha % i to'planadi?
        hm = summ["hourly"]
        prof = [sum(hm[w][h] for w in range(7)) for h in range(24)]
        ptot = sum(prof) or 1
        elapsed_frac = (sum(prof[:now.hour]) + prof[now.hour] * (now.minute / 60.0)) / ptot

        HOL = [c["bold"] + " HOLAT" + c["reset"], ""]
        if elapsed_frac >= 0.05 and avg_tok:
            proj_today = today_tok / elapsed_frac
            ratio = proj_today / avg_tok
            if ratio >= 1.5:
                HOL.append(c["byellow"] + " ▲ Bugun jadal " + c["reset"] + c["gray"]
                           + f"— ~{human_tokens(proj_today)} proyeksiya, o'rtachadan {ratio:.1f}×" + c["reset"])
            elif ratio <= 0.6:
                HOL.append(c["bcyan"] + " ▼ Bugun sokin " + c["reset"] + c["gray"]
                           + f"— ~{human_tokens(proj_today)} proyeksiya, o'rtachaning {ratio:.1f}×" + c["reset"])
            else:
                HOL.append(c["gray"] + f" = Bugun odatdagidek — ~{human_tokens(proj_today)} proyeksiya" + c["reset"])
        else:
            HOL.append(c["gray"] + f" • Bugun hozircha {human_tokens(today_tok)}"
                       + " (erta — proyeksiya hali beqaror)" + c["reset"])
        # haftalik limit traektoriyasi
        drate = wk / 7.0
        if wk_lim and wk >= wk_lim:
            HOL.append(c["bred"] + " ⚠ Haftalik limit oshib ketgan" + c["reset"])
        elif wk_lim and drate > 0 and wk < wk_lim:
            dleft = (wk_lim - wk) / drate
            col = c["bred"] if dleft <= 2 else (c["byellow"] if dleft <= 4 else c["gray"])
            HOL.append(col + f" • Shu sur'atda haftalik limitga ~{dleft:.1f} kun" + c["reset"])
        # hafta-hafta solishtirish (to'liq 7-kunlik oynalar — halol)
        ad = summ["all_day"]
        td = now.date()
        this7 = sum(ad.get((td - timedelta(days=i)).isoformat(), 0) for i in range(0, 7))
        prev7 = sum(ad.get((td - timedelta(days=i)).isoformat(), 0) for i in range(7, 14))
        HOL.append(c["gray"] + f" • Bu hafta: {human_tokens(this7)}  "
                   + delta_str(this7, prev7, c) + c["gray"] + " o'tgan haftaga nisbatan" + c["reset"])
        # aktiv 5-soat ETA (agar bor bo'lsa)
        if active and sess_lim and rate > 0 and projected >= sess_lim:
            eta = (sess_lim - tok) / rate
            HOL.append(c["bred"] + f" ⚠ 5-soatlik limitga ~{dur(eta)} qoldi (shu sur'atda)" + c["reset"])

        NAQ = [c["bold"] + " NAQSHLAR" + c["reset"], ""]
        # eng faol vaqt
        hm = summ["hourly"]
        peak = max(((hm[w][h], w, h) for w in range(7) for h in range(24)), default=(0, 0, 0))
        if peak[0] > 0:
            NAQ.append(c["gray"] + f" • Eng faol vaqt: {dab[peak[1]]}, soat {peak[2]:02d}:00" + c["reset"])
        # eng ko'p ishlatilgan vosita
        tls = sorted(summ.get("tools", {}).items(), key=lambda kv: kv[1], reverse=True)
        if tls:
            n, cnt = tls[0]
            tt = sum(v for _, v in tls) or 1
            NAQ.append(c["gray"] + f" • Eng ko'p vosita: {tool_short(n)} ({cnt/tt*100:.0f}%)" + c["reset"])
        # eng ko'p loyiha
        bp = sorted(summ["by_project"].items(), key=lambda kv: kv[1]["tokens"], reverse=True)
        tot_tok = summ["all"]["tokens"] or 1
        if bp:
            p, d = bp[0]
            NAQ.append(c["gray"] + f" • Eng ko'p loyiha: {p} ({d['tokens']/tot_tok*100:.0f}%)" + c["reset"])
        # eng faol branch
        bb = sorted(((k, v) for k, v in summ["by_branch"].items() if k),
                    key=lambda kv: kv[1]["tokens"], reverse=True)
        if bb:
            k, v = bb[0]
            NAQ.append(c["gray"] + f" • Faol branch: {k} ({human_tokens(v['tokens'])})" + c["reset"])
        # model kontsentratsiyasi
        opus = sum(d["cost"] for m, d in summ["by_model"].items() if "opus" in m.lower())
        tot_cost = summ["all"]["cost"] or 1
        oshare = opus / tot_cost
        if oshare >= 0.6:
            # 30% Opus→Sonnet ko'chirilsa taxminiy oylik tejash (Sonnet ~0.6× narx)
            avg_daily = sum(x["cost"] for x in summ["series30"]) / 30
            save = 0.4 * 0.3 * (avg_daily * 30) * oshare
            NAQ.append(c["gray"] + f" • Opus xarajatning {oshare*100:.0f}% i — 30% ni Sonnet"
                       + f" qilsangiz ~{money(save)}/oy tejash" + c["reset"])
        # kesh hukmi
        ch = summ["cache"]["hit"] * 100
        verdict = ("ajoyib, tejamkor" if ch >= 95 else ("yaxshi" if ch >= 80
                   else "past — katta kontekst qayta ishlanmoqda"))
        NAQ.append(c["gray"] + f" • Kesh: {ch:.0f}% ({verdict})" + c["reset"])
        # eng gavjum kun (rekord)
        ad = summ["all_day"]
        if ad:
            rday = max(ad, key=ad.get)
            rcost = summ["all_day_cost"].get(rday, 0)
            NAQ.append(c["gray"] + f" • Eng gavjum kun: {rday} "
                       + f"({human_tokens(ad[rday])} · {money(rcost)})" + c["reset"])
            # faollik streak (ketma-ket faol kunlar)
            td = now.date()
            start = td if ad.get(td.isoformat(), 0) > 0 else td - timedelta(days=1)
            streak, dd = 0, start
            while ad.get(dd.isoformat(), 0) > 0:
                streak += 1
                dd -= timedelta(days=1)
            if streak >= 2:
                NAQ.append(c["gray"] + f" • Faollik: {streak} kun ketma-ket" + c["reset"])
        # eng qimmat sessiya
        rs = summ["recent_sessions"]
        if rs:
            sid, sd = max(rs, key=lambda kv: kv[1]["cost"])
            title = sd.get("title") or "(nomsiz)"
            NAQ.append(lr(c["gray"] + f" • Eng qimmat sessiya: {title}" + c["reset"],
                          c["gray"] + money(sd["cost"]) + " " + c["reset"]))
        return [HOL, NAQ]

    # ================= YORDAM (overlay) =================
    def mk_help():
        K = [c["bold"] + " TUGMALAR" + c["reset"], ""]
        for key, desc in (("1 – 5", "ekranni tanlash"),
                          ("← →  yoki  h l", "oldingi / keyingi ekran"),
                          ("r", "darhol yangilash"),
                          ("?", "shu yordam ekrani"),
                          ("q  yoki  Ctrl+C", "chiqish")):
            K.append(" " + c["byellow"] + f"{key:<16}" + c["reset"]
                     + c["gray"] + desc + c["reset"])
        F = [c["bold"] + " REJIM / EKSPORT" + c["reset"], ""]
        for fl, desc in (("--report / --csv / --json", "markdown / CSV / JSON eksport"),
                         ("--set-limit session=N", "haqiqiy limitni sozlash"),
                         ("--theme NAME", "rang temasi (default/mono/ocean/matrix/amber)"),
                         ("--notify", "limit 90% da desktop bildirishnoma"),
                         ("--compact", "bir qatorli (tmux/statusline)")):
            F.append(" " + c["bcyan"] + f"{fl:<26}" + c["reset"]
                     + c["gray"] + desc + c["reset"])
        return [K, F]

    # ================= SESSIYA KARTASI (drill-down) =================
    def mk_card(query):
        q = (query or "").lower()
        matches = [(sid, sd) for sid, sd in summ["recent_sessions"]
                   if q in (sd.get("title") or "").lower() or sid.lower().startswith(q)
                   or q in sid.lower()]
        if not matches:
            return [[c["bold"] + " SESSIYA TOPILMADI" + c["reset"], "",
                     c["gray"] + f" '{query}' bo'yicha moslik yo'q" + c["reset"]]]
        sid, sd = matches[0]  # eng so'nggisi (recent_sessions last bo'yicha tartiblangan)
        title = sd.get("title") or "(nomsiz)"
        durs = (sd["last"] - sd["first"]).total_seconds()
        when = sd["last"].astimezone().strftime("%Y-%m-%d %H:%M")
        models = ", ".join(model_short(m) for m in sorted(sd["models"]))
        INFO = [c["bold"] + " SESSIYA: " + c["reset"] + c["bcyan"] + title + c["reset"], ""]
        for lab, val in (("loyiha", sd.get("project") or "—"),
                         ("branch", sd.get("branch") or "—"),
                         ("model", models),
                         ("davomiylik", dur(durs)),
                         ("oxirgi faollik", when),
                         ("xabarlar", str(sd["count"]))):
            INFO.append(lr(c["cyan"] + f" {lab}" + c["reset"], c["gray"] + str(val) + " " + c["reset"]))
        INFO.append(lr(c["cyan"] + " token" + c["reset"],
                       c["bgreen"] + human_tokens(sd["tokens"]) + " " + c["reset"]))
        INFO.append(lr(c["cyan"] + " qiymat" + c["reset"],
                       c["bgreen"] + money(sd["cost"]) + " " + c["reset"]))
        INFO.append(lr(c["cyan"] + " sid" + c["reset"], c["gray"] + sid[:20] + " " + c["reset"]))
        if len(matches) > 1:
            INFO.append(c["gray"] + f" ({len(matches)} ta moslik — eng so'nggisi ko'rsatildi)" + c["reset"])
        TL = [c["bold"] + " VOSITALAR " + c["reset"] + c["gray"] + "(shu sessiya)" + c["reset"], ""]
        st = sd.get("tools", {})
        tt = sum(st.values()) or 1
        for n, cnt in sorted(st.items(), key=lambda kv: kv[1], reverse=True)[:8]:
            TL.append(fillbar(" " + c["bcyan"] + f"{tool_short(n):<14}" + c["reset"],
                              f" {cnt:>6}", cnt / tt, c["bblue"]))
        if len(TL) == 2:
            TL.append(c["gray"] + " vosita chaqiruvi yo'q" + c["reset"])
        return [INFO, TL]

    _builders = {"overview": mk_overview, "sessions": mk_sessions,
                 "activity": mk_activity, "trends": mk_trends,
                 "insights": mk_insights, "help": mk_help}

    # ---- tana bo'limlarini USTUN kengligida quramiz ----
    W = WBOX if ncols == 1 else CW + 1   # builderlar fillbar/lr shu W-1 ni maqsad qiladi
    if session_query is not None:
        body = mk_card(session_query)
        tabbar = c["gray"] + f"sessiya tafsiloti: '{session_query}'" + c["reset"]
    else:
        if view not in _builders:
            view = "overview"
        body = _builders[view]()
        _tabs = [("1", "Umumiy", "overview"), ("2", "Sessiya", "sessions"),
                 ("3", "Faollik", "activity"), ("4", "Trend", "trends"),
                 ("5", "Fikrlar", "insights")]
        tabbar = " ".join(
            ((c["bold"] + c["bcyan"]) if v == view else c["gray"]) + f"[{k}]{n}" + c["reset"]
            for k, n, v in _tabs)
    W = WBOX

    def boxraw(s):
        return B + "│" + c["reset"] + padcell(s, WBOX) + B + "│" + c["reset"]

    foot_on = (H <= 0) or (H >= 12)
    dropped = 0

    out = [B + "╭" + "─" * WBOX + "╮" + c["reset"]]
    for hl in hdr:
        out.append(boxln(hl))

    if ncols == 1:
        # ---- bitta ustun: prioritet-prefiks + bo'sh-qator toggle ----
        if H <= 0:
            budget = 10 ** 9
        else:
            budget = max(1, (H - 1) - 2 - len(hdr) - (1 if foot_on else 0))

        def fitv(blanks):
            used, inc_ = 0, []
            for i, s in enumerate(body):
                sec = s if blanks else [x for x in s if x != ""]
                cost = 1 + len(sec)      # ├─┤ + qatorlar
                if used + cost <= budget:
                    used += cost; inc_.append(i)
                else:
                    break
            return inc_
        incA, incB = fitv(True), fitv(False)
        use_blanks = len(incA) >= len(incB)
        inc = incA if use_blanks else incB
        dropped = len(body) - len(inc)
        bi = 0
        for i in inc:
            out.append(B + "├" + "─" * WBOX + "┤" + c["reset"])
            sec = body[i] if use_blanks else [x for x in body[i] if x != ""]
            for raw in sec:
                out.append(boxln(raw))
            bi += 1 + len(sec)
        # bo'yiga to'ldirish: quti pastgacha cho'zilsin
        if H > 0:
            while bi < budget:
                out.append(boxln(""))
                bi += 1
    else:
        # ---- ko'p ustun: bo'limlarni balanslaymiz, sig'masa oxirgilarni tashlaymiz ----
        if H <= 0:
            budget = 10 ** 9
        else:
            budget = max(1, (H - 1) - 2 - len(hdr) - 1 - (1 if foot_on else 0))

        def tile(secs, strip):
            cols = [[] for _ in range(ncols)]
            hts = [0] * ncols
            for s in secs:
                sec = [x for x in s if x != ""] if strip else s
                j = hts.index(min(hts))
                if cols[j]:
                    cols[j].append(""); hts[j] += 1
                cols[j].extend(sec); hts[j] += len(sec)
            return cols, (max(hts) if hts else 0)

        body_use = body[:]
        cols, mh = tile(body_use, False)
        if H > 0 and mh > budget:
            cols, mh = tile(body_use, True)
            while mh > budget and len(body_use) > 1:
                body_use = body_use[:-1]
                cols, mh = tile(body_use, True)
        dropped = len(body) - len(body_use)
        if H > 0:
            nrows = budget            # bo'yiga to'ldiramiz — ustun chiziqlari pastgacha
            if mh > budget:
                dropped += 1          # baland bo'lim sig'madi (kesildi)
        else:
            nrows = mh

        out.append(B + "├" + "─" * WBOX + "┤" + c["reset"])
        sep = " " + c["gray"] + "│" + c["reset"] + " "
        for i in range(nrows):
            cells = [padcell(cols[j][i] if i < len(cols[j]) else "", colw[j])
                     for j in range(ncols)]
            out.append(boxraw(" " + sep.join(cells)))

    out.append(B + "╰" + "─" * WBOX + "╯" + c["reset"])

    if foot_on:
        foot = "  " + tabbar
        if dropped > 0:
            foot += c["gray"] + f"  ·{dropped}⬇" + c["reset"]
        if live_hint:
            foot += c["gray"] + "  q=chiqish" + c["reset"]
        out.append(clip(foot, WBOX + 2))
    return "\n".join(out)


def limit_fractions(summ, limits):
    """(5-soat ulushi, 7-kun ulushi, eng yuqori foiz) qaytaradi."""
    active = summ["active"]
    fl = 0.0
    if active:
        sess_lim = limits["session"] if limits["session"] > 0 else summ["baseline"]
        fl = (active["tokens"] / sess_lim) if sess_lim else 0
    wk_lim = limits["weekly"] if limits["weekly"] > 0 else summ["weekly_baseline"]
    fw = (summ["week"]["tokens"] / wk_lim) if wk_lim else 0
    return fl, fw, max(fl, fw) * 100


NOTIFY_STATE = os.path.join(CACHE_DIR, "notify-state.json")


def maybe_notify(worst, enabled):
    """Limit 90% ga birinchi marta yetganda desktop bildirishnoma + qo'ng'iroq.
    Bir marta — pastga tushib qайta ko'tarilmaguncha takror bermaydi."""
    if not enabled:
        return
    bucket = 90 if worst >= 90 else (75 if worst >= 75 else 0)
    prev = 0
    try:
        with open(NOTIFY_STATE) as f:
            prev = int(json.load(f).get("bucket", 0))
    except Exception:
        pass
    if bucket >= 90 and prev < 90:
        msg = f"Claude limit {worst:.0f}% ishlatildi — sekinlashtiring"
        sys.stderr.write("\a")  # terminal qo'ng'irog'i
        sys.stderr.flush()
        try:
            import shutil as _sh
            if _sh.which("notify-send"):
                os.system(f'notify-send -u critical "claudetop" {json.dumps(msg)} >/dev/null 2>&1')
            elif _sh.which("osascript"):  # macOS
                os.system(f'osascript -e {json.dumps("display notification " + json.dumps(msg) + " with title " + json.dumps("claudetop"))} >/dev/null 2>&1')
        except Exception:
            pass
    if bucket != prev:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(NOTIFY_STATE, "w") as f:
                json.dump({"bucket": bucket}, f)
        except OSError:
            pass


def set_limits(pairs):
    """--set-limit session=N weekly=N → config.json ni yangilaydi."""
    cfg = {}
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    keymap = {"session": "session_token_limit", "weekly": "weekly_token_limit"}
    changed = []
    for pair in pairs or []:
        if "=" not in pair:
            sys.stderr.write(f"Xato: '{pair}' — format: session=N yoki weekly=N\n")
            return 2
        k, v = pair.split("=", 1)
        k = k.strip().lower()
        if k not in keymap:
            sys.stderr.write(f"Xato: noma'lum kalit '{k}' (session|weekly)\n")
            return 2
        try:
            cfg[keymap[k]] = int(v)
        except ValueError:
            sys.stderr.write(f"Xato: '{v}' butun son emas\n")
            return 2
        changed.append(f"{keymap[k]}={int(v)}")
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        sys.stderr.write(f"Xato: config yozilmadi: {e}\n")
        return 1
    print(f"✔ sozlandi: {', '.join(changed)}  →  {CONFIG_FILE}")
    return 0


def render_compact(summ, limits=None, color=True, theme="default"):
    """tmux/statusline uchun bir qatorli xulosa."""
    limits = limits or {"session": 0, "weekly": 0}
    c = theme_palette(color, theme)
    active = summ["active"]
    parts = []
    if active:
        remaining = (active["end"] - summ["now"]).total_seconds()
        tok = active["tokens"]
        sess_lim = limits["session"] if limits["session"] > 0 else summ["baseline"]
        frac = (tok / sess_lim * 100) if sess_lim else 0
        col = c["bred"] if frac >= 90 else (c["byellow"] if frac >= 70 else c["bgreen"])
        parts.append(f"⏳{dur(remaining)}")
        parts.append(col + f"5h {frac:.0f}%" + c["reset"])
    else:
        parts.append(c["gray"] + "5h idle" + c["reset"])
    wk = summ["week"]["tokens"]
    wk_lim = limits["weekly"] if limits["weekly"] > 0 else summ["weekly_baseline"]
    fwk = (wk / wk_lim * 100) if wk_lim else 0
    parts.append(c["bmag"] + f"7d {fwk:.0f}%" + c["reset"])
    parts.append(c["gray"] + f"bugun {human_tokens(summ['today']['tokens'])} "
                 + money(summ['today']['cost']) + c["reset"])
    return "  ".join(parts)


def to_json(summ, creds, limits=None):
    def blk(b):
        return {
            "start": b["start"].isoformat(), "end": b["end"].isoformat(),
            "tokens": b["tokens"], "cost": round(b["cost"], 4),
        }
    active = summ["active"]
    limits = limits or {"session": 0, "weekly": 0}
    return json.dumps({
        "plan": creds["plan"], "tier": creds["tier"],
        "generated_at": summ["now_local"].isoformat(),
        "entry_count": summ["entry_count"],
        "today": summ["today"], "week": summ["week"], "all": summ["all"],
        "limits": limits,
        "baseline_block_tokens": summ["baseline"],
        "weekly_baseline_tokens": summ["weekly_baseline"],
        "active_block": blk(active) if active else None,
        "cache": summ["cache"],
        "by_model": {k: {"tokens": v["tokens"], "cost": round(v["cost"], 4)}
                     for k, v in summ["by_model"].items()},
        "by_project": {k: {"tokens": v["tokens"], "cost": round(v["cost"], 4)}
                       for k, v in summ["by_project"].items()},
        "by_branch": {k: {"tokens": v["tokens"], "cost": round(v["cost"], 4)}
                      for k, v in summ["by_branch"].items()},
        "tools": summ.get("tools", {}),
        "effort": summ.get("effort", {}),
        "day_series": summ["day_series"],
    }, indent=2, default=str)


def to_csv(summ):
    """Kunlik sarf CSV (spreadsheet/BI uchun)."""
    ad, ac, ar = summ["all_day"], summ["all_day_cost"], summ["all_day_req"]
    lines = ["date,tokens,cost_usd,requests"]
    for d in sorted(ad):
        lines.append(f"{d},{ad[d]},{ac.get(d, 0):.4f},{ar.get(d, 0)}")
    return "\n".join(lines)


def to_report(summ, creds, limits):
    """Ulashsa bo'ladigan markdown hisobot."""
    fl, fw, worst = limit_fractions(summ, limits)
    now = summ["now_local"]
    L = []
    L.append(f"# Claude Code — foydalanish hisoboti")
    L.append("")
    L.append(f"- **Sana:** {now.strftime('%Y-%m-%d %H:%M %Z')}")
    L.append(f"- **Reja:** {creds['plan']} · {creds['tier']}")
    L.append(f"- **Tahlil qilingan xabarlar:** {summ['entry_count']:,}")
    L.append("")
    L.append("## Limit oynalari")
    L.append("")
    L.append("| Oyna | Foiz | Asos |")
    L.append("|---|---|---|")
    sb = "sozlangan" if limits["session"] > 0 else "heuristik"
    wb = "sozlangan" if limits["weekly"] > 0 else "heuristik"
    L.append(f"| 5-soat | {fl*100:.0f}% | {sb} |")
    L.append(f"| 7-kun | {fw*100:.0f}% | {wb} |")
    L.append("")
    L.append("## Sarf (token · API-ekvivalent qiymat)")
    L.append("")
    L.append("| Davr | Token | Qiymat |")
    L.append("|---|---|---|")
    for lab, d in (("Bugun", summ["today"]), ("7 kun", summ["week"]), ("Jami", summ["all"])):
        L.append(f"| {lab} | {human_tokens(d['tokens'])} | {money(d['cost'])} |")
    L.append("")
    L.append(f"Kesh: cache-read {human_tokens(summ['cache']['read'])} "
             f"(~{summ['cache']['hit']*100:.0f}% input keshlangan)")
    L.append("")
    L.append("## Model bo'yicha")
    L.append("")
    L.append("| Model | Token | Qiymat |")
    L.append("|---|---|---|")
    for m, d in sorted(summ["by_model"].items(), key=lambda kv: -kv[1]["cost"])[:6]:
        if d["cost"] > 0 or d["tokens"] > 0:
            L.append(f"| {model_short(m)} | {human_tokens(d['tokens'])} | {money(d['cost'])} |")
    L.append("")
    L.append("## Loyiha bo'yicha (top 5)")
    L.append("")
    for p, d in sorted(summ["by_project"].items(), key=lambda kv: -kv[1]["tokens"])[:5]:
        if d["tokens"] > 0:
            L.append(f"- **{p}** — {human_tokens(d['tokens'])} · {money(d['cost'])}")
    branches = [(k, v) for k, v in summ["by_branch"].items() if k]
    if branches:
        L.append("")
        L.append("## Git branch bo'yicha (top 5)")
        L.append("")
        for b, d in sorted(branches, key=lambda kv: -kv[1]["tokens"])[:5]:
            L.append(f"- `{b}` — {human_tokens(d['tokens'])} · {money(d['cost'])}")
    tls = sorted(summ.get("tools", {}).items(), key=lambda kv: -kv[1])[:8]
    if tls:
        tt = sum(summ["tools"].values()) or 1
        L.append("")
        L.append("## Ko'p ishlatilgan vositalar (top 8)")
        L.append("")
        for n, cnt in tls:
            L.append(f"- **{tool_short(n)}** — {cnt:,} chaqiruv ({cnt/tt*100:.0f}%)")
    L.append("")
    L.append("## Prognoz (30-kun asosida)")
    L.append("")
    s30 = summ["series30"]
    avg_cost = sum(x["cost"] for x in s30) / 30
    L.append(f"- Kunlik o'rtacha: {money(avg_cost)}")
    L.append(f"- Oylik proyeksiya: ~{money(avg_cost*30)}")
    L.append(f"- Yillik proyeksiya: ~{money(avg_cost*365)}")
    L.append("")
    L.append("> Qiymatlar API-ekvivalent (Max/Pro obunada token uchun to'lanmaydi). "
             "claudetop bilan yaratildi.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--set-limit", action="append")
    ap.add_argument("--theme", default=os.environ.get("CLAUDETOP_THEME", "default"),
                    choices=list(THEMES.keys()))
    ap.add_argument("--session", default=None)
    ap.add_argument("--view", default="overview",
                    choices=["overview", "sessions", "activity", "trends", "insights", "help"])
    args = ap.parse_args()

    if args.set_limit is not None:
        sys.exit(set_limits(args.set_limit))

    if not os.path.isdir(PROJECTS_DIR):
        sys.stderr.write("Xato: ~/.claude/projects topilmadi. Claude Code o'rnatilganmi?\n")
        sys.exit(2)

    rows, aux = collect()
    summ = summarize(rows, aux)
    creds = load_credentials()
    limits = load_limits()
    color = not args.no_color

    if args.notify:
        _, _, worst = limit_fractions(summ, limits)
        maybe_notify(worst, True)

    if args.json:
        print(to_json(summ, creds, limits))
    elif args.csv:
        print(to_csv(summ))
    elif args.report:
        print(to_report(summ, creds, limits))
    elif args.compact:
        print(render_compact(summ, limits, color=color, theme=args.theme))
    else:
        live = load_live_sessions()
        print(render(summ, creds, limits=limits, color=color,
                     width=args.width, height=args.height,
                     view=args.view, live_sessions=live, theme=args.theme,
                     session_query=args.session))


if __name__ == "__main__":
    main()

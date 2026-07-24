#!/usr/bin/env python3
"""claudetop QA — barcha ekran/o'lcham/tema/eksportni avtomatik tekshiradi.

Ishlatish:  python3 .qa/verify.py
Engine'ni to'g'ridan-to'g'ri import qiladi (tez). Tekshiradi:
  • quti qatorlari har enda (tor va keng/ko'p-ustunli) mukammal tekislangan
  • hech bir ekran terminal balandligidan oshmaydi (scroll yo'q)
  • --json valid, --csv sarlavha+raqamli, --report markdown, --compact
  • barcha rang temalari xatosiz
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import engine  # noqa: E402

ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")
VIEWS = ["overview", "sessions", "activity", "trends", "insights", "help"]
THEMES = list(engine.THEMES.keys())
WIDTHS = [36, 48, 64, 80, 104, 110, 120, 160, 200, 260]
fails = []


def vlen(s):
    return len(ANSI.sub("", s))


def boxlines(s):
    return [l for l in s.split("\n") if l and ANSI.sub("", l)[:1] in "╭│├╰"]


def main():
    rows, aux = engine.collect()
    summ = engine.summarize(rows, aux)
    creds = engine.load_credentials()
    limits = engine.load_limits()
    print("QA: tekislik, overflow, temalar, eksportlar…")

    # tekislik (barcha en × ekran) + ko'p ustun
    for w in WIDTHS:
        for v in VIEWS:
            s = engine.render(summ, creds, limits=limits, width=w, view=v)
            widths = {vlen(l) for l in boxlines(s)}
            if len(widths) > 1:
                fails.append(f"MISALIGN {v} w={w}: {sorted(widths)}")

    # overflow (en × ekran × balandlik)
    for w in (48, 80, 120, 160, 200):
        for v in VIEWS:
            for h in range(6, 46):
                n = len(engine.render(summ, creds, limits=limits,
                                      width=w, height=h, view=v).split("\n"))
                if n > h - 1 and n != 1:
                    fails.append(f"OVERFLOW {v} w={w} h={h}: {n}>{h-1}")

    # temalar
    for t in THEMES:
        s = engine.render(summ, creds, limits=limits, width=100, theme=t)
        if len({vlen(l) for l in boxlines(s)}) != 1:
            fails.append(f"THEME {t} tekislik buzilgan")

    # sessiya kartasi
    s = engine.render(summ, creds, limits=limits, width=80, session_query="")
    if len({vlen(l) for l in boxlines(s)}) != 1:
        fails.append("SESSION card tekislik buzilgan")

    # eksportlar
    import json
    try:
        json.loads(engine.to_json(summ, creds, limits))
    except Exception as e:
        fails.append(f"JSON invalid: {e}")
    csv = engine.to_csv(summ).splitlines()
    if not csv or csv[0] != "date,tokens,cost_usd,requests":
        fails.append("CSV sarlavha noto'g'ri")
    if not engine.to_report(summ, creds, limits).startswith("# "):
        fails.append("REPORT markdown sarlavhasi yo'q")
    if not engine.render_compact(summ, limits).strip():
        fails.append("COMPACT bo'sh")

    if fails:
        print(f"\n✗ {len(fails)} ta muammo:")
        for f in fails[:30]:
            print("  -", f)
        sys.exit(1)
    print(f"✓ hammasi joyida — {len(WIDTHS)} en × {len(VIEWS)} ekran tekislik "
          f"(1/2/3 ustun), overflow-yo'q, {len(THEMES)} tema, json/csv/report/compact")


if __name__ == "__main__":
    main()

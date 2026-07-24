#!/usr/bin/env python3
"""Soxta ~/.claude ma'lumotini yaratadi — CI'da (haqiqiy ma'lumotsiz) QA'ni
mazmunli qilish uchun.

Ishlatish:  python3 .qa/make_fixture.py <HOME_DIR>
Keyin:      HOME=<HOME_DIR> python3 .qa/verify.py
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

MODELS = ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001",
          "claude-fable-5"]
EFFORTS = ["xhigh", "high", "max", "medium"]
TOOLS = ["Bash", "Edit", "Read", "Write", "Grep", "TaskUpdate"]


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Ishlatish: make_fixture.py <HOME_DIR>\n")
        sys.exit(2)
    home = os.path.abspath(sys.argv[1])
    proj = os.path.join(home, ".claude", "projects", "-home-runner-Documents-demo")
    sess_dir = os.path.join(home, ".claude", "sessions")
    os.makedirs(proj, exist_ok=True)
    os.makedirs(sess_dir, exist_ok=True)

    now = datetime.now(timezone.utc)
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    lines = []
    lines.append(json.dumps({"type": "ai-title", "sessionId": sid,
                             "aiTitle": "Demo session for CI fixture"}))
    # oxirgi 12 kun × turli soatlar bo'ylab yozuvlar
    n = 0
    for day in range(12):
        for k in range(6):
            n += 1
            ts = now - timedelta(days=day, hours=(k * 3), minutes=(n % 30))
            model = MODELS[n % len(MODELS)]
            inp = 200 + (n % 5) * 40
            out = 400 + (n % 7) * 90
            cr = 20000 + (n % 11) * 3000
            c5 = 5000 + (n % 4) * 800
            content = [{"type": "text", "text": "…"},
                       {"type": "tool_use", "name": TOOLS[n % len(TOOLS)],
                        "id": f"toolu_{n}", "input": {}}]
            lines.append(json.dumps({
                "type": "assistant",
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "requestId": f"req_{n}",
                "sessionId": sid,
                "effort": EFFORTS[n % len(EFFORTS)],
                "gitBranch": "main" if n % 3 else "feature/x",
                "version": "2.1.218",
                "entrypoint": "cli",
                "message": {
                    "id": f"msg_{n}",
                    "model": model,
                    "content": content,
                    "usage": {
                        "input_tokens": inp,
                        "output_tokens": out,
                        "cache_read_input_tokens": cr,
                        "cache_creation": {"ephemeral_5m_input_tokens": c5,
                                           "ephemeral_1h_input_tokens": 0},
                    },
                },
            }))
    with open(os.path.join(proj, "demo.jsonl"), "w") as f:
        f.write("\n".join(lines) + "\n")

    # jonli sessiya holati
    now_ms = int(now.timestamp() * 1000)
    with open(os.path.join(sess_dir, "1234.json"), "w") as f:
        json.dump({"pid": 1234, "sessionId": sid, "name": "demo-session",
                   "cwd": "/home/runner/Documents/demo", "status": "busy",
                   "kind": "interactive", "version": "2.1.218",
                   "startedAt": now_ms - 600000, "updatedAt": now_ms - 5000}, f)

    # obuna (sirlar soxta — CI uchun)
    with open(os.path.join(home, ".claude", ".credentials.json"), "w") as f:
        json.dump({"claudeAiOauth": {
            "accessToken": "x", "refreshToken": "x", "expiresAt": 0,
            "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x",
            "scopes": []}}, f)

    print(f"✔ fixture: {proj}/demo.jsonl ({n} yozuv)")


if __name__ == "__main__":
    main()

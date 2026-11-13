"""一键生成测试截图（需要本机安装 Microsoft Edge）。

用法：
    python scripts/capture_screenshots.py            # 默认 http://127.0.0.1:8801
    python scripts/capture_screenshots.py --base http://127.0.0.1:9000

说明：
  * 脚本通过 /api/auth/login 获取演示账号 Token，再以 ?token= 方式免登录打开页面；
  * 使用 Edge 无头模式（--headless=new --screenshot）逐页截图到 screenshots/；
  * 若 Edge 路径不同，可通过 --edge 指定 msedge.exe 的完整路径。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots"

USERS = ["admin", "kadmin", "zhangsan", "lisi", "chenjing"]

PAGES = [
    ("01-login.png", None, ""),
    ("02-dashboard.png", "admin", "#/dashboard"),
    ("03-chat.png", "zhangsan", "#/chat?session=1"),
    ("04-knowledge.png", "kadmin", "#/kb"),
    ("05-faq-candidates.png", "kadmin", "#/ops?tab=candidates"),
    ("06-faq-published.png", "kadmin", "#/ops?tab=faqs"),
    ("07-gaps.png", "kadmin", "#/ops?tab=gaps"),
    ("08-org-depts.png", "admin", "#/org?tab=depts"),
    ("09-org-roles.png", "admin", "#/org?tab=roles"),
    ("10-org-config.png", "admin", "#/org?tab=config"),
    ("11-audit.png", "admin", "#/org?tab=audit"),
]

DEFAULT_EDGE = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/microsoft-edge",
]


def find_edge(explicit: str | None) -> str:
    if explicit:
        return explicit
    for p in DEFAULT_EDGE:
        if Path(p).exists():
            return p
    raise SystemExit("未找到 Edge，请使用 --edge 指定浏览器可执行文件路径")


def login(base: str) -> dict:
    tokens = {}
    for u in USERS:
        data = json.dumps({"username": u, "password": "123456"}).encode()
        req = urllib.request.Request(f"{base}/api/auth/login", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            tokens[u] = json.loads(resp.read().decode())["data"]["token"]
        print(f"  登录成功：{u}")
    return tokens


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8801")
    parser.add_argument("--edge", default=None)
    args = parser.parse_args()

    edge = find_edge(args.edge)
    SHOTS.mkdir(exist_ok=True)
    profile = ROOT / ".edge-shot"
    tokens = login(args.base)

    for name, user, hashpart in PAGES:
        url = f"{args.base}/" if not user else f"{args.base}/?token={tokens[user]}{hashpart}"
        out = SHOTS / name
        cmd = [
            edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--window-size=1600,1000", f"--user-data-dir={profile}", "--no-first-run",
            "--virtual-time-budget=9000", f"--screenshot={out}", url,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(0.4)
        print(f"  {'OK ' if out.exists() else 'FAIL'} {name}")


if __name__ == "__main__":
    sys.exit(main())

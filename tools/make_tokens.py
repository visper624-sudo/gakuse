"""生成演示账号 Token（用于自动化截图）。"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8801"
USERS = ["admin", "kadmin", "zhangsan", "lisi", "chenjing"]
out = {}
for u in USERS:
    data = json.dumps({"username": u, "password": "123456"}).encode()
    req = urllib.request.Request(BASE + "/api/auth/login", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode())
    out[u] = body["data"]["token"]
    print(f"  {u:10s} ok")
with open("build/tokens.json", "w", encoding="utf-8") as f:
    json.dump(out, f)
print("tokens saved")

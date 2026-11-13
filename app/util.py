"""通用工具：时间、JSON、密码散列、JWT、文本规范化、token 估算。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
from datetime import datetime, timedelta
from typing import Any

from . import config

CST = "Asia/Shanghai"


# ---------------------------------------------------------------- 时间
def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def days_ago_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def day_list(days: int) -> list[str]:
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


# ---------------------------------------------------------------- JSON
def jloads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def jdumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)


# ---------------------------------------------------------------- 密码
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return base64.b16encode(dk).decode().lower(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, password_hash)


def secrets_hex(n: int) -> str:
    import secrets

    return secrets.token_hex(n)


# ---------------------------------------------------------------- JWT (HS256)
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def jwt_encode(payload: dict) -> str:
    header = {"alg": config.JWT_ALG, "typ": "JWT"}
    body = dict(payload)
    body.setdefault("exp", int(time.time()) + config.TOKEN_TTL_SECONDS)
    body.setdefault("iat", int(time.time()))
    seg = [_b64e(json.dumps(header, separators=(",", ":")).encode()),
           _b64e(json.dumps(body, separators=(",", ":")).encode())]
    signing_input = ".".join(seg).encode()
    sig = hmac.new(config.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return ".".join(seg + [_b64e(sig)])


def jwt_decode(token: str) -> dict | None:
    try:
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        expect = hmac.new(config.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(s), expect):
            return None
        payload = json.loads(_b64d(p))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------- 文本
_WS = re.compile(r"[\s\u3000]+")
_PUNCT = re.compile(r"[，。！？；：、“”‘’（）《》【】…—·,.!?;:\"'()\[\]{}<>/\\|@#$%^&*_+=~`]+")


def normalize_query(q: str) -> str:
    """提问规范化：去空白、去标点、全角转半角、小写，用于聚类去重与缓存匹配。"""
    q = (q or "").strip().lower()
    q = q.replace("？", "").replace("?", "").replace("！", "").replace("!", "")
    q = _WS.sub("", q)
    q = _PUNCT.sub("", q)
    return q


CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """轻量分词：中文二元组 + 拉丁词/数字，兼顾检索召回与零依赖。

    例："差旅住宿标准 500 元" -> ["差旅", "旅住", "住宿", "宿标", "标准", "500", "元"]
    """
    text = (text or "").lower()
    tokens: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9][a-z0-9._%-]*", text):
        if CJK.match(run[0]):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
                if len(run) <= 6:
                    tokens.append(run)  # 短词整词也入索引，提升精确匹配
        else:
            tokens.append(run)
    return tokens


def estimate_tokens(text: str) -> int:
    """中文按字、英文按词估算 token（无需 tiktoken 依赖）。"""
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + words + max(0, len(text) - cjk) // 8


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；!?;\n])", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def kv_escape(s: str) -> str:
    return (s or "").replace("\r", " ").replace("\n", " ")


def pct(a: int, b: int) -> float:
    return round(a * 100.0 / b, 2) if b else 0.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    k = (len(vs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(vs[int(k)])
    return float(vs[lo] * (hi - k) + vs[hi] * (k - lo))

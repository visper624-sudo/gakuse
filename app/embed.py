"""向量化适配层：支持三种引擎，可在「系统配置」页一键切换。

  1. local                 本地 signed-hashing（512 维，零依赖，默认）
  2. openai                OpenAI 兼容 /embeddings 接口，适合 BGE-M3 托管服务
                           （硅基流动 / 阿里百炼 / 智谱 / Ollama / vLLM / TEI 等）
  3. sentence_transformers 本机加载 BAAI/bge-m3（需 pip install sentence-transformers）

切换引擎后必须重建索引：`python scripts/reindex_embeddings.py`（或 `run.py --reset`）。
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Iterable

from . import config
from .util import tokenize


class EmbeddingError(RuntimeError):
    """向量化服务不可用（网络、鉴权、模型不存在等）。"""


# ---------------------------------------------------------------- 本地哈希向量
def _fnv1a(token: str) -> int:
    h = 0xCBF29CE484222325
    for b in token.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def embed_local(text: str, dim: int = config.VEC_DIM) -> list[float]:
    vec = [0.0] * dim
    counts = Counter(tokenize(text))
    if not counts:
        return vec
    for token, tf in counts.items():
        h = _fnv1a(token)
        idx = h % dim
        sign = 1.0 if (h >> 63) & 1 else -1.0
        vec[idx] += sign * (1.0 + math.log(tf))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ---------------------------------------------------------------- 配置解析
def _api_settings(cfg: dict) -> tuple[str, str, str, int, int]:
    base = (cfg.get("embedding_base_url") or cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("embedding_api_key") or cfg.get("api_key") or ""
    model = cfg.get("embedding_model") or "BAAI/bge-m3"
    dim = int(cfg.get("embedding_dim") or 0)
    batch = max(1, int(cfg.get("embedding_batch") or 16))
    return base, key, model, dim, batch


def describe(cfg: dict) -> dict:
    mode = cfg.get("embedding_mode") or "local"
    base, key, model, dim, _ = _api_settings(cfg)
    if mode == "local":
        return {"mode": "local", "model": "本地 signed-hashing", "dim": config.VEC_DIM,
                "base_url": "", "key_masked": ""}
    return {
        "mode": mode,
        "model": model,
        "dim": dim or "自动",
        "base_url": base,
        "key_masked": (key[:6] + "****" + key[-4:]) if len(key) > 12 else ("已配置" if key else "未配置"),
    }


def signature(cfg: dict) -> str:
    """当前向量口径签名，用于判断库内向量是否需要重建。"""
    mode = cfg.get("embedding_mode") or "local"
    if mode == "local":
        return f"local:hash{config.VEC_DIM}"
    return f"{mode}:{cfg.get('embedding_model') or 'BAAI/bge-m3'}"


# ---------------------------------------------------------------- OpenAI 兼容接口
def _post_embeddings(texts: list[str], cfg: dict) -> list[list[float]]:
    base, key, model, dim, _ = _api_settings(cfg)
    if not base:
        raise EmbeddingError("未配置向量服务地址（embedding_base_url）")
    url = f"{base}/embeddings"
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=cfg.get("timeout", 60)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [_normalize(item["embedding"]) for item in sorted(data["data"], key=lambda x: x.get("index", 0))]
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "ignore")
            last_err = EmbeddingError(f"HTTP {exc.code}：{detail}")
            if exc.code in (400, 401, 403, 404):
                break                      # 鉴权/模型错误不重试
        except Exception as exc:  # noqa: BLE001
            last_err = EmbeddingError(str(exc))
        time.sleep(0.6 * (attempt + 1))
    raise last_err or EmbeddingError("向量服务调用失败")


def _normalize(vec: Iterable[float]) -> list[float]:
    vals = [float(v) for v in vec]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


# ---------------------------------------------------------------- 本地 BGE-M3
_st_model = None
_st_name = ""


def _st_embed(texts: list[str], model_name: str) -> list[list[float]]:
    global _st_model, _st_name
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError("未安装 sentence-transformers，无法使用本地语义模型；"
                             "请 pip install sentence-transformers，或改用 openai 模式") from exc
    if _st_model is None or _st_name != model_name:
        try:
            _st_model = SentenceTransformer(model_name)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"加载本地模型失败：{exc}") from exc
        _st_name = model_name
    vecs = _st_model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return [list(map(float, v)) for v in vecs]


# ---------------------------------------------------------------- 统一入口
def embed_texts(texts: list[str], cfg: dict | None = None, strict: bool = False) -> list[list[float]]:
    """批量向量化。strict=True 时失败直接抛错（用于重建索引，避免脏数据）。"""
    cfg = cfg or {}
    mode = cfg.get("embedding_mode") or "local"
    if mode == "local":
        return [embed_local(t) for t in texts]

    _, _, _, dim, batch = _api_settings(cfg)
    try:
        if mode == "sentence_transformers":
            out = _st_embed(texts, cfg.get("embedding_model") or "BAAI/bge-m3")
        elif mode == "openai":
            out = []
            for i in range(0, len(texts), batch):
                out.extend(_post_embeddings(texts[i:i + batch], cfg))
        else:
            raise EmbeddingError(f"未知向量模式：{mode}")
    except EmbeddingError:
        if strict:
            raise
        return [embed_local(t) for t in texts]   # 问答链路降级，保证可用；索引侧必须 strict

    if dim and out and len(out[0]) != dim:
        raise EmbeddingError(f"向量维度不符：期望 {dim}，实际 {len(out[0])}。"
                             f"请修正 embedding_dim 或重建索引")
    return out


def embed(text: str, cfg: dict | None = None, strict: bool = False) -> list[float]:
    return embed_texts([text], cfg, strict=strict)[0]


# ---------------------------------------------------------------- 序列化
def pack(vec: Iterable[float]) -> bytes:
    vals = list(vec)
    return struct.pack(f"<{len(vals)}f", *vals)


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    try:
        return list(struct.unpack(f"<{len(blob) // 4}f", blob))
    except struct.error:
        return []


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return float(sum(a[i] * b[i] for i in range(n)))


def similarity(text_a: str, text_b: str, cfg: dict | None = None) -> float:
    va, vb = embed_texts([text_a, text_b], cfg)
    return cosine(va, vb)


def content_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

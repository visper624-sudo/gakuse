"""全局配置：路径、密钥、检索与大模型参数。

所有路径均相对项目根目录，可用环境变量覆盖，便于换机部署。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

# 项目根目录（kb_platform/）
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("KB_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
SEED_DIR = BASE_DIR / "seed" / "docs"
STATIC_DIR = BASE_DIR / "app" / "static"

for _d in (DATA_DIR, UPLOAD_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("KB_DB_PATH", DATA_DIR / "kb.sqlite3"))


def _load_secret() -> str:
    """签名密钥：首次启动生成并落盘，避免重启后登录态失效。"""
    p = DATA_DIR / "secret.key"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    s = secrets.token_hex(32)
    p.write_text(s, encoding="utf-8")
    return s


JWT_SECRET = os.environ.get("KB_JWT_SECRET") or _load_secret()
JWT_ALG = "HS256"
TOKEN_TTL_SECONDS = int(os.environ.get("KB_TOKEN_TTL", 12 * 3600))

HOST = os.environ.get("KB_HOST", "127.0.0.1")
PORT = int(os.environ.get("KB_PORT", 8801))

# ---------- 检索参数 ----------
VEC_DIM = 512                 # 本地哈希向量维度
CHUNK_SIZE = 480              # 目标切片长度（按 token 估算）
CHUNK_OVERLAP = 0.15          # 15% 重叠
VECTOR_TOP_K = 20
KEYWORD_TOP_K = 10
CONTEXT_TOP_N = 6             # 最终进入 Prompt 的切片数量
RRF_K = 60

# ---------- 沉淀与缺口 ----------
# 阈值与向量引擎口径绑定：本地哈希向量用「余弦 + 词形 Jaccard」融合分，阈值 0.60 / 0.72 / 0.34；
# 接入语义向量（BGE-M3 / 真实 Embedding）后相似度整体上移，应切换为 0.88 / 0.90 / 0.65。
FAQ_CLUSTER_THRESHOLD = float(os.environ.get("KB_FAQ_THRESHOLD", 0.60))
FAQ_CLUSTER_MIN_FREQ = int(os.environ.get("KB_FAQ_MIN_FREQ", 5))
FAQ_CACHE_THRESHOLD = float(os.environ.get("KB_FAQ_CACHE_THRESHOLD", 0.72))
FAQ_CACHE_ENABLED = True
GAP_THRESHOLD = float(os.environ.get("KB_GAP_THRESHOLD", 0.34))

# 按向量引擎自动选择阈值预设（显式设置环境变量时以环境变量为准）
_THRESHOLD_PRESETS = {
    "local": {"cluster": 0.60, "cache": 0.72, "gap": 0.34},
    # 语义向量（BGE-M3）实测：同一问题簇内相似度 0.81~0.99，跨主题 0.32~0.64，
    # 相关问题的检索置信度 0.59~0.75，无关问题约 0.41 → 缺口阈值取 0.50。
    "semantic": {"cluster": 0.88, "cache": 0.90, "gap": 0.50},
}


def active_thresholds(embedding_mode: str = "local") -> dict:
    mode = "semantic" if embedding_mode in ("openai", "sentence_transformers") else "local"
    preset = dict(_THRESHOLD_PRESETS[mode])
    if os.environ.get("KB_FAQ_THRESHOLD"):
        preset["cluster"] = FAQ_CLUSTER_THRESHOLD
    if os.environ.get("KB_FAQ_CACHE_THRESHOLD"):
        preset["cache"] = FAQ_CACHE_THRESHOLD
    if os.environ.get("KB_GAP_THRESHOLD"):
        preset["gap"] = GAP_THRESHOLD
    return preset

# ---------- 大模型默认参数（可在“系统配置”页覆盖） ----------
DEFAULT_LLM = {
    "provider": os.environ.get("KB_LLM_PROVIDER", "local"),   # local | openai
    "base_url": os.environ.get("KB_LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "api_key": os.environ.get("KB_LLM_API_KEY", ""),
    "model": os.environ.get("KB_LLM_MODEL", "deepseek-chat"),
    "embedding_model": os.environ.get("KB_EMBEDDING_MODEL", "BAAI/bge-m3"),
    "embedding_mode": os.environ.get("KB_EMBEDDING_MODE", "local"),  # local | openai | sentence_transformers
    "embedding_base_url": os.environ.get("KB_EMBEDDING_BASE_URL", ""),
    "embedding_api_key": os.environ.get("KB_EMBEDDING_API_KEY", ""),
    "embedding_dim": int(os.environ.get("KB_EMBEDDING_DIM", 1024)),
    "embedding_batch": int(os.environ.get("KB_EMBEDDING_BATCH", 16)),
    "temperature": 0.2,
    "timeout": 60,
}

APP_NAME = "知识库管理平台"
APP_VERSION = "1.0.0"

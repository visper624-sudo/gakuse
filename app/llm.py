"""大模型适配层：OpenAI 兼容接口（DeepSeek / Qwen / vLLM / Ollama）+ 本地检索增强生成引擎。

设计要点：
  * 真实模型走 /chat/completions 流式接口，逐帧解析 SSE；
  * 未配置 Key 或调用失败时自动降级为“本地抽取式引擎”，保证演示链路永不中断；
  * 降级不伪造模型输出，答案完全来自检索片段并标注引用编号。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Iterator

from . import config, db, embed
from .util import jdumps, jloads, split_sentences, tokenize, truncate

LOCAL_PROVIDER = "local"


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- 配置
def get_config() -> dict:
    raw = db.kv_get("llm_config")
    cfg = dict(config.DEFAULT_LLM)
    if raw:
        cfg.update(jloads(raw, {}) or {})
    return cfg


def save_config(patch: dict) -> dict:
    cfg = get_config()
    for k in ("provider", "base_url", "model", "embedding_model", "embedding_mode",
              "embedding_base_url", "embedding_dim", "embedding_batch", "temperature"):
        if k in patch and patch[k] not in (None, ""):
            cfg[k] = patch[k]
    if patch.get("api_key"):
        cfg["api_key"] = patch["api_key"]
    if patch.get("clear_api_key"):
        cfg["api_key"] = ""
    if patch.get("embedding_api_key"):
        cfg["embedding_api_key"] = patch["embedding_api_key"]
    if patch.get("clear_embedding_api_key"):
        cfg["embedding_api_key"] = ""
    if "temperature" in patch and patch["temperature"] is not None:
        cfg["temperature"] = float(patch["temperature"])
    if cfg.get("embedding_dim") not in (None, ""):
        cfg["embedding_dim"] = int(cfg["embedding_dim"])
    if cfg.get("embedding_batch"):
        cfg["embedding_batch"] = int(cfg["embedding_batch"])
    db.kv_set("llm_config", jdumps(cfg))
    return cfg


def public_config() -> dict:
    cfg = dict(get_config())
    key = cfg.get("api_key") or ""
    ekey = cfg.get("embedding_api_key") or ""
    cfg["api_key_masked"] = f"{key[:6]}****{key[-4:]}" if len(key) > 12 else ("已配置" if key else "未配置")
    cfg["embedding_api_key_masked"] = f"{ekey[:6]}****{ekey[-4:]}" if len(ekey) > 12 else ("已配置" if ekey else "未配置")
    cfg["engine"] = "真实大模型（OpenAI 兼容）" if is_remote(cfg) else "本地检索增强引擎（离线）"
    cfg["embedding"] = embed.describe(cfg)      # 必须在清空 Key 之前计算，否则掩码显示不正确
    cfg["api_key"] = ""
    cfg["embedding_api_key"] = ""
    return cfg


def thresholds() -> dict:
    return config.active_thresholds(get_config().get("embedding_mode", "local"))


def is_remote(cfg: dict | None = None) -> bool:
    cfg = cfg or get_config()
    return cfg.get("provider") == "openai" and bool(cfg.get("api_key"))


def test_connection() -> dict:
    cfg = get_config()
    if not is_remote(cfg):
        return {"ok": True, "mode": "local", "message": "当前为本地检索增强引擎，无需外部连接"}
    t0 = time.time()
    try:
        msgs = [{"role": "user", "content": "回复两个字：在线"}]
        text = ""
        for ev in stream_openai(msgs, cfg, max_tokens=16):
            if ev["type"] == "delta":
                text += ev["text"]
        return {"ok": True, "mode": "remote", "message": f"连通正常，返回：{text.strip()[:40]}",
                "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": "remote", "message": f"连接失败：{exc}"}


def test_embedding() -> dict:
    """向量服务连通性测试：返回维度、耗时与模式，便于确认是否真的接上 BGE-M3。"""
    cfg = get_config()
    info = embed.describe(cfg)
    t0 = time.time()
    try:
        vec = embed.embed("差旅住宿标准", cfg, strict=True)
        ms = int((time.time() - t0) * 1000)
        return {"ok": True, **info, "dim": len(vec), "latency_ms": ms,
                "message": f"向量服务正常：{info['model']}，维度 {len(vec)}，耗时 {ms}ms"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, **info, "message": f"向量服务不可用：{exc}"}


def reindex_hint() -> str:
    """当前向量模式是否与库内向量一致（不一致需重建索引）。"""
    cfg = get_config()
    mode = cfg.get("embedding_mode") or "local"
    stored = db.kv_get("embedding_signature") or ""
    expected = embed.signature(cfg)
    if stored == expected:
        return ""
    if stored or mode != "local":
        return f"向量模式已变更（{stored or '未记录'} → {expected}），请执行 python scripts/reindex_embeddings.py 重建索引"
    return ""


# ---------------------------------------------------------------- 真实模型（SSE）
def stream_openai(messages: list[dict], cfg: dict, max_tokens: int = 1200) -> Iterator[dict]:
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise LLMError("未配置 base_url")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    payload = json.dumps({
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": True,
        "temperature": cfg.get("temperature", 0.2),
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('api_key', '')}",
        "Accept": "text/event-stream",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=cfg.get("timeout", 60))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "ignore")
        raise LLMError(f"HTTP {exc.code} {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc

    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = {
                    "prompt_tokens": obj["usage"].get("prompt_tokens", 0),
                    "completion_tokens": obj["usage"].get("completion_tokens", 0),
                }
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                yield {"type": "delta", "text": text}
    yield {"type": "usage", "usage": usage}


# ---------------------------------------------------------------- 本地抽取式引擎
def _clean_sentence(s: str) -> str:
    s = re.sub(r"^《[^》]*》\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _score_sentence(sent: str, q_terms: set[str]) -> float:
    if not sent:
        return 0.0
    terms = set(tokenize(sent))
    if not terms:
        return 0.0
    overlap = len(terms & q_terms)
    return overlap / (len(q_terms) ** 0.5 or 1) + min(len(sent), 60) / 600


def local_answer(query: str, chunks: list[dict]) -> tuple[str, list[int]]:
    """基于检索片段的抽取式回答：先结论、后要点、附带引用编号。

    返回 (答案文本, 实际引用到的切片序号列表)，用于前端只展示真正被使用的溯源卡片。
    """
    if not chunks:
        return ("未能在知识库中检索到与该问题匹配的内容。\n\n"
                "该问题已自动记入「知识缺口清单」，知识管理员补充文档后可再次提问。"), []

    q_terms = set(tokenize(query))
    q_norm = re.sub(r"[\s？?。.,，！!]", "", query)
    picks: list[tuple[float, str, int]] = []   # (score, sentence, chunk_index)
    for idx, ch in enumerate(chunks[:4], start=1):
        body = re.sub(r"^《[^》]*》\n?", "", ch["content"]).strip()
        for sent in split_sentences(body):
            s = _clean_sentence(sent)
            if len(s) < 10 or s.startswith(("|", "---")):
                continue
            if re.match(r"^第[一二三四五六七八九十百]+[章节]", s):
                continue                      # 跳过章节标题
            if s.rstrip().endswith(("？", "?")):
                continue                      # 跳过设问句（不是答案内容）
            if s.startswith(("-", "1.", "2.")):
                s = re.sub(r"^[-\d.、\s]+", "", s)
            if len(s) < 26 and s[-1] not in "。！？；：":
                continue                      # 过短的标题式文本不进入答案
            if re.sub(r"[\s？?。.,，！!]", "", s) == q_norm:
                continue                      # 跳过与提问重复的句子
            picks.append((_score_sentence(s, q_terms), s, idx))
    picks = [p for p in picks if p[0] > 0]
    if picks:
        # 只保留与最佳句得分接近的句子，避免拼入弱相关内容
        best_score = max(p[0] for p in picks)
        picks = [p for p in picks if p[0] >= best_score * 0.45]
    picks.sort(key=lambda x: -x[0])

    selected: list[tuple[str, int]] = []
    seen = set()
    for score, s, idx in picks:
        key = s[:20]
        if key in seen:
            continue
        seen.add(key)
        selected.append((s, idx))
        if len(selected) >= 4:
            break

    if not selected:
        body = re.sub(r"^《[^》]*》\n?", "", chunks[0]["content"]).strip()
        sents = [s for s in split_sentences(body) if len(s) > 10][:2]
        selected = [(s.strip(), 1) for s in sents] or [(truncate(body, 160), 1)]

    conclusion, cidx = selected[0]
    lines = ["根据知识库检索结果，为你整理如下：", "", "**结论**", f"{conclusion}（[{cidx}]）"]
    bullets = selected[1:4]
    if bullets:
        lines += ["", "**关键要点**"]
        for i, (b, idx) in enumerate(bullets, start=1):
            lines.append(f"{i}. {b}（[{idx}]）")
    used = sorted({idx for _, idx in selected})
    lines += ["", f"> 以上内容由检索片段抽取生成（本地引擎），共参考 {len(chunks)} 个知识片段。"]
    return "\n".join(lines), used


def local_stream(text: str, chunk_size: int = 14, delay: float = 0.012) -> Iterator[dict]:
    for i in range(0, len(text), chunk_size):
        yield {"type": "delta", "text": text[i:i + chunk_size]}
        if delay:
            time.sleep(delay)
    prompt = len(tokenize(text))
    yield {"type": "usage", "usage": {"prompt_tokens": prompt, "completion_tokens": len(text) // 2}}


# ---------------------------------------------------------------- Prompt 编排
SYSTEM_PROMPT = (
    "你是企业知识库智能助手。请严格依据【参考资料】回答，遵守以下规则：\n"
    "1) 只能使用参考资料中的信息，禁止编造或补充资料以外的内容；\n"
    "2) 每个结论后用 [编号] 标注来源，编号与参考资料序号一致；\n"
    "3) 资料不足时明确说明“现有知识库资料不足”，不要猜测；\n"
    "4) 使用简体中文，结构清晰，可分点作答。"
)


def build_messages(query: str, chunks: list[dict], history: list[dict] | None = None) -> list[dict]:
    ctx_lines = []
    for i, ch in enumerate(chunks, start=1):
        head = f"《{ch['heading_path']}》" if ch.get("heading_path") else ""
        pos = f"P{ch['page_no']} 第{ch['para_index'] + 1}段"
        body = re.sub(r"^《[^》]*》\n?", "", ch["content"]).strip()
        ctx_lines.append(f"[{i}] 《{ch['title']}》 {pos} {head}\n{truncate(body, 700)}")
    context = "\n\n".join(ctx_lines) if ctx_lines else "（无可用参考资料）"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": truncate(h["content"], 500)})
    messages.append({
        "role": "user",
        "content": f"【参考资料】\n{context}\n\n【用户问题】\n{query}\n\n请依据参考资料作答，并标注引用编号。",
    })
    return messages


FAQ_SYSTEM = (
    "你是企业知识库运营助手。请基于给定问题簇与关联知识片段，输出一条可直接发布的标准 FAQ。"
    "输出 JSON：{\"question\": \"标准问题\", \"answer\": \"标准答案（分点、简洁、可执行）\"}"
)


def generate_faq(question: str, chunks: list[dict], cfg: dict | None = None) -> dict:
    cfg = cfg or get_config()
    ctx = "\n\n".join(truncate(re.sub(r"^《[^》]*》\n?", "", c["content"]), 500) for c in chunks[:3])
    if is_remote(cfg):
        try:
            msgs = [{"role": "system", "content": FAQ_SYSTEM},
                    {"role": "user", "content": f"问题簇代表问句：{question}\n\n关联知识片段：\n{ctx}"}]
            text = ""
            for ev in stream_openai(msgs, cfg, max_tokens=600):
                if ev["type"] == "delta":
                    text += ev["text"]
            m = re.search(r"\{.*\}", text, re.S)
            obj = json.loads(m.group(0)) if m else {}
            if obj.get("answer"):
                return {"question": obj.get("question") or question, "answer": obj["answer"]}
        except Exception:  # noqa: BLE001
            pass
    if chunks:
        text, _ = local_answer(question, chunks)
        text = re.sub(r"^根据知识库检索结果.*?\n+", "", text)
        text = re.sub(r"（\[\d+\]）", "", text)
        text = re.sub(r"\n>\s*以上内容.*$", "", text).strip()
        answer = text + f"\n\n来源：《{chunks[0]['title']}》（P{chunks[0].get('page_no', 1)}）"
    else:
        answer = "该问题暂无关联知识文档，建议补充对应制度文档后再发布标准答案。"
    return {"question": question, "answer": answer}

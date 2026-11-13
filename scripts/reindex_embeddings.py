"""重建全量向量索引（切换向量引擎后必须执行）。

用法：
    python scripts/reindex_embeddings.py            # 按当前配置重建
    python scripts/reindex_embeddings.py --dry-run  # 只测试连通性与维度，不写库

逻辑复用 app/reindex.py，页面上的「重建向量索引」按钮走的是同一段代码。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, embed, llm, reindex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只探测向量服务与维度")
    parser.add_argument("--batch", type=int, default=0, help="批大小，默认取配置")
    args = parser.parse_args()

    db.init_db()
    cfg = llm.get_config()
    info = embed.describe(cfg)
    print("=" * 62)
    print(f"  向量模式 : {info['mode']}")
    print(f"  模型     : {info['model']}")
    print(f"  服务地址 : {info['base_url'] or '（本地计算）'}")
    print(f"  期望维度 : {info['dim']}")
    print("=" * 62)

    if not args.dry_run:
        last = {"done": 0}

        def progress(done: int, total: int) -> None:
            if done != last["done"]:
                last["done"] = done
                print(f"\r  重建进度   : {done}/{total}", end="", flush=True)

        try:
            res = reindex.run(batch=args.batch or None, progress=progress)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[失败] {exc}")
            print("       请检查 embedding_base_url / embedding_api_key / 模型名 / 网络")
            return 1
        print()
        print(f"  完成     : {res['chunks']} 个切片 + {res['faqs']} 条 FAQ，"
              f"维度 {res['dim']}，用时 {res['elapsed_ms'] / 1000:.1f}s")
        print(f"  索引签名 : {res['signature']}")
        return 0

    try:
        res = reindex.run(dry_run=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 向量服务不可用：{exc}")
        return 1
    print(f"  探测成功 : 维度 {res['dim']}")
    print(f"  待重建   : {res['chunks']} 个切片，{res['faqs']} 条 FAQ")
    print("  --dry-run：未写入任何数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""知识库管理平台（2.9）启动入口。

    python run.py                 # 启动服务（首次运行自动初始化演示数据）
    python run.py --seed          # 仅初始化/补充演示数据
    python run.py --reset         # 清空数据库后重新初始化
    python run.py --port 9000     # 指定端口
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, db, seed, server  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=config.APP_NAME)#创建命令行解析器，`description` 是帮助文案，取值来自配置文件里的应用名。
    parser.add_argument("--host", default=config.HOST)#添加命令行参数`--host`，默认值来自配置文件。
    parser.add_argument("--port", type=int, default=config.PORT)#添加命令行参数`--port`，类型为整数，默认值来自配置文件。
    parser.add_argument("--seed", action="store_true", help="仅执行演示数据初始化")#添加命令行参数`--seed`，类型为布尔值，默认值为False，表示是否执行演示数据初始化。
    parser.add_argument("--reindex", action="store_true", help="重建全量向量索引后退出")#添加命令行参数`--reindex`，类型为布尔值，默认值为False，表示是否重建全量向量索引。
    parser.add_argument("--reset", action="store_true", help="清空数据后重新初始化")#添加命令行参数`--reset`，类型为布尔值，默认值为False，表示是否清空数据后重新初始化。
    args = parser.parse_args()
#解析命令行传入参数，所有参数都放到 `args` 对象上，后面业务代码判断：
    if args.reset:
        ## 删除 sqlite 主库、wal、shm 文件
        for p in (config.DB_PATH, Path(str(config.DB_PATH) + "-wal"), Path(str(config.DB_PATH) + "-shm")):
            if p.exists():
                p.unlink()
                # 删除上传目录
        if config.UPLOAD_DIR.exists():
            shutil.rmtree(config.UPLOAD_DIR)
            # 重建上传目录
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        print("已清空数据库与上传文件")

    db.init_db()# 初始化数据库表结构
    # 如果标记 seeded 不是 "1"，就跑 seed 初始化
    if db.kv_get("seeded") != "1":#判断数据库中是否已经存在演示数据
        seed.run()#如果不存在，则调用 seed.py 文件中的 run 函数，初始化演示数据
        """
        **自动种子数据逻辑**

- 从数据库 KV 里读 key = `seeded` 的值
- 如果不等于 `"1"` → 自动执行 `seed.run()`，灌入演示 / 初始数据
- seed 跑完之后，代码里应该会执行 `db.kv_set("seeded", "1")` 打标记
- 作用：**第一次启动自动初始化演示数据；后续重启不会反复灌种子**
        """
    if args.seed:#如果命令行参数中包含 `--seed`，则执行 seed 初始化
        seed.run()#调用 seed.py 文件中的 run 函数，初始化演示数据
        return#返回，不再执行后续代码
    if args.reindex:
        import subprocess

        raise SystemExit(subprocess.call([sys.executable, str(Path(__file__).parent / "scripts" / "reindex_embeddings.py")]))
    server.run(args.host, args.port)


if __name__ == "__main__":
    main()

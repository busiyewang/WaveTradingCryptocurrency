import argparse
import json
import os
from pathlib import Path
import sys

from .config import StrategyConfig
from .demo import dataset
from .replay import run_replay
from .storage import ResearchStore, encode


def main():
    parser = argparse.ArgumentParser(description="缠论量化研究：历史信号回放，不下单")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="创建/迁移研究表")
    commands.add_parser("demo", help="合成数据端到端演示，不代表市场收益")
    replay = commands.add_parser("replay", help="回放含 inst、4H、15m 数组的 JSON")
    replay.add_argument("input", type=Path)
    replay.add_argument("--fee-rate", type=float, default=0.0005)
    replay.add_argument("--slippage-rate", type=float, default=0.0005)
    report = commands.add_parser("report", help="查询持久化回放报告")
    report.add_argument("run_id")
    args = parser.parse_args()
    store = None
    try:
        store = ResearchStore(os.environ.get("DATABASE_URL", "sqlite:///var/quant.db"))
        store.migrate()
        if args.command == "init-db":
            result = {"status": "ready", "backend": "mysql" if store.mysql else "sqlite"}
        elif args.command == "report":
            result = store.report(args.run_id)
        else:
            if args.command == "demo":
                source, config = dataset(), StrategyConfig()
            else:
                source = json.loads(args.input.read_text(encoding="utf-8"))
                config = StrategyConfig(fee_rate=args.fee_rate, slippage_rate=args.slippage_rate)
            result = run_replay(source, store, config,
                                progress=lambda n: print("已回放 {} 个收盘事件".format(n), file=sys.stderr))
        print(encode(result))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        # 文件或驱动异常可能携带 URL；数据库/系统错误不原样输出。
        if isinstance(exc, ValueError):
            print("输入错误：{}".format(exc), file=sys.stderr)
        else:
            print("操作失败 ({})，请检查输入文件、数据库连接与 requirements-quant.txt；不输出连接凭据。".format(type(exc).__name__), file=sys.stderr)
        return 1
    except Exception as exc:
        print("操作失败 ({})，请检查数据库可用性和迁移状态；不输出连接凭据。".format(type(exc).__name__), file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())

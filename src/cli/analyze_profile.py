"""联系人画像分析命令行程序。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wechat_tool.services.application import WechatChatApplication


def main() -> None:
    """分析指定联系人的双向画像。"""
    load_dotenv()
    args = _parse_args()
    app = WechatChatApplication.from_env()
    app.analyze_contact_profiles(
        args.keyword,
        output_sqlite_path=args.output_path
        or (os.getenv("PROFILE_OUTPUT_PATH", "").strip() or None),
        slice_size=args.slice_size or int(os.getenv("PROFILE_SLICE_SIZE", "500")),
        limit=args.limit,
        reset_existing=args.reset_existing,
    )


def _parse_args() -> argparse.Namespace:
    """解析画像分析 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="分析指定联系人的双向聊天画像。"
    )
    parser.add_argument(
        "keyword",
        help="联系人关键字，支持备注、昵称、别名或直接传 username。",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        type=Path,
        default=None,
        help="画像输出 SQLite 路径，默认使用 PROFILE_OUTPUT_PATH 或 data/out/db/messages.db。",
    )
    parser.add_argument(
        "--slice-size",
        type=int,
        default=None,
        help="每片分析的消息条数，默认使用 PROFILE_SLICE_SIZE 或 500。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="可选。限制参与分析的消息条数。",
    )
    parser.add_argument(
        "--reset",
        dest="reset_existing",
        action="store_true",
        help="分析前清空已有画像。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

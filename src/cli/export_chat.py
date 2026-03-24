"""聊天记录导出命令行程序。"""

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
    """按联系人关键字导出聊天记录。"""
    load_dotenv()
    args = _parse_args()
    app = WechatChatApplication.from_env()
    app.export_by_contact_name_to_sqlite(
        args.keyword,
        output_sqlite_path=args.output_path
        or (os.getenv("EXPORT_OUTPUT_PATH", "").strip() or None),
        limit=args.limit,
    )


def _parse_args() -> argparse.Namespace:
    """解析聊天导出 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="导出指定联系人的聊天记录到 SQLite。"
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
        help="输出 SQLite 路径，默认使用 EXPORT_OUTPUT_PATH 或 data/out/db/messages.db。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="可选。限制导出的消息条数。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

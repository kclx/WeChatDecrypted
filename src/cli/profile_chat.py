"""画像库终端问答程序。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wechat_tool.profile.qa_service import WechatProfileQAService


def main() -> None:
    """启动基于联系人画像库的终端问答。"""
    load_dotenv()
    args = _parse_args()
    service = WechatProfileQAService.from_env()
    service.run_terminal_chat(profile_db_path=args.db_path)


def _parse_args() -> argparse.Namespace:
    """解析终端问答程序的命令行参数。"""
    parser = argparse.ArgumentParser(
        description="启动画像库终端问答。程序会从 contact_profiles 中检索联系人画像并回答问题。"
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=None,
        help="画像库 SQLite 路径，默认使用 data/out/db/messages.db 或环境变量覆盖后的默认值。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

"""媒体导出命令行程序。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wechat_tool.media.manager import WechatMediaManager


def main() -> None:
    """按消息表和 local_id 导出单条媒体。"""
    load_dotenv()
    args = _parse_args()
    manager = WechatMediaManager.from_env()
    output_dir = args.output_dir.expanduser()

    if args.media_type == "image":
        output = manager.export_image(args.msg_table, args.local_id, output_dir)
    elif args.media_type == "video":
        output = manager.export_video(args.msg_table, args.local_id, output_dir)
    elif args.media_type == "voice":
        output = manager.export_voice(args.msg_table, args.local_id, output_dir)
    else:
        raise ValueError(f"unsupported media_type: {args.media_type}")

    print(output)


def _parse_args() -> argparse.Namespace:
    """解析媒体导出 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="按消息表和 local_id 导出单条图片、视频或语音。"
    )
    parser.add_argument(
        "media_type",
        choices=("image", "video", "voice"),
        help="要导出的媒体类型。",
    )
    parser.add_argument(
        "msg_table",
        help="原始消息表名，例如 Msg_xxx。",
    )
    parser.add_argument(
        "local_id",
        type=int,
        help="消息 local_id。",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="导出目录。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

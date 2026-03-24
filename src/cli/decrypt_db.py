"""SQLCipher 数据库探测与解密命令行程序。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wechat_tool.database.sqlcipher_probe import WechatSQLCipherProbe


def main() -> None:
    """执行数据库探测或完整解密。"""
    load_dotenv()
    args = _parse_args()
    probe = WechatSQLCipherProbe.from_env()

    if args.command == "probe":
        result = probe.decrypt_first_page(
            args.db_path,
            page_size=args.page_size,
            reserve=args.reserve,
        )
        print(f"header_ok={result['header_ok']}")
        print(f"salt_matches_capture={result['salt_matches_capture']}")
        print(f"salt={result['salt'].hex()}")
        print(f"iv={result['iv'].hex()}")
        print(f"key={result['key'].hex()}")
        return

    if args.command == "decrypt":
        key = probe.decrypt_db(
            args.db_path,
            args.output_path,
            page_size=args.page_size,
            reserve=args.reserve,
        )
        print(f"output={args.output_path}")
        print(f"key={key.hex()}")
        return

    raise ValueError(f"unsupported command: {args.command}")


def _parse_args() -> argparse.Namespace:
    """解析数据库探测 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="探测或解密微信 SQLCipher 数据库。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser(
        "probe",
        help="仅探测第一页是否能成功解密。",
    )
    probe_parser.add_argument("db_path", type=Path, help="输入的加密数据库路径。")
    probe_parser.add_argument("--page-size", type=int, default=4096, help="SQLite 页大小。")
    probe_parser.add_argument("--reserve", type=int, default=80, help="页尾保留字节数。")

    decrypt_parser = subparsers.add_parser(
        "decrypt",
        help="解密整个数据库到指定输出路径。",
    )
    decrypt_parser.add_argument("db_path", type=Path, help="输入的加密数据库路径。")
    decrypt_parser.add_argument("output_path", type=Path, help="输出的解密数据库路径。")
    decrypt_parser.add_argument("--page-size", type=int, default=4096, help="SQLite 页大小。")
    decrypt_parser.add_argument("--reserve", type=int, default=80, help="页尾保留字节数。")

    return parser.parse_args()


if __name__ == "__main__":
    main()

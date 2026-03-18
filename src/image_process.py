from __future__ import annotations

import sqlite3
from pathlib import Path

try:
    from verify_wechat_dat import recover_wechat_dat
except ModuleNotFoundError:
    from src.verify_wechat_dat import recover_wechat_dat


class WechatImageLocator:
    """根据消息表和 local_id 定位微信图片消息对应的本地文件路径。"""

    def __init__(
        self,
        message_db_path: Path,
        message_resource_db_path: Path,
        account_root: Path,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.message_resource_db_path = Path(message_resource_db_path)
        self.account_root = Path(account_root)

    def find_image_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        with sqlite3.connect(self.message_db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "ATTACH DATABASE ? AS message_resource",
                (str(self.message_resource_db_path),),
            )

            message = self._fetch_message(conn, msg_table, local_id)
            resource_info = self._fetch_resource_info(conn, msg_table, message)
            resource_details = (
                self._fetch_resource_details(conn, resource_info["message_id"])
                if resource_info is not None
                else []
            )

        file_base = self._extract_file_base(message["packed_info_data"])
        chat_md5 = msg_table.removeprefix("Msg_")
        month_dir = self._format_month_dir(message["create_time"])
        image_dir = self.account_root / "msg" / "attach" / chat_md5 / month_dir / "Img"

        result = {
            "message": dict(message),
            "resource_info": dict(resource_info) if resource_info is not None else None,
            "resource_details": [dict(row) for row in resource_details],
            "chat_md5": chat_md5,
            "month_dir": month_dir,
            "file_base": file_base,
            "image_dir": str(image_dir),
            "main_file_name": f"{file_base}.dat",
            "thumb_file_name": f"{file_base}_t.dat",
            "hd_file_name": f"{file_base}_h.dat",
            "main_file_path": str(image_dir / f"{file_base}.dat"),
            "thumb_file_path": str(image_dir / f"{file_base}_t.dat"),
            "hd_file_path": str(image_dir / f"{file_base}_h.dat"),
        }
        return result

    def recover_image(
        self,
        msg_table: str,
        local_id: int,
        key32: str,
        output_dir: Path,
        preferred_variant: str = "thumb",
    ) -> dict[str, object]:
        result = self.find_image_paths(msg_table, local_id)

        variant_map = {
            "thumb": Path(str(result["thumb_file_path"])),
            "hd": Path(str(result["hd_file_path"])),
            "main": Path(str(result["main_file_path"])),
        }
        if preferred_variant not in variant_map:
            raise ValueError(f"unsupported variant: {preferred_variant}")

        dat_path = variant_map[preferred_variant]
        if not dat_path.exists():
            raise FileNotFoundError(f"dat file not found: {dat_path}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{result['file_base']}_{preferred_variant}.jpg"

        recover_result = recover_wechat_dat(dat_path, key32, output_file)
        recover_result.update(
            {
                "variant": preferred_variant,
                "output_file": str(output_file),
                "image_lookup": result,
            }
        )
        return recover_result

    def _fetch_message(self, conn: sqlite3.Connection, msg_table: str, local_id: int) -> sqlite3.Row:
        query = f"""
            SELECT local_id, server_id, local_type, real_sender_id, create_time, packed_info_data
            FROM [{msg_table}]
            WHERE local_id = ?
        """
        row = conn.execute(query, (local_id,)).fetchone()
        if row is None:
            raise ValueError(f"message not found: table={msg_table}, local_id={local_id}")
        if row["local_type"] != 3:
            raise ValueError(
                f"message is not an image: table={msg_table}, local_id={local_id}, local_type={row['local_type']}"
            )
        return row

    def _fetch_resource_info(
        self,
        conn: sqlite3.Connection,
        msg_table: str,
        message: sqlite3.Row,
    ) -> sqlite3.Row | None:
        chat_md5 = msg_table.removeprefix("Msg_")
        query = """
            SELECT
                mri.message_id,
                mri.chat_id,
                mri.sender_id,
                mri.message_local_type,
                mri.message_create_time,
                mri.message_local_id,
                mri.message_svr_id
            FROM message_resource.MessageResourceInfo AS mri
            WHERE mri.message_local_id = ?
              AND mri.message_svr_id = ?
              AND mri.message_create_time = ?
              AND mri.message_local_type = ?
              AND EXISTS (
                    SELECT 1
                    FROM message_resource.ChatName2Id AS c
                    WHERE c.rowid = mri.chat_id
                      AND lower(hex(md5(c.user_name))) = ?
              )
        """
        conn.create_function("md5", 1, self._sqlite_md5)
        row = conn.execute(
            query,
            (
                message["local_id"],
                message["server_id"],
                message["create_time"],
                message["local_type"],
                chat_md5,
            ),
        ).fetchone()
        return row

    def _fetch_resource_details(
        self,
        conn: sqlite3.Connection,
        message_id: int,
    ) -> list[sqlite3.Row]:
        rows = conn.execute(
            """
            SELECT
                resource_id,
                message_id,
                type,
                size,
                create_time,
                access_time,
                status,
                data_index
            FROM message_resource.MessageResourceDetail
            WHERE message_id = ?
            ORDER BY type
            """,
            (message_id,),
        ).fetchall()
        return rows

    @staticmethod
    def _extract_file_base(packed_info_data: bytes | str | None) -> str:
        if not packed_info_data:
            raise ValueError("packed_info_data is empty")

        if isinstance(packed_info_data, str):
            data = packed_info_data.encode("utf-8", errors="ignore")
        else:
            data = packed_info_data

        hex_chars = []
        for byte in data:
            char = chr(byte)
            if char in "0123456789abcdefABCDEF":
                hex_chars.append(char.lower())
                if len(hex_chars) == 32:
                    return "".join(hex_chars)
            else:
                hex_chars.clear()

        raise ValueError("failed to extract image file base from packed_info_data")

    @staticmethod
    def _format_month_dir(create_time: int) -> str:
        return __import__("datetime").datetime.fromtimestamp(create_time).strftime("%Y-%m")

    @staticmethod
    def _sqlite_md5(value: str | bytes | None) -> bytes:
        import hashlib

        if value is None:
            return b""
        if isinstance(value, str):
            value = value.encode("utf-8")
        return hashlib.md5(value).digest()


if __name__ == "__main__":
    locator = WechatImageLocator(
        message_db_path=Path("data/db/decrypted/message_0.db"),
        message_resource_db_path=Path("data/db/decrypted/message_resource.db"),
        account_root=Path("/path/to/xwechat_files/<account_id>"),
    )
    result = locator.find_image_paths(
        msg_table="Msg_<chat_md5>",
        local_id=12345,
    )
    for key, value in result.items():
        print(f"{key}: {value}")

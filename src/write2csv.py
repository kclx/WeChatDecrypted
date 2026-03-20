from __future__ import annotations

import csv
import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from media_manager import WechatMediaManager


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DECRYPTED_DB_DIR = PROJECT_ROOT / "data" / "db" / "decrypted"
DEFAULT_DECRYPTED_DB_DIR_ALT = PROJECT_ROOT / "data" / "db" / "dec"


class WechatMessageCsvExporter:
    MSG_TYPE_MAPPER: dict[int, str] = {
        1: "文本",
        3: "图片",
        34: "语音",
        43: "视频",
    }

    def __init__(
        self,
        message_db_path: Path,
        contact_db_path: Path,
        media_manager: WechatMediaManager,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.contact_db_path = Path(contact_db_path)
        self.media_manager = media_manager
        self.wxid_name_cache: dict[str, str] = {}
        self.real_sender_wxid_mapper: dict[int, str] = {}

    @classmethod
    def from_env(cls) -> WechatMessageCsvExporter:
        load_dotenv(ENV_PATH)
        default_db_dir = (
            DEFAULT_DECRYPTED_DB_DIR
            if DEFAULT_DECRYPTED_DB_DIR.exists()
            else DEFAULT_DECRYPTED_DB_DIR_ALT
        )
        decrypted_db_dir = Path(
            os.getenv("DECRYPTED_DB_DIR", str(default_db_dir))
        ).expanduser()
        media_manager = WechatMediaManager.from_env()
        return cls(
            message_db_path=Path(
                os.getenv("MESSAGE_DB_PATH", str(decrypted_db_dir / "message_0.db"))
            ),
            contact_db_path=Path(
                os.getenv("CONTACT_DB_PATH", str(decrypted_db_dir / "contact.db"))
            ),
            media_manager=media_manager,
        )

    def export_by_contact_name(
        self,
        contact_name_keyword: str,
        output_csv_path: Path,
        *,
        limit: int | None = None,
    ) -> Path:
        output_csv_path = Path(output_csv_path)
        output_csv_path.parent.mkdir(parents=True, exist_ok=True)

        with (
            sqlite3.connect(self.message_db_path) as message_conn,
            sqlite3.connect(self.contact_db_path) as contact_conn,
        ):
            message_cursor = message_conn.cursor()
            contact_cursor = contact_conn.cursor()
            table_name = self._find_message_table(contact_cursor, contact_name_keyword)
            rows = self._fetch_message_rows(message_cursor, table_name, limit)
            csv_rows = [
                self._build_csv_row(contact_conn, table_name, row) for row in rows
            ]

        with output_csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(
                csv_file,
                quoting=csv.QUOTE_MINIMAL,
                quotechar='"',
                escapechar="\\",
            )
            writer.writerow(["local_id", "sender", "msg_type", "msg_time", "msg"])
            writer.writerows(csv_rows)

        return output_csv_path

    def _find_message_table(
        self,
        contact_cursor: sqlite3.Cursor,
        contact_name_keyword: str,
    ) -> str:
        row = contact_cursor.execute(
            "SELECT username FROM contact WHERE nick_name LIKE ? OR remark LIKE ? LIMIT 1",
            (f"%{contact_name_keyword}%", f"%{contact_name_keyword}%"),
        ).fetchone()
        if row is None:
            raise ValueError(f"contact not found: {contact_name_keyword}")

        username = str(row[0])
        table_name = f"Msg_{hashlib.md5(username.encode('utf-8')).hexdigest()}"
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", table_name):
            raise ValueError(f"invalid message table name: {table_name}")
        return table_name

    @classmethod
    def _fetch_message_rows(
        cls,
        message_cursor: sqlite3.Cursor,
        table_name: str,
        limit: int | None,
    ) -> list[tuple[int, int, int, int, str]]:
        sql = (
            f"SELECT local_id, real_sender_id, local_type, create_time, message_content "
            f"FROM [{table_name}] "
            f"WHERE local_type IN (1, 3, 34, 43) "
            f"ORDER BY sort_seq ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return message_cursor.execute(sql).fetchall()

    def _build_csv_row(
        self,
        contact_conn: sqlite3.Connection,
        table_name: str,
        row: tuple[int, int, int, int, str],
    ) -> list[object]:
        local_id, real_sender_id, local_type, create_time, message_content = row
        sender = self._get_sender_name(
            contact_conn=contact_conn,
            real_sender_id=real_sender_id,
            message_content=message_content if local_type == 1 else None,
        )
        msg = self._build_message_content(
            table_name=table_name,
            local_id=local_id,
            local_type=local_type,
            message_content=message_content,
        )
        msg_type = self.MSG_TYPE_MAPPER.get(local_type, str(local_type))
        msg_time = self._format_timestamp(create_time)
        return [local_id, sender, msg_type, msg_time, msg]

    def _build_message_content(
        self,
        table_name: str,
        local_id: int,
        local_type: int,
        message_content: str | bytes,
    ) -> str:
        if local_type == 1:
            _, text_content = self._split_sender_and_text(message_content)
            if text_content:
                return text_content
            if isinstance(message_content, bytes):
                return f"[未识别字节消息] size={len(message_content)}"
            return self._decode_message_content(message_content).strip()
        if local_type == 3:
            return self.media_manager.export_image(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "img"
            )
        if local_type == 34:
            return self.media_manager.export_voice(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "voice"
            )
        if local_type == 43:
            return self.media_manager.export_video(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "video"
            )
        return self._decode_message_content(message_content)

    def _get_sender_name(
        self,
        contact_conn: sqlite3.Connection,
        real_sender_id: int,
        message_content: str | bytes | None,
    ) -> str:
        if real_sender_id == 5:
            return "我"

        wxid = self.real_sender_wxid_mapper.get(real_sender_id)
        if wxid is None and message_content:
            wxid, _ = self._split_sender_and_text(message_content)
            if wxid:
                self.real_sender_wxid_mapper[real_sender_id] = wxid

        if not wxid:
            return f"real_sender_id:{real_sender_id}"

        return self._get_wxid_name(contact_conn, wxid) or wxid

    def _get_wxid_name(self, contact_conn: sqlite3.Connection, wxid: str) -> str | None:
        cached_name = self.wxid_name_cache.get(wxid)
        if cached_name is not None:
            return cached_name

        row = (
            contact_conn.cursor()
            .execute(
                "SELECT remark, nick_name FROM contact WHERE username = ?",
                (wxid,),
            )
            .fetchone()
        )
        if row is None:
            return None

        remark, nick_name = row
        display_name = remark or nick_name
        if display_name:
            self.wxid_name_cache[wxid] = display_name
        return display_name

    @staticmethod
    def _decode_message_content(text: str | bytes) -> str:
        if isinstance(text, bytes):
            return text.decode("utf-8", errors="ignore")
        return text

    @classmethod
    def _split_sender_and_text(cls, text: str | bytes) -> tuple[str | None, str]:
        if isinstance(text, bytes):
            sender_marker = b"wxid_"
            sender_start = text.find(sender_marker)
            separator = b":\n"
            if sender_start >= 0:
                separator_index = text.find(separator, sender_start)
                if separator_index > sender_start:
                    sender = text[sender_start:separator_index].decode("utf-8", errors="ignore").strip()
                    content_start = separator_index + len(separator)
                    content_end = text.find(b"\x01\x00", content_start)
                    if content_end < 0:
                        content_end = len(text)
                    content = text[content_start:content_end].decode("utf-8", errors="ignore").strip()
                    return (sender or None), content

            content_end = text.find(b"\x01\x00")
            if content_end < 0:
                content_end = len(text)
            content_bytes = text[:content_end]

            bracket_start = content_bytes.find(b"[")
            if bracket_start >= 0:
                content = cls._clean_decoded_text(
                    content_bytes[bracket_start:].decode("utf-8", errors="ignore")
                )
                if cls._is_readable_text(content):
                    return None, content

            decoded_content = cls._clean_decoded_text(
                content_bytes.decode("utf-8", errors="ignore")
            )
            if cls._is_readable_text(decoded_content):
                return None, decoded_content

            return None, ""

        text = cls._decode_message_content(text)
        if ":" not in text:
            return None, text.strip()

        sender, content = text.split(":", 1)
        sender = sender.strip()
        content = content.strip()
        if not sender:
            return None, content
        return sender, content

    @staticmethod
    def _clean_decoded_text(text: str) -> str:
        text = text.replace("\x00", "")
        text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
        return text.strip()

    @staticmethod
    def _is_readable_text(text: str) -> bool:
        if not text:
            return False

        significant_chars = [ch for ch in text if not ch.isspace()]
        if not significant_chars:
            return False

        suspicious_count = sum(
            1 for ch in significant_chars if not WechatMessageCsvExporter._is_allowed_text_char(ch)
        )
        if suspicious_count / len(significant_chars) > 0.02:
            return False

        return any(
            ("\u4e00" <= ch <= "\u9fff")
            or ("a" <= ch.lower() <= "z")
            or ch.isdigit()
            for ch in significant_chars
        )

    @staticmethod
    def _is_allowed_text_char(ch: str) -> bool:
        return (
            "\u4e00" <= ch <= "\u9fff"
            or "a" <= ch.lower() <= "z"
            or ch.isdigit()
            or ch in "[](){}<>-_.,!?;:'\"/@#%&*+=|\\~`$^，。！？；：、…“”‘’【】《》"
        )

    @staticmethod
    def _format_timestamp(timestamp: int | float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    load_dotenv(ENV_PATH)
    exporter = WechatMessageCsvExporter.from_env()
    output_csv = exporter.export_by_contact_name(
        contact_name_keyword=os.getenv("CSV_CONTACT_NAME_KEYWORD", "第一"),
        output_csv_path=Path(
            os.getenv("CSV_OUTPUT_PATH", str(PROJECT_ROOT / "data" / "out" / "messages.csv"))
        ).expanduser(),
    )
    print(output_csv)


if __name__ == "__main__":
    main()

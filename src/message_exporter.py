from __future__ import annotations

import av
import csv
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

from ai import WechatAIClient
from media_manager import WechatMediaManager


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DECRYPTED_DB_DIR = PROJECT_ROOT / "data" / "db" / "decrypted"
DEFAULT_DECRYPTED_DB_DIR_ALT = PROJECT_ROOT / "data" / "db" / "dec"
DEFAULT_EXPORT_CSV_DIR = PROJECT_ROOT / "data" / "out" / "csv"
DEFAULT_EXPORT_DB_DIR = PROJECT_ROOT / "data" / "out" / "db"
DEFAULT_EXPORT_DB_NAME = "messages.db"


@dataclass(frozen=True)
class ContactInfo:
    username: str
    nick_name: str
    remark: str
    table_name: str

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.username


class WechatMessageExporter:
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
        self_wxid: str = "",
        ai_client: WechatAIClient | None = None,
        image_model_spec: str = "",
        video_model_spec: str = "",
        audio_model_spec: str = "",
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.contact_db_path = Path(contact_db_path)
        self.media_manager = media_manager
        self.self_wxid = self_wxid.strip()
        self.ai_client = ai_client
        self.image_model_spec = image_model_spec.strip()
        self.video_model_spec = video_model_spec.strip()
        self.audio_model_spec = audio_model_spec.strip()
        self.wxid_name_cache: dict[str, str] = {}
        self.wxid_remark_cache: dict[str, str] = {}
        self.real_sender_wxid_mapper: dict[int, str] = {}

    @classmethod
    def from_env(cls) -> "WechatMessageExporter":
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
        ai_client = (
            WechatAIClient.from_env()
            if (
                os.getenv("OPENAI_API_KEY", "").strip()
                or os.getenv("GOOGLE_API_KEY", "").strip()
            )
            else None
        )
        return cls(
            message_db_path=Path(
                os.getenv("MESSAGE_DB_PATH", str(decrypted_db_dir / "message_0.db"))
            ),
            contact_db_path=Path(
                os.getenv("CONTACT_DB_PATH", str(decrypted_db_dir / "contact.db"))
            ),
            media_manager=media_manager,
            self_wxid=os.getenv("WXID", ""),
            ai_client=ai_client,
            image_model_spec=os.getenv("AI_IMAGE_MODEL", ""),
            video_model_spec=os.getenv("AI_VIDEO_MODEL", ""),
            audio_model_spec=os.getenv("AI_AUDIO_MODEL", ""),
        )

    def export_by_contact_name(
        self,
        contact_name_keyword: str,
        output_path: Path | None = None,
        *,
        output_format: str = "csv",
        limit: int | None = None,
    ) -> Path:
        output_format = output_format.lower().strip()
        if output_format == "csv":
            return self.export_by_contact_name_to_csv(
                contact_name_keyword=contact_name_keyword,
                output_csv_path=output_path,
                limit=limit,
            )
        if output_format in {"sqlite", "db"}:
            return self.export_by_contact_name_to_sqlite(
                contact_name_keyword=contact_name_keyword,
                output_sqlite_path=output_path,
                limit=limit,
            )
        raise ValueError(f"unsupported output_format: {output_format}")

    def export_by_contact_name_to_csv(
        self,
        contact_name_keyword: str,
        output_csv_path: Path | None = None,
        *,
        limit: int | None = None,
    ) -> Path:
        contact_info, csv_rows = self._collect_export_rows(
            contact_name_keyword, limit=limit
        )
        output_csv_path = self._resolve_output_path(
            output_path=output_csv_path,
            default_dir=DEFAULT_EXPORT_CSV_DIR,
            file_stem=contact_info.display_name,
            suffix=".csv",
        )
        output_csv_path.parent.mkdir(parents=True, exist_ok=True)

        with output_csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(
                csv_file,
                quoting=csv.QUOTE_MINIMAL,
                quotechar='"',
                escapechar="\\",
            )
            writer.writerow(
                [
                    "local_id",
                    "sender",
                    "wxid",
                    "remark",
                    "msg_type",
                    "msg_time",
                    "msg",
                ]
            )
            writer.writerows(
                [
                    [
                        row["local_id"],
                        row["sender"],
                        row["wxid"],
                        row["remark"],
                        row["msg_type"],
                        row["msg_time"],
                        row["msg"],
                    ]
                    for row in csv_rows
                ]
            )

        return output_csv_path

    def export_by_contact_name_to_sqlite(
        self,
        contact_name_keyword: str,
        output_sqlite_path: Path | None = None,
        *,
        limit: int | None = None,
    ) -> Path:
        contact_info, export_rows = self._collect_export_rows(
            contact_name_keyword, limit=limit
        )
        output_sqlite_path = self._resolve_output_path(
            output_path=output_sqlite_path,
            default_dir=DEFAULT_EXPORT_DB_DIR,
            file_stem="messages",
            suffix=".db",
        )
        output_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        table_name = self._resolve_sqlite_table_name(contact_info)

        with sqlite3.connect(output_sqlite_path) as out_conn:
            cursor = out_conn.cursor()
            quoted_table_name = self._quote_sqlite_identifier(table_name)

            cursor.execute(f"DROP TABLE IF EXISTS {quoted_table_name}")
            cursor.execute(
                """
                CREATE TABLE {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_id INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    wxid TEXT NOT NULL,
                    remark TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    msg_time TEXT NOT NULL,
                    msg TEXT NOT NULL
                )
                """.format(table_name=quoted_table_name)
            )

            cursor.executemany(
                """
                INSERT INTO {table_name} (
                    local_id, sender, wxid, remark, msg_type, msg_time, msg
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """.format(table_name=quoted_table_name),
                [
                    (
                        row["local_id"],
                        row["sender"],
                        row["wxid"],
                        row["remark"],
                        row["msg_type"],
                        row["msg_time"],
                        row["msg"],
                    )
                    for row in export_rows
                ],
            )

            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._quote_sqlite_identifier(f'idx_{table_name}_local_id')} "
                f"ON {quoted_table_name}(local_id)"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._quote_sqlite_identifier(f'idx_{table_name}_msg_time')} "
                f"ON {quoted_table_name}(msg_time)"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._quote_sqlite_identifier(f'idx_{table_name}_sender')} "
                f"ON {quoted_table_name}(sender)"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._quote_sqlite_identifier(f'idx_{table_name}_wxid')} "
                f"ON {quoted_table_name}(wxid)"
            )

            out_conn.commit()

        return output_sqlite_path

    def _collect_export_rows(
        self,
        contact_name_keyword: str,
        *,
        limit: int | None = None,
    ) -> tuple[ContactInfo, list[dict[str, object]]]:
        with (
            sqlite3.connect(self.message_db_path) as message_conn,
            sqlite3.connect(self.contact_db_path) as contact_conn,
        ):
            message_cursor = message_conn.cursor()
            contact_cursor = contact_conn.cursor()
            contact_info = self._find_contact_info(contact_cursor, contact_name_keyword)
            rows = self._fetch_message_rows(
                message_cursor, contact_info.table_name, limit
            )
            export_rows: list[dict[str, object]] = []
            progress = tqdm(
                rows,
                desc=f"导出 {contact_info.display_name}",
                unit="msg",
                dynamic_ncols=True,
            )
            for row in progress:
                export_rows.append(
                    self._build_export_row(
                        contact_conn,
                        contact_info.table_name,
                        row,
                        previous_rows=export_rows,
                    )
                )
            return (
                contact_info,
                export_rows,
            )

    def _find_contact_info(
        self,
        contact_cursor: sqlite3.Cursor,
        contact_name_keyword: str,
    ) -> ContactInfo:
        row = contact_cursor.execute(
            """
            SELECT username, nick_name, remark
            FROM contact
            WHERE nick_name LIKE ? OR remark LIKE ?
            LIMIT 1
            """,
            (f"%{contact_name_keyword}%", f"%{contact_name_keyword}%"),
        ).fetchone()
        if row is None:
            raise ValueError(f"contact not found: {contact_name_keyword}")

        username = str(row[0])
        nick_name = self._decode_message_content(row[1] or "").strip()
        remark = self._decode_message_content(row[2] or "").strip()
        table_name = f"Msg_{hashlib.md5(username.encode('utf-8')).hexdigest()}"
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", table_name):
            raise ValueError(f"invalid message table name: {table_name}")
        return ContactInfo(
            username=username,
            nick_name=nick_name,
            remark=remark,
            table_name=table_name,
        )

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

    def _build_export_row(
        self,
        contact_conn: sqlite3.Connection,
        table_name: str,
        row: tuple[int, int, int, int, str],
        *,
        previous_rows: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        local_id, real_sender_id, local_type, create_time, message_content = row
        sender, wxid = self._get_sender_info(
            contact_conn=contact_conn,
            real_sender_id=real_sender_id,
            message_content=message_content if local_type == 1 else None,
        )
        msg, remark = self._build_message_content_and_remark(
            table_name=table_name,
            local_id=local_id,
            local_type=local_type,
            message_content=message_content,
            previous_rows=previous_rows or [],
        )
        msg_type = self.MSG_TYPE_MAPPER.get(local_type, str(local_type))
        msg_time = self._format_timestamp(create_time)
        return {
            "local_id": local_id,
            "sender": sender,
            "wxid": wxid,
            "remark": remark,
            "msg_type": msg_type,
            "msg_time": msg_time,
            "msg": msg,
        }

    def _build_message_content_and_remark(
        self,
        table_name: str,
        local_id: int,
        local_type: int,
        message_content: str | bytes,
        *,
        previous_rows: list[dict[str, object]],
    ) -> tuple[str, str]:
        if local_type == 1:
            _, text_content = self._split_sender_and_text(message_content)
            if text_content:
                return text_content, ""
            if isinstance(message_content, bytes):
                return f"[未识别字节消息] size={len(message_content)}", ""
            return self._decode_message_content(message_content).strip(), ""
        if local_type == 3:
            image_path = self.media_manager.export_image(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "img"
            )
            return image_path, self._build_image_remark(image_path, previous_rows)
        if local_type == 34:
            voice_path = self.media_manager.export_voice(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "voice"
            )
            return voice_path, self._build_voice_remark(voice_path)
        if local_type == 43:
            video_path = self.media_manager.export_video(
                table_name, local_id, PROJECT_ROOT / "data" / "msg" / "video"
            )
            return video_path, self._build_video_remark(video_path, previous_rows)
        return self._decode_message_content(message_content), ""

    def _build_image_remark(
        self,
        image_path: str,
        previous_rows: list[dict[str, object]],
    ) -> str:
        if self.ai_client is None:
            return ""
        context = self._build_recent_context(previous_rows)
        prompt = (
            "你正在整理微信聊天导出。请用中文简洁描述这张图片的主要内容，并结合给出的上文判断它在当前对话里的可能含义。"
            "如果上文帮助不大，就只描述图片可见内容。输出控制在2到4句。"
        )
        if context:
            prompt += f"\n\n上文5句：\n{context}"
        return self._safe_describe_image(
            image_path,
            prompt,
            model_spec=self.image_model_spec,
        )

    def _build_voice_remark(self, voice_path: str) -> str:
        if self.ai_client is None:
            return ""
        return self._safe_transcribe_audio(
            voice_path,
            model_spec=self.audio_model_spec,
        )

    def _build_video_remark(
        self,
        video_path: str,
        previous_rows: list[dict[str, object]],
    ) -> str:
        if self.ai_client is None:
            return ""
        preview_image = self._resolve_video_preview_image(Path(video_path))
        if preview_image is None:
            return ""
        context = self._build_recent_context(previous_rows)
        prompt = (
            "你正在整理微信聊天导出。请根据这个视频封面或抽帧，用中文简洁说明视频大致内容；"
            "若上文能帮助理解场景，请一并考虑。输出控制在2到4句。"
        )
        if context:
            prompt += f"\n\n上文5句：\n{context}"
        return self._safe_describe_image(
            preview_image,
            prompt,
            model_spec=self.video_model_spec,
        )

    def _build_recent_context(
        self,
        previous_rows: list[dict[str, object]],
        *,
        max_items: int = 5,
    ) -> str:
        context_lines: list[str] = []
        for row in previous_rows[-max_items:]:
            sender = str(row.get("sender", "")).strip()
            msg_type = str(row.get("msg_type", "")).strip()
            remark = str(row.get("remark", "")).strip()
            msg = str(row.get("msg", "")).strip()
            if msg_type == "语音" and remark:
                content = remark
            elif msg_type in {"图片", "视频"} and remark:
                content = remark
            else:
                content = msg
            if not content:
                continue
            context_lines.append(f"{sender}: {content}")
        return "\n".join(context_lines)

    def _resolve_video_preview_image(self, video_path: Path) -> Path | None:
        video_path = Path(video_path)
        if video_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return video_path
        if video_path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            return None

        frame_dir = PROJECT_ROOT / "data" / "msg" / "video" / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / f"{video_path.stem}_frame.jpg"

        try:
            with av.open(str(video_path), mode="r") as container:
                for frame in container.decode(video=0):
                    image = frame.to_image()
                    image.save(frame_path, format="JPEG", quality=90)
                    return frame_path
        except Exception:
            return None
        return None

    def _safe_describe_image(
        self,
        image_path: str | Path,
        prompt: str,
        *,
        model_spec: str = "",
    ) -> str:
        if self.ai_client is None:
            return ""
        try:
            return self.ai_client.describe_image(
                image_path,
                prompt,
                model_spec=model_spec,
            )
        except Exception:
            return ""

    def _safe_transcribe_audio(
        self,
        audio_path: str | Path,
        *,
        model_spec: str = "",
    ) -> str:
        if self.ai_client is None:
            return ""
        try:
            return self.ai_client.transcribe_audio(
                audio_path,
                model_spec=model_spec,
            )
        except Exception:
            return ""

    def _get_sender_info(
        self,
        contact_conn: sqlite3.Connection,
        real_sender_id: int,
        message_content: str | bytes | None,
    ) -> tuple[str, str]:
        if real_sender_id == 5:
            return "我", self.self_wxid

        wxid = self.real_sender_wxid_mapper.get(real_sender_id)
        if wxid is None and message_content:
            wxid, _ = self._split_sender_and_text(message_content)
            if wxid:
                self.real_sender_wxid_mapper[real_sender_id] = wxid

        if not wxid:
            return f"real_sender_id:{real_sender_id}", ""

        sender = self._get_wxid_name(contact_conn, wxid) or wxid
        return sender, wxid

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
        remark = self._decode_message_content(remark or "").strip()
        nick_name = self._decode_message_content(nick_name or "").strip()
        display_name = remark or nick_name
        self.wxid_remark_cache[wxid] = remark
        if display_name:
            self.wxid_name_cache[wxid] = display_name
        return display_name

    @staticmethod
    def _sanitize_file_stem(file_stem: str) -> str:
        sanitized = re.sub(r'[\\/:*?"<>|]+', "_", file_stem).strip()
        sanitized = sanitized.rstrip(". ")
        return sanitized or "messages"

    def _resolve_output_path(
        self,
        output_path: Path | None,
        *,
        default_dir: Path,
        file_stem: str,
        suffix: str,
    ) -> Path:
        if output_path is not None:
            path = Path(output_path).expanduser()
            if path.suffix.lower() == suffix:
                return path
            return path / f"{self._sanitize_file_stem(file_stem)}{suffix}"
        return default_dir / f"{self._sanitize_file_stem(file_stem)}{suffix}"

    @classmethod
    def _resolve_sqlite_table_name(cls, contact_info: ContactInfo) -> str:
        table_name = cls._sanitize_sqlite_identifier(contact_info.display_name)
        if table_name:
            return table_name
        return cls._sanitize_sqlite_identifier(contact_info.username) or "messages"

    @staticmethod
    def _sanitize_sqlite_identifier(name: str) -> str:
        sanitized = (name or "").replace("\x00", "").strip()
        sanitized = sanitized.replace("]", "]]")
        return sanitized

    @staticmethod
    def _quote_sqlite_identifier(identifier: str) -> str:
        return f"[{identifier}]"

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
                    sender = (
                        text[sender_start:separator_index]
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    content_start = separator_index + len(separator)
                    content_end = text.find(b"\x01\x00", content_start)
                    if content_end < 0:
                        content_end = len(text)
                    content = (
                        text[content_start:content_end]
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
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
            1
            for ch in significant_chars
            if not WechatMessageExporter._is_allowed_text_char(ch)
        )
        if suspicious_count / len(significant_chars) > 0.02:
            return False

        return any(
            ("\u4e00" <= ch <= "\u9fff") or ("a" <= ch.lower() <= "z") or ch.isdigit()
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
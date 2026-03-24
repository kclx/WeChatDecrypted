"""导出与画像服务共享的基础能力。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.wechat_tool.clients.ai import WechatAIClient
from src.wechat_tool.common.models import (
    CONTACT_TYPE_CHATROOM,
    CONTACT_TYPE_PERSON,
    CONTACT_TYPE_UNSUPPORTED,
    ContactCandidate,
    ContactInfo,
    ContactSelectionError,
    PROFILE_STATUS_KNOWN,
    PROFILE_STATUS_NOT_ENOUGH,
    PROFILE_STATUS_UNKNOWN,
)
from src.wechat_tool.media.manager import WechatMediaManager


class WechatServiceBase:
    """封装联系人解析、路径处理、AI 调用等共享底层能力。"""

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
        profile_model_spec: str = "",
        export_dir: Path | None = None,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.contact_db_path = Path(contact_db_path)
        self.media_manager = media_manager
        self.self_wxid = self_wxid.strip()
        self.ai_client = ai_client
        self.image_model_spec = image_model_spec.strip()
        self.video_model_spec = video_model_spec.strip()
        self.audio_model_spec = audio_model_spec.strip()
        self.profile_model_spec = profile_model_spec.strip()
        self.wxid_name_cache: dict[str, str] = {}
        self.wxid_remark_cache: dict[str, str] = {}
        self.real_sender_wxid_mapper: dict[int, str] = {}
        self.current_contact_display_name = ""
        self.export_dir = (
            Path(export_dir).expanduser()
            if export_dir is not None
            else Path("data/out")
        )

    @classmethod
    def from_env(cls) -> "WechatServiceBase":
        """从环境变量构造服务实例。"""
        load_dotenv()
        default_db_dir = (
            Path("data/db/decrypted")
            if Path("data/db/decrypted").exists()
            else Path("data/db/dec")
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
            profile_model_spec=os.getenv("AI_PROFILE_MODEL", ""),
            export_dir=Path(os.getenv("EXPORT_DIR", "data/out")),
        )

    def _find_contact_info(
        self,
        contact_cursor: sqlite3.Cursor,
        contact_name_keyword: str,
    ) -> ContactInfo:
        keyword = contact_name_keyword.strip()
        if not keyword:
            raise ValueError("contact keyword is required")

        if self._looks_like_direct_username(keyword):
            candidate = self._query_contact_by_username(contact_cursor, keyword)
            if candidate is None:
                raise ValueError(f"contact not found by username: {keyword}")
            return self._build_contact_info(candidate)

        for field_name, exact in (
            ("remark", True),
            ("nick_name", True),
            ("alias", True),
            ("remark", False),
            ("nick_name", False),
            ("alias", False),
        ):
            candidates = self._query_contact_candidates(
                contact_cursor,
                field_name=field_name,
                keyword=keyword,
                exact=exact,
            )
            if not candidates:
                continue
            if len(candidates) > 1:
                raise ContactSelectionError(keyword, candidates)
            return self._build_contact_info(candidates[0])

        raise ValueError(f"contact not found: {contact_name_keyword}")

    def _query_contact_by_username(
        self,
        contact_cursor: sqlite3.Cursor,
        username: str,
    ) -> ContactCandidate | None:
        row = contact_cursor.execute(
            """
            SELECT username, alias, nick_name, remark
            FROM contact
            WHERE delete_flag = 0 AND username = ?
            LIMIT 1
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return self._build_contact_candidate(row)

    def _query_contact_candidates(
        self,
        contact_cursor: sqlite3.Cursor,
        *,
        field_name: str,
        keyword: str,
        exact: bool,
    ) -> list[ContactCandidate]:
        if field_name not in {"alias", "remark", "nick_name"}:
            raise ValueError(f"unsupported field_name: {field_name}")
        operator = "=" if exact else "LIKE"
        value = keyword if exact else f"%{keyword}%"
        rows = contact_cursor.execute(
            f"""
            SELECT username, alias, nick_name, remark
            FROM contact
            WHERE delete_flag = 0
              AND COALESCE({field_name}, '') <> ''
              AND {field_name} {operator} ?
            ORDER BY id ASC
            """,
            (value,),
        ).fetchall()
        return [self._build_contact_candidate(row) for row in rows]

    def _build_contact_candidate(
        self,
        row: sqlite3.Row | tuple[Any, ...],
    ) -> ContactCandidate:
        return ContactCandidate(
            username=str(row[0] or "").strip(),
            alias=self._decode_message_content(row[1] or "").strip(),
            nick_name=self._decode_message_content(row[2] or "").strip(),
            remark=self._decode_message_content(row[3] or "").strip(),
        )

    def _build_contact_info(self, candidate: ContactCandidate) -> ContactInfo:
        table_name = f"Msg_{hashlib.md5(candidate.username.encode('utf-8')).hexdigest()}"
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", table_name):
            raise ValueError(f"invalid message table name: {table_name}")
        return ContactInfo(
            username=candidate.username,
            nick_name=candidate.nick_name,
            remark=candidate.remark,
            alias=candidate.alias,
            table_name=table_name,
            contact_type=self._detect_contact_type(candidate.username),
        )

    @staticmethod
    def _detect_contact_type(username: str) -> str:
        if username.startswith("wxid_"):
            return CONTACT_TYPE_PERSON
        if username.endswith("@chatroom"):
            return CONTACT_TYPE_CHATROOM
        return CONTACT_TYPE_UNSUPPORTED

    @staticmethod
    def _looks_like_direct_username(keyword: str) -> bool:
        value = keyword.strip()
        if not value or any(ch.isspace() for ch in value):
            return False
        return value.startswith(("wxid_", "gh_", "v1_", "v2_", "openim_")) or value.endswith(
            "@chatroom"
        ) or value == "filehelper"

    def _find_existing_message_table(
        self,
        output_sqlite_path: Path,
        table_name: str,
    ) -> str | None:
        if not output_sqlite_path.exists():
            return None
        with sqlite3.connect(output_sqlite_path) as out_conn:
            row = out_conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = ?
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def _table_exists_on_path(self, output_sqlite_path: Path, table_name: str) -> bool:
        return self._find_existing_message_table(output_sqlite_path, table_name) is not None

    @staticmethod
    def _table_exists(out_conn: sqlite3.Connection, table_name: str) -> bool:
        row = out_conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _resolve_messages_db_path(self, output_path: Path | None) -> Path:
        return self._resolve_output_path(
            output_path=output_path,
            default_dir=self.export_dir / "db",
            file_stem="messages",
            suffix=".db",
        )

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

    def _safe_chat(self, prompt: str, *, model_spec: str = "") -> str:
        if self.ai_client is None:
            return ""
        try:
            return self.ai_client.chat(prompt, model_spec=model_spec or None)
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
        message_conn: sqlite3.Connection,
        contact_conn: sqlite3.Connection,
        table_name: str,
        contact_info: ContactInfo,
        local_id: int,
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
            wxid = self._resolve_sender_wxid_from_history(
                message_conn=message_conn,
                table_name=table_name,
                real_sender_id=real_sender_id,
                local_id=local_id,
            )
            if wxid:
                self.real_sender_wxid_mapper[real_sender_id] = wxid

        if not wxid and not contact_info.username.endswith("@chatroom"):
            wxid = contact_info.username
            self.real_sender_wxid_mapper[real_sender_id] = wxid

        if not wxid:
            return f"real_sender_id:{real_sender_id}", ""

        sender = self._get_wxid_name(contact_conn, wxid) or wxid
        return sender, wxid

    def _resolve_sender_wxid_from_history(
        self,
        *,
        message_conn: sqlite3.Connection,
        table_name: str,
        real_sender_id: int,
        local_id: int,
    ) -> str | None:
        rows = (
            message_conn.cursor()
            .execute(
                f"""
            SELECT message_content
            FROM [{table_name}]
            WHERE real_sender_id = ?
              AND local_type = 1
              AND local_id <> ?
            ORDER BY ABS(local_id - ?) ASC, local_id DESC
            LIMIT 20
            """,
                (real_sender_id, local_id, local_id),
            )
            .fetchall()
        )
        for row in rows:
            candidate_wxid, _ = self._split_sender_and_text(row[0] or "")
            if candidate_wxid:
                return candidate_wxid
        return None

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
        default_dir.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _looks_like_sender_id(value: str) -> bool:
        sender = value.strip()
        if not sender or any(ch.isspace() for ch in sender):
            return False
        return sender.startswith(("wxid_", "gh_", "v1_", "v2_", "openim_")) or sender.endswith(
            "@chatroom"
        ) or sender == "filehelper"

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
        if ":\n" not in text:
            return None, text.strip()

        sender, content = text.split(":\n", 1)
        sender = sender.strip()
        content = content.strip()
        if not cls._looks_like_sender_id(sender):
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
            1 for ch in significant_chars if not WechatServiceBase._is_allowed_text_char(ch)
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

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number < 0:
            return 0.0
        if number > 1:
            return 1.0
        return number

    @staticmethod
    def _normalize_profile_status(value: Any) -> str:
        if value in {
            PROFILE_STATUS_KNOWN,
            PROFILE_STATUS_UNKNOWN,
            PROFILE_STATUS_NOT_ENOUGH,
        }:
            return str(value)
        return PROFILE_STATUS_UNKNOWN

    def _normalize_evidence_refs(
        self,
        value: Any,
        *,
        message_lookup: dict[int, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        evidence_list: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            local_id = item.get("local_id")
            msg_time = str(item.get("msg_time") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            normalized_local_id: int | None = None
            if local_id not in (None, ""):
                try:
                    normalized_local_id = int(local_id)
                except (TypeError, ValueError):
                    normalized_local_id = None
            source_row = None
            if normalized_local_id is not None and message_lookup is not None:
                source_row = message_lookup.get(normalized_local_id)
            if source_row is not None:
                msg_time = str(source_row.get("msg_time") or msg_time)
                snippet = self._build_evidence_snippet(source_row)
            if local_id in (None, "") and not snippet:
                continue
            evidence_list.append(
                {
                    "local_id": normalized_local_id if normalized_local_id is not None else local_id,
                    "msg_time": msg_time,
                    "snippet": snippet,
                }
            )
        return evidence_list[:10]

    @staticmethod
    def _build_evidence_snippet(row: dict[str, Any]) -> str:
        msg_type = str(row.get("msg_type") or "")
        msg = str(row.get("msg") or "").strip()
        remark = str(row.get("remark") or "").strip()
        content = remark if msg_type in {"图片", "视频", "语音"} and remark else msg
        content = content.strip()
        if len(content) > 80:
            return f"{content[:80]}..."
        return content

    @staticmethod
    def _extract_json_object(text: str) -> str:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("AI response does not contain a JSON object")
        return raw[start : end + 1]

    @staticmethod
    def _load_json_text(text: Any) -> Any:
        if text in (None, ""):
            return None
        if isinstance(text, (dict, list)):
            return text
        return json.loads(str(text))

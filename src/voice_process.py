from __future__ import annotations

import hashlib
import os
import sqlite3
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pysilk
from dotenv import load_dotenv


SILK_MAGIC = b"#!SILK_V3"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DECRYPTED_DB_DIR = PROJECT_ROOT / "data" / "db" / "decrypted"
DEFAULT_DECRYPTED_DB_DIR_ALT = PROJECT_ROOT / "data" / "db" / "dec"


@dataclass
class VoiceSummary:
    msg_table: str
    local_id: int
    server_id: int
    create_time: int
    month_dir: str
    chat_name_id: int | None
    talker_user_name: str | None
    voice_data_path: str | None
    voice_data_size: int | None
    voice_format: str | None
    needs_strip_prefix_byte: bool
    silk_path: str | None
    normalized_silk_path: str | None
    pcm_path: str | None
    wav_path: str | None
    has_voice_data: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WechatVoiceParser:
    """封装微信语音消息的数据库定位与 .silk 导出。"""

    def __init__(
        self,
        message_db_path: Path,
        media_db_path: Path,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.media_db_path = Path(media_db_path)

    @classmethod
    def from_env(cls) -> WechatVoiceParser:
        load_dotenv(ENV_PATH)
        decrypted_db_dir = (
            DEFAULT_DECRYPTED_DB_DIR
            if DEFAULT_DECRYPTED_DB_DIR.exists()
            else DEFAULT_DECRYPTED_DB_DIR_ALT
        )
        return cls(
            message_db_path=Path(
                os.getenv("MESSAGE_DB_PATH", str(decrypted_db_dir / "message_0.db"))
            ).expanduser(),
            media_db_path=Path(
                os.getenv("MEDIA_DB_PATH", str(decrypted_db_dir / "media_0.db"))
            ).expanduser(),
        )

    def find_voice_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        with sqlite3.connect(self.message_db_path) as msg_conn, sqlite3.connect(self.media_db_path) as media_conn:
            msg_conn.row_factory = sqlite3.Row
            media_conn.row_factory = sqlite3.Row

            message = self._fetch_message(msg_conn, msg_table, local_id)
            talker_user_name = self._msg_table_to_user_name(media_conn, msg_table)
            chat_name_id = None if talker_user_name is None else self._fetch_chat_name_id(media_conn, talker_user_name)
            voice_row = self._fetch_voice_row(media_conn, chat_name_id, message)

        voice_info = self._build_voice_info(voice_row)
        return {
            "message": dict(message),
            "month_dir": self._format_month_dir(message["create_time"]),
            "talker_user_name": talker_user_name,
            "chat_name_id": chat_name_id,
            "voice_row": None if voice_row is None else dict(voice_row),
            "voice_info": voice_info,
        }

    def export_voice(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, object]:
        detail = self.find_voice_paths(msg_table, local_id)
        voice_row = detail["voice_row"]
        if voice_row is None:
            raise FileNotFoundError(
                f"voice data not found: table={msg_table}, local_id={local_id}, server_id={detail['message']['server_id']}"
            )

        voice_data = voice_row["voice_data"]
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{msg_table}_{detail['message']['local_id']}"
        raw_path = output_dir / f"{base_name}.db.silk"
        raw_path.write_bytes(voice_data)

        normalized = self._normalize_silk_bytes(voice_data)
        normalized_path = None
        if normalized is not None:
            normalized_path = output_dir / f"{base_name}.silk"
            normalized_path.write_bytes(normalized)

        pcm_path = None
        wav_path = None
        pcm_path = output_dir / f"{base_name}.pcm"
        self._decode_silk(raw_path, pcm_path)
        wav_path = output_dir / f"{base_name}.wav"
        self._pcm_to_wav(pcm_path, wav_path)

        exported = {
            **detail,
            "voice_data_path": str(raw_path),
            "normalized_silk_path": None if normalized_path is None else str(normalized_path),
            "pcm_path": None if pcm_path is None else str(pcm_path),
            "wav_path": None if wav_path is None else str(wav_path),
        }
        return exported

    def find_voice_summary(self, msg_table: str, local_id: int, output_dir: Path | None = None) -> VoiceSummary:
        detail = self.find_voice_paths(msg_table, local_id)
        voice_info = detail["voice_info"]
        voice_data_path = None
        normalized_silk_path = None
        pcm_path = None
        wav_path = None

        if output_dir is not None and detail["voice_row"] is not None:
            exported = self.export_voice(msg_table, local_id, output_dir)
            voice_data_path = exported["voice_data_path"]
            normalized_silk_path = exported["normalized_silk_path"]
            pcm_path = exported["pcm_path"]
            wav_path = exported["wav_path"]

        message = detail["message"]
        return VoiceSummary(
            msg_table=msg_table,
            local_id=local_id,
            server_id=message["server_id"],
            create_time=message["create_time"],
            month_dir=detail["month_dir"],
            chat_name_id=detail["chat_name_id"],
            talker_user_name=detail["talker_user_name"],
            voice_data_path=voice_data_path,
            voice_data_size=voice_info["voice_data_size"],
            voice_format=voice_info["voice_format"],
            needs_strip_prefix_byte=voice_info["needs_strip_prefix_byte"],
            silk_path=voice_data_path,
            normalized_silk_path=normalized_silk_path,
            pcm_path=pcm_path,
            wav_path=wav_path,
            has_voice_data=detail["voice_row"] is not None,
        )

    @staticmethod
    def _format_month_dir(create_time: int) -> str:
        return datetime.fromtimestamp(create_time).strftime("%Y-%m")

    def _fetch_message(self, conn: sqlite3.Connection, msg_table: str, local_id: int) -> sqlite3.Row:
        query = f"""
            SELECT local_id, server_id, local_type, real_sender_id, create_time, packed_info_data
            FROM [{msg_table}]
            WHERE local_id = ?
        """
        row = conn.execute(query, (local_id,)).fetchone()
        if row is None:
            raise ValueError(f"message not found: table={msg_table}, local_id={local_id}")
        if row["local_type"] != 34:
            raise ValueError(
                f"message is not a voice message: table={msg_table}, local_id={local_id}, local_type={row['local_type']}"
            )
        return row

    def _msg_table_to_user_name(self, conn: sqlite3.Connection, msg_table: str) -> str | None:
        chat_md5 = msg_table.removeprefix("Msg_")
        for row in conn.execute("SELECT user_name FROM Name2Id"):
            if hashlib.md5(row[0].encode("utf-8")).hexdigest() == chat_md5:
                return row[0]
        return None

    @staticmethod
    def _fetch_chat_name_id(conn: sqlite3.Connection, user_name: str) -> int | None:
        row = conn.execute("SELECT rowid FROM Name2Id WHERE user_name = ?", (user_name,)).fetchone()
        return None if row is None else int(row[0])

    @staticmethod
    def _fetch_voice_row(
        conn: sqlite3.Connection,
        chat_name_id: int | None,
        message: sqlite3.Row,
    ) -> sqlite3.Row | None:
        if chat_name_id is None:
            return None

        return conn.execute(
            """
            SELECT chat_name_id, create_time, local_id, svr_id, voice_data, data_index
            FROM VoiceInfo
            WHERE chat_name_id = ?
              AND local_id = ?
              AND svr_id = ?
              AND create_time = ?
            """,
            (
                chat_name_id,
                message["local_id"],
                message["server_id"],
                message["create_time"],
            ),
        ).fetchone()

    @staticmethod
    def _build_voice_info(voice_row: sqlite3.Row | None) -> dict[str, object]:
        if voice_row is None:
            return {
                "voice_data_size": None,
                "voice_format": None,
                "needs_strip_prefix_byte": False,
                "header_hex": None,
            }

        voice_data = voice_row["voice_data"]
        needs_strip = voice_data.startswith(b"\x02" + SILK_MAGIC)
        if needs_strip:
            voice_format = "wechat_silk_prefixed"
        elif voice_data.startswith(SILK_MAGIC):
            voice_format = "silk"
        else:
            voice_format = "binary"

        return {
            "voice_data_size": len(voice_data),
            "voice_format": voice_format,
            "needs_strip_prefix_byte": needs_strip,
            "header_hex": voice_data[:32].hex(),
        }

    @staticmethod
    def _normalize_silk_bytes(voice_data: bytes) -> bytes | None:
        if voice_data.startswith(b"\x02" + SILK_MAGIC):
            return voice_data[1:]
        if voice_data.startswith(SILK_MAGIC):
            return voice_data
        return None

    def _decode_silk(self, silk_path: Path, pcm_path: Path) -> None:
        with silk_path.open("rb") as silk_fp, pcm_path.open("wb") as pcm_fp:
            pysilk.decode(silk_fp, pcm_fp, 24000)

    @staticmethod
    def _pcm_to_wav(pcm_path: Path, wav_path: Path) -> None:
        with pcm_path.open("rb") as pcm_fp, wave.open(str(wav_path), "wb") as wav_fp:
            wav_fp.setnchannels(1)
            wav_fp.setsampwidth(2)
            wav_fp.setframerate(24000)
            wav_fp.writeframes(pcm_fp.read())

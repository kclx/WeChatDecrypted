from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

from image_process import WechatImageParser
from video_process import WechatVideoParser
from voice_process import WechatVoiceParser


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DECRYPTED_DB_DIR = PROJECT_ROOT / "data" / "db" / "decrypted"
DEFAULT_DECRYPTED_DB_DIR_ALT = PROJECT_ROOT / "data" / "db" / "dec"


class WechatMediaManager:
    """统一封装图片、视频、语音三类媒体的解析与导出。"""

    def __init__(
        self,
        message_db_path: Path,
        account_root: Path,
        message_resource_db_path: Path | None = None,
        media_db_path: Path | None = None,
        hardlink_db_path: Path | None = None,
        key32: str | None = None,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.account_root = Path(account_root)
        self.message_resource_db_path = (
            None if message_resource_db_path is None else Path(message_resource_db_path)
        )
        self.media_db_path = None if media_db_path is None else Path(media_db_path)
        self.hardlink_db_path = None if hardlink_db_path is None else Path(hardlink_db_path)
        self.key32 = key32

        self._image_parser: WechatImageParser | None = None
        self._video_parser: WechatVideoParser | None = None
        self._voice_parser: WechatVoiceParser | None = None

    @classmethod
    def from_env(cls) -> WechatMediaManager:
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
            message_resource_db_path=Path(
                os.getenv(
                    "MESSAGE_RESOURCE_DB_PATH",
                    str(decrypted_db_dir / "message_resource.db"),
                )
            ).expanduser(),
            account_root=Path(os.environ["WECHAT_ROOT"]).expanduser(),
            media_db_path=Path(
                os.getenv("MEDIA_DB_PATH", str(decrypted_db_dir / "media_0.db"))
            ).expanduser(),
            hardlink_db_path=Path(
                os.getenv("HARDLINK_DB_PATH", str(decrypted_db_dir / "hardlink.db"))
            ).expanduser(),
            key32=os.environ["KEY32"],
        )

    def export_image(self, msg_table: str, local_id: int, output_dir: Path) -> str:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for variant in ("hd", "main", "thumb"):
            try:
                result = self.image._recover_variant(
                    msg_table,
                    local_id,
                    output_dir,
                    variant,
                    output_stem=f"{msg_table}_{local_id}",
                )
                return str(result["output_file"])
            except FileNotFoundError:
                continue

        raise FileNotFoundError(f"no image asset found: table={msg_table}, local_id={local_id}")

    def export_video(self, msg_table: str, local_id: int, output_dir: Path) -> str:
        detail = self.video.find_video_paths(msg_table, local_id)
        preferred_paths = detail["preferred_paths"]
        source = (
            preferred_paths.get("raw")
            or preferred_paths.get("play")
            or preferred_paths.get("poster")
            or preferred_paths.get("thumb")
        )
        if not source:
            raise FileNotFoundError(f"no video asset found: table={msg_table}, local_id={local_id}")

        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"video file not found: {src}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target_path = output_dir / f"{msg_table}_{local_id}{src.suffix}"
        self._replace_existing_file(target_path)
        shutil.copy2(src, target_path)
        return str(target_path)

    def export_voice(self, msg_table: str, local_id: int, output_dir: Path) -> str:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        exported = self.voice.export_voice(msg_table, local_id, output_dir)
        preferred_output = (
            exported.get("wav_path")
            or exported.get("normalized_silk_path")
            or exported.get("voice_data_path")
        )
        if not preferred_output:
            raise FileNotFoundError(f"no voice asset found: table={msg_table}, local_id={local_id}")

        preferred_path = Path(str(preferred_output))
        if not preferred_path.exists():
            raise FileNotFoundError(f"voice output file not found: {preferred_path}")

        target_path = output_dir / f"{msg_table}_{local_id}{preferred_path.suffix}"
        if preferred_path != target_path:
            self._replace_existing_file(target_path)
            shutil.copy2(preferred_path, target_path)

        for candidate in (
            exported.get("voice_data_path"),
            exported.get("normalized_silk_path"),
            exported.get("pcm_path"),
            exported.get("wav_path"),
        ):
            if not candidate:
                continue
            candidate_path = Path(str(candidate))
            if candidate_path.exists() and candidate_path != target_path:
                candidate_path.unlink()

        return str(target_path)

    @staticmethod
    def _replace_existing_file(target_path: Path) -> None:
        if not target_path.exists():
            return
        target_path.chmod(target_path.stat().st_mode | 0o200)
        target_path.unlink()

    @property
    def image(self) -> WechatImageParser:
        if self._image_parser is None:
            if self.message_resource_db_path is None or self.key32 is None:
                raise ValueError("image manager requires message_resource_db_path and key32")
            self._image_parser = WechatImageParser(
                message_db_path=self.message_db_path,
                message_resource_db_path=self.message_resource_db_path,
                account_root=self.account_root,
                key32=self.key32,
            )
        return self._image_parser

    @property
    def video(self) -> WechatVideoParser:
        if self._video_parser is None:
            if self.message_resource_db_path is None or self.hardlink_db_path is None:
                raise ValueError("video manager requires message_resource_db_path and hardlink_db_path")
            self._video_parser = WechatVideoParser(
                message_db_path=self.message_db_path,
                message_resource_db_path=self.message_resource_db_path,
                hardlink_db_path=self.hardlink_db_path,
                account_root=self.account_root,
            )
        return self._video_parser

    @property
    def voice(self) -> WechatVoiceParser:
        if self._voice_parser is None:
            if self.media_db_path is None:
                raise ValueError("voice manager requires media_db_path")
            self._voice_parser = WechatVoiceParser(
                message_db_path=self.message_db_path,
                media_db_path=self.media_db_path,
            )
        return self._voice_parser

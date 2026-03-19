from __future__ import annotations

from pathlib import Path
from typing import Literal

from image_process import ImageSummary, WechatImageParser
from video_process import VideoSummary, WechatVideoParser
from voice_process import VoiceSummary, WechatVoiceParser


MediaKind = Literal["image", "video", "voice"]


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
        silk_decoder_path: Path | None = None,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.account_root = Path(account_root)
        self.message_resource_db_path = (
            None if message_resource_db_path is None else Path(message_resource_db_path)
        )
        self.media_db_path = None if media_db_path is None else Path(media_db_path)
        self.hardlink_db_path = None if hardlink_db_path is None else Path(hardlink_db_path)
        self.key32 = key32
        self._legacy_silk_decoder_path = (
            None if silk_decoder_path is None else Path(silk_decoder_path)
        )

        self._image_parser: WechatImageParser | None = None
        self._video_parser: WechatVideoParser | None = None
        self._voice_parser: WechatVoiceParser | None = None

    def find_media_paths(self, media_kind: MediaKind, msg_table: str, local_id: int) -> dict[str, object]:
        if media_kind == "image":
            return self.find_image_paths(msg_table, local_id)
        if media_kind == "video":
            return self.find_video_paths(msg_table, local_id)
        if media_kind == "voice":
            return self.find_voice_paths(msg_table, local_id)
        raise ValueError(f"unsupported media_kind: {media_kind}")

    def find_media_summary(
        self,
        media_kind: MediaKind,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> ImageSummary | VideoSummary | VoiceSummary:
        if media_kind == "image":
            return self.find_image_summary(msg_table, local_id, output_dir)
        if media_kind == "video":
            return self.find_video_summary(msg_table, local_id, output_dir)
        if media_kind == "voice":
            return self.find_voice_summary(msg_table, local_id, output_dir)
        raise ValueError(f"unsupported media_kind: {media_kind}")

    def find_image_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        return self.image.find_image_paths(msg_table, local_id)

    def find_image_summary(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> ImageSummary:
        return self.image.find_image_summary(msg_table, local_id, output_dir)

    def find_video_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        return self.video.find_video_paths(msg_table, local_id)

    def find_video_summary(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> VideoSummary:
        return self.video.find_video_summary(msg_table, local_id, output_dir)

    def find_voice_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        return self.voice.find_voice_paths(msg_table, local_id)

    def find_voice_summary(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> VoiceSummary:
        return self.voice.find_voice_summary(msg_table, local_id, output_dir)

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
                silk_decoder_path=self._legacy_silk_decoder_path,
            )
        return self._voice_parser

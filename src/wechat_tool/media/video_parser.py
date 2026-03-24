"""微信视频消息定位、本地文件探测与导出能力。"""

from __future__ import annotations

import os
import sqlite3
import struct
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import av
from dotenv import load_dotenv


@dataclass
class VideoSummary:
    msg_table: str
    local_id: int
    server_id: int
    create_time: int
    month_dir: str
    file_base: str
    play_path: str | None
    raw_path: str | None
    thumb_path: str | None
    poster_path: str | None
    has_play: bool
    has_raw: bool
    has_dual_mp4: bool
    resource_roles: dict[int, str]
    best_video_path: str | None
    best_video_size: int | None
    best_video_layout: str | None
    duration: str | None
    video_codec: str | None
    video_profile: str | None
    width: int | None
    height: int | None
    frame_rate: str | None
    video_bit_rate: str | None
    audio_codec: str | None
    audio_bit_rate: str | None
    play_raw_diff: dict[str, object] | None
    exported_play_path: str | None = None
    exported_raw_path: str | None = None
    exported_thumb_path: str | None = None
    exported_poster_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WechatVideoParser:
    """封装微信视频消息的数据库定位与本地文件探测。"""

    TAIL_SCAN_BYTES = 1024 * 1024

    def __init__(
        self,
        message_db_path: Path,
        message_resource_db_path: Path,
        hardlink_db_path: Path,
        account_root: Path | None = None,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.message_resource_db_path = Path(message_resource_db_path)
        self.hardlink_db_path = Path(hardlink_db_path)
        self.account_root = Path(account_root) if account_root is not None else None

    @classmethod
    def from_env(cls) -> WechatVideoParser:
        load_dotenv()
        decrypted_db_dir = (
            Path("data/db/decrypted")
            if Path("data/db/decrypted").exists()
            else Path("data/db/dec")
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
            hardlink_db_path=Path(
                os.getenv("HARDLINK_DB_PATH", str(decrypted_db_dir / "hardlink.db"))
            ).expanduser(),
            account_root=Path(os.environ["WECHAT_ROOT"]).expanduser(),
        )

    def find_video_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        with sqlite3.connect(self.message_db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "ATTACH DATABASE ? AS message_resource",
                (str(self.message_resource_db_path),),
            )

            message = self._fetch_message(conn, msg_table, local_id)
            resource_info = self._fetch_resource_info(conn, message)
            resource_details = (
                self._fetch_resource_details(conn, resource_info["message_id"])
                if resource_info is not None
                else []
            )

        file_base = self._extract_file_base(message["packed_info_data"])
        month_dir = self._format_month_dir(message["create_time"])
        hardlink_entries = self._fetch_hardlink_entries(file_base, month_dir)

        preview_name = f"{file_base}.jpg"
        thumb_name = f"{file_base}_thumb.jpg"
        video_name = f"{file_base}.mp4"
        raw_video_name = f"{file_base}_raw.mp4"
        candidate_paths = (
            self._build_candidate_paths(
                self.account_root,
                msg_table.removeprefix("Msg_"),
                month_dir,
                preview_name,
                thumb_name,
                video_name,
                raw_video_name,
            )
            if self.account_root is not None
            else {}
        )

        file_inspection = {}
        for key, path in candidate_paths.items():
            file_inspection[key] = self.inspect_media_file(path)

        preferred_paths = self._pick_preferred_paths(candidate_paths)
        variant_summary = self._summarize_variants(preferred_paths, file_inspection)

        return {
            "message": dict(message),
            "resource_info": dict(resource_info) if resource_info is not None else None,
            "resource_details": [dict(row) for row in resource_details],
            "resource_roles": {
                row["type"]: self._resource_type_name(row["type"]) for row in resource_details
            },
            "file_base": file_base,
            "month_dir": month_dir,
            "poster_file_name": preview_name,
            "thumb_file_name": thumb_name,
            "video_file_name": video_name,
            "raw_video_file_name": raw_video_name,
            "hardlink_entries": hardlink_entries,
            "candidate_paths": {key: str(path) for key, path in candidate_paths.items()},
            "existing_paths": {
                key: str(path) for key, path in candidate_paths.items() if Path(path).exists()
            },
            "preferred_paths": {key: str(path) for key, path in preferred_paths.items() if path is not None},
            "file_inspection": file_inspection,
            "file_inspect": file_inspection,
            "variant_summary": variant_summary,
        }

    def export_video_assets(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, str]:
        detail = self.find_video_paths(msg_table, local_id)
        preferred_paths = detail["preferred_paths"]
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{msg_table}_{local_id}"
        exported: dict[str, str] = {}
        variants = {
            "play": preferred_paths.get("play"),
            "raw": preferred_paths.get("raw"),
            "thumb": preferred_paths.get("thumb"),
            "poster": preferred_paths.get("poster"),
        }
        for variant, source in variants.items():
            if not source:
                continue
            src = Path(source)
            if not src.exists():
                continue
            target = output_dir / f"{base_name}{src.suffix}"
            if variant in {"thumb", "poster"} and src.suffix.lower() == ".jpg":
                target = output_dir / f"{base_name}_{variant}.jpg"
            elif variant == "raw":
                target = output_dir / f"{base_name}_raw{src.suffix}"
            shutil.copy2(src, target)
            exported[variant] = str(target)
        return exported

    def find_video_summary(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> VideoSummary:
        detail = self.find_video_paths(msg_table, local_id)
        message = detail["message"]
        preferred_paths = detail["preferred_paths"]
        variant_summary = detail["variant_summary"]
        resource_roles = detail["resource_roles"]
        file_inspection = detail["file_inspection"]

        play_path = preferred_paths.get("play")
        raw_path = preferred_paths.get("raw")
        thumb_path = preferred_paths.get("thumb")
        poster_path = preferred_paths.get("poster")

        play_info = self._find_inspection_result(Path(play_path), file_inspection) if play_path else None
        raw_info = self._find_inspection_result(Path(raw_path), file_inspection) if raw_path else None

        best_video_info = play_info or raw_info
        best_probe = (
            best_video_info.get("media_metadata")
            if isinstance(best_video_info, dict)
            else None
        )
        best_video_stream = (
            self._select_stream(best_probe, "video")
            if isinstance(best_probe, dict)
            else None
        )
        best_audio_stream = (
            self._select_stream(best_probe, "audio")
            if isinstance(best_probe, dict)
            else None
        )
        best_format = best_probe.get("format", {}) if isinstance(best_probe, dict) else {}
        exported = self.export_video_assets(msg_table, local_id, output_dir) if output_dir is not None else {}

        return VideoSummary(
            msg_table=msg_table,
            local_id=local_id,
            server_id=message["server_id"],
            create_time=message["create_time"],
            month_dir=detail["month_dir"],
            file_base=detail["file_base"],
            play_path=play_path,
            raw_path=raw_path,
            thumb_path=thumb_path,
            poster_path=poster_path,
            has_play=variant_summary["has_play"],
            has_raw=variant_summary["has_raw"],
            has_dual_mp4=variant_summary["has_dual_mp4"],
            resource_roles=resource_roles,
            best_video_path=play_path or raw_path,
            best_video_size=None if best_video_info is None else best_video_info.get("file_size"),
            best_video_layout=None if best_video_info is None else best_video_info.get("mp4_layout"),
            duration=best_format.get("duration"),
            video_codec=None if best_video_stream is None else best_video_stream.get("codec_name"),
            video_profile=None if best_video_stream is None else best_video_stream.get("profile"),
            width=None if best_video_stream is None else best_video_stream.get("width"),
            height=None if best_video_stream is None else best_video_stream.get("height"),
            frame_rate=None if best_video_stream is None else best_video_stream.get("avg_frame_rate"),
            video_bit_rate=None if best_video_stream is None else best_video_stream.get("bit_rate"),
            audio_codec=None if best_audio_stream is None else best_audio_stream.get("codec_name"),
            audio_bit_rate=None if best_audio_stream is None else best_audio_stream.get("bit_rate"),
            play_raw_diff=variant_summary.get("play_vs_raw"),
            exported_play_path=exported.get("play"),
            exported_raw_path=exported.get("raw"),
            exported_thumb_path=exported.get("thumb"),
            exported_poster_path=exported.get("poster"),
        )

    def inspect_media_file(self, path: Path) -> dict[str, object]:
        path = Path(path)
        if not path.exists():
            return {
                "exists": False,
                "path": str(path),
            }

        file_size = path.stat().st_size
        with path.open("rb") as fp:
            head = fp.read(4096)
            tail = b""
            if file_size > 4096:
                tail_size = min(file_size, self.TAIL_SCAN_BYTES)
                fp.seek(max(0, file_size - tail_size))
                tail = fp.read(tail_size)
        info = {
            "exists": True,
            "path": str(path),
            "file_size": file_size,
            "header_hex": head[:32].hex(),
            "file_type": self._detect_file_type(head),
        }
        if info["file_type"] == "mp4":
            info["mp4_head_boxes"] = self._parse_mp4_boxes(head)
            info["mp4_tail_moov_offset"] = self._find_tail_box(tail, "moov", file_size)
            info["mp4_tail_mdat_offset"] = self._find_tail_box(tail, "mdat", file_size)
            info["mp4_layout"] = self._classify_mp4_layout(info["mp4_head_boxes"], info["mp4_tail_moov_offset"])
            info["media_metadata"] = self._probe_media_metadata(path)
            info["ffprobe"] = info["media_metadata"]
        return info

    def inspect_file(self, path: Path) -> dict[str, object]:
        return self.inspect_media_file(path)

    @staticmethod
    def _build_candidate_paths(
        account_root: Path,
        chat_md5: str,
        month_dir: str,
        preview_name: str,
        thumb_name: str,
        video_name: str,
        raw_video_name: str,
    ) -> dict[str, Path]:
        file_names = {
            "preview_jpg": preview_name,
            "preview_thumb_jpg": thumb_name,
            "play_mp4": video_name,
            "raw_mp4": raw_video_name,
        }
        roots = {
            "msg_video_month": Path("msg") / "video" / month_dir,
            "msg_attach_video_chat": Path("msg") / "attach" / chat_md5 / month_dir / "Video",
            "msg_attach_video_chat_lower": Path("msg") / "attach" / chat_md5 / month_dir / "video",
        }

        candidates: dict[str, Path] = {}
        for root_key, relative_root in roots.items():
            for file_key, file_name in file_names.items():
                candidates[f"{root_key}:{file_key}"] = account_root / relative_root / file_name
        return candidates

    @staticmethod
    def _extract_file_base(packed_info_data: bytes | str | None) -> str:
        if not packed_info_data:
            raise ValueError("packed_info_data is empty")

        data = (
            packed_info_data.encode("utf-8", errors="ignore")
            if isinstance(packed_info_data, str)
            else packed_info_data
        )

        hex_chars: list[str] = []
        for byte in data:
            char = chr(byte)
            if char in "0123456789abcdefABCDEF":
                hex_chars.append(char.lower())
                if len(hex_chars) == 32:
                    return "".join(hex_chars)
            else:
                hex_chars.clear()

        raise ValueError("failed to extract video file base from packed_info_data")

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
        if row["local_type"] != 43:
            raise ValueError(
                f"message is not a video: table={msg_table}, local_id={local_id}, local_type={row['local_type']}"
            )
        return row

    @staticmethod
    def _fetch_resource_info(conn: sqlite3.Connection, message: sqlite3.Row) -> sqlite3.Row | None:
        query = """
            SELECT
                message_id,
                chat_id,
                sender_id,
                message_local_type,
                message_create_time,
                message_local_id,
                message_svr_id
            FROM message_resource.MessageResourceInfo
            WHERE message_local_id = ?
              AND message_svr_id = ?
              AND message_create_time = ?
              AND message_local_type = ?
        """
        return conn.execute(
            query,
            (
                message["local_id"],
                message["server_id"],
                message["create_time"],
                message["local_type"],
            ),
        ).fetchone()

    @staticmethod
    def _fetch_resource_details(conn: sqlite3.Connection, message_id: int) -> list[sqlite3.Row]:
        return conn.execute(
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

    def _fetch_hardlink_entries(self, file_base: str, month_dir: str) -> list[dict[str, object]]:
        with sqlite3.connect(self.hardlink_db_path) as conn:
            conn.row_factory = sqlite3.Row
            month_row = conn.execute(
                "SELECT rowid FROM dir2id WHERE username = ?",
                (month_dir,),
            ).fetchone()
            if month_row is None:
                return []

            rows = conn.execute(
                """
                SELECT file_name, file_size, modify_time, md5, type, dir1, dir2
                FROM video_hardlink_info_v4
                WHERE dir1 = ?
                  AND file_name IN (?, ?, ?, ?)
                ORDER BY file_name
                """,
                (
                    month_row["rowid"],
                    f"{file_base}.jpg",
                    f"{file_base}_thumb.jpg",
                    f"{file_base}.mp4",
                    f"{file_base}_raw.mp4",
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _detect_file_type(data: bytes) -> str:
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "mp4"
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "webp"
        return "binary"

    @staticmethod
    def _parse_mp4_boxes(data: bytes) -> list[dict[str, object]]:
        boxes: list[dict[str, object]] = []
        offset = 0
        while offset + 8 <= len(data):
            size = struct.unpack(">I", data[offset : offset + 4])[0]
            box_type = data[offset + 4 : offset + 8].decode("ascii", errors="replace")
            if size == 0:
                boxes.append(
                    {
                        "type": box_type,
                        "offset": offset,
                        "size": len(data) - offset,
                    }
                )
                break
            if size == 1:
                if offset + 16 > len(data):
                    break
                size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
                header_size = 16
            else:
                header_size = 8
            if size < header_size:
                break
            boxes.append(
                {
                    "type": box_type,
                    "offset": offset,
                    "size": size,
                }
            )
            offset += size
        return boxes

    @staticmethod
    def _find_tail_box(data: bytes, box_type: str, file_size: int) -> int | None:
        if not data:
            return None
        index = data.find(box_type.encode("ascii"))
        if index < 4:
            return None
        box_offset = file_size - len(data) + index - 4
        return box_offset if box_offset >= 0 else None

    @staticmethod
    def _classify_mp4_layout(
        head_boxes: list[dict[str, object]],
        tail_moov_offset: int | None,
    ) -> str:
        head_types = [str(box["type"]) for box in head_boxes]
        if "moov" in head_types:
            return "faststart_or_front_moov"
        if "mdat" in head_types and tail_moov_offset is not None:
            return "mdat_front_tail_moov"
        if "mdat" in head_types:
            return "mdat_front"
        return "unknown"

    @staticmethod
    def _resource_type_name(resource_type: int) -> str:
        type_map = {
            65538: "raw_video_mp4",
            131074: "play_video_mp4",
            196610: "thumb_jpg",
        }
        return type_map.get(resource_type, f"unknown_{resource_type}")

    @staticmethod
    def _pick_preferred_paths(candidate_paths: dict[str, Path]) -> dict[str, Path | None]:
        priorities = {
            "poster": [
                "msg_video_month:preview_jpg",
                "msg_video_month:preview_thumb_jpg",
                "msg_attach_video_chat:preview_jpg",
                "msg_attach_video_chat_lower:preview_jpg",
            ],
            "thumb": [
                "msg_video_month:preview_thumb_jpg",
                "msg_video_month:preview_jpg",
                "msg_attach_video_chat:preview_thumb_jpg",
                "msg_attach_video_chat_lower:preview_thumb_jpg",
            ],
            "play": [
                "msg_video_month:play_mp4",
                "msg_attach_video_chat:play_mp4",
                "msg_attach_video_chat_lower:play_mp4",
            ],
            "raw": [
                "msg_video_month:raw_mp4",
                "msg_attach_video_chat:raw_mp4",
                "msg_attach_video_chat_lower:raw_mp4",
            ],
        }
        preferred: dict[str, Path | None] = {}
        for target, keys in priorities.items():
            chosen = None
            for key in keys:
                path = candidate_paths.get(key)
                if path is not None and path.exists():
                    chosen = path
                    break
            preferred[target] = chosen
        return preferred

    @classmethod
    def _summarize_variants(
        cls,
        preferred_paths: dict[str, Path | None],
        file_inspection: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        play_path = preferred_paths.get("play")
        raw_path = preferred_paths.get("raw")
        play_info = cls._find_inspection_result(play_path, file_inspection)
        raw_info = cls._find_inspection_result(raw_path, file_inspection)
        return {
            "has_play": play_info is not None,
            "has_raw": raw_info is not None,
            "has_dual_mp4": play_info is not None and raw_info is not None,
            "play_layout": None if play_info is None else play_info.get("mp4_layout"),
            "raw_layout": None if raw_info is None else raw_info.get("mp4_layout"),
            "play_vs_raw": cls._diff_media_metadata(play_info, raw_info),
        }

    @staticmethod
    def _find_inspection_result(
        path: Path | None,
        file_inspection: dict[str, dict[str, object]],
    ) -> dict[str, object] | None:
        if path is None:
            return None
        path_str = str(path)
        for info in file_inspection.values():
            if info.get("path") == path_str and info.get("exists"):
                return info
        return None

    @classmethod
    def _diff_media_metadata(
        cls,
        play_info: dict[str, object] | None,
        raw_info: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if play_info is None or raw_info is None:
            return None

        play_probe = play_info.get("media_metadata")
        raw_probe = raw_info.get("media_metadata")
        if not isinstance(play_probe, dict) or not isinstance(raw_probe, dict):
            return None

        play_video = cls._select_stream(play_probe, "video")
        raw_video = cls._select_stream(raw_probe, "video")
        play_audio = cls._select_stream(play_probe, "audio")
        raw_audio = cls._select_stream(raw_probe, "audio")
        play_format = play_probe.get("format", {})
        raw_format = raw_probe.get("format", {})

        return {
            "play_size": play_info.get("file_size"),
            "raw_size": raw_info.get("file_size"),
            "size_ratio": cls._safe_ratio(raw_info.get("file_size"), play_info.get("file_size")),
            "play_duration": play_format.get("duration"),
            "raw_duration": raw_format.get("duration"),
            "play_video_codec": None if play_video is None else play_video.get("codec_name"),
            "raw_video_codec": None if raw_video is None else raw_video.get("codec_name"),
            "play_video_profile": None if play_video is None else play_video.get("profile"),
            "raw_video_profile": None if raw_video is None else raw_video.get("profile"),
            "play_video_bitrate": None if play_video is None else play_video.get("bit_rate"),
            "raw_video_bitrate": None if raw_video is None else raw_video.get("bit_rate"),
            "play_video_frame_rate": None if play_video is None else play_video.get("avg_frame_rate"),
            "raw_video_frame_rate": None if raw_video is None else raw_video.get("avg_frame_rate"),
            "play_audio_bitrate": None if play_audio is None else play_audio.get("bit_rate"),
            "raw_audio_bitrate": None if raw_audio is None else raw_audio.get("bit_rate"),
        }

    @staticmethod
    def _select_stream(media_metadata: dict[str, object], codec_type: str) -> dict[str, object] | None:
        streams = media_metadata.get("streams")
        if not isinstance(streams, list):
            return None
        for stream in streams:
            if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
                return stream
        return None

    @staticmethod
    def _safe_ratio(numerator: object, denominator: object) -> float | None:
        try:
            num = float(numerator)
            den = float(denominator)
        except (TypeError, ValueError):
            return None
        if den == 0:
            return None
        return num / den

    @staticmethod
    def _probe_media_metadata(path: Path) -> dict[str, object] | None:
        try:
            with av.open(str(path), mode="r") as container:
                streams: list[dict[str, object]] = []
                for stream in container.streams:
                    codec_context = stream.codec_context
                    stream_info: dict[str, object] = {
                        "index": stream.index,
                        "codec_type": stream.type,
                        "codec_name": codec_context.name,
                        "profile": codec_context.profile,
                        "bit_rate": codec_context.bit_rate,
                    }
                    if stream.type == "video":
                        stream_info.update(
                            {
                                "width": codec_context.width,
                                "height": codec_context.height,
                                "avg_frame_rate": (
                                    None if stream.average_rate is None else str(stream.average_rate)
                                ),
                            }
                        )
                    elif stream.type == "audio":
                        stream_info.update(
                            {
                                "sample_rate": codec_context.sample_rate,
                                "channels": codec_context.channels,
                            }
                        )
                    streams.append(stream_info)

                duration = None
                if container.duration is not None:
                    duration = str(container.duration / av.time_base)

                bit_rate = container.bit_rate
                return {
                    "format": {
                        "duration": duration,
                        "bit_rate": None if bit_rate is None else str(bit_rate),
                    },
                    "streams": streams,
                }
        except (FileNotFoundError, av.FFmpegError, OSError):
            return None

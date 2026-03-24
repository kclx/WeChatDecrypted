"""聊天记录导出服务。"""

from __future__ import annotations

import av
import csv
import sqlite3
from pathlib import Path

from tqdm import tqdm

from src.wechat_tool.common.models import ContactInfo, RawMessage
from src.wechat_tool.common.service_base import WechatServiceBase


class WechatChatExportService(WechatServiceBase):
    """负责联系人消息读取、媒体补全和导出落盘。"""

    def export_by_contact_name(
        self,
        contact_name_keyword: str,
        output_path: Path | None = None,
        *,
        output_format: str = "csv",
        limit: int | None = None,
    ) -> Path:
        """按输出格式分发聊天导出。"""
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
        """导出处理后的聊天记录为 CSV。"""
        contact_info, csv_rows = self._collect_export_rows(
            contact_name_keyword, limit=limit
        )
        output_csv_path = self._resolve_output_path(
            output_path=output_csv_path,
            default_dir=self.export_dir / "csv",
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
        """导出处理后的聊天记录到 SQLite。"""
        contact_info, export_rows = self._collect_export_rows(
            contact_name_keyword, limit=limit
        )
        output_sqlite_path = self._resolve_messages_db_path(output_sqlite_path)
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
                """.format(
                    table_name=quoted_table_name
                )
            )

            cursor.executemany(
                """
                INSERT INTO {table_name} (
                    local_id, sender, wxid, remark, msg_type, msg_time, msg
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """.format(
                    table_name=quoted_table_name
                ),
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
            self.current_contact_display_name = self._sanitize_file_stem(
                contact_info.display_name
            )
            self.real_sender_wxid_mapper = {}
            rows = self._fetch_message_rows(
                message_cursor, contact_info.table_name, limit
            )
            text_rows = self._build_text_result_rows(
                message_conn=message_conn,
                contact_conn=contact_conn,
                contact_info=contact_info,
                table_name=contact_info.table_name,
                rows=rows,
            )
            media_rows = self._build_media_result_rows(
                message_conn=message_conn,
                contact_conn=contact_conn,
                contact_info=contact_info,
                table_name=contact_info.table_name,
                rows=rows,
                text_rows=text_rows,
            )
            return (
                contact_info,
                self._merge_export_rows(rows, text_rows, media_rows),
            )

    @classmethod
    def _fetch_message_rows(
        cls,
        message_cursor: sqlite3.Cursor,
        table_name: str,
        limit: int | None,
    ) -> list[RawMessage]:
        sql = (
            f"SELECT local_id, real_sender_id, local_type, create_time, message_content "
            f"FROM [{table_name}] "
            f"WHERE local_type IN (1, 3, 34, 43) "
            f"ORDER BY sort_seq ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [
            RawMessage(
                local_id=int(row[0]),
                real_sender_id=int(row[1]),
                local_type=int(row[2]),
                create_time=int(row[3]),
                message_content=row[4],
            )
            for row in message_cursor.execute(sql).fetchall()
        ]

    def _build_text_result_rows(
        self,
        message_conn: sqlite3.Connection,
        contact_conn: sqlite3.Connection,
        contact_info: ContactInfo,
        table_name: str,
        rows: list[RawMessage],
    ) -> dict[int, dict[str, object]]:
        text_messages = [row for row in rows if row.local_type == 1]
        export_rows: dict[int, dict[str, object]] = {}
        progress = tqdm(
            text_messages,
            desc=f"导入文本 {contact_info.display_name}",
            unit="msg",
            dynamic_ncols=True,
        )
        for row in progress:
            sender, wxid = self._get_sender_info(
                message_conn=message_conn,
                contact_conn=contact_conn,
                table_name=table_name,
                contact_info=contact_info,
                local_id=row.local_id,
                real_sender_id=row.real_sender_id,
                message_content=row.message_content,
            )
            export_rows[row.local_id] = self._compose_export_row(
                local_id=row.local_id,
                sender=sender,
                wxid=wxid,
                remark="",
                msg_type=self.MSG_TYPE_MAPPER.get(row.local_type, str(row.local_type)),
                msg_time=self._format_timestamp(row.create_time),
                msg=self._build_text_message(row.message_content),
            )
        return export_rows

    def _build_media_result_rows(
        self,
        message_conn: sqlite3.Connection,
        contact_conn: sqlite3.Connection,
        contact_info: ContactInfo,
        table_name: str,
        rows: list[RawMessage],
        *,
        text_rows: dict[int, dict[str, object]],
    ) -> dict[int, dict[str, object]]:
        media_messages = [row for row in rows if row.local_type != 1]
        export_rows: dict[int, dict[str, object]] = {}
        progress = tqdm(
            media_messages,
            desc=f"处理媒体 {contact_info.display_name}",
            unit="msg",
            dynamic_ncols=True,
        )
        for row in progress:
            sender, wxid = self._get_sender_info(
                message_conn=message_conn,
                contact_conn=contact_conn,
                table_name=table_name,
                contact_info=contact_info,
                local_id=row.local_id,
                real_sender_id=row.real_sender_id,
                message_content=None,
            )
            msg, remark = self._build_media_message_and_remark(
                table_name=table_name,
                local_id=row.local_id,
                local_type=row.local_type,
                message_content=row.message_content,
                text_context=self._build_text_context(
                    row.local_id,
                    text_rows,
                ),
            )
            export_rows[row.local_id] = self._compose_export_row(
                local_id=row.local_id,
                sender=sender,
                wxid=wxid,
                remark=remark,
                msg_type=self.MSG_TYPE_MAPPER.get(row.local_type, str(row.local_type)),
                msg_time=self._format_timestamp(row.create_time),
                msg=msg,
            )
        return export_rows

    @staticmethod
    def _merge_export_rows(
        rows: list[RawMessage],
        text_rows: dict[int, dict[str, object]],
        media_rows: dict[int, dict[str, object]],
    ) -> list[dict[str, object]]:
        export_rows: list[dict[str, object]] = []
        for row in rows:
            if row.local_type == 1:
                export_rows.append(text_rows[row.local_id])
                continue
            export_rows.append(media_rows[row.local_id])
        return export_rows

    @staticmethod
    def _compose_export_row(
        *,
        local_id: int,
        sender: str,
        wxid: str,
        remark: str,
        msg_type: str,
        msg_time: str,
        msg: str,
    ) -> dict[str, object]:
        return {
            "local_id": local_id,
            "sender": sender,
            "wxid": wxid,
            "remark": remark,
            "msg_type": msg_type,
            "msg_time": msg_time,
            "msg": msg,
        }

    def _build_text_message(self, message_content: str | bytes) -> str:
        """提取文本消息正文。"""
        _, text_content = self._split_sender_and_text(message_content)
        if text_content:
            return text_content
        if isinstance(message_content, bytes):
            return f"[未识别字节消息] size={len(message_content)}"
        return self._decode_message_content(message_content).strip()

    def _build_media_message_and_remark(
        self,
        table_name: str,
        local_id: int,
        local_type: int,
        message_content: str | bytes,
        *,
        text_context: str,
    ) -> tuple[str, str]:
        """导出媒体文件，并在可用时补充 AI 备注。"""
        media_base_dir = self.export_dir / "media" / self.current_contact_display_name
        if local_type == 3:
            image_path = self.media_manager.export_image(
                table_name, local_id, media_base_dir / "img"
            )
            return image_path, self._build_image_remark(image_path, text_context)
        if local_type == 34:
            voice_path = self.media_manager.export_voice(
                table_name, local_id, media_base_dir / "voice"
            )
            return voice_path, self._build_voice_remark(voice_path, text_context)
        if local_type == 43:
            video_path = self.media_manager.export_video(
                table_name, local_id, media_base_dir / "video"
            )
            return video_path, self._build_video_remark(video_path, text_context)
        return self._decode_message_content(message_content), ""

    def _build_image_remark(
        self,
        image_path: str,
        text_context: str,
    ) -> str:
        if self.ai_client is None:
            return ""
        prompt = (
            "你正在整理微信聊天导出。请用中文简洁描述这张图片的主要内容，并结合给出的上文判断它在当前对话里的可能含义。"
            "如果上文帮助不大，就只描述图片可见内容。输出控制在2到4句。"
        )
        if text_context:
            prompt += f"\n\n聊天上下文（按时间顺序）：\n{text_context}"
        return self._safe_describe_image(
            image_path,
            prompt,
            model_spec=self.image_model_spec,
        )

    def _build_voice_remark(self, voice_path: str, text_context: str) -> str:
        if self.ai_client is None:
            return ""
        transcript = self._safe_transcribe_audio(
            voice_path,
            model_spec=self.audio_model_spec,
        )
        if not transcript:
            return ""
        if not text_context:
            return transcript
        prompt = (
            "你正在整理微信聊天导出。下面提供语音转写结果和聊天上下文，请用中文整理成适合写入备注的简洁内容。"
            "要求保留语音核心信息，必要时结合上下文补全指代，但不要编造转写里没有出现的事实。"
            "输出控制在1到3句。\n\n"
            f"聊天上下文（按时间顺序）：\n{text_context}\n\n"
            f"语音转写：\n{transcript}"
        )
        return self._safe_chat(prompt, model_spec=self.profile_model_spec) or transcript

    def _build_video_remark(
        self,
        video_path: str,
        text_context: str,
    ) -> str:
        if self.ai_client is None:
            return ""
        preview_image = self._resolve_video_preview_image(Path(video_path))
        if preview_image is None:
            return ""
        prompt = (
            "你正在整理微信聊天导出。请根据这个视频封面或抽帧，用中文简洁说明视频大致内容；"
            "若上文能帮助理解场景，请一并考虑。输出控制在2到4句。"
        )
        if text_context:
            prompt += f"\n\n聊天上下文（按时间顺序）：\n{text_context}"
        return self._safe_describe_image(
            preview_image,
            prompt,
            model_spec=self.video_model_spec,
        )

    @staticmethod
    def _build_text_context(
        target_local_id: int,
        text_rows: dict[int, dict[str, object]],
        *,
        max_items: int = 5,
    ) -> str:
        ordered_ids = sorted(text_rows)
        if not ordered_ids:
            return ""

        before_target = [
            local_id for local_id in ordered_ids if local_id < target_local_id
        ]
        after_target = [
            local_id for local_id in ordered_ids if local_id > target_local_id
        ]
        before_limit = (max_items + 1) // 2
        after_limit = max_items // 2

        selected_ids = before_target[-before_limit:] + after_target[:after_limit]
        remaining = max_items - len(selected_ids)
        if remaining > 0:
            extra_before = before_target[: -before_limit or None]
            extra_after = after_target[after_limit:]
            selected_ids = extra_before[-remaining:] + selected_ids
            remaining = max_items - len(selected_ids)
            if remaining > 0:
                selected_ids.extend(extra_after[:remaining])

        context_lines: list[str] = []
        for local_id in selected_ids:
            row = text_rows[local_id]
            sender = str(row.get("sender", "")).strip()
            msg = str(row.get("msg", "")).strip()
            if not msg:
                continue
            context_lines.append(f"{sender}: {msg}")
        return "\n".join(context_lines)

    def _resolve_video_preview_image(self, video_path: Path) -> Path | None:
        video_path = Path(video_path)
        if video_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return video_path
        if video_path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            return None

        frame_dir = (
            self.export_dir
            / "media"
            / self.current_contact_display_name
            / "video"
            / "frames"
        )
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

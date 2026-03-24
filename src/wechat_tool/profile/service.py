"""联系人画像分析服务。"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.wechat_tool.common.models import (
    CONTACT_TYPE_CHATROOM,
    ContactInfo,
    PROFILE_ANALYSIS_VERSION,
    PROFILE_FIELD_SPECS,
    PROFILE_KEYWORDS,
    PROFILE_STATUS_KNOWN,
    PROFILE_STATUS_UNKNOWN,
)
from src.wechat_tool.common.service_base import WechatServiceBase
from src.wechat_tool.export.service import WechatChatExportService


class WechatContactProfileService(WechatServiceBase):
    """负责读取处理后消息、切片分析并写入联系人画像。"""

    def __init__(
        self,
        message_db_path: Path,
        contact_db_path: Path,
        media_manager,
        self_wxid: str = "",
        ai_client=None,
        image_model_spec: str = "",
        video_model_spec: str = "",
        audio_model_spec: str = "",
        profile_model_spec: str = "",
        export_dir: Path | None = None,
        export_service: WechatChatExportService | None = None,
    ) -> None:
        super().__init__(
            message_db_path=message_db_path,
            contact_db_path=contact_db_path,
            media_manager=media_manager,
            self_wxid=self_wxid,
            ai_client=ai_client,
            image_model_spec=image_model_spec,
            video_model_spec=video_model_spec,
            audio_model_spec=audio_model_spec,
            profile_model_spec=profile_model_spec,
            export_dir=export_dir,
        )
        self.export_service = export_service

    def analyze_contact_profiles(
        self,
        keyword: str,
        output_sqlite_path: Path | None = None,
        *,
        slice_size: int = 500,
        limit: int | None = None,
        reset_existing: bool = False,
    ) -> Path:
        """执行“我/对方”双向画像分析。"""
        if self.ai_client is None:
            raise ValueError("AI client is required for profile analysis")
        if not self.self_wxid:
            raise ValueError("WXID is required for profile analysis")
        if slice_size <= 0:
            raise ValueError("slice_size must be positive")

        output_sqlite_path = self._resolve_messages_db_path(output_sqlite_path)
        output_sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        table_name = self._find_existing_message_table(output_sqlite_path, keyword)
        contact_info: ContactInfo | None = None

        with sqlite3.connect(self.contact_db_path) as contact_conn:
            contact_cursor = contact_conn.cursor()
            if table_name is None:
                contact_info = self._find_contact_info(contact_cursor, keyword)
                if contact_info.contact_type == CONTACT_TYPE_CHATROOM:
                    raise NotImplementedError(
                        "chatroom profile analysis is not implemented yet"
                    )
                table_name = self._resolve_sqlite_table_name(contact_info)
                if not self._table_exists_on_path(output_sqlite_path, table_name):
                    self._get_export_service().export_by_contact_name_to_sqlite(
                        contact_info.username,
                        output_sqlite_path=output_sqlite_path,
                        limit=limit,
                    )
            with sqlite3.connect(output_sqlite_path) as out_conn:
                self._ensure_contact_profiles_table(out_conn)
                if table_name is None:
                    raise ValueError(f"message table not found: {keyword}")

                messages = self._load_processed_messages(
                    out_conn,
                    table_name,
                    limit=limit,
                )
                if not messages:
                    raise ValueError(f"message table is empty: {table_name}")

                peer_wxid = self._resolve_peer_wxid(messages)
                if not peer_wxid:
                    raise ValueError(f"unable to resolve peer wxid from table: {table_name}")

                if contact_info is None:
                    contact_info = self._build_contact_info_from_table(
                        contact_cursor,
                        table_name,
                        peer_wxid,
                    )
                elif contact_info.contact_type == CONTACT_TYPE_CHATROOM:
                    raise NotImplementedError(
                        "chatroom profile analysis is not implemented yet"
                    )

                self._analyze_dual_profiles(
                    out_conn=out_conn,
                    contact_cursor=contact_cursor,
                    contact_info=contact_info,
                    table_name=table_name,
                    messages=messages,
                    slice_size=slice_size,
                    reset_existing=reset_existing,
                )
                out_conn.commit()

        return output_sqlite_path

    def _get_export_service(self) -> WechatChatExportService:
        """延迟获取导出服务，避免画像层直接依赖导出实现细节。"""
        if self.export_service is None:
            self.export_service = WechatChatExportService(
                message_db_path=self.message_db_path,
                contact_db_path=self.contact_db_path,
                media_manager=self.media_manager,
                self_wxid=self.self_wxid,
                ai_client=self.ai_client,
                image_model_spec=self.image_model_spec,
                video_model_spec=self.video_model_spec,
                audio_model_spec=self.audio_model_spec,
                profile_model_spec=self.profile_model_spec,
                export_dir=self.export_dir,
            )
        return self.export_service

    def _analyze_dual_profiles(
        self,
        *,
        out_conn: sqlite3.Connection,
        contact_cursor: sqlite3.Cursor,
        contact_info: ContactInfo,
        table_name: str,
        messages: list[dict[str, Any]],
        slice_size: int,
        reset_existing: bool,
    ) -> None:
        peer_display_name = self._resolve_peer_display_name(contact_cursor, contact_info)
        subjects = [
            {
                "subject_wxid": self.self_wxid,
                "subject_role": "self",
                "subject_display_name": "我",
            },
            {
                "subject_wxid": contact_info.username,
                "subject_role": "peer",
                "subject_display_name": peer_display_name,
            },
        ]

        for subject in subjects:
            if reset_existing:
                out_conn.execute(
                    "DELETE FROM contact_profiles WHERE subject_wxid = ?",
                    (subject["subject_wxid"],),
                )
                existing_state = None
            else:
                existing_state = self._load_existing_profile(
                    out_conn,
                    subject["subject_wxid"],
                )
            profile_doc = self._build_empty_profile_doc()
            raw_outputs: list[dict[str, Any]] = []
            if existing_state is not None:
                profile_doc = self._merge_profile_docs(profile_doc, existing_state["profile"])
                raw_outputs = list(existing_state["raw_outputs"])

            for slice_index, message_slice in enumerate(
                self._slice_messages(messages, slice_size),
                start=1,
            ):
                slice_stats = self._build_slice_stats(message_slice)
                message_lookup = self._build_message_lookup(message_slice)
                prompt = self._build_profile_patch_prompt(
                    subject=subject,
                    contact_info=contact_info,
                    table_name=table_name,
                    message_slice=message_slice,
                    slice_index=slice_index,
                    slice_stats=slice_stats,
                    existing_profile=profile_doc,
                )
                raw_response = self._safe_chat(
                    prompt,
                    model_spec=self.profile_model_spec,
                )
                if not raw_response:
                    raise ValueError(
                        f"empty AI response for profile patch: {subject['subject_role']}"
                    )
                patch = self._parse_profile_patch_response(raw_response)
                profile_doc = self._merge_profile_patch(
                    profile_doc=profile_doc,
                    patch=patch,
                    source_contact_username=contact_info.username,
                    source_contact_table=table_name,
                    slice_index=slice_index,
                    message_lookup=message_lookup,
                )
                raw_outputs.append(
                    {
                        "slice_index": slice_index,
                        "subject_role": subject["subject_role"],
                        "stats": slice_stats,
                        "response": raw_response,
                    }
                )

            self._upsert_contact_profile(
                out_conn=out_conn,
                subject=subject,
                contact_info=contact_info,
                table_name=table_name,
                messages=messages,
                slice_size=slice_size,
                profile_doc=self._finalize_profile_doc(
                    profile_doc,
                    subject_display_name=subject["subject_display_name"],
                ),
                raw_outputs=raw_outputs,
            )

    def _build_profile_patch_prompt(
        self,
        *,
        subject: dict[str, str],
        contact_info: ContactInfo,
        table_name: str,
        message_slice: list[dict[str, Any]],
        slice_index: int,
        slice_stats: dict[str, Any],
        existing_profile: dict[str, Any],
    ) -> str:
        informative_messages = self._select_informative_messages(
            message_slice,
            subject_wxid=subject["subject_wxid"],
        )
        schema_hint = self._build_profile_schema_hint()
        counterpart_display_name = (
            contact_info.display_name if subject["subject_role"] == "self" else "我"
        )
        payload = {
            "subject_role": subject["subject_role"],
            "subject_wxid": subject["subject_wxid"],
            "subject_display_name": subject["subject_display_name"],
            "counterpart_display_name": counterpart_display_name,
            "source_contact_username": contact_info.username,
            "source_contact_display_name": contact_info.display_name,
            "source_contact_table": table_name,
            "slice_index": slice_index,
            "slice_stats": slice_stats,
            "existing_profile": existing_profile,
            "messages": informative_messages,
        }
        return (
            "你是一个严谨的聊天画像分析器。请基于当前聊天切片，对指定分析对象输出增量更新 patch。"
            "只能依据聊天中明确或高度可推断的信息更新字段，不能捏造，也不能因为当前切片没提到就删除旧字段。"
            "分析对象只有一个：subject_display_name。不要把对方说的话、对方展示的经历、对方发的场景内容误判到当前分析对象身上。"
            "优先使用 speaker_role=subject 的消息。speaker_role=counterpart 只能在明确评价、提问、描述分析对象时作为辅助证据。"
            "content_source=media_remark 只能辅助判断兴趣、作品、活动，不足以单独推出职业、地域、家庭、价值观等高层结论。"
            "如果当前切片不足以支持更新，就不要返回该字段。value 请写成 1 到 3 句中文概括，尽量具体，不要只写一个词。"
            "每个返回字段必须包含 value、status、confidence、evidence_refs。"
            "status 只能是 known、unknown、not_enough_evidence。"
            "evidence_refs 中每条证据至少包含 local_id、msg_time、snippet，snippet 必须直接摘抄输入 messages 里的 content，不要改写成人称旁白。"
            "如果可以形成概括，请填写 profile_summary，写 2 到 4 句中文总结。"
            "输出必须是纯 JSON，对象顶层允许包含 profile_summary、confidence_overall、traits、habits、basic_info。"
            "traits、habits、basic_info 下只返回有更新的字段。\n\n"
            f"字段定义：\n{schema_hint}\n\n"
            f"输入数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _parse_profile_patch_response(self, raw_response: str) -> dict[str, Any]:
        payload = self._extract_json_object(raw_response)
        patch = json.loads(payload)
        if not isinstance(patch, dict):
            raise ValueError("profile patch must be a JSON object")
        return patch

    def _merge_profile_patch(
        self,
        *,
        profile_doc: dict[str, Any],
        patch: dict[str, Any],
        source_contact_username: str,
        source_contact_table: str,
        slice_index: int,
        message_lookup: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        merged = self._merge_profile_docs(self._build_empty_profile_doc(), profile_doc)
        new_summary = str(patch.get("profile_summary") or "").strip()
        new_confidence = self._normalize_confidence(patch.get("confidence_overall"))
        if new_summary and (
            not merged["profile_summary"]
            or new_confidence >= float(merged["confidence_overall"])
        ):
            merged["profile_summary"] = new_summary
            merged["confidence_overall"] = new_confidence
        elif new_confidence > float(merged["confidence_overall"]):
            merged["confidence_overall"] = new_confidence

        update_source = {
            "source_contact_username": source_contact_username,
            "source_contact_table": source_contact_table,
            "slice_index": slice_index,
        }
        for section_name in PROFILE_FIELD_SPECS:
            section_patch = patch.get(section_name)
            if not isinstance(section_patch, dict):
                continue
            for field_key in PROFILE_FIELD_SPECS[section_name]:
                field_patch = section_patch.get(field_key)
                normalized_patch = self._normalize_profile_field_patch(
                    field_patch,
                    update_source,
                    message_lookup,
                )
                if normalized_patch is None:
                    continue
                current_field = merged[section_name][field_key]
                if not self._should_apply_field_patch(current_field, normalized_patch):
                    continue
                merged[section_name][field_key] = normalized_patch

        return merged

    def _upsert_contact_profile(
        self,
        *,
        out_conn: sqlite3.Connection,
        subject: dict[str, str],
        contact_info: ContactInfo,
        table_name: str,
        messages: list[dict[str, Any]],
        slice_size: int,
        profile_doc: dict[str, Any],
        raw_outputs: list[dict[str, Any]],
    ) -> None:
        stats = self._build_slice_stats(messages)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        evidence = self._collect_profile_evidence(profile_doc)
        out_conn.execute(
            """
            INSERT INTO contact_profiles (
                subject_wxid,
                subject_role,
                subject_display_name,
                source_contact_username,
                source_contact_table,
                analysis_version,
                last_slice_size,
                last_message_count,
                last_time_start,
                last_time_end,
                profile_summary,
                confidence_overall,
                traits_json,
                habits_json,
                basic_info_json,
                evidence_json,
                raw_model_output_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_wxid) DO UPDATE SET
                subject_role = excluded.subject_role,
                subject_display_name = excluded.subject_display_name,
                source_contact_username = excluded.source_contact_username,
                source_contact_table = excluded.source_contact_table,
                analysis_version = excluded.analysis_version,
                last_slice_size = excluded.last_slice_size,
                last_message_count = excluded.last_message_count,
                last_time_start = excluded.last_time_start,
                last_time_end = excluded.last_time_end,
                profile_summary = excluded.profile_summary,
                confidence_overall = excluded.confidence_overall,
                traits_json = excluded.traits_json,
                habits_json = excluded.habits_json,
                basic_info_json = excluded.basic_info_json,
                evidence_json = excluded.evidence_json,
                raw_model_output_json = excluded.raw_model_output_json,
                updated_at = excluded.updated_at
            """,
            (
                subject["subject_wxid"],
                subject["subject_role"],
                subject["subject_display_name"],
                contact_info.username,
                table_name,
                PROFILE_ANALYSIS_VERSION,
                slice_size,
                stats["message_count"],
                stats["time_start"],
                stats["time_end"],
                profile_doc["profile_summary"],
                float(profile_doc["confidence_overall"]),
                json.dumps(profile_doc["traits"], ensure_ascii=False),
                json.dumps(profile_doc["habits"], ensure_ascii=False),
                json.dumps(profile_doc["basic_info"], ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(raw_outputs, ensure_ascii=False),
                now,
            ),
        )

    def _load_existing_profile(
        self,
        out_conn: sqlite3.Connection,
        subject_wxid: str,
    ) -> dict[str, Any] | None:
        row = out_conn.execute(
            """
            SELECT profile_summary, confidence_overall, traits_json, habits_json,
                   basic_info_json, raw_model_output_json
            FROM contact_profiles
            WHERE subject_wxid = ?
            """,
            (subject_wxid,),
        ).fetchone()
        if row is None:
            return None

        profile = self._build_empty_profile_doc()
        profile["profile_summary"] = str(row[0] or "").strip()
        profile["confidence_overall"] = self._normalize_confidence(row[1])
        profile["traits"] = self._normalize_profile_section(
            "traits",
            self._load_json_text(row[2]),
        )
        profile["habits"] = self._normalize_profile_section(
            "habits",
            self._load_json_text(row[3]),
        )
        profile["basic_info"] = self._normalize_profile_section(
            "basic_info",
            self._load_json_text(row[4]),
        )
        raw_outputs = self._load_json_text(row[5])
        if not isinstance(raw_outputs, list):
            raw_outputs = []
        return {
            "profile": profile,
            "raw_outputs": raw_outputs,
        }

    def _build_empty_profile_doc(self) -> dict[str, Any]:
        return {
            "profile_summary": "",
            "confidence_overall": 0.0,
            "traits": self._build_empty_profile_section("traits"),
            "habits": self._build_empty_profile_section("habits"),
            "basic_info": self._build_empty_profile_section("basic_info"),
        }

    def _build_empty_profile_section(self, section_name: str) -> dict[str, Any]:
        return {
            field_key: self._build_empty_profile_field(field_label)
            for field_key, field_label in PROFILE_FIELD_SPECS[section_name].items()
        }

    @staticmethod
    def _build_empty_profile_field(field_label: str) -> dict[str, Any]:
        return {
            "label": field_label,
            "value": None,
            "status": PROFILE_STATUS_UNKNOWN,
            "confidence": 0.0,
            "evidence_refs": [],
            "updated_from": {},
        }

    def _merge_profile_docs(
        self,
        base_doc: dict[str, Any],
        overlay_doc: dict[str, Any],
    ) -> dict[str, Any]:
        merged = self._build_empty_profile_doc()
        merged["profile_summary"] = str(overlay_doc.get("profile_summary") or "").strip()
        merged["confidence_overall"] = self._normalize_confidence(
            overlay_doc.get("confidence_overall")
        )
        for section_name in PROFILE_FIELD_SPECS:
            merged[section_name] = self._normalize_profile_section(
                section_name,
                overlay_doc.get(section_name),
            )
        return merged

    def _normalize_profile_section(
        self,
        section_name: str,
        section_payload: Any,
    ) -> dict[str, Any]:
        normalized = self._build_empty_profile_section(section_name)
        if not isinstance(section_payload, dict):
            return normalized
        for field_key, field_label in PROFILE_FIELD_SPECS[section_name].items():
            normalized_field = self._normalize_profile_field_doc(
                field_label,
                section_payload.get(field_key),
            )
            if normalized_field is not None:
                normalized[field_key] = normalized_field
        return normalized

    def _normalize_profile_field_doc(
        self,
        field_label: str,
        payload: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        normalized = self._build_empty_profile_field(field_label)
        normalized["value"] = payload.get("value")
        normalized["status"] = self._normalize_profile_status(payload.get("status"))
        normalized["confidence"] = self._normalize_confidence(payload.get("confidence"))
        normalized["evidence_refs"] = self._normalize_evidence_refs(
            payload.get("evidence_refs")
        )
        updated_from = payload.get("updated_from")
        normalized["updated_from"] = updated_from if isinstance(updated_from, dict) else {}
        return normalized

    def _normalize_profile_field_patch(
        self,
        payload: Any,
        update_source: dict[str, Any],
        message_lookup: dict[int, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        status = self._normalize_profile_status(payload.get("status"))
        evidence_refs = self._normalize_evidence_refs(
            payload.get("evidence_refs"),
            message_lookup=message_lookup,
        )
        value = payload.get("value")
        confidence = self._normalize_confidence(payload.get("confidence"))
        if status == PROFILE_STATUS_KNOWN and not evidence_refs:
            return None
        if status == PROFILE_STATUS_KNOWN and value in (None, "", []):
            return None
        return {
            "value": value,
            "status": status,
            "confidence": confidence,
            "evidence_refs": evidence_refs,
            "updated_from": update_source,
        }

    @staticmethod
    def _should_apply_field_patch(
        current_field: dict[str, Any],
        new_field: dict[str, Any],
    ) -> bool:
        current_status = str(current_field.get("status") or PROFILE_STATUS_UNKNOWN)
        new_status = str(new_field.get("status") or PROFILE_STATUS_UNKNOWN)
        current_confidence = float(current_field.get("confidence") or 0.0)
        new_confidence = float(new_field.get("confidence") or 0.0)

        if new_status == PROFILE_STATUS_KNOWN:
            if current_status != PROFILE_STATUS_KNOWN:
                return True
            if not current_field.get("value"):
                return True
            return new_confidence >= current_confidence

        return current_status == PROFILE_STATUS_UNKNOWN and new_confidence >= current_confidence

    def _collect_profile_evidence(self, profile_doc: dict[str, Any]) -> list[dict[str, Any]]:
        evidence_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        for section_name in PROFILE_FIELD_SPECS:
            section = profile_doc[section_name]
            for field_key in PROFILE_FIELD_SPECS[section_name]:
                field_doc = section[field_key]
                for evidence in field_doc.get("evidence_refs", []):
                    key = (
                        evidence.get("local_id"),
                        evidence.get("msg_time"),
                        evidence.get("snippet"),
                    )
                    evidence_by_key[key] = evidence
        return list(evidence_by_key.values())

    def _build_profile_schema_hint(self) -> str:
        lines: list[str] = []
        for section_name, fields in PROFILE_FIELD_SPECS.items():
            joined_fields = ", ".join(
                [f"{field_key}({field_label})" for field_key, field_label in fields.items()]
            )
            lines.append(f"{section_name}: {joined_fields}")
        return "\n".join(lines)

    def _build_slice_stats(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if not messages:
            return {
                "message_count": 0,
                "time_start": "",
                "time_end": "",
                "sender_distribution": {},
            }
        sender_distribution: dict[str, int] = {}
        for row in messages:
            sender = str(row.get("sender") or "").strip()
            sender_distribution[sender] = sender_distribution.get(sender, 0) + 1
        return {
            "message_count": len(messages),
            "time_start": str(messages[0].get("msg_time") or ""),
            "time_end": str(messages[-1].get("msg_time") or ""),
            "sender_distribution": sender_distribution,
        }

    def _slice_messages(
        self,
        messages: list[dict[str, Any]],
        slice_size: int,
    ) -> list[list[dict[str, Any]]]:
        return [
            messages[index : index + slice_size]
            for index in range(0, len(messages), slice_size)
        ]

    def _select_informative_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        subject_wxid: str,
        max_items: int = 60,
    ) -> list[dict[str, Any]]:
        scored_rows: list[tuple[int, int, dict[str, Any]]] = []
        for index, row in enumerate(messages):
            score = self._score_message_for_profile(
                row,
                subject_wxid=subject_wxid,
            )
            if score <= 0:
                continue
            scored_rows.append((score, index, row))
        scored_rows.sort(key=lambda item: (-item[0], item[1]))

        selected_indexes: set[int] = set()
        for _, index, row in scored_rows:
            if len(selected_indexes) >= max_items:
                break
            selected_indexes.add(index)
            if str(row.get("wxid") or "").strip() != subject_wxid:
                continue
            for neighbor in (index - 1, index + 1):
                if 0 <= neighbor < len(messages) and len(selected_indexes) < max_items:
                    selected_indexes.add(neighbor)

        selected = [
            self._compress_message_for_prompt(
                messages[index],
                subject_wxid=subject_wxid,
            )
            for index in sorted(selected_indexes)
        ]
        if selected:
            return selected
        return [
            self._compress_message_for_prompt(row, subject_wxid=subject_wxid)
            for row in messages[: min(20, len(messages))]
        ]

    def _score_message_for_profile(
        self,
        row: dict[str, Any],
        *,
        subject_wxid: str,
    ) -> int:
        msg_type = str(row.get("msg_type") or "")
        msg = str(row.get("msg") or "").strip()
        remark = str(row.get("remark") or "").strip()
        content = remark if msg_type in {"图片", "视频", "语音"} and remark else msg
        content = content.strip()
        if not content:
            return 0

        score = 10
        sender_wxid = str(row.get("wxid") or "").strip()
        is_subject_message = sender_wxid == subject_wxid
        if msg_type == "文本":
            score += min(len(content), 120)
        else:
            score += 30
        if is_subject_message:
            score += 140
        elif "你" in content or "他" in content or "她" in content:
            score += 25
        else:
            score -= 15
        if len(content) <= 1:
            score -= 30
        if any(keyword in content for keyword in PROFILE_KEYWORDS):
            score += 120
        if re.search(r"\d{2,4}", content):
            score += 20
        if "http" in content.lower():
            score -= 20
        if content in {"嗯", "哦", "好", "好的", "哈哈", "？", "。"}:
            score -= 40
        return score

    @staticmethod
    def _compress_message_for_prompt(
        row: dict[str, Any],
        *,
        subject_wxid: str,
    ) -> dict[str, Any]:
        msg_type = str(row.get("msg_type") or "")
        msg = str(row.get("msg") or "").strip()
        remark = str(row.get("remark") or "").strip()
        content = remark if msg_type in {"图片", "视频", "语音"} and remark else msg
        if len(content) > 240:
            content = f"{content[:240]}..."
        sender_wxid = str(row.get("wxid") or "").strip()
        return {
            "local_id": row.get("local_id"),
            "sender": row.get("sender"),
            "wxid": sender_wxid,
            "speaker_role": "subject" if sender_wxid == subject_wxid else "counterpart",
            "content_source": (
                "media_remark" if msg_type in {"图片", "视频", "语音"} and remark else "message"
            ),
            "msg_type": msg_type,
            "msg_time": row.get("msg_time"),
            "content": content,
        }

    @staticmethod
    def _build_message_lookup(
        messages: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        return {
            int(row["local_id"]): row
            for row in messages
            if row.get("local_id") is not None
        }

    def _finalize_profile_doc(
        self,
        profile_doc: dict[str, Any],
        *,
        subject_display_name: str,
    ) -> dict[str, Any]:
        finalized = self._merge_profile_docs(self._build_empty_profile_doc(), profile_doc)
        if not finalized["profile_summary"]:
            finalized["profile_summary"] = self._generate_profile_summary(
                finalized,
                subject_display_name=subject_display_name,
            )
        if float(finalized["confidence_overall"]) <= 0:
            confidences: list[float] = []
            for section_name in PROFILE_FIELD_SPECS:
                for field_key in PROFILE_FIELD_SPECS[section_name]:
                    field_doc = finalized[section_name][field_key]
                    if field_doc.get("status") == PROFILE_STATUS_KNOWN:
                        confidences.append(float(field_doc.get("confidence") or 0.0))
            if confidences:
                finalized["confidence_overall"] = sum(confidences) / len(confidences)
        return finalized

    def _generate_profile_summary(
        self,
        profile_doc: dict[str, Any],
        *,
        subject_display_name: str,
    ) -> str:
        summary_parts: list[str] = []
        for section_name in ("basic_info", "traits", "habits"):
            for field_key in PROFILE_FIELD_SPECS[section_name]:
                field_doc = profile_doc[section_name][field_key]
                if field_doc.get("status") != PROFILE_STATUS_KNOWN:
                    continue
                value = str(field_doc.get("value") or "").strip()
                if not value:
                    continue
                summary_parts.append(value)
                if len(summary_parts) >= 4:
                    break
            if len(summary_parts) >= 4:
                break
        if not summary_parts:
            return ""
        return f"{subject_display_name}：{'；'.join(summary_parts)}"

    def _load_processed_messages(
        self,
        out_conn: sqlite3.Connection,
        table_name: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        quoted_table_name = self._quote_sqlite_identifier(table_name)
        sql = (
            f"SELECT local_id, sender, wxid, remark, msg_type, msg_time, msg "
            f"FROM {quoted_table_name} ORDER BY id ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = out_conn.execute(sql).fetchall()
        return [
            {
                "local_id": int(row[0]),
                "sender": str(row[1] or ""),
                "wxid": str(row[2] or ""),
                "remark": str(row[3] or ""),
                "msg_type": str(row[4] or ""),
                "msg_time": str(row[5] or ""),
                "msg": str(row[6] or ""),
            }
            for row in rows
        ]

    def _resolve_peer_wxid(self, messages: list[dict[str, Any]]) -> str:
        counts: dict[str, int] = {}
        for row in messages:
            wxid = str(row.get("wxid") or "").strip()
            if not wxid or wxid == self.self_wxid:
                continue
            counts[wxid] = counts.get(wxid, 0) + 1
        if not counts:
            return ""
        if len(counts) > 1:
            raise NotImplementedError("chatroom profile analysis is not implemented yet")
        return next(iter(counts))

    def _resolve_peer_display_name(
        self,
        contact_cursor: sqlite3.Cursor,
        contact_info: ContactInfo,
    ) -> str:
        display_name = contact_info.display_name
        if display_name:
            return display_name
        candidate = self._query_contact_by_username(contact_cursor, contact_info.username)
        if candidate is not None:
            return candidate.display_name
        return contact_info.username

    def _build_contact_info_from_table(
        self,
        contact_cursor: sqlite3.Cursor,
        table_name: str,
        peer_wxid: str,
    ) -> ContactInfo:
        candidate = self._query_contact_by_username(contact_cursor, peer_wxid)
        if candidate is not None:
            contact_info = self._build_contact_info(candidate)
            return ContactInfo(
                username=contact_info.username,
                nick_name=contact_info.nick_name,
                remark=contact_info.remark,
                alias=contact_info.alias,
                table_name=table_name,
                contact_type=contact_info.contact_type,
            )
        return ContactInfo(
            username=peer_wxid,
            nick_name=table_name,
            remark="",
            alias="",
            table_name=table_name,
            contact_type=self._detect_contact_type(peer_wxid),
        )

    def _ensure_contact_profiles_table(self, out_conn: sqlite3.Connection) -> None:
        out_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_profiles (
                subject_wxid TEXT PRIMARY KEY,
                subject_role TEXT NOT NULL,
                subject_display_name TEXT NOT NULL,
                source_contact_username TEXT NOT NULL,
                source_contact_table TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                last_slice_size INTEGER NOT NULL,
                last_message_count INTEGER NOT NULL,
                last_time_start TEXT NOT NULL,
                last_time_end TEXT NOT NULL,
                profile_summary TEXT NOT NULL,
                confidence_overall REAL NOT NULL,
                traits_json TEXT NOT NULL,
                habits_json TEXT NOT NULL,
                basic_info_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                raw_model_output_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        out_conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contact_profiles_subject_role
            ON contact_profiles(subject_role)
            """
        )
        out_conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contact_profiles_source_contact_username
            ON contact_profiles(source_contact_username)
            """
        )

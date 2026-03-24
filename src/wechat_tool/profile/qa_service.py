"""基于画像库的终端问答服务。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.wechat_tool.common.service_base import WechatServiceBase


class WechatProfileQAService(WechatServiceBase):
    """读取画像库，并基于联系人画像回答终端中的自然语言问题。"""

    def answer_question(
        self,
        question: str,
        profile_db_path: Path | None = None,
    ) -> dict[str, Any]:
        """从画像库检索相关画像，并生成最终回答。"""
        if self.ai_client is None:
            raise ValueError("AI client is required for profile QA")

        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question is required")

        db_path = self._resolve_profile_db_path(profile_db_path)
        candidates = self._search_profile_candidates(db_path, normalized_question)
        if not candidates:
            return {
                "answer": "画像库里没有找到可用于回答这个问题的联系人画像。",
                "matched_profiles": [],
            }

        prompt = self._build_profile_qa_prompt(
            question=normalized_question,
            candidates=candidates,
        )
        answer = self._safe_chat(
            prompt,
            model_spec=self.profile_model_spec,
        ).strip()
        if not answer:
            answer = "暂时无法根据画像库生成回答。"
        return {
            "answer": answer,
            "matched_profiles": [
                {
                    "subject_display_name": candidate["subject_display_name"],
                    "subject_role": candidate["subject_role"],
                    "subject_wxid": candidate["subject_wxid"],
                    "confidence_overall": candidate["confidence_overall"],
                }
                for candidate in candidates
            ],
        }

    def run_terminal_chat(self, profile_db_path: Path | None = None) -> None:
        """启动终端 REPL，持续回答画像相关问题。"""
        db_path = self._resolve_profile_db_path(profile_db_path)
        print(f"画像问答已启动，画像库：{db_path}")
        print("输入问题开始提问，输入 quit / exit / 退出 结束。")

        while True:
            try:
                question = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出画像问答。")
                return

            if not question:
                continue
            if question.lower() in {"quit", "exit"} or question == "退出":
                print("已退出画像问答。")
                return

            try:
                result = self.answer_question(question, profile_db_path=db_path)
            except Exception as exc:
                print(f"\n助手> 处理失败：{exc}")
                continue

            matched = result["matched_profiles"]
            if matched:
                matched_text = "，".join(
                    [
                        f"{item['subject_display_name']}({item['subject_role']})"
                        for item in matched
                    ]
                )
                print(f"\n命中画像：{matched_text}")
            print(f"\n助手> {result['answer']}")

    def _resolve_profile_db_path(self, profile_db_path: Path | None) -> Path:
        """解析画像库路径，默认复用 messages.db。"""
        return self._resolve_messages_db_path(profile_db_path)

    def _search_profile_candidates(
        self,
        profile_db_path: Path,
        question: str,
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """从画像库中粗排最相关的联系人画像。"""
        if not profile_db_path.exists():
            raise FileNotFoundError(f"profile db not found: {profile_db_path}")

        with sqlite3.connect(profile_db_path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._table_exists(conn, "contact_profiles"):
                raise ValueError(f"contact_profiles table not found: {profile_db_path}")
            rows = conn.execute(
                """
                SELECT
                    subject_wxid,
                    subject_role,
                    subject_display_name,
                    source_contact_username,
                    source_contact_table,
                    profile_summary,
                    confidence_overall,
                    traits_json,
                    habits_json,
                    basic_info_json,
                    evidence_json,
                    updated_at
                FROM contact_profiles
                ORDER BY updated_at DESC, confidence_overall DESC
                """
            ).fetchall()

        scored_rows: list[tuple[int, dict[str, Any]]] = []
        question_lower = question.lower()
        for row in rows:
            record = self._build_profile_record(row)
            score = self._score_profile_record(record, question, question_lower)
            if score <= 0:
                continue
            scored_rows.append((score, record))

        scored_rows.sort(
            key=lambda item: (
                -item[0],
                -float(item[1].get("confidence_overall") or 0.0),
                str(item[1].get("updated_at") or ""),
            )
        )
        return [item[1] for item in scored_rows[:limit]]

    def _build_profile_record(self, row: sqlite3.Row) -> dict[str, Any]:
        """把数据库中的画像行还原成适合检索与提示词使用的结构。"""
        return {
            "subject_wxid": str(row["subject_wxid"] or ""),
            "subject_role": str(row["subject_role"] or ""),
            "subject_display_name": str(row["subject_display_name"] or ""),
            "source_contact_username": str(row["source_contact_username"] or ""),
            "source_contact_table": str(row["source_contact_table"] or ""),
            "profile_summary": str(row["profile_summary"] or ""),
            "confidence_overall": float(row["confidence_overall"] or 0.0),
            "traits": self._load_json_text(row["traits_json"]) or {},
            "habits": self._load_json_text(row["habits_json"]) or {},
            "basic_info": self._load_json_text(row["basic_info_json"]) or {},
            "evidence": self._load_json_text(row["evidence_json"]) or [],
            "updated_at": str(row["updated_at"] or ""),
        }

    def _score_profile_record(
        self,
        record: dict[str, Any],
        question: str,
        question_lower: str,
    ) -> int:
        """根据名称命中和画像文本命中做简单粗排。"""
        score = 0
        direct_match = False
        display_name = str(record.get("subject_display_name") or "")
        contact_table = str(record.get("source_contact_table") or "")
        subject_role = str(record.get("subject_role") or "")
        profile_text = self._flatten_profile_record(record).lower()

        if display_name and display_name in question:
            score += 300
            direct_match = True
        if contact_table and contact_table in question:
            score += 220
            direct_match = True
        if subject_role == "self" and any(token in question for token in ("我", "自己", "本人")):
            score += 260
            direct_match = True
        if display_name.lower() in question_lower and display_name:
            score += 200
            direct_match = True

        matched_terms = 0
        for token in self._extract_question_terms(question):
            if token and token.lower() in profile_text:
                matched_terms += 1
        if not direct_match and matched_terms == 0:
            return 0
        score += matched_terms * 40

        if str(record.get("profile_summary") or "").strip():
            score += 30
        score += int(float(record.get("confidence_overall") or 0.0) * 20)
        return score

    def _flatten_profile_record(self, record: dict[str, Any]) -> str:
        """把画像记录压平成纯文本，便于做关键词粗匹配。"""
        pieces = [
            str(record.get("subject_display_name") or ""),
            str(record.get("source_contact_table") or ""),
            str(record.get("profile_summary") or ""),
        ]
        for section_name in ("basic_info", "traits", "habits"):
            section = record.get(section_name)
            if not isinstance(section, dict):
                continue
            for field in section.values():
                if not isinstance(field, dict):
                    continue
                value = field.get("value")
                if value not in (None, ""):
                    pieces.append(str(value))
                for evidence in field.get("evidence_refs", []):
                    if isinstance(evidence, dict):
                        snippet = str(evidence.get("snippet") or "").strip()
                        if snippet:
                            pieces.append(snippet)
        return "\n".join(pieces)

    @staticmethod
    def _extract_question_terms(question: str) -> list[str]:
        """提取问题中的高信息量词，供画像粗检索使用。"""
        separators = "，。！？、,.?？!；;：:\"'“”‘’()（）[]【】<>《》 \t\r\n"
        normalized = question
        for separator in separators:
            normalized = normalized.replace(separator, " ")
        raw_terms = [term.strip() for term in normalized.split(" ") if term.strip()]
        return [term for term in raw_terms if len(term) >= 2]

    def _build_profile_qa_prompt(
        self,
        *,
        question: str,
        candidates: list[dict[str, Any]],
    ) -> str:
        """构造画像问答提示词，限制模型只能基于画像库作答。"""
        payload = []
        for candidate in candidates:
            payload.append(
                {
                    "subject_display_name": candidate["subject_display_name"],
                    "subject_role": candidate["subject_role"],
                    "subject_wxid": candidate["subject_wxid"],
                    "source_contact_table": candidate["source_contact_table"],
                    "profile_summary": candidate["profile_summary"],
                    "confidence_overall": candidate["confidence_overall"],
                    "basic_info": candidate["basic_info"],
                    "traits": candidate["traits"],
                    "habits": candidate["habits"],
                }
            )

        return (
            "你是一个画像库问答助手。你只能依据提供的联系人画像回答问题，不能编造。"
            "如果画像库里没有足够证据，就直接说“画像库中没有足够信息回答这个问题”。"
            "如果问题里提到了具体人名，优先回答该人；如果命中了多个候选，先简短说明你主要依据的是谁。"
            "回答请使用中文，简洁直接，不要输出 JSON。\n\n"
            f"用户问题：\n{question}\n\n"
            f"可用画像：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

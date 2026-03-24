"""聊天导出与画像分析共享的数据模型和常量。"""

from __future__ import annotations

from dataclasses import dataclass

CONTACT_TYPE_PERSON = "person"
CONTACT_TYPE_CHATROOM = "chatroom"
CONTACT_TYPE_UNSUPPORTED = "unsupported"

PROFILE_ANALYSIS_VERSION = "chat-profile-v1"
PROFILE_STATUS_KNOWN = "known"
PROFILE_STATUS_UNKNOWN = "unknown"
PROFILE_STATUS_NOT_ENOUGH = "not_enough_evidence"

PROFILE_FIELD_SPECS: dict[str, dict[str, str]] = {
    "traits": {
        "background_info": "背景信息",
        "outer_presentation": "外在呈现",
        "behavior_style": "处事方式",
        "relationship_pattern": "人际模式",
        "thinking_pattern": "思维模式",
        "goals_and_pursuits": "追求与目标",
        "value_priorities": "价值观排序",
        "self_cognition": "自我认知",
    },
    "habits": {
        "routine": "作息",
        "diet": "饮食",
        "hygiene_and_order": "卫生与秩序",
        "exercise_and_health": "运动与身体管理",
        "spending": "消费习惯",
        "work_style": "工作习惯",
        "life_and_leisure": "生活与休闲",
    },
    "basic_info": {
        "identity_attributes": "身份属性",
        "appearance": "外貌特征",
        "health": "健康状况",
        "education": "教育背景",
        "professional_skills": "专业技能",
        "special_experience": "特殊经历",
        "assets": "资产状况",
        "relationship_status": "情感与婚姻",
        "family_of_origin": "原生家庭",
        "children": "子女状况",
        "family_relationships": "家庭关系",
        "life_radius": "生活半径",
    },
}

PROFILE_KEYWORDS = (
    "工作",
    "上班",
    "下班",
    "公司",
    "职业",
    "工资",
    "加班",
    "学校",
    "大学",
    "研究生",
    "博士",
    "高中",
    "家里",
    "爸",
    "妈",
    "父母",
    "孩子",
    "对象",
    "结婚",
    "离婚",
    "单身",
    "住",
    "租房",
    "买房",
    "老家",
    "出生",
    "旅游",
    "健身",
    "跑步",
    "喝酒",
    "咖啡",
    "睡觉",
    "起床",
    "喜欢",
    "讨厌",
    "性格",
    "习惯",
    "兴趣",
    "爱好",
    "目标",
    "打算",
    "计划",
    "考证",
    "驾照",
    "生病",
    "医院",
    "过敏",
)


@dataclass(frozen=True)
class ContactCandidate:
    """联系人候选项。"""

    username: str
    alias: str
    nick_name: str
    remark: str

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.alias or self.username


@dataclass(frozen=True)
class ContactInfo:
    """已确定的联系人信息。"""

    username: str
    nick_name: str
    remark: str
    alias: str
    table_name: str
    contact_type: str

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.alias or self.username


@dataclass(frozen=True)
class RawMessage:
    """原始消息表中的消息记录。"""

    local_id: int
    real_sender_id: int
    local_type: int
    create_time: int
    message_content: str | bytes


class ContactSelectionError(ValueError):
    """关键字命中多个联系人时抛出的异常。"""

    def __init__(self, keyword: str, candidates: list[ContactCandidate]) -> None:
        self.keyword = keyword
        self.candidates = candidates
        candidate_lines = "\n".join(
            [
                f"- username={candidate.username}, alias={candidate.alias or '-'}, "
                f"nick_name={candidate.nick_name or '-'}, remark={candidate.remark or '-'}"
                for candidate in candidates
            ]
        )
        super().__init__(
            f"multiple contacts found for keyword: {keyword}\n"
            f"please retry with username.\n{candidate_lines}"
        )

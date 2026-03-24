"""应用层编排，统一装配导出与画像分析服务。"""

from __future__ import annotations

from pathlib import Path

from src.wechat_tool.clients.ai import WechatAIClient
from src.wechat_tool.export.service import WechatChatExportService
from src.wechat_tool.media.manager import WechatMediaManager
from src.wechat_tool.profile.qa_service import WechatProfileQAService
from src.wechat_tool.profile.service import WechatContactProfileService


class WechatChatApplication:
    """统一装配导出服务与画像服务，供 CLI 和兼容层调用。"""

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
        common_kwargs = {
            "message_db_path": message_db_path,
            "contact_db_path": contact_db_path,
            "media_manager": media_manager,
            "self_wxid": self_wxid,
            "ai_client": ai_client,
            "image_model_spec": image_model_spec,
            "video_model_spec": video_model_spec,
            "audio_model_spec": audio_model_spec,
            "profile_model_spec": profile_model_spec,
            "export_dir": export_dir,
        }
        self.export_service = WechatChatExportService(**common_kwargs)
        self.profile_service = WechatContactProfileService(
            **common_kwargs,
            export_service=self.export_service,
        )
        self.profile_qa_service = WechatProfileQAService(**common_kwargs)

    @classmethod
    def from_env(cls) -> "WechatChatApplication":
        runtime = WechatChatExportService.from_env()
        return cls(
            message_db_path=runtime.message_db_path,
            contact_db_path=runtime.contact_db_path,
            media_manager=runtime.media_manager,
            self_wxid=runtime.self_wxid,
            ai_client=runtime.ai_client,
            image_model_spec=runtime.image_model_spec,
            video_model_spec=runtime.video_model_spec,
            audio_model_spec=runtime.audio_model_spec,
            profile_model_spec=runtime.profile_model_spec,
            export_dir=runtime.export_dir,
        )

    def export_by_contact_name(
        self,
        contact_name_keyword: str,
        output_path: Path | None = None,
        *,
        output_format: str = "csv",
        limit: int | None = None,
    ) -> Path:
        """统一导出入口，按格式分发到导出服务。"""
        return self.export_service.export_by_contact_name(
            contact_name_keyword,
            output_path=output_path,
            output_format=output_format,
            limit=limit,
        )

    def export_by_contact_name_to_csv(
        self,
        contact_name_keyword: str,
        output_csv_path: Path | None = None,
        *,
        limit: int | None = None,
    ) -> Path:
        """导出处理后的聊天记录为 CSV。"""
        return self.export_service.export_by_contact_name_to_csv(
            contact_name_keyword,
            output_csv_path=output_csv_path,
            limit=limit,
        )

    def export_by_contact_name_to_sqlite(
        self,
        contact_name_keyword: str,
        output_sqlite_path: Path | None = None,
        *,
        limit: int | None = None,
    ) -> Path:
        """导出处理后的聊天记录到 SQLite。"""
        return self.export_service.export_by_contact_name_to_sqlite(
            contact_name_keyword,
            output_sqlite_path=output_sqlite_path,
            limit=limit,
        )

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
        return self.profile_service.analyze_contact_profiles(
            keyword,
            output_sqlite_path=output_sqlite_path,
            slice_size=slice_size,
            limit=limit,
            reset_existing=reset_existing,
        )

    def answer_profile_question(
        self,
        question: str,
        profile_db_path: Path | None = None,
    ) -> dict[str, object]:
        """基于画像库回答自然语言问题。"""
        return self.profile_qa_service.answer_question(
            question,
            profile_db_path=profile_db_path,
        )

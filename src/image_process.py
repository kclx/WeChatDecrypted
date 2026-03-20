from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from verify_wechat_dat import _WechatDatRecover


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DECRYPTED_DB_DIR = PROJECT_ROOT / "data" / "db" / "decrypted"
DEFAULT_DECRYPTED_DB_DIR_ALT = PROJECT_ROOT / "data" / "db" / "dec"


@dataclass
class ImageSummary:
    msg_table: str
    local_id: int
    server_id: int
    create_time: int
    month_dir: str
    file_base: str
    main_dat_path: str | None
    thumb_dat_path: str | None
    hd_dat_path: str | None
    exported_main_path: str | None
    exported_thumb_path: str | None
    exported_hd_path: str | None
    has_main: bool
    has_thumb: bool
    has_hd: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WechatImageParser:
    """统一封装消息定位与微信图片 .dat 恢复。"""

    def __init__(
        self,
        message_db_path: Path,
        message_resource_db_path: Path,
        account_root: Path,
        key32: str,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.message_resource_db_path = Path(message_resource_db_path)
        self.account_root = Path(account_root)
        self.key32 = key32
        self._dat_recoverer: _WechatDatRecover | None = None

    @classmethod
    def from_env(cls) -> WechatImageParser:
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
            key32=os.environ["KEY32"],
        )

    def find_image_paths(self, msg_table: str, local_id: int) -> dict[str, object]:
        with sqlite3.connect(self.message_db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "ATTACH DATABASE ? AS message_resource",
                (str(self.message_resource_db_path),),
            )

            message = self._fetch_message(conn, msg_table, local_id)
            resource_info = self._fetch_resource_info(conn, msg_table, message)
            resource_details = (
                self._fetch_resource_details(conn, resource_info["message_id"])
                if resource_info is not None
                else []
            )

        file_base = self._extract_file_base(message["packed_info_data"])
        chat_md5 = msg_table.removeprefix("Msg_")
        month_dir = self._format_month_dir(message["create_time"])
        image_dir = self.account_root / "msg" / "attach" / chat_md5 / month_dir / "Img"

        return {
            "message": dict(message),
            "resource_info": dict(resource_info) if resource_info is not None else None,
            "resource_details": [dict(row) for row in resource_details],
            "chat_md5": chat_md5,
            "month_dir": month_dir,
            "file_base": file_base,
            "image_dir": str(image_dir),
            "main_file_name": f"{file_base}.dat",
            "thumb_file_name": f"{file_base}_t.dat",
            "hd_file_name": f"{file_base}_h.dat",
            "main_file_path": str(image_dir / f"{file_base}.dat"),
            "thumb_file_path": str(image_dir / f"{file_base}_t.dat"),
            "hd_file_path": str(image_dir / f"{file_base}_h.dat"),
        }

    def recover_thumb(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, object]:
        return self._recover_variant(msg_table, local_id, output_dir, "thumb")

    def recover_main(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, object]:
        return self._recover_variant(msg_table, local_id, output_dir, "main")

    def recover_hd(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, object]:
        return self._recover_variant(msg_table, local_id, output_dir, "hd")

    def export_image_assets(self, msg_table: str, local_id: int, output_dir: Path) -> dict[str, str]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        exported: dict[str, str] = {}
        for variant in ("main", "thumb", "hd"):
            try:
                result = self._recover_variant(
                    msg_table,
                    local_id,
                    output_dir,
                    variant,
                    output_stem=f"{msg_table}_{local_id}_{variant}",
                )
            except FileNotFoundError:
                continue
            exported[variant] = str(result["output_file"])
        return exported

    def find_image_summary(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> ImageSummary:
        detail = self.find_image_paths(msg_table, local_id)
        exported = self.export_image_assets(msg_table, local_id, output_dir) if output_dir is not None else {}

        main_dat_path = str(detail["main_file_path"])
        thumb_dat_path = str(detail["thumb_file_path"])
        hd_dat_path = str(detail["hd_file_path"])
        return ImageSummary(
            msg_table=msg_table,
            local_id=local_id,
            server_id=int(detail["message"]["server_id"]),
            create_time=int(detail["message"]["create_time"]),
            month_dir=str(detail["month_dir"]),
            file_base=str(detail["file_base"]),
            main_dat_path=main_dat_path,
            thumb_dat_path=thumb_dat_path,
            hd_dat_path=hd_dat_path,
            exported_main_path=exported.get("main"),
            exported_thumb_path=exported.get("thumb"),
            exported_hd_path=exported.get("hd"),
            has_main=Path(main_dat_path).exists(),
            has_thumb=Path(thumb_dat_path).exists(),
            has_hd=Path(hd_dat_path).exists(),
        )

    def _recover_variant(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path,
        variant: str,
        output_stem: str | None = None,
    ) -> dict[str, object]:
        result = self.find_image_paths(msg_table, local_id)
        dat_path = self._select_variant_path(result, variant)
        dat_recoverer = self._get_dat_recoverer()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = output_stem or f"{result['file_base']}_{variant}"
        temp_output = output_dir / f"{stem}.bin"

        recovery_result = dat_recoverer.recover(dat_path, temp_output)
        ext = str(recovery_result.get("final_type") or "bin")
        output_file = output_dir / f"{stem}.{ext}"
        if temp_output != output_file:
            temp_output.replace(output_file)
        recovery_result.update(
            {
                "variant": variant,
                "output_file": str(output_file),
                "image_lookup": result,
            }
        )
        return recovery_result

    @staticmethod
    def _select_variant_path(result: dict[str, object], variant: str) -> Path:
        variant_map = {
            "thumb": Path(str(result["thumb_file_path"])),
            "hd": Path(str(result["hd_file_path"])),
            "main": Path(str(result["main_file_path"])),
        }
        if variant not in variant_map:
            raise ValueError(f"unsupported variant: {variant}")

        dat_path = variant_map[variant]
        if not dat_path.exists():
            raise FileNotFoundError(f"dat file not found: {dat_path}")
        return dat_path

    @staticmethod
    def _extract_file_base(packed_info_data: bytes | str | None) -> str:
        if not packed_info_data:
            raise ValueError("packed_info_data is empty")

        if isinstance(packed_info_data, str):
            data = packed_info_data.encode("utf-8", errors="ignore")
        else:
            data = packed_info_data

        hex_chars: list[str] = []
        for byte in data:
            char = chr(byte)
            if char in "0123456789abcdefABCDEF":
                hex_chars.append(char.lower())
                if len(hex_chars) == 32:
                    return "".join(hex_chars)
            else:
                hex_chars.clear()

        raise ValueError("failed to extract image file base from packed_info_data")

    @staticmethod
    def _format_month_dir(create_time: int) -> str:
        return datetime.fromtimestamp(create_time).strftime("%Y-%m")

    @staticmethod
    def _sqlite_md5(value: str | bytes | None) -> bytes:
        if value is None:
            return b""
        if isinstance(value, str):
            value = value.encode("utf-8")
        return hashlib.md5(value).digest()

    def _fetch_message(self, conn: sqlite3.Connection, msg_table: str, local_id: int) -> sqlite3.Row:
        query = f"""
            SELECT local_id, server_id, local_type, real_sender_id, create_time, packed_info_data
            FROM [{msg_table}]
            WHERE local_id = ?
        """
        row = conn.execute(query, (local_id,)).fetchone()
        if row is None:
            raise ValueError(f"message not found: table={msg_table}, local_id={local_id}")
        if row["local_type"] != 3:
            raise ValueError(
                f"message is not an image: table={msg_table}, local_id={local_id}, local_type={row['local_type']}"
            )
        return row

    def _fetch_resource_info(
        self,
        conn: sqlite3.Connection,
        msg_table: str,
        message: sqlite3.Row,
    ) -> sqlite3.Row | None:
        chat_md5 = msg_table.removeprefix("Msg_")
        query = """
            SELECT
                mri.message_id,
                mri.chat_id,
                mri.sender_id,
                mri.message_local_type,
                mri.message_create_time,
                mri.message_local_id,
                mri.message_svr_id
            FROM message_resource.MessageResourceInfo AS mri
            WHERE mri.message_local_id = ?
              AND mri.message_svr_id = ?
              AND mri.message_create_time = ?
              AND mri.message_local_type = ?
              AND EXISTS (
                    SELECT 1
                    FROM message_resource.ChatName2Id AS c
                    WHERE c.rowid = mri.chat_id
                      AND lower(hex(md5(c.user_name))) = ?
              )
        """
        conn.create_function("md5", 1, self._sqlite_md5)
        return conn.execute(
            query,
            (
                message["local_id"],
                message["server_id"],
                message["create_time"],
                message["local_type"],
                chat_md5,
            ),
        ).fetchone()

    @staticmethod
    def _fetch_resource_details(
        conn: sqlite3.Connection,
        message_id: int,
    ) -> list[sqlite3.Row]:
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

    def _get_dat_recoverer(self) -> _WechatDatRecover:
        if self._dat_recoverer is None:
            self._dat_recoverer = _WechatDatRecover(self.key32)
        return self._dat_recoverer


WechatImageRecover = WechatImageParser

"""微信表情消息解析与素材恢复工具。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import av
from dotenv import load_dotenv
from PIL import Image
from Crypto.Cipher import AES

HEADER_MAGIC = bytes.fromhex("28b52ffd")
DEFAULT_EMOTICON_OUTPUT_DIR = Path("data") / "msg" / "emoticon"
EXPORT_INDEX_NAME = "index.json"


@dataclass
class EmoticonRecord:
    type: int
    md5: str
    caption: str
    product_id: str
    aes_key: str
    thumb_url: str
    tp_url: str
    auth_key: str
    cdn_url: str
    extern_url: str
    extern_md5: str
    encrypt_url: str
    thumb_path: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class EmoticonMessageInfo:
    msg_table: str
    local_id: int
    server_id: int
    real_sender_id: int
    create_time: int
    packed_info_hex: str
    packed_info_flags: list[int]
    message_content_size: int
    message_content_sha1: str
    source_size: int
    source_sha1: str | None
    source_xml: str | None
    source_signature: str | None
    source_publisher_id: str | None
    message_ascii_fragments: list[str]
    source_ascii_fragments: list[str]
    message_fingerprint: str
    exact_md5: str | None
    emoticon_record: EmoticonRecord | None
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        if self.emoticon_record is not None:
            data["emoticon_record"] = self.emoticon_record.to_dict()
        return data


@dataclass
class StoreEmoticonAsset:
    md5: str
    package_id: str
    package_path: str
    emoticon_size: int
    emoticon_offset: int
    thumb_size: int
    thumb_offset: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WechatEmoticonParser:
    """解析 local_type=47 的表情消息和 emoticon.db 元信息。"""

    def __init__(
        self,
        message_db_path: Path,
        emoticon_db_path: Path,
        account_root: Path,
    ) -> None:
        self.message_db_path = Path(message_db_path)
        self.emoticon_db_path = Path(emoticon_db_path)
        self.account_root = Path(account_root)

    @classmethod
    def from_env(cls) -> WechatEmoticonParser:
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
            emoticon_db_path=Path(
                os.getenv("EMOTICON_DB_PATH", str(decrypted_db_dir / "emoticon.db"))
            ).expanduser(),
            account_root=Path(os.environ["WECHAT_ROOT"]).expanduser(),
        )

    def get_emoticon_record(self, md5: str) -> EmoticonRecord | None:
        with sqlite3.connect(self.emoticon_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM kNonStoreEmoticonTable WHERE md5 = ? LIMIT 1",
                (md5,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_emoticon_record(row)

    def list_emoticon_records(self, limit: int = 100) -> list[EmoticonRecord]:
        with sqlite3.connect(self.emoticon_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM kNonStoreEmoticonTable ORDER BY md5 ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_emoticon_record(row) for row in rows]

    def list_message_fingerprints(
        self,
        msg_table: str,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", msg_table):
            raise ValueError(f"invalid message table name: {msg_table}")

        with sqlite3.connect(self.message_db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    hex(message_content) AS message_hex,
                    COUNT(*) AS message_count,
                    MIN(local_id) AS min_local_id,
                    MAX(local_id) AS max_local_id,
                    MIN(create_time) AS min_create_time,
                    MAX(create_time) AS max_create_time
                FROM [{msg_table}]
                WHERE local_type = 47
                GROUP BY message_hex
                ORDER BY message_count DESC, min_local_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        fingerprints: list[dict[str, object]] = []
        for message_hex, message_count, min_local_id, max_local_id, min_create_time, max_create_time in rows:
            message_bytes = bytes.fromhex(message_hex)
            message_fingerprint = hashlib.sha1(message_bytes).hexdigest()
            fingerprints.append(
                {
                    "message_fingerprint": message_fingerprint,
                    "message_count": int(message_count),
                    "min_local_id": int(min_local_id),
                    "max_local_id": int(max_local_id),
                    "min_create_time": int(min_create_time),
                    "max_create_time": int(max_create_time),
                    "message_size": len(message_bytes),
                    "packed_info_candidates": self._list_packed_info_candidates(
                        msg_table,
                        message_hex,
                    ),
                    "ascii_fragments": self._extract_ascii_fragments(message_bytes),
                }
            )
        return fingerprints

    def find_emoticon_message_info(self, msg_table: str, local_id: int) -> EmoticonMessageInfo:
        message = self._fetch_message(msg_table, local_id)
        source_bytes = self._ensure_bytes(message["source"])
        message_bytes = self._ensure_bytes(message["message_content"])
        source_xml = self._extract_xml_fragment(source_bytes)
        exact_md5 = self._extract_md5_candidate(message_bytes)
        emoticon_record = None if exact_md5 is None else self.get_emoticon_record(exact_md5)

        notes: list[str] = []
        if message["local_type"] != 47:
            notes.append("local_type is not 47")
        if exact_md5 is None:
            notes.append("message_content 中未直接提取到 32 位 md5")
        if source_xml is not None:
            notes.append("source 中存在可提取的 XML 片段")
        if emoticon_record is None:
            notes.append("当前未建立 message_content/source 到 emoticon.db.md5 的稳定映射")

        return EmoticonMessageInfo(
            msg_table=msg_table,
            local_id=int(message["local_id"]),
            server_id=int(message["server_id"]),
            real_sender_id=int(message["real_sender_id"]),
            create_time=int(message["create_time"]),
            packed_info_hex=self._ensure_bytes(message["packed_info_data"]).hex(),
            packed_info_flags=list(self._ensure_bytes(message["packed_info_data"])),
            message_content_size=len(message_bytes),
            message_content_sha1=hashlib.sha1(message_bytes).hexdigest(),
            source_size=len(source_bytes),
            source_sha1=None if not source_bytes else hashlib.sha1(source_bytes).hexdigest(),
            source_xml=source_xml,
            source_signature=self._extract_xml_tag(source_xml, "signature"),
            source_publisher_id=self._extract_xml_tag(source_xml, "publisher-id"),
            message_ascii_fragments=self._extract_ascii_fragments(message_bytes),
            source_ascii_fragments=self._extract_ascii_fragments(source_bytes),
            message_fingerprint=hashlib.sha1(message_bytes).hexdigest(),
            exact_md5=exact_md5,
            emoticon_record=emoticon_record,
            notes=notes,
        )

    def export_emoticon(
        self,
        msg_table: str,
        local_id: int,
        output_dir: Path | None = None,
    ) -> str:
        message = self._fetch_message(msg_table, local_id)
        if int(message["local_type"]) != 47:
            raise ValueError(f"message is not emoticon: table={msg_table}, local_id={local_id}")

        output_dir = DEFAULT_EMOTICON_OUTPUT_DIR if output_dir is None else Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / EXPORT_INDEX_NAME
        export_index = self._load_export_index(index_path)

        info = self.find_emoticon_message_info(msg_table, local_id)
        existing_path = self._find_existing_export(
            export_index,
            output_dir,
            info.message_fingerprint,
            info.exact_md5,
        )
        if existing_path is not None:
            return str(existing_path)

        candidate_assets = self._collect_candidate_assets(info)
        if not candidate_assets:
            raise FileNotFoundError(
                f"no emoticon asset candidate found: table={msg_table}, local_id={local_id}, "
                f"fingerprint={info.message_fingerprint}"
            )

        decode_errors: list[str] = []
        for candidate_name, candidate_bytes, aes_key_hex in candidate_assets:
            try:
                decoded_bytes, file_extension = self._decode_candidate_asset(
                    candidate_bytes,
                    aes_key_hex=aes_key_hex,
                )
            except ValueError as exc:
                decode_errors.append(f"{candidate_name}: {exc}")
                continue

            target_path = output_dir / f"{info.message_fingerprint}.{file_extension}"
            self._replace_existing_file(target_path)
            target_path.write_bytes(decoded_bytes)
            self._upsert_export_index(
                export_index,
                info.message_fingerprint,
                {
                    "message_fingerprint": info.message_fingerprint,
                    "exported_path": target_path.name,
                    "format": file_extension,
                    "md5": info.exact_md5,
                    "source_local_id": info.local_id,
                    "source_msg_table": info.msg_table,
                },
            )
            self._save_export_index(index_path, export_index)
            return str(target_path)

        raise RuntimeError(
            "failed to decode emoticon asset to a readable image: "
            f"table={msg_table}, local_id={local_id}, candidates={len(candidate_assets)}, "
            f"errors={decode_errors}"
        )

    def export_emoticon_thumb(self, md5: str, output_dir: Path) -> str:
        source_path = self.find_thumb_path(md5)
        if source_path is None or not source_path.exists():
            raise FileNotFoundError(f"emoticon thumb not found: {md5}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target_path = output_dir / f"{md5}.thumb"
        if target_path.exists():
            target_path.chmod(target_path.stat().st_mode | 0o200)
            target_path.unlink()
        target_path.write_bytes(source_path.read_bytes())
        return str(target_path)

    def find_thumb_path(self, md5: str) -> Path | None:
        thumb_path = self.account_root / "business" / "emoticon" / "Thumb" / md5[:2] / f"{md5}.thumb"
        return thumb_path if thumb_path.exists() else None

    def find_persist_path(self, md5: str) -> Path | None:
        persist_path = self.account_root / "business" / "emoticon" / "Persist" / md5[:2] / md5
        return persist_path if persist_path.exists() else None

    def find_cache_paths(self, md5: str) -> list[Path]:
        cache_root = self.account_root / "cache"
        if not cache_root.exists():
            return []
        paths = sorted(cache_root.glob(f"*/Emoticon/{md5[:2]}/{md5}"))
        return [path for path in paths if path.exists()]

    def find_store_package_path(self, package_id: str) -> Path | None:
        package_hash = hashlib.md5(package_id.encode("utf-8")).hexdigest()
        package_path = (
            self.account_root
            / "business"
            / "emoticon"
            / "PersistStore"
            / package_hash[:2]
            / package_hash
        )
        return package_path if package_path.exists() else None

    def export_thumb_by_md5_list(self, md5_values: list[str], output_dir: Path) -> list[str]:
        exported: list[str] = []
        for md5 in md5_values:
            try:
                exported.append(self.export_emoticon_thumb(md5, output_dir))
            except FileNotFoundError:
                continue
        return exported

    def _collect_candidate_assets(
        self,
        info: EmoticonMessageInfo,
    ) -> list[tuple[str, bytes, str | None]]:
        candidates: list[tuple[str, bytes, str | None]] = []
        record = info.emoticon_record
        if record is not None:
            if record.thumb_path:
                thumb_path = Path(record.thumb_path)
                candidates.append((str(thumb_path), thumb_path.read_bytes(), record.aes_key or None))
            persist_path = self.find_persist_path(record.md5)
            if persist_path is not None:
                candidates.append((str(persist_path), persist_path.read_bytes(), record.aes_key or None))
            for cache_path in self.find_cache_paths(record.md5):
                candidates.append((str(cache_path), cache_path.read_bytes(), record.aes_key or None))
            if record.extern_md5:
                extern_persist_path = self.find_persist_path(record.extern_md5)
                if extern_persist_path is not None:
                    candidates.append(
                        (str(extern_persist_path), extern_persist_path.read_bytes(), record.aes_key or None)
                    )
                for cache_path in self.find_cache_paths(record.extern_md5):
                    candidates.append((str(cache_path), cache_path.read_bytes(), record.aes_key or None))

        if info.exact_md5:
            store_asset = self.get_store_emoticon_asset(info.exact_md5)
            if store_asset is not None:
                package_bytes = Path(store_asset.package_path).read_bytes()
                emoticon_end = store_asset.emoticon_offset + store_asset.emoticon_size
                thumb_end = store_asset.thumb_offset + store_asset.thumb_size
                candidates.append(
                    (
                        f"{store_asset.package_path}#emoticon:{store_asset.md5}",
                        package_bytes[store_asset.emoticon_offset:emoticon_end],
                        None,
                    )
                )
                candidates.append(
                    (
                        f"{store_asset.package_path}#thumb:{store_asset.md5}",
                        package_bytes[store_asset.thumb_offset:thumb_end],
                        None,
                    )
                )

        unique_candidates: list[tuple[str, bytes, str | None]] = []
        seen: set[str] = set()
        for candidate_name, candidate_bytes, aes_key_hex in candidates:
            candidate_key = f"{candidate_name}:{hashlib.sha1(candidate_bytes).hexdigest()}"
            if candidate_key in seen or not candidate_bytes:
                continue
            seen.add(candidate_key)
            unique_candidates.append((candidate_name, candidate_bytes, aes_key_hex))
        return unique_candidates

    def _decode_candidate_asset(
        self,
        candidate_bytes: bytes,
        aes_key_hex: str | None,
    ) -> tuple[bytes, str]:
        direct_decode = self._decode_standard_image_bytes(candidate_bytes)
        if direct_decode is not None:
            return direct_decode

        if aes_key_hex:
            for aes_key in self._iter_aes_keys(aes_key_hex):
                for decrypted_bytes in self._iter_aes_candidates(candidate_bytes, aes_key):
                    decoded = self._decode_standard_image_bytes(decrypted_bytes)
                    if decoded is not None:
                        return decoded

        raise ValueError("candidate is not a readable standard image or gif/webp asset")

    @staticmethod
    def _iter_aes_keys(aes_key_hex: str) -> list[bytes]:
        candidates: list[bytes] = []
        try:
            candidates.append(bytes.fromhex(aes_key_hex))
        except ValueError:
            pass

        aes_key_utf8 = aes_key_hex.encode("utf-8")
        if len(aes_key_utf8) in {16, 24, 32} and aes_key_utf8 not in candidates:
            candidates.append(aes_key_utf8)
        return candidates

    @staticmethod
    def _iter_aes_candidates(raw_bytes: bytes, aes_key: bytes) -> list[bytes]:
        candidates: list[bytes] = []
        if len(raw_bytes) >= 12:
            for nonce in (raw_bytes[:12], raw_bytes[-12:]):
                try:
                    payload = raw_bytes[12:] if nonce == raw_bytes[:12] else raw_bytes[:-12]
                    candidates.append(AES.new(aes_key, AES.MODE_GCM, nonce=nonce).decrypt(payload))
                except Exception:
                    pass

        if len(raw_bytes) < 16:
            return candidates

        aligned = raw_bytes[: len(raw_bytes) // 16 * 16]
        if not aligned:
            return candidates

        try:
            candidates.append(AES.new(aes_key, AES.MODE_ECB).decrypt(aligned))
        except Exception:
            pass
        try:
            candidates.append(AES.new(aes_key, AES.MODE_CBC, iv=b"\x00" * 16).decrypt(aligned))
        except Exception:
            pass
        if len(raw_bytes) > 16 and (len(raw_bytes) - 16) % 16 == 0:
            try:
                candidates.append(
                    AES.new(aes_key, AES.MODE_CBC, iv=raw_bytes[:16]).decrypt(raw_bytes[16:])
                )
            except Exception:
                pass
        return candidates

    def get_store_emoticon_asset(self, md5: str) -> StoreEmoticonAsset | None:
        with sqlite3.connect(self.emoticon_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT
                    package_id_,
                    md5_,
                    emoticon_size_,
                    emoticon_offset_,
                    thumb_size_,
                    thumb_offset_
                FROM kStoreEmoticonFilesTable
                WHERE md5_ = ?
                LIMIT 1
                """,
                (md5,),
            ).fetchone()
        if row is None:
            return None

        package_path = self.find_store_package_path(str(row["package_id_"]))
        if package_path is None:
            return None

        return StoreEmoticonAsset(
            md5=str(row["md5_"]),
            package_id=str(row["package_id_"]),
            package_path=str(package_path),
            emoticon_size=int(row["emoticon_size_"]),
            emoticon_offset=int(row["emoticon_offset_"]),
            thumb_size=int(row["thumb_size_"]),
            thumb_offset=int(row["thumb_offset_"]),
        )

    @staticmethod
    def _decode_standard_image_bytes(data: bytes) -> tuple[bytes, str] | None:
        if not data:
            return None

        if WechatEmoticonParser._has_known_magic(data):
            detected_extension = WechatEmoticonParser._detect_extension(data)
            if detected_extension is not None:
                return data, detected_extension

        try:
            image = Image.open(io.BytesIO(data))
            image_format = (image.format or "").lower()
            extension = "jpg" if image_format == "jpeg" else image_format
            if extension in {"jpg", "png", "gif", "webp"}:
                return data, extension
        except Exception:
            pass

        try:
            with av.open(io.BytesIO(data), mode="r") as container:
                format_names = (container.format.name or "").split(",")
                for format_name in format_names:
                    extension = format_name.strip().lower()
                    if extension in {"gif", "webp", "png", "jpeg", "jpg"}:
                        return data, "jpg" if extension == "jpeg" else extension
        except Exception:
            pass

        return None

    @staticmethod
    def _has_known_magic(data: bytes) -> bool:
        return (
            data.startswith(b"\xff\xd8\xff")
            or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"GIF8")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        )

    @staticmethod
    def _detect_extension(data: bytes) -> str | None:
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if data.startswith(b"GIF8"):
            return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        return None

    @staticmethod
    def _replace_existing_file(target_path: Path) -> None:
        if not target_path.exists():
            return
        target_path.chmod(target_path.stat().st_mode | 0o200)
        target_path.unlink()

    @staticmethod
    def _load_export_index(index_path: Path) -> dict[str, dict[str, object]]:
        if not index_path.exists():
            return {}
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): value
            for key, value in data.items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _save_export_index(index_path: Path, export_index: dict[str, dict[str, object]]) -> None:
        index_path.write_text(
            json.dumps(export_index, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _upsert_export_index(
        export_index: dict[str, dict[str, object]],
        message_fingerprint: str,
        record: dict[str, object],
    ) -> None:
        export_index[message_fingerprint] = record

    @staticmethod
    def _find_existing_export(
        export_index: dict[str, dict[str, object]],
        output_dir: Path,
        message_fingerprint: str,
        md5: str | None,
    ) -> Path | None:
        record = export_index.get(message_fingerprint)
        if record is not None:
            candidate_path = output_dir / str(record.get("exported_path", ""))
            if candidate_path.exists():
                return candidate_path

        if md5:
            for item in export_index.values():
                if item.get("md5") != md5:
                    continue
                candidate_path = output_dir / str(item.get("exported_path", ""))
                if candidate_path.exists():
                    return candidate_path

        return None

    @staticmethod
    def _extract_md5_candidate(data: bytes) -> str | None:
        if not data:
            return None
        matches = re.findall(rb"[0-9a-f]{32}", data)
        if not matches:
            return None
        return matches[0].decode("ascii")

    @staticmethod
    def _extract_ascii_fragments(data: bytes, limit: int = 20) -> list[str]:
        if not data:
            return []
        text = "".join(chr(value) if 32 <= value < 127 else " " for value in data)
        fragments = [fragment for fragment in text.split() if len(fragment) >= 4]
        return fragments[:limit]

    @staticmethod
    def _extract_xml_fragment(data: bytes) -> str | None:
        if not data:
            return None
        for marker in (b"<?xml", b"<msgsource", b"<msg", b"<emoji"):
            start = data.find(marker)
            if start < 0:
                continue
            snippet = data[start:].decode("utf-8", errors="ignore")
            return snippet.strip() or None
        return None

    @staticmethod
    def _extract_xml_tag(xml_text: str | None, tag_name: str) -> str | None:
        if not xml_text:
            return None
        match = re.search(
            rf"<{re.escape(tag_name)}>(.*?)</",
            xml_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return None
        value = match.group(1).strip()
        return value or None

    @staticmethod
    def _ensure_bytes(value: bytes | str | None) -> bytes:
        if value is None:
            return b""
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8", errors="ignore")

    def _fetch_message(self, msg_table: str, local_id: int) -> sqlite3.Row:
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", msg_table):
            raise ValueError(f"invalid message table name: {msg_table}")
        with sqlite3.connect(self.message_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"""
                SELECT
                    local_id,
                    server_id,
                    local_type,
                    real_sender_id,
                    create_time,
                    source,
                    message_content,
                    packed_info_data
                FROM [{msg_table}]
                WHERE local_id = ?
                LIMIT 1
                """,
                (local_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"message not found: table={msg_table}, local_id={local_id}")
        return row

    def _list_packed_info_candidates(
        self,
        msg_table: str,
        message_hex: str,
    ) -> list[str]:
        with sqlite3.connect(self.message_db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT hex(packed_info_data)
                FROM [{msg_table}]
                WHERE local_type = 47
                  AND packed_info_data IS NOT NULL
                  AND hex(message_content) = ?
                """,
                (message_hex,),
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _row_to_emoticon_record(self, row: sqlite3.Row) -> EmoticonRecord:
        md5 = str(row["md5"])
        thumb_path = self.find_thumb_path(md5)
        return EmoticonRecord(
            type=int(row["type"]),
            md5=md5,
            caption=str(row["caption"] or ""),
            product_id=str(row["product_id"] or ""),
            aes_key=str(row["aes_key"] or ""),
            thumb_url=str(row["thumb_url"] or ""),
            tp_url=str(row["tp_url"] or ""),
            auth_key=str(row["auth_key"] or ""),
            cdn_url=str(row["cdn_url"] or ""),
            extern_url=str(row["extern_url"] or ""),
            extern_md5=str(row["extern_md5"] or ""),
            encrypt_url=str(row["encrypt_url"] or ""),
            thumb_path=None if thumb_path is None else str(thumb_path),
        )

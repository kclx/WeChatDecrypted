"""微信图片 dat 文件的恢复与格式探测工具。"""

from __future__ import annotations

import io
import os
import struct
import zlib
from pathlib import Path

import av
from Crypto.Cipher import AES
from dotenv import load_dotenv


HEADER_LEN = 15
BLOCK1_XOR_KEY = 0xB5
WXGF_MAGIC = b"wxgf"
WXGF_PATTERNS = (b"\x00\x00\x00\x01", b"\x00\x00\x01")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGIC = b"GIF8"
BMP_MAGIC = b"BM"
TIFF_LE_MAGIC = b"II*\x00"
TIFF_BE_MAGIC = b"MM\x00*"


class _WechatDatRecover:
    def __init__(self, key32: str) -> None:
        key32_ascii = key32.encode("ascii")
        if len(key32_ascii) != 32:
            raise ValueError("key32 must be exactly 32 ASCII chars")
        self.key32 = key32
        self.key16_ascii = key32_ascii[:16]

    @staticmethod
    def align16(value: int) -> int:
        return ((value + 15) // 16) * 16

    def decrypt_block0(self, block0: bytes) -> bytes:
        return AES.new(self.key16_ascii, AES.MODE_ECB).decrypt(block0)

    @staticmethod
    def remove_padding(data: bytes, flag: int) -> bytes:
        if flag != 1 or not data:
            return data
        pad = data[-1]
        if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
            return data[:-pad]
        return data

    @staticmethod
    def decode_block1(block1: bytes) -> bytes:
        return bytes(byte ^ BLOCK1_XOR_KEY for byte in block1)

    @staticmethod
    def detect_image_type(data: bytes) -> str | None:
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if data.startswith(PNG_MAGIC):
            return "png"
        if data.startswith(GIF_MAGIC):
            return "gif"
        if data.startswith(BMP_MAGIC):
            return "bmp"
        if data.startswith(TIFF_LE_MAGIC) or data.startswith(TIFF_BE_MAGIC):
            return "tiff"
        return None

    @staticmethod
    def _iter_png_chunks(data: bytes) -> list[tuple[int, int, bytes, int]]:
        if not data.startswith(PNG_MAGIC):
            raise ValueError("not a png stream")

        chunks: list[tuple[int, int, bytes, int]] = []
        offset = len(PNG_MAGIC)
        while offset + 8 <= len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(data):
                raise ValueError(f"png chunk truncated at {offset:#x}")
            if not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type):
                raise ValueError(f"invalid png chunk type at {offset:#x}: {chunk_type!r}")

            crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
            calc = zlib.crc32(data[offset + 4 : offset + 8 + length]) & 0xFFFFFFFF
            if crc != calc:
                raise ValueError(
                    f"png crc mismatch at {offset:#x}: expected {crc:#x}, got {calc:#x}"
                )

            chunks.append((offset, length, chunk_type, chunk_end))
            offset = chunk_end
            if chunk_type == b"IEND":
                return chunks

        raise ValueError("png stream missing IEND")

    @classmethod
    def _validate_png(cls, data: bytes) -> bool:
        try:
            chunks = cls._iter_png_chunks(data)
            idat_payload = b"".join(
                data[offset + 8 : offset + 8 + length]
                for offset, length, chunk_type, _ in chunks
                if chunk_type == b"IDAT"
            )
            zlib.decompress(idat_payload)
            return True
        except Exception:
            return False

    @classmethod
    def _repair_png_full_tail_fallback(cls, raw_png: bytes, xor_png: bytes) -> bytes | None:
        if not raw_png.startswith(PNG_MAGIC) or not xor_png.startswith(PNG_MAGIC):
            return None

        raw_chunks: list[tuple[int, int, bytes, int]] = []
        offset = len(PNG_MAGIC)
        transition_chunk: tuple[int, int, bytes, int] | None = None

        while offset + 8 <= len(raw_png):
            length = struct.unpack(">I", raw_png[offset : offset + 4])[0]
            chunk_type = raw_png[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(raw_png):
                break
            if not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type):
                break

            crc = struct.unpack(">I", raw_png[offset + 8 + length : chunk_end])[0]
            calc = zlib.crc32(raw_png[offset + 4 : offset + 8 + length]) & 0xFFFFFFFF
            if crc == calc:
                raw_chunks.append((offset, length, chunk_type, chunk_end))
                offset = chunk_end
                if chunk_type == b"IEND":
                    return raw_png
                continue

            transition_chunk = (offset, length, chunk_type, chunk_end)
            break

        if transition_chunk is None or transition_chunk[2] != b"IDAT":
            return None

        transition_offset, transition_length, _, transition_end = transition_chunk
        raw_payload = raw_png[transition_offset + 8 : transition_offset + 8 + transition_length]
        xor_payload = xor_png[transition_offset + 8 : transition_offset + 8 + transition_length]
        raw_crc = raw_png[transition_offset + 8 + transition_length : transition_end]
        xor_crc = xor_png[transition_offset + 8 + transition_length : transition_end]

        switch_index: int | None = None
        use_xor_crc = False
        for index in range(transition_length + 1):
            payload = raw_payload[:index] + xor_payload[index:]
            calc = zlib.crc32(b"IDAT" + payload) & 0xFFFFFFFF
            if calc == struct.unpack(">I", xor_crc)[0]:
                switch_index = index
                use_xor_crc = True
                break
            if calc == struct.unpack(">I", raw_crc)[0]:
                switch_index = index
                use_xor_crc = False
                break

        if switch_index is None:
            return None

        repaired = bytearray(raw_png[:transition_offset])
        repaired.extend(raw_png[transition_offset : transition_offset + 8])
        repaired.extend(raw_payload[:switch_index])
        repaired.extend(xor_payload[switch_index:])
        repaired.extend(xor_crc if use_xor_crc else raw_crc)
        repaired.extend(xor_png[transition_end:])

        candidate = bytes(repaired)
        return candidate if cls._validate_png(candidate) else None

    @staticmethod
    def find_wxgf_partition(data: bytes) -> tuple[int, int] | None:
        if len(data) < 5 or not data.startswith(WXGF_MAGIC):
            return None

        header_len = data[4]
        if header_len >= len(data):
            return None

        best: tuple[int, int] | None = None
        best_ratio = -1.0
        for pattern in WXGF_PATTERNS:
            offset = 0
            while header_len + offset <= len(data):
                index = data.find(pattern, header_len + offset)
                if index < 0:
                    break
                if index < 4:
                    offset = index - header_len + 1
                    continue

                length = int.from_bytes(data[index - 4 : index], "big")
                if length <= 0 or index + length > len(data):
                    offset = index - header_len + 1
                    continue

                ratio = length / len(data)
                if ratio > best_ratio:
                    best = (index, length)
                    best_ratio = ratio
                offset = index - header_len + length

        return best

    def convert_wxgf_to_jpg(self, data: bytes) -> tuple[bytes, dict[str, int]]:
        partition = self.find_wxgf_partition(data)
        if partition is None:
            raise ValueError("wxgf partition not found")
        offset, size = partition
        payload = data[offset : offset + size]

        try:
            with av.open(io.BytesIO(payload), mode="r") as container:
                frame = next(container.decode(video=0), None)
        except av.AVError as exc:
            raise RuntimeError(f"PyAV failed to decode wxgf payload: {exc}") from exc

        if frame is None:
            raise RuntimeError("PyAV did not return a video frame")

        image = frame.to_image()
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=92)
        jpg_bytes = output.getvalue()
        if not jpg_bytes.startswith(b"\xff\xd8\xff"):
            raise RuntimeError("PyAV output is not a JPEG")

        return jpg_bytes, {"partition_offset": offset, "partition_size": size}

    def recover(self, dat_file: Path, output: Path | None = None) -> dict[str, object]:
        raw = Path(dat_file).read_bytes()
        if len(raw) < HEADER_LEN:
            raise ValueError("file too small")

        header = raw[:HEADER_LEN]
        if header[:3] != b"\x07\x08\x56" or header[3:4] != b"2" or header[4:6] != b"\x08\x07":
            raise ValueError(f"unexpected header signature: {header.hex()}")

        payload0 = struct.unpack("<I", header[6:10])[0]
        block1_size = struct.unpack("<I", header[10:14])[0]
        flag = header[14]
        block0_readlen = self.align16(payload0) + 0x10
        expected_size = HEADER_LEN + block0_readlen + block1_size
        size_mode = "header"
        if expected_size != len(raw):
            # Some _h.dat samples pin block1_size to 0x100000 in the header,
            # while the actual payload continues until EOF.
            if payload0 == 1024 and block1_size == 0x100000 and len(raw) > expected_size:
                block1_size = len(raw) - HEADER_LEN - block0_readlen
                expected_size = len(raw)
                size_mode = "full_tail_fallback"
            else:
                raise ValueError(
                    f"size mismatch: expected {expected_size:#x}, actual {len(raw):#x}"
                )

        block0 = raw[HEADER_LEN : HEADER_LEN + block0_readlen]
        block1 = raw[HEADER_LEN + block0_readlen : expected_size]

        dec0 = self.remove_padding(self.decrypt_block0(block0), flag)
        raw_tail_container = dec0 + block1
        dec1 = self.decode_block1(block1)
        container = dec0 + dec1
        recovered = container
        container_type = "binary"
        final_type = "bin"
        wxgf_info: dict[str, int] | None = None

        if (
            size_mode == "full_tail_fallback"
            and raw_tail_container.startswith(PNG_MAGIC)
            and container.startswith(PNG_MAGIC)
            and not self._validate_png(container)
        ):
            repaired_png = self._repair_png_full_tail_fallback(raw_tail_container, container)
            if repaired_png is not None:
                container = repaired_png
                recovered = repaired_png

        if container.startswith(WXGF_MAGIC):
            container_type = "wxgf"
            recovered, wxgf_info = self.convert_wxgf_to_jpg(container)
            final_type = "jpg"
        else:
            image_type = self.detect_image_type(container)
            if image_type is not None:
                container_type = image_type
                final_type = image_type

        if output is not None:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(recovered)

        return {
            "dat_file": str(dat_file),
            "header": header.hex(),
            "payload0": payload0,
            "block0_readlen": block0_readlen,
            "block1_size": block1_size,
            "flag": flag,
            "size_mode": size_mode,
            "key16_ascii": self.key16_ascii.decode("ascii"),
            "dec0_len": len(dec0),
            "dec1_len": len(dec1),
            "block1_xor_key": BLOCK1_XOR_KEY,
            "container_type": container_type,
            "final_type": final_type,
            "wxgf_info": wxgf_info,
            "output": str(output) if output is not None else None,
            "recovered_bytes": recovered,
            "container_bytes": container,
        }


def recover_wechat_dat(dat_file: Path, key32: str, output: Path | None = None) -> dict[str, object]:
    return _WechatDatRecover(key32).recover(dat_file, output)


def recover_wechat_dat_from_env(
    dat_file: Path,
    output: Path | None = None,
) -> dict[str, object]:
    load_dotenv()
    return recover_wechat_dat(dat_file, os.environ["KEY32"], output)

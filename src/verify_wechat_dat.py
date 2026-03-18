from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path


HEADER_LEN = 15
BLOCK1_XOR_KEY = 0xB5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify WeChat image .dat container by decrypting block0."
    )
    parser.add_argument("dat_file", type=Path, help="Path to _t.dat or _h.dat")
    parser.add_argument(
        "--key32",
        required=True,
        help="32-byte ASCII blob captured at runtime; first 16 ASCII bytes are used as AES-128 key",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/wechat_dat_test.jpg"),
        help="Output file path for decrypted block0 plus raw block1",
    )
    return parser.parse_args()


def align16(value: int) -> int:
    return ((value + 15) // 16) * 16


def decrypt_block0(block0: bytes, key16_ascii: bytes) -> bytes:
    key_hex = key16_ascii.hex()
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-ecb",
            "-d",
            "-nopad",
            "-nosalt",
            "-K",
            key_hex,
        ],
        input=block0,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout


def remove_padding(data: bytes, flag: int) -> bytes:
    if flag != 1 or not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def decode_block1(block1: bytes) -> bytes:
    return bytes(byte ^ BLOCK1_XOR_KEY for byte in block1)


def recover_wechat_dat(dat_file: Path, key32: str, output: Path | None = None) -> dict[str, object]:
    raw = Path(dat_file).read_bytes()
    if len(raw) < HEADER_LEN:
        raise ValueError("file too small")

    header = raw[:HEADER_LEN]
    if header[:3] != b"\x07\x08\x56" or header[3:4] != b"2" or header[4:6] != b"\x08\x07":
        raise ValueError(f"unexpected header signature: {header.hex()}")

    payload0 = struct.unpack("<I", header[6:10])[0]
    block1_size = struct.unpack("<I", header[10:14])[0]
    flag = header[14]
    block0_readlen = align16(payload0) + 0x10
    expected_size = HEADER_LEN + block0_readlen + block1_size
    if expected_size != len(raw):
        raise ValueError(
            f"size mismatch: expected {expected_size:#x}, actual {len(raw):#x}"
        )

    key32_ascii = key32.encode("ascii")
    if len(key32_ascii) != 32:
        raise ValueError("key32 must be exactly 32 ASCII chars")
    key16_ascii = key32_ascii[:16]

    block0 = raw[HEADER_LEN : HEADER_LEN + block0_readlen]
    block1 = raw[HEADER_LEN + block0_readlen : expected_size]

    dec0 = decrypt_block0(block0, key16_ascii)
    dec0 = remove_padding(dec0, flag)
    dec1 = decode_block1(block1)
    recovered = dec0 + dec1

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
        "key16_ascii": key16_ascii.decode("ascii"),
        "dec0_len": len(dec0),
        "dec1_len": len(dec1),
        "block1_xor_key": BLOCK1_XOR_KEY,
        "output": str(output) if output is not None else None,
        "recovered_bytes": recovered,
    }


def main() -> int:
    args = parse_args()
    result = recover_wechat_dat(args.dat_file, args.key32, args.output)

    print(f"dat_file={result['dat_file']}")
    print(f"header={result['header']}")
    print(f"payload0={result['payload0']:#x}")
    print(f"block0_readlen={result['block0_readlen']:#x}")
    print(f"block1_size={result['block1_size']:#x}")
    print(f"flag={result['flag']}")
    print(f"key16_ascii={result['key16_ascii']}")
    print(f"dec0_len={result['dec0_len']:#x}")
    print(f"dec1_len={result['dec1_len']:#x}")
    print(f"block1_xor_key={result['block1_xor_key']:#x}")
    print(f"out_file={args.output}")
    print(f"dec0_head={result['recovered_bytes'][:32].hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

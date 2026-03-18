"""探测并解密微信使用的候选 SQLCipher 数据库。

典型用法：

```python
from pathlib import Path

from wechat_sqlcipher_probe import WechatSQLCipherProbe

probe = WechatSQLCipherProbe()
result = probe.decrypt_first_page(Path("message.db"))
if result["header_ok"]:
    probe.decrypt_db(Path("message.db"), Path("message.decrypted.db"))
```

"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


SQLITE_HEADER = b"SQLite format 3\x00"


class WechatSQLCipherProbe:
    """微信数据库的 SQLCipher 探测与解密工具类。

    这个类用于在其他 Python 代码中直接导入和调用，
    不再依赖命令行入口。

    常见调用流程：
    1. 创建实例，按需覆盖 password、salt 或 OpenSSL 路径。
    2. 调用 `decrypt_first_page()` 判断数据库是否可正常解密。
    3. 调用 `decrypt_db()` 输出完整的解密后 SQLite 数据库。
    """

    def __init__(
        self,
        password: bytes | None = None,
        captured_salt: bytes | None = None,
        rounds: int = 256000,
        key_len: int = 32,
        openssl_path: str = "/opt/homebrew/bin/openssl",
    ) -> None:
        """初始化探测参数。

        参数说明：
            password: PBKDF2 使用的原始口令字节；未传入时必须由调用方显式提供。
            captured_salt: 仅用于比对的预期 salt；解密本身不依赖它。
            rounds: PBKDF2 迭代次数。
            key_len: 派生密钥长度，单位为字节。
            openssl_path: 执行 AES 解密时使用的 OpenSSL 可执行文件路径。
        """
        if password is None:
            raise ValueError("password is required")
        if captured_salt is None:
            raise ValueError("captured_salt is required")
        self.password = password
        self.captured_salt = captured_salt
        self.rounds = rounds
        self.key_len = key_len
        self.openssl_path = openssl_path

    def derive_key(self, salt: bytes) -> bytes:
        """根据数据库 salt 派生 SQLCipher 使用的页面密钥。"""
        return hashlib.pbkdf2_hmac(
            "sha512", self.password, salt, self.rounds, self.key_len
        )

    def openssl_decrypt(self, ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        """使用 OpenSSL 解密一段 AES-256-CBC 密文。"""
        proc = subprocess.run(
            [
                self.openssl_path,
                "enc",
                "-d",
                "-aes-256-cbc",
                "-nopad",
                "-K",
                key.hex(),
                "-iv",
                iv.hex(),
            ],
            input=ciphertext,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
        return proc.stdout

    def decrypt_page(self, ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        """解密单个数据库页中的有效载荷部分。"""
        return self.openssl_decrypt(ciphertext, key, iv)

    def decrypt_first_page(
        self,
        db_path: Path,
        page_size: int = 4096,
        reserve: int = 80,
    ) -> dict[str, object]:
        """探测第一页并返回诊断信息。

        参数说明：
            db_path: 已加密的微信数据库路径。
            page_size: SQLite 页大小。
            reserve: 每页尾部保留字节数。

        返回值：
            返回包含 salt、iv、key 等字段的字典，其中 `header_ok`
            表示重建后的第一页是否以标准 SQLite 头
            `SQLite format 3\\0` 开头。
        """
        raw = db_path.read_bytes()[:page_size]
        salt = raw[:16]
        reserve_block = raw[page_size - reserve : page_size]
        iv = reserve_block[:16]
        ciphertext = raw[16 : page_size - reserve]
        key = self.derive_key(salt)
        plaintext = self.decrypt_page(ciphertext, key, iv)
        reconstructed = SQLITE_HEADER + plaintext + reserve_block
        return {
            "salt": salt,
            "salt_matches_capture": salt == self.captured_salt,
            "iv": iv,
            "key": key,
            "plaintext": plaintext,
            "reconstructed": reconstructed,
            "header_ok": reconstructed.startswith(SQLITE_HEADER),
        }

    def decrypt_db(
        self,
        db_path: Path,
        out_path: Path,
        page_size: int = 4096,
        reserve: int = 80,
    ) -> bytes:
        """解密整个数据库，并将结果写入 `out_path`。

        参数说明：
            db_path: 输入的加密数据库路径。
            out_path: 输出的解密后 SQLite 数据库路径。
            page_size: SQLite 页大小。
            reserve: 每页尾部保留字节数。

        返回值：
            返回本次解密使用的派生密钥。
        """
        raw = db_path.read_bytes()
        if len(raw) % page_size:
            raise ValueError(
                f"{db_path} size is not divisible by page size {page_size}"
            )

        salt = raw[:16]
        key = self.derive_key(salt)
        out = bytearray()
        page_count = len(raw) // page_size

        for page_no in range(page_count):
            start = page_no * page_size
            page = raw[start : start + page_size]
            reserve_block = page[page_size - reserve : page_size]
            iv = reserve_block[:16]
            if page_no == 0:
                ciphertext = page[16 : page_size - reserve]
                plaintext = SQLITE_HEADER + self.decrypt_page(ciphertext, key, iv)
            else:
                ciphertext = page[: page_size - reserve]
                plaintext = self.decrypt_page(ciphertext, key, iv)
            out.extend(plaintext)
            out.extend(reserve_block)

        out_path.write_bytes(out)
        return key

# 微信数据库解密指南

## 文档目标

本文用于说明如何在 macOS 环境下定位、识别、分析并离线解密桌面版微信数据库。内容覆盖以下几个方面：

1. 微信聊天数据库的存储位置
2. 数据库无法直接打开的原因
3. 解密所需关键参数的获取方法
4. 数据库的离线解密流程
5. 解密结果的验证方式

本文默认前提如下：

- 操作系统：macOS 26.1
- 微信版本：桌面版 WeChat 4.1.8.27
- 当前机器已登录过目标微信账号
- 可使用终端，并具备管理员权限

文中的路径和示例命令均为脱敏后的示例值。复现时请替换为你自己的用户名、账号目录和文件路径。

## 复现结论

本文涉及的关键流程已经在当前机器上完整验证，以下数据库均已成功解密：

1. `contact.db`
2. `session.db`
3. `message_0.db`

## 一、数据库存储结构概览

微信本地聊天数据通常不会以明文形式直接存储，而是按业务拆分为多个数据库。常见划分如下：

- 消息库：保存消息正文及相关内容
- 联系人库：保存昵称、备注、头像地址等联系人信息
- 会话库：保存会话列表、会话摘要和最近消息元数据

在 macOS 上，微信相关数据通常位于以下目录：

```text
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

本文实际分析的账号目录示例如下：

```text
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001
```

对应的关键数据库路径如下：

```text
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/contact/contact.db
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/session/session.db
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/message/message_0.db
```

如果无法快速定位数据库文件，可以通过脚本递归搜索：

```python
from pathlib import Path


def find_databases(root_dir, db_names):
    root = Path(root_dir)
    results = []

    for db_name in db_names:
        results.extend(root.rglob(db_name))

    return results


paths = find_databases(
    # 这里需要填写绝对路径，不能直接使用 "~"
    "/Users/<你的用户名>/Library/Containers/com.tencent.xinWeChat",
    ["message_0.db", "session.db", "contact.db"],
)

for path in paths:
    print(path)
```

## 二、确认数据库是否已加密

### 1. 使用 `sqlite3` 直接尝试打开

```bash
sqlite3 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/contact/contact.db ".tables"
```

```bash
sqlite3 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/session/session.db ".tables"
```

```bash
sqlite3 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_001/db_storage/message/message_0.db ".tables"
```

如果返回类似以下错误：

```text
file is not a database
```

说明该文件不是明文 SQLite 数据库，而是经过加密后的数据库文件。

## 三、确认加密方案与 SQLCipher 相关

微信自身框架中包含 SQLCipher 相关符号，可以先做静态验证：

```bash
strings /Applications/WeChat.app/Contents/Frameworks/roam_server.framework/Versions/A/roam_server | rg "sqlcipher|cipher_page_size|hexkey|PBKDF2"
```

若输出中出现以下关键词之一或多个：

- `sqlcipher_export`
- `hexkey`
- `cipher_page_size`
- `cipher_plaintext_header_size`
- `PBKDF2_HMAC_SHA512`

则基本可以判断：微信使用了与 SQLCipher 相近的数据库加密方案，以及对应的密钥派生逻辑。

## 四、准备调试环境

如果需要从运行中的微信进程中提取解密参数，需要先为 `lldb` 调试准备系统权限。

### 1. 启用 Developer Tools 调试能力

```bash
sudo DevToolsSecurity -enable
```

### 2. 验证启用状态

```bash
DevToolsSecurity -status
```

正常情况下应看到类似输出：

```text
Developer mode is currently enabled.
```

### 3. 确认当前用户具备开发者组权限

```bash
id
```

如果输出中不包含 `_developer`，可执行：

```bash
sudo dseditgroup -o edit -a "$USER" -t user _developer
```

### 4. 在系统设置中授权终端

需要在 macOS 图形界面中手动完成以下授权：

- 打开“系统设置”
- 进入“隐私与安全性”
- 打开“开发者工具”
- 为当前使用的终端或宿主应用授予权限

如果授权后仍无法附加调试，通常需要完全退出并重新打开终端应用。

### 5. 关闭 SIP（如调试环境要求）

部分环境下，如果系统完整性保护阻止调试，可在恢复环境中执行：

```bash
csrutil disable
```

该步骤属于高权限系统级修改，仅在确有必要时执行。调试完成后建议重新启用 SIP。

## 五、在运行时提取解密参数

### 1. 冷启动微信

```bash
open -a /Applications/WeChat.app
```

### 2. 获取微信主进程 PID

```bash
for i in {1..20}; do pgrep -af '/Applications/WeChat.app/Contents/MacOS/WeChat$' && break; sleep 0.3; done
```

示例输出：

```text
28577
```

### 3. 使用 `lldb` 附加进程

```bash
lldb -p 28577
```

示例输出：

```text
Process 28577 stopped
* thread #1, queue = 'com.apple.main-thread', stop reason = signal SIGSTOP
    frame #0: 0x000000019b10ec34 libsystem_kernel.dylib`mach_msg2_trap + 8
libsystem_kernel.dylib`mach_msg2_trap:
->  0x19b10ec34 <+8>: ret

libsystem_kernel.dylib`macx_swapon:
    0x19b10ec38 <+0>: mov    x16, #-0x30
    0x19b10ec3c <+4>: svc    #0x80
    0x19b10ec40 <+8>: ret
Target 0: (WeChat) stopped.
Executable module set to "/Applications/WeChat.app/Contents/MacOS/WeChat".
Architecture set to: arm64-apple-macosx-.
```

### 4. 在 PBKDF 派生函数上设置断点

```lldb
br se -n CCKeyDerivationPBKDF
```

示例输出：

```text
Breakpoint 1: where = libcommonCrypto.dylib`CCKeyDerivationPBKDF, address = 0x00000001aaadc970
```

### 5. 继续执行并等待命中断点

```lldb
continue
```

示例输出：

```text
Process 28577 resuming
Process 28577 stopped
* thread #74, name = 'ThreadPoolForegroundWorker', stop reason = breakpoint 1.1
    frame #0: 0x00000001aaadc970 libcommonCrypto.dylib`CCKeyDerivationPBKDF
libcommonCrypto.dylib`CCKeyDerivationPBKDF:
->  0x1aaadc970 <+0>:  pacibsp
    0x1aaadc974 <+4>:  stp    x26, x25, [sp, #-0x50]!
    0x1aaadc978 <+8>:  stp    x24, x23, [sp, #0x10]
    0x1aaadc97c <+12>: stp    x22, x21, [sp, #0x20]
Target 0: (WeChat) stopped.
```

### 6. 读取关键寄存器

```lldb
register read x0 x1 x2 x3 x4 x5 x6 x7 sp
```

示例输出：

```text
      x0 = 0x0000000000000002
      x1 = 0x0000000c42ea31c0
      x2 = 0x0000000000000020
      x3 = 0x0000000c41442c20
      x4 = 0x0000000000000010
      x5 = 0x0000000000000005
      x6 = 0x000000000003e800
      x7 = 0x0000000c42ea3920
      sp = 0x000000017c12d090
```

### 7. 读取栈顶，确认派生密钥长度

```lldb
memory read --format x --size 8 --count 4 $sp
```

示例输出：

```text
0x17c12d090: 0x0000000000000020 0x0000000c3fc05140
0x17c12d0a0: 0x000000017c12d0e0 0x000000011dbae2a0
```

其中，栈上第一个 8 字节值 `0x20` 即 `derived_key_len = 32`。

### 8. 读取 password 原始字节

```lldb
memory read --size 1 --count 32 0x0000000c42ea31c0
```

示例输出：

```text
0xc42ea31c0: 69 d2 aa a3 6b a6 40 bd 93 af a0 91 ee eb b0 bd  i...k.@.........
0xc42ea31d0: af c1 6a b0 e7 e5 43 93 b5 79 8c df 5d f4 86 d2  ..j...C..y..]...
```

### 9. 读取 salt 原始字节

```lldb
memory read --size 1 --count 16 0x0000000c41442c20
```

示例输出：

```text
0xc41442c20: 92 14 5f 5d 37 85 29 4c 5e 3e 67 1a 5f ce e2 e7  .._]7.)L^>g._...
```

### 10. 脱离调试进程

```lldb
detach
quit
```

示例输出：

```text
Process 28577 detached
```

### 11. 最终提取到的 KDF 参数

```text
alg = 2
password_len = 32
salt_len = 16
prf = 5
rounds = 0x3e800 = 256000
derived_key_len = 32
```

寄存器与参数的对应关系如下：

```text
x0  -> alg
x1  -> password 地址
x2  -> password_len
x3  -> salt 地址
x4  -> salt_len
x5  -> prf
x6  -> rounds
x7  -> output 地址
sp  -> 栈上第一个 8 字节值为 derived_key_len
```

提取到的 `password`：

```hex
<已脱敏，实际值请自行提取>
```

提取到的 `salt`：

```hex
<已脱敏，实际值请自行提取>
```

## 六、离线解密数据库

可直接使用项目中的 `src/wechat_sqlcipher_probe.py` 完成解密。

示例代码如下：

```python
from pathlib import Path

from wechat_sqlcipher_probe import WechatSQLCipherProbe


probe = WechatSQLCipherProbe(
    password=bytes.fromhex(
        "<请填入你自己提取到的 password hex>"
    ),
    captured_salt=bytes.fromhex("<请填入你自己提取到的 salt hex>"),
)

result = probe.decrypt_first_page(Path("/path/to/message_0.db"))
print(result["header_ok"])

probe.decrypt_db(
    Path("/path/to/message_0.db"),
    Path("/path/to/message_0.decrypted.db"),
)
```

如果 `header_ok` 为 `True`，通常说明第一页已按预期恢复出 SQLite 文件头，后续可继续进行整库解密。

## 七、验证解密结果

解密完成后，可以使用 `sqlite3` 再次验证：

```bash
sqlite3 /path/to/message_0.decrypted.db ".tables"
```

如果能够正常列出表结构，而不再出现 `file is not a database`，则说明解密结果可被 SQLite 正常识别。

进一步还可以执行简单查询，例如：

```bash
sqlite3 /path/to/message_0.decrypted.db "select name from sqlite_master where type='table' limit 20;"
```

## 八、收尾建议

如果此前为调试关闭了 SIP，建议在验证完成后重新启用：

```bash
csrutil enable
```

同时建议将以下信息单独保存，便于后续重复解密：

- 目标账号目录路径
- 提取到的 `password`
- 对应数据库的 `salt`
- 页大小与保留字节配置

以上信息一旦确认，后续通常无需再次进行完整的动态调试流程。

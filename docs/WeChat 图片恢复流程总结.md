# WeChat 图片恢复流程总结

## 结论概览

- 微信图片 `.dat` 不是简单改后缀文件，而是私有容器。
- 缩略图 `_t.dat` 的恢复链已跑通，可稳定恢复为 JPEG。
- 原图 `.dat` 的恢复链也已打通，但中间态不是 JPEG，而是 `wxgf` 容器。
- `wxgf` 主分区可进一步解析为 `HEVC Main Still Picture`，并导出高清 JPEG。
- 高清图 `_h.dat` 的恢复链也已补完。
- 当前总结覆盖：
  - `_t.dat -> JPEG`
  - `.dat -> wxgf -> HEVC -> JPEG`
  - `_h.dat -> PNG/JPEG 或 wxgf -> JPEG`

## 容器格式

所有当前确认的图片容器都使用固定 `15` 字节头：

- `header[0..2] = 07 08 56`
- `header[3] = '2'`
- `header[4..5] = 08 07`
- `header[6..9]`：`payload0`
- `header[10..13]`：`block1_size`
- `header[14]`：`flag`

计算规则：

- `block0_readlen = align16(payload0) + 0x10`
- `file_size = 0x0f + block0_readlen + block1_size`

## 已确认函数职责

- `252120`：读取 15 字节头
- `252119`：校验头签名
- `252118`：解析头字段
- `252129`：驱动后续块处理
- `281127`：处理 `block0`
- `292232`：AES key expansion
- `292233`：AES 解密 round key 预处理
- `292235`：AES T-table 解密核心

## `_t.dat` 缩略图恢复链

### 运行时确认

- `block0` 经过 AES-128 解密。
- `flag = 1` 时会去掉尾部 padding。
- `281127` 的输入包含一个 32 字节 ASCII blob。
- 当前离线实现使用该 32 字节 blob 的前 16 个 ASCII 字节作为 AES-128 key。

### 当前离线恢复规则

1. 读取 `.dat` 头部
2. 切出 `block0` 和 `block1`
3. `block0`：
   - 用 `AES-128-ECB`
   - key = `key32[:16].encode("ascii")`
   - 解密后按 `flag` 去 padding
4. `block1`：
   - 每字节执行 `^ 0xB5`
5. 输出：
   - `dec0 + dec1`

### 当前结果

- 已验证多个 `_t.dat` 样本可按上述规则恢复。
- 对已验证样本，恢复后的文件具备合法 JPEG 头，且图像内容正常。
- 当前缩略图链实现已稳定。

## 原图 `.dat` 恢复链

### 关键发现

- 原图 `.dat` 仍然使用同一 15 字节头。
- 但 `dec0 + dec1` 的结果不是 JPEG，而是以 `wxgf` 开头的二级容器：
  - `77 78 67 66`
- `wxgf` 头部第 5 个字节表示 header 长度，当前样本为 `0x13`。
- 在 `wxgf` 数据中可按 Annex-B 起始码查找主分区：
  - 起始码模式优先匹配 `00 00 00 01`
  - 分区长度取起始码前 4 字节的大端长度
  - 选择占比最大的分区作为主图像分区

### 当前样本确认

- 当前原图样本的 `wxgf` 主分区为：
  - `offset = 42`
  - `size = 79055`
- 该主分区可被媒体解析器识别为：
  - `HEVC / H.265`
  - `Main Still Picture`
  - `1080x1920`
- 通过 Python 包链路解出首帧后，可导出高清 JPEG。

### 当前原图恢复规则

1. 先按 `_t.dat` 同样的容器规则解出：
   - `dec0`
   - `dec1`
2. 合并为：
   - `container = dec0 + dec1`
3. 如果 `container` 以 `wxgf` 开头：
   - 解析 `wxgf` 头
   - 找最大主分区
   - 将主分区作为 HEVC Still Picture 输入解码链路
   - 导出第一帧 JPEG

### 当前结果

- 原图 `.dat` 已可恢复为高清 JPEG，不再停留在“需继续逆向”的阶段。
- 当前样本已恢复出 `1080x1920` JPEG。

## `_h.dat` 高清图恢复链

### 普通样本

大部分 `_h.dat` 与 `_t.dat` 共用同一条基础链：

1. 读取同样的 15 字节头
2. `block0`：
   - `AES-128-ECB`
   - key = `key32[:16].encode("ascii")`
   - 按 `flag` 去 padding
3. `block1`：
   - 每字节执行 `^ 0xB5`
4. 拼出容器数据：
   - `dec0 + dec1`
5. 再按容器类型分流：
   - 直接图片：`png/jpg/gif/bmp/tiff`
   - `wxgf`：提取主分区，再转为 JPEG

已验证普通 `_h.dat` 样本：

- `4bf1a209ac41f639d9e47ac3ae025a1d_h.dat`
  - `container_type = png`
  - `final_type = png`
  - `size_mode = header`
- `29afc5a769b98f5c84a8b2589743f443_h.dat`
  - `container_type = png`
  - `final_type = png`
  - `size_mode = header`
- `1385233d292627e1354c236b224ffd20_h.dat`
  - `container_type = wxgf`
  - `final_type = jpg`
  - `size_mode = header`

### `full_tail_fallback` 特例

少量 `_h.dat` 样本头部会把 `block1_size` 固定写成 `0x100000`，但真实尾部比这个值更长。

当前识别条件：

- `payload0 == 1024`
- `block1_size == 0x100000`
- `actual_file_size > 0x0f + block0_readlen + 0x100000`

这类样本不能只按 header 的 `block1_size` 切尾部，而要：

- 将 `block1` 扩展为“从 `block0` 之后直到 EOF 的整个剩余尾部”
- `size_mode = full_tail_fallback`

### `full_tail_fallback` 的 PNG 过渡块规则

对这类 `_h.dat`，恢复后的内容通常是 PNG，但不是“整段尾部统一 `^ 0xB5`”。

已经确认的一条稳定修复规则：

1. PNG 前缀 chunk 保持 `raw`
2. 到最后一个过渡 `IDAT` chunk 时：
   - chunk header 保持 `raw`
   - payload 第 1 个字节保留 `raw`
   - payload 剩余部分使用 `^ 0xB5`
   - CRC 使用 xor 后的 CRC
3. 从下一个 chunk 开始：
   - 后续所有 chunk 都使用 xor 后的数据

在样本 `0a83355d1bd93f45c58cd3c1c08f53f9_h.dat` 上已验证：

- 输出文件类型：PNG
- `size_mode = full_tail_fallback`
- PNG chunk / CRC 全部合法
- `IDAT` 的 zlib 解压通过
- 最终图片尺寸：`1624 x 1141`

另外又补了一批 `full_tail_fallback` 样本回归，均可成功恢复：

- `70ae2ba7fe3857ab2679362824c64871_h.dat`
- `bdb1a12d0d8a018226dbcfb0458d0d3c_h.dat`
- `9950e0fb77b88af4f60dd99f983d6797_h.dat`
- `0ba5b1f8e4c479b5db6187f95e82f12f_h.dat`
- `8f040dd0d510e9cbf786884f6343cd6a_h.dat`

这些样本均成功恢复为 `jpg`，且 `size_mode = full_tail_fallback`。

## `message_resource` 缺记录时的回退逻辑

有些图片消息在 `message_resource` 中没有记录，但本地文件已存在。

当前 `src/image_process.py` 已支持：

- 先查消息表
- 再尝试查 `message_resource`
- 如果查不到：
  - 从 `packed_info_data` 提取 `file_base`
  - 用 `create_time` 推导 `YYYY-MM`
  - 直接拼本地路径

这能覆盖“消息表存在，但 `message_resource` 缺记录”的情况。

## 当前实现结构

### `src/verify_wechat_dat.py`

- 内部恢复实现：
  - `_WechatDatRecover`
  - 负责 `.dat` 解密与容器转换
  - `block0` AES-128-ECB 解密
  - `block1 ^ 0xB5`
  - `_h.dat` 的 `full_tail_fallback` 与 PNG 过渡块修复
  - 自动识别 `jpeg` / `wxgf`
  - 对 `wxgf` 自动提取主分区并导出 JPEG
- 兼容函数：
  - `recover_wechat_dat(...)`
  - 内部直接委托给 `_WechatDatRecover`

### `src/image_process.py`

- 对外统一入口：
  - `WechatImageParser`
- 初始化时传入：
  - `message_db_path`
  - `message_resource_db_path`
  - `account_root`
  - `key32`
- 对外方法：
  - `find_image_paths(msg_table, local_id)`
  - `recover_thumb(msg_table, local_id, output_dir)`
  - `recover_main(msg_table, local_id, output_dir)`
  - `recover_hd(msg_table, local_id, output_dir)`
- 当前推荐只通过这个类调用，不再依赖命令行参数

## 推荐调用方式

```python
from pathlib import Path
from image_process import WechatImageParser

recover = WechatImageParser(
    message_db_path=Path("data/db/decrypted/message_0.db"),
    message_resource_db_path=Path("data/db/decrypted/message_resource.db"),
    account_root=Path("/path/to/xwechat_files/<account_id>"),
    key32="your_32_char_ascii_key",
)

thumb_result = recover.recover_thumb(
    msg_table="Msg_xxx",
    local_id=1234,
    output_dir=Path("output"),
)

main_result = recover.recover_main(
    msg_table="Msg_xxx",
    local_id=1234,
    output_dir=Path("output"),
)
```

## 当前限制

- `key32` 仍需要调用方在初始化 `WechatImageParser` 时提供。
- `wxgf -> jpg` 依赖已安装的 Python 包 `av` 与 `pillow`。
- `HEVC` 主分区的选择当前按“最大合法分区”判断，已覆盖现有样本，但后续仍可继续补更多样本验证。
- `recover_hd(...)` 已可直接恢复高清图，但如果本地不存在 `_h.dat`，仍会直接报文件不存在。

## 状态

这份文档当前对应的恢复流程已经闭环，可视为本阶段结项版本。

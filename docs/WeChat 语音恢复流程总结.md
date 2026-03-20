# WeChat 语音恢复流程总结

## 结论概览

- 微信语音消息可通过 `message_0.db` 与 `media_0.db` 联合定位。
- 语音消息类型为 `local_type = 34`。
- 当前样本中，语音主数据不依赖会话目录落盘，而是直接存放在 `media_0.db.VoiceInfo.voice_data`。
- `Msg_*` 表与 `VoiceInfo` 的关联需要经过 `media_0.db.Name2Id`。
- 当前样本中的语音二进制通常以 `0x02 + "#!SILK_V3"` 开头。
- 当前实现已支持导出：
  - 原始数据库字节流
  - 标准化 `.silk`
  - 解码后的 `.pcm`
  - 转换后的 `.wav`

## 一、消息定位链路

### 1. 消息类型

- `Msg_*` 表中，`local_type = 34` 表示语音消息。

### 2. 消息表职责

`message_0.db` 负责提供：

- `local_id`
- `server_id`
- `create_time`
- `local_type`

这些字段用于和 `media_0.db.VoiceInfo` 做精确关联。

### 3. 会话映射关系

语音不是直接通过 `Msg_*` 表名查询 `VoiceInfo`。

当前链路是：

1. 从 `msg_table` 取出后缀：
   - `Msg_<chat_md5>`
2. 遍历 `media_0.db.Name2Id.user_name`
3. 计算 `md5(user_name)`
4. 找到与 `<chat_md5>` 一致的 `user_name`
5. 再取得对应的 `Name2Id.rowid`
6. 用该 `rowid` 作为 `VoiceInfo.chat_name_id`

### 4. 最终匹配条件

定位 `VoiceInfo` 时，当前实现使用以下联合条件：

- `chat_name_id`
- `local_id`
- `svr_id`
- `create_time`

命中后可取到：

- `voice_data`
- `data_index`

## 二、数据库角色

### 1. `message_0.db`

负责：

- 确认消息是否为语音
- 提供消息主键与时间信息

### 2. `media_0.db`

负责：

- `Name2Id`
  - 提供会话名到内部 id 的映射
- `VoiceInfo`
  - 存放语音二进制数据

当前语音恢复核心表为：

- `Name2Id`
- `VoiceInfo`

## 三、语音数据格式

### 1. 当前已确认头部

当前样本中常见语音头为：

```text
0x02 + "#!SILK_V3"
```

可理解为：

- 前面多了 1 个微信包装前缀字节
- 后面是真正的 SILK 文件头

### 2. 当前格式分类

当前实现中，语音格式被分为三类：

- `wechat_silk_prefixed`
  - 以 `0x02 + "#!SILK_V3"` 开头
- `silk`
  - 直接以 `#!SILK_V3` 开头
- `binary`
  - 未识别为 SILK 头的其他二进制

### 3. 标准化规则

如果语音数据头是：

```text
0x02 + "#!SILK_V3"
```

则标准化 `.silk` 的生成规则是：

- 去掉最前面的 1 个字节

如果本身就以 `#!SILK_V3` 开头，则直接作为标准 `.silk`。

## 四、导出与解码流程

### 1. 原始导出

首先将 `VoiceInfo.voice_data` 原样导出为：

```text
Msg_xxx_<local_id>.db.silk
```

这一步保留数据库中的原始字节。

### 2. 标准化导出

若检测到可标准化的 SILK 头，则额外导出：

```text
Msg_xxx_<local_id>.silk
```

### 3. SILK 解码

当前实现直接使用 Python 包 `pysilk` 解码为：

```text
Msg_xxx_<local_id>.pcm
```

当前实现直接以导出的 `.db.silk` 作为解码输入，已在真实样本中验证通过。

### 4. PCM 转 WAV

当前实现再使用 Python 标准库 `wave` 将 `.pcm` 转为：

```text
Msg_xxx_<local_id>.wav
```

当前转换参数为：

- 采样格式：`s16le`
- 采样率：`24000`
- 声道数：`1`

## 五、当前实现结构

文件位置：

- `src/voice_process.py`

当前对外对象：

- `WechatVoiceParser`
- `VoiceSummary`

### 主要方法

- `find_voice_paths(msg_table, local_id)`
  - 返回详细定位结果
- `export_voice(msg_table, local_id, output_dir)`
  - 导出语音相关文件
- `find_voice_summary(msg_table, local_id, output_dir=None)`
  - 返回精简 dataclass 结果

## 六、统一保存命名

当前语音导出文件名统一为：

- 原始数据库字节流：
  - `Msg_xxx_<local_id>.db.silk`
- 标准 SILK：
  - `Msg_xxx_<local_id>.silk`
- 解码 PCM：
  - `Msg_xxx_<local_id>.pcm`
- 最终 WAV：
  - `Msg_xxx_<local_id>.wav`

这套命名与图片、视频方向保持一致，均以：

```text
Msg_<chat_md5>_<local_id>
```

作为导出前缀。

## 七、推荐调用方式

### 1. 直接使用 `WechatVoiceParser`

```python
from pathlib import Path

from voice_process import WechatVoiceParser

parser = WechatVoiceParser(
    message_db_path=Path("data/db/decrypted/message_0.db"),
    media_db_path=Path("data/db/decrypted/media_0.db"),
)

summary = parser.find_voice_summary(
    msg_table="Msg_xxx",
    local_id=300,
    output_dir=Path("output/voice"),
)

print(summary.wav_path)
print(summary.voice_format)
print(summary.to_dict())
```

### 2. 通过统一门面 `WechatMediaManager`

```python
from pathlib import Path

from media_manager import WechatMediaManager

manager = WechatMediaManager(
    message_db_path=Path("data/db/decrypted/message_0.db"),
    message_resource_db_path=Path("data/db/decrypted/message_resource.db"),
    media_db_path=Path("data/db/decrypted/media_0.db"),
    hardlink_db_path=Path("data/db/decrypted/hardlink.db"),
    account_root=Path("/path/to/xwechat_files/<account_id>"),
    key32="<32_char_ascii_key>",
)

summary = manager.find_voice_summary(
    msg_table="Msg_xxx",
    local_id=300,
    output_dir=Path("output/voice"),
)

print(summary.to_dict())
```

也可以走统一分发接口：

```python
summary = manager.find_media_summary(
    "voice",
    msg_table="Msg_xxx",
    local_id=300,
    output_dir=Path("output/voice"),
)
```

## 八、当前边界

- 当前样本已确认的主格式是 SILK。
- 当前实现已能稳定完成：
  - 数据库定位
  - 原始导出
  - 标准化导出
  - 解码为 PCM
  - 转换为 WAV
- 如果后续遇到新的语音头格式或非 SILK 数据，还需要补充格式识别与分支处理。

## 九、与统一文档的关系

如果你要看图片、视频、语音三类的统一入口和保存规则，参考：

- `docs/WeChat 多媒体解析与保存使用.md`

这份文档侧重语音方向的单独恢复链路与实现细节。

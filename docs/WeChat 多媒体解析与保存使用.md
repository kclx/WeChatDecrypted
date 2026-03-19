# WeChat 多媒体解析与保存使用

## 适用范围

这份文档统一说明图片、视频、语音三类消息的解析、导出与保存命名。

当前对外统一入口有两层：

- 具体解析器：
  - `WechatImageParser`
  - `WechatVideoParser`
  - `WechatVoiceParser`
- 统一门面：
  - `WechatMediaManager`

推荐业务侧优先使用 `WechatMediaManager`。

## 一、统一设计

三类媒体现在都遵循同一套风格：

- 详细排查接口：
  - `find_*_paths(msg_table, local_id)`
- 精简业务接口：
  - `find_*_summary(msg_table, local_id, output_dir=None)`
- 精简结果对象：
  - `ImageSummary`
  - `VideoSummary`
  - `VoiceSummary`
- 需要字典时：
  - `summary.to_dict()`

只要传入 `output_dir`，就会按统一命名导出恢复结果。

## 二、统一门面

文件位置：

- `src/media_manager.py`

### 初始化

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
    silk_decoder_path=Path("/path/to/python-silk-decoder"),
)
```

其中：

- 图片需要：
  - `message_db_path`
  - `message_resource_db_path`
  - `account_root`
  - `key32`
- 视频需要：
  - `message_db_path`
  - `message_resource_db_path`
  - `hardlink_db_path`
  - `account_root`
- 语音需要：
  - `message_db_path`
  - `media_db_path`
  - `silk_decoder_path`

### 按类型统一调用

```python
image_summary = manager.find_media_summary(
    "image",
    msg_table="Msg_xxx",
    local_id=100,
    output_dir=Path("output/image"),
)

video_summary = manager.find_media_summary(
    "video",
    msg_table="Msg_xxx",
    local_id=200,
    output_dir=Path("output/video"),
)

voice_summary = manager.find_media_summary(
    "voice",
    msg_table="Msg_xxx",
    local_id=300,
    output_dir=Path("output/voice"),
)
```

也可以直接调用分类方法：

```python
image_summary = manager.find_image_summary("Msg_xxx", 100, Path("output/image"))
video_summary = manager.find_video_summary("Msg_xxx", 200, Path("output/video"))
voice_summary = manager.find_voice_summary("Msg_xxx", 300, Path("output/voice"))
```

## 三、图片

文件位置：

- `src/image_process.py`

### 消息类型

- `local_type = 3`

### 主要路径规则

图片本地目录规则：

```text
<account_root>/msg/attach/<chat_md5>/<YYYY-MM>/Img/
```

常见文件：

- `<file_base>.dat`
- `<file_base>_t.dat`
- `<file_base>_h.dat`

### 精简接口

```python
summary = manager.find_image_summary(
    msg_table="Msg_xxx",
    local_id=100,
    output_dir=Path("output/image"),
)

print(summary.exported_main_path)
print(summary.exported_thumb_path)
print(summary.exported_hd_path)
print(summary.to_dict())
```

### 保存命名

- 主图：
  - `Msg_xxx_<local_id>_main.<ext>`
- 缩略图：
  - `Msg_xxx_<local_id>_thumb.<ext>`
- 高清图：
  - `Msg_xxx_<local_id>_hd.<ext>`

### 当前结论

- 缩略图 `_t.dat` 可稳定恢复为普通图片。
- 原图 `.dat` 常先解出 `wxgf`，再可进一步转为 JPEG。
- 高清图 `_h.dat` 既可能直接是普通图片，也可能仍需 `wxgf` 转换。

## 四、视频

文件位置：

- `src/video_process.py`

### 消息类型

- `local_type = 43`

### 主要路径规则

当前已确认视频主路径：

```text
<account_root>/msg/video/<YYYY-MM>/
```

常见文件：

- `<file_base>.mp4`
- `<file_base>_raw.mp4`
- `<file_base>_thumb.jpg`
- 部分样本还有 `<file_base>.jpg`

### 资源类型

- `65538 -> raw_video_mp4`
- `131074 -> play_video_mp4`
- `196610 -> thumb_jpg`

### 精简接口

```python
summary = manager.find_video_summary(
    msg_table="Msg_xxx",
    local_id=200,
    output_dir=Path("output/video"),
)

print(summary.best_video_path)
print(summary.thumb_path)
print(summary.has_raw)
print(summary.to_dict())
```

### 保存命名

- 播放版：
  - `Msg_xxx_<local_id>.mp4`
- 原始版：
  - `Msg_xxx_<local_id>_raw.mp4`
- 缩略图：
  - `Msg_xxx_<local_id>_thumb.jpg`
- 封面图：
  - `Msg_xxx_<local_id>_poster.jpg`

### 当前结论

- 视频基名可从 `packed_info_data` 中稳定提取。
- 一部分视频只有播放版。
- 一部分视频同时存在播放版和 `_raw.mp4` 原始版。
- `base.mp4` 与 `base_raw.mp4` 不是简单改名关系，当前样本已确认编码参数和体积明显不同。

## 五、语音

文件位置：

- `src/voice_process.py`

### 消息类型

- `local_type = 34`

### 主要存储规则

语音主数据不依赖会话目录落盘，当前样本直接存于：

- `media_0.db`
- 表：`VoiceInfo`
- 字段：`voice_data`

`Msg_*` 与 `VoiceInfo` 的关联依赖：

- `Name2Id.user_name`
- `Msg_*` 表后缀 MD5

### 精简接口

```python
summary = manager.find_voice_summary(
    msg_table="Msg_xxx",
    local_id=300,
    output_dir=Path("output/voice"),
)

print(summary.wav_path)
print(summary.voice_format)
print(summary.to_dict())
```

### 保存命名

- 原始数据库导出：
  - `Msg_xxx_<local_id>.db.silk`
- 去前缀标准 SILK：
  - `Msg_xxx_<local_id>.silk`
- 解码 PCM：
  - `Msg_xxx_<local_id>.pcm`
- 转换 WAV：
  - `Msg_xxx_<local_id>.wav`

### 当前结论

- 当前样本中的语音头通常是：
  - `0x02 + "#!SILK_V3"`
- 这类数据可稳定导出为 `.db.silk`
- 当前实现已支持进一步解码为 `.wav`

## 六、统一保存规则

传入 `output_dir` 后，三类媒体都遵循：

- 文件名前缀统一为：
  - `Msg_<chat_md5>_<local_id>`
- 不再使用真实账号目录或原始基名作为导出文件名
- 导出结果面向“消息定位”而不是“内部资源命名”

这使得导出后的文件更适合：

- 二次检索
- 和消息表对照
- 批量归档

## 七、推荐使用方式

如果目标是业务使用或批量导出，推荐只用 `find_*_summary(...)`：

```python
summary = manager.find_media_summary(
    "video",
    msg_table="Msg_xxx",
    local_id=200,
    output_dir=Path("output/video"),
)

print(summary.to_dict())
```

如果目标是逆向分析、问题排查、看数据库细节，再使用：

```python
detail = manager.find_media_paths("video", "Msg_xxx", 200)
print(detail)
```

## 八、补充说明

现有深挖文档仍可继续参考：

- `docs/WeChat 图片恢复流程总结.md`
- `docs/WeChat 视频恢复流程总结.md`

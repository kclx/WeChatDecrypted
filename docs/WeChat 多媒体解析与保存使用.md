# WeChat 多媒体解析与保存使用

## 环境变量

项目根目录提供了模板文件：

- `.env.example`

先复制为 `.env`，再按实际环境填写。

最常用的变量有：

- `WECHAT_ROOT`
- `KEY32`
- `DECRYPTED_DB_DIR`
- `MESSAGE_DB_PATH`
- `MESSAGE_RESOURCE_DB_PATH`
- `MEDIA_DB_PATH`
- `HARDLINK_DB_PATH`
- `CONTACT_DB_PATH`
- `CAPTURED_SALT`
- `PASSWORD_1`
- `PASSWORD_2`

规则统一为：

- 直接 `__init__(...)` 时，显式传参。
- 调用 `from_env()` 时，内部会 `load_dotenv()` 并从 `.env` 构造实例。

## 统一门面

文件：

- `src/media_manager.py`

推荐业务侧优先使用：

- `WechatMediaManager.from_env()`

示例：

```python
from pathlib import Path

from media_manager import WechatMediaManager

manager = WechatMediaManager.from_env()

image_path = manager.export_image("Msg_xxx", 100, Path("output/image"))
video_path = manager.export_video("Msg_xxx", 200, Path("output/video"))
voice_path = manager.export_voice("Msg_xxx", 300, Path("output/voice"))
```

导出规则：

- 图片自动优先选择 `hd > main > thumb`
- 视频自动优先选择 `raw > play > poster > thumb`
- 语音自动优先选择 `wav > silk > db.silk`

## 单类解析器

### 图片

文件：

- `src/image_process.py`

推荐初始化：

```python
from image_process import WechatImageParser

parser = WechatImageParser.from_env()
detail = parser.find_image_paths("Msg_xxx", 100)
```

### 视频

文件：

- `src/video_process.py`

推荐初始化：

```python
from video_process import WechatVideoParser

parser = WechatVideoParser.from_env()
detail = parser.find_video_paths("Msg_xxx", 200)
```

### 语音

文件：

- `src/voice_process.py`

推荐初始化：

```python
from voice_process import WechatVoiceParser

parser = WechatVoiceParser.from_env()
detail = parser.find_voice_paths("Msg_xxx", 300)
```

## SQLCipher 数据库解密

文件：

- `src/wechat_sqlcipher_probe.py`

推荐初始化：

```python
from pathlib import Path

from wechat_sqlcipher_probe import WechatSQLCipherProbe

probe = WechatSQLCipherProbe.from_env()
result = probe.decrypt_first_page(Path("data/db/message_0.db"))

if result["header_ok"]:
    probe.decrypt_db(
        Path("data/db/message_0.db"),
        Path("data/db/dec/message_0.db"),
    )
```

`.env` 中可同时提供：

- `PASSWORD_1`
- `PASSWORD_2`

`WechatSQLCipherProbe.from_env()` 会按下面的方式构造：

```python
probe = WechatSQLCipherProbe(
    password=bytes.fromhex(os.getenv("PASSWORD_1") + os.getenv("PASSWORD_2")),
    captured_salt=bytes.fromhex(os.getenv("CAPTURED_SALT")),
)
```

## CSV 导出

文件：

- `src/write2csv.py`

推荐初始化：

```python
from pathlib import Path

from write2csv import WechatMessageCsvExporter

exporter = WechatMessageCsvExporter.from_env()
output_csv = exporter.export_by_contact_name(
    contact_name_keyword="第一",
    output_csv_path=Path("data/out/messages.csv"),
)
```

也可以直接通过 `.env` 里的默认值运行：

```bash
PYTHONPATH=src python src/write2csv.py
```

对应变量：

- `CSV_CONTACT_NAME_KEYWORD`
- `CSV_OUTPUT_PATH`

## 说明

- `util.py` 保持为纯工具函数，不读取 `.env`。
- `main.py`、各解析器、CSV 导出器、SQLCipher 探测器都支持通过 `.env` 初始化。

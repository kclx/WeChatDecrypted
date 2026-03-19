# WeChat 视频恢复流程总结

## 结论概览

- 微信聊天视频消息可通过 `Msg_*`、`message_resource.db`、`hardlink.db` 三层信息稳定定位到本地文件。
- 当前样本中，视频文件真实主目录规则已确认是：
  - `msg/video/<YYYY-MM>/`
- 视频消息的 `packed_info_data` 中可直接提取 32 位十六进制文件基名。
- 当前视频相关文件通常围绕同一基名出现以下几类：
  - `base.mp4`
  - `base_raw.mp4`
  - `base_thumb.jpg`
  - 有时还有 `base.jpg`
- `message_resource.db` 中的视频资源类型当前可稳定解释为：
  - `65538` -> `raw_video_mp4`
  - `131074` -> `play_video_mp4`
  - `196610` -> `thumb_jpg`
- `base.mp4` 与 `base_raw.mp4` 不是简单改名关系，当前样本已确认二者编码参数和体积明显不同。

## 一、消息定位链路

### 1. 消息类型

- `Msg_*` 表中，`local_type = 43` 表示视频消息。

### 2. 文件基名来源

- 当前样本中，`Msg_*.packed_info_data` 内可直接提取 32 位十六进制字符串。
- 该字符串可直接作为视频文件基名。

示例：

```text
<32位十六进制文件基名 A>
<32位十六进制文件基名 B>
```

### 3. 月份目录来源

- `create_time` 可直接转换为 `YYYY-MM`
- 该值对应视频主存储目录的月份层

### 4. 当前已确认的实际路径规则

聊天视频当前已确认主路径为：

```text
<account_root>/msg/video/<YYYY-MM>/<file_base>.mp4
<account_root>/msg/video/<YYYY-MM>/<file_base>_raw.mp4
<account_root>/msg/video/<YYYY-MM>/<file_base>_thumb.jpg
<account_root>/msg/video/<YYYY-MM>/<file_base>.jpg
```

其中：

- `<file_base>.mp4` 通常是可直接播放的视频版本
- `<file_base>_raw.mp4` 通常是更原始、更大的视频版本
- `<file_base>_thumb.jpg` 通常是缩略图
- `<file_base>.jpg` 在部分样本中存在，更像封面图或派生预览图

### 5. 旧候选路径的结论

代码中仍保留了以下候选路径作为兼容探测：

```text
msg/attach/<chat_md5>/<YYYY-MM>/Video/
msg/attach/<chat_md5>/<YYYY-MM>/video/
```

但当前聊天视频样本的真实命中路径均在：

```text
msg/video/<YYYY-MM>/
```

## 二、数据库角色

### 1. `message_*.db`

负责：

- 确认该消息是视频消息
- 提供 `local_id`、`server_id`、`create_time`
- 提供 `packed_info_data`

### 2. `message_resource.db`

负责：

- 记录某条视频消息有哪些资源
- 提供每种资源的大小、状态、类型

当前已确认的视频资源类型：

- `65538`
  - 原始视频资源
  - 常对应 `<file_base>_raw.mp4`
- `131074`
  - 播放视频资源
  - 常对应 `<file_base>.mp4`
- `196610`
  - 缩略图资源
  - 常对应 `<file_base>_thumb.jpg`

### 3. `hardlink.db`

负责：

- 提供某个月份目录中实际落盘的文件名与文件大小
- 作为本地文件存在性验证层

当前视频方向使用表：

- `video_hardlink_info_v4`
- `dir2id`

其中：

- `dir2id.rowid` 当前可映射月份目录
- `video_hardlink_info_v4.dir1` 当前样本中对应月份目录 id

## 三、批量样本统计

基于当前仓库中的真实数据库样本，已得到以下统计：

- 视频消息总数：`115`
- `packed_info_data` 成功提取 32 位基名：`115 / 115`
- 在当前 `hardlink.db` 中命中本地落盘文件：`51 / 115`
- 同时命中 `.jpg + .mp4 + _raw.mp4` 三件套：`31 / 115`

当前视频资源组合只出现两类：

```text
(131074, 196610)
(65538, 131074, 196610)
```

可解释为：

- 一类消息只有“播放版 + 缩略图”
- 一类消息同时具有“原始版 + 播放版 + 缩略图”

## 四、真实样本结论

### 样本 A：只有播放版

提取结果：

- 月份目录：`2025-12`

资源表：

- `131074` -> `531029`
- `196610` -> `7640`

本地实际文件：

- `base.mp4`
- `base_thumb.jpg`

未发现：

- `base_raw.mp4`
- `base.jpg`

媒体元数据结果表明：

- 播放版视频为 `HEVC / H.265`
- 分辨率 `1280x720`
- 帧率约 `29 fps`
- 时长约 `5.22 秒`
- 音频为 `AAC`

### 样本 B：同时存在播放版和原始版

提取结果：

- 月份目录：`2026-02`

资源表：

- `65538` -> `78159503`
- `131074` -> `20401026`
- `196610` -> `8468`

本地实际文件：

- `base.mp4`
- `base_raw.mp4`
- `base.jpg`
- `base_thumb.jpg`

## 五、播放版与原始版差异

### 1. 容器层

当前样本已确认：

- `base.mp4`
  - 为标准 MP4
  - `moov` 在文件前部
  - 属于可直接播放的 `faststart_or_front_moov`
- `base_raw.mp4`
  - 也是标准 MP4
  - 文件前部主要是 `ftyp/free/mdat`
  - `moov` 位于后部
  - 当前分类为 `mdat_front_tail_moov`

这说明：

- `_raw.mp4` 不是私有加密容器
- `_raw.mp4` 也不是损坏文件
- 它本身就是合法 MP4，只是布局更接近采集/原始输出

### 2. 编码参数差异

在“同时存在播放版和原始版”的样本上已确认：

- `base.mp4`
  - `H.264 Main`
  - `25 fps`
  - 视频码率约 `568 kb/s`
  - 音频码率约 `72 kb/s`
  - 文件大小约 `20.4 MB`
- `base_raw.mp4`
  - `H.264 High`
  - `29.97 fps`
  - 视频码率约 `2335 kb/s`
  - 音频码率约 `130 kb/s`
  - 文件大小约 `78.2 MB`

当前样本中：

- `raw / play` 体积比约 `3.83`

### 3. 当前稳定判断

`base.mp4` 与 `base_raw.mp4` 当前可稳定判断为：

- 不是单纯文件重命名
- 不是相同 bitstream 的简单复制
- 至少经过了重封装，且高概率伴随转码或重新压缩

## 六、当前实现结构

### `src/video_process.py`

当前统一入口：

- `WechatVideoParser`
- `VideoSummary`

已实现能力：

- 通过消息表读取视频消息
- 从 `packed_info_data` 提取文件基名
- 关联 `message_resource.db`
- 关联 `hardlink.db`
- 生成候选路径与实际命中路径
- 自动挑选最可信的：
  - `play`
  - `raw`
  - `poster`
  - `thumb`
- 提供 dataclass 形式的精简摘要接口
- 对已存在 MP4 进行：
  - 文件头识别
  - box 粗解析
  - `ffprobe` 摘要
  - 播放版与原始版差异汇总

### 返回值重点字段

调用：

```python
res = video_manager.find_video_paths(msg_table="Msg_xxx", local_id=123)
```

重点看：

- `preferred_paths`
  - 当前最可信的文件路径
- `existing_paths`
  - 本机实际存在的文件
- `resource_roles`
  - 当前资源类型解释
- `variant_summary`
  - 是否同时存在播放版和原始版
  - 两者布局差异与 `ffprobe` 差异摘要

### 精简接口

当前推荐在业务侧直接调用：

```python
from pathlib import Path

from video_process import WechatVideoParser

video_manager = WechatVideoParser(
    message_db_path=Path("data/db/decrypted/message_0.db"),
    message_resource_db_path=Path("data/db/decrypted/message_resource.db"),
    hardlink_db_path=Path("data/db/decrypted/hardlink.db"),
    account_root=Path("/path/to/xwechat_files/<account_id>"),
)

summary = video_manager.find_video_summary(
    msg_table="Msg_xxx",
    local_id=123,
)
```

`find_video_summary(...)` 当前返回 `VideoSummary` dataclass，而不是普通字典。

常用字段：

- `summary.best_video_path`
  - 当前最适合直接播放或导出的 MP4
- `summary.thumb_path`
  - 当前最可信的缩略图路径
- `summary.has_raw`
  - 是否存在 `_raw.mp4`
- `summary.video_codec`
  - 最佳视频文件的编码
- `summary.width`
- `summary.height`
- `summary.duration`

如需继续转为字典：

```python
summary_dict = summary.to_dict()
```

## 七、当前边界

当前视频方向已经解决：

- 通过消息记录稳定定位视频相关文件
- 识别资源类型与本地落盘文件的对应关系
- 区分播放版 MP4 与原始版 MP4
- 对 MP4 做基础容器与编码层检查

当前尚未完全解决：

- 微信到底是在本地把 `_raw.mp4` 转成 `base.mp4`，还是服务端直接下发两套资源
- `base.jpg` 的生成时机与来源是否稳定
- 某些只存在资源表但本地尚未落盘的视频，何时会实际写入 `msg/video/<YYYY-MM>/`

## 八、阶段性总结

截至当前：

- 视频消息定位链已经跑通
- 聊天视频真实主路径已确认是 `msg/video/<YYYY-MM>/`
- `packed_info_data -> file_base` 在当前样本中已稳定成立
- 资源类型 `65538 / 131074 / 196610` 已可稳定解释
- 播放版与原始版视频的容器层与编码层差异已被实证确认

当前这份总结可视为视频方向第一阶段结项版本：

- 已完成“消息 -> 文件”的定位闭环
- 已完成“播放版 / 原始版”的基本分类闭环
- 后续若继续研究，重点应转向：
  - 原始版到播放版的生成关系
  - 更细的 MP4 box / sample 表差异
  - 批量导出与恢复工具化

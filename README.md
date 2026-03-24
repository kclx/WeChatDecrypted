# WeiChatTool

一个面向本地微信数据的工具集，当前主要提供四类能力：

- 解密并探测微信数据库
- 导出单个联系人的处理后聊天记录
- 基于聊天记录生成“我 / 对方”双向画像
- 基于画像库进行终端问答

项目代码已经按职责拆分为两层：

- 业务实现放在 [`src/wechat_tool`](src/wechat_tool)
- 命令行入口放在 [`src/cli`](src/cli)

[`src/main.py`](src/main.py) 仅保留为手工联调入口，不作为正式 CLI 使用。

## 功能概览

### 1. 聊天记录导出

把原始微信数据库中的单个联系人会话导出为结构化聊天记录，支持：

- 文本消息清洗
- 图片、视频、语音消息导出
- 媒体消息结合上下文生成备注
- 导出为 CSV 或 SQLite

导出的 SQLite 默认写入 `data/out/db/messages.db`，每个联系人一张表。

### 2. 用户画像分析

基于已经处理好的联系人聊天表，调用 AI 做双向画像分析：

- `self`：我
- `peer`：对方

画像写入同一个 `messages.db` 中的 `contact_profiles` 表。  
如果消息表不存在，会先自动触发聊天导出，再进行画像分析。

### 3. 画像问答

从 `contact_profiles` 中检索相关联系人画像，再让 AI 仅基于画像作答。

适用场景例如：

- “XXX喜欢什么？”
- “我平时是不是经常出差？”
- “xxx 多高？”

如果画像库没有足够证据，程序会明确回答信息不足，而不是编造。

### 4. 媒体导出与数据库解密工具

项目还提供独立 CLI：

- 单条图片 / 视频 / 语音导出
- SQLCipher 数据库第一页探测
- SQLCipher 数据库完整解密

## 环境要求

- Python `>= 3.10`
- 本地可访问的微信账号数据目录
- 已准备好的解密数据库，或至少具备数据库探测 / 解密所需参数

主要依赖定义在 [`pyproject.toml`](pyproject.toml)。

## 安装

如果你使用 `venv + pip`：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

如果你使用 `uv`：

```bash
uv sync
```

## 配置

复制示例配置：

```bash
cp .env.example .env
```

然后根据你的本地环境修改 [`.env.example`](.env.example) 中对应项。  
项目当前实际使用的环境变量都已经写在该文件里，并带有中文注释。

最常用的配置是：

- `WECHAT_ROOT`
- `WXID`
- `DECRYPTED_DB_DIR`
- `MESSAGE_DB_PATH`
- `CONTACT_DB_PATH`
- `MESSAGE_RESOURCE_DB_PATH`
- `MEDIA_DB_PATH`
- `HARDLINK_DB_PATH`
- `OPENAI_API_KEY` / `GOOGLE_API_KEY`
- `AI_IMAGE_MODEL`
- `AI_VIDEO_MODEL`
- `AI_AUDIO_MODEL`
- `AI_PROFILE_MODEL`

## CLI 用法

正式命令行程序都放在 [`src/cli`](src/cli)。

### 1. 导出聊天记录

```bash
source .venv/bin/activate
python src/cli/export_chat.py 联系人备注
```

可选参数：

```bash
python src/cli/export_chat.py 联系人备注 --output data/out/db/messages.db --limit 500
```

### 2. 生成联系人画像

```bash
python src/cli/analyze_profile.py 联系人备注
```

重置已有画像后重跑：

```bash
python src/cli/analyze_profile.py 联系人备注 --reset
```

控制切片大小和消息条数：

```bash
python src/cli/analyze_profile.py 联系人备注 --slice-size 300 --limit 1000
```

### 3. 画像库问答

```bash
python src/cli/profile_chat.py
```

指定画像库：

```bash
python src/cli/profile_chat.py --db data/out/db/messages.db
```

### 4. 导出单条媒体

导出图片：

```bash
python src/cli/export_media.py image Msg_xxx 284 data/out/media_test
```

导出视频：

```bash
python src/cli/export_media.py video Msg_xxx 284 data/out/media_test
```

导出语音：

```bash
python src/cli/export_media.py voice Msg_xxx 284 data/out/media_test
```

### 5. 探测数据库是否可解密

```bash
python src/cli/decrypt_db.py probe /path/to/message.db
```

### 6. 解密完整数据库

```bash
python src/cli/decrypt_db.py decrypt /path/to/message.db /path/to/message.dec.db
```

## 数据流

项目的主路径大致如下：

1. 原始 / 解密后的微信数据库作为输入
2. 聊天导出服务读取联系人和消息表
3. 文本消息直接清洗，媒体消息导出文件并补备注
4. 结果落到 `messages.db` 的联系人消息表
5. 画像分析服务从联系人消息表中分片读取消息
6. AI 生成增量画像 patch
7. 最终画像写入 `contact_profiles`
8. 画像问答服务读取 `contact_profiles` 回答自然语言问题

## 输出结构

### 1. 聊天导出库

默认输出文件：

- `data/out/db/messages.db`

内容包括：

- 每个联系人一张消息表
- 表字段：`local_id / sender / wxid / remark / msg_type / msg_time / msg`

### 2. 画像表

画像也写在同一个 `messages.db` 里，表名为：

- `contact_profiles`

核心字段包括：

- `subject_wxid`
- `subject_role`
- `subject_display_name`
- `source_contact_username`
- `source_contact_table`
- `profile_summary`
- `confidence_overall`
- `traits_json`
- `habits_json`
- `basic_info_json`
- `evidence_json`
- `raw_model_output_json`

### 3. 媒体文件

默认导出目录：

- `data/out/media/<联系人名>/img`
- `data/out/media/<联系人名>/video`
- `data/out/media/<联系人名>/voice`

### 4. CSV 文件

默认目录：

- `data/out/csv`

## 联系人解析规则

当前联系人解析规则如下：

- 普通关键字只查 `contact.alias / contact.remark / contact.nick_name`
- 不把 `username` 作为普通关键字搜索字段
- 如果你直接传的是 `wxid_*` 或 `xxx@chatroom`，会按 `username` 精确解析
- 优先级：
  - `remark` 精确
  - `nick_name` 精确
  - `alias` 精确
  - `remark` 模糊
  - `nick_name` 模糊
  - `alias` 模糊
- 如果命中多个联系人，不自动选择，会要求你改用 `username`

## 代码结构

### CLI

- [export_chat.py](src/cli/export_chat.py)：聊天记录导出 CLI
- [analyze_profile.py](src/cli/analyze_profile.py)：联系人画像分析 CLI
- [profile_chat.py](src/cli/profile_chat.py)：画像问答 CLI
- [export_media.py](src/cli/export_media.py)：单条媒体导出 CLI
- [decrypt_db.py](src/cli/decrypt_db.py)：数据库探测 / 解密 CLI

### 业务模块

- [application.py](src/wechat_tool/services/application.py)：应用装配层
- [service.py](src/wechat_tool/export/service.py)：聊天导出服务
- [service.py](src/wechat_tool/profile/service.py)：画像分析服务
- [qa_service.py](src/wechat_tool/profile/qa_service.py)：画像问答服务
- [service_base.py](src/wechat_tool/common/service_base.py)：共享基础能力
- [manager.py](src/wechat_tool/media/manager.py)：媒体导出管理器
- [sqlcipher_probe.py](src/wechat_tool/database/sqlcipher_probe.py)：数据库探测与解密
- [ai.py](src/wechat_tool/clients/ai.py)：AI 适配层

## 已知限制

- 当前画像分析只支持单聊个人用户，群聊会直接报未实现
- 画像质量高度依赖聊天内容密度与模型能力
- `gpt-4o-mini` 可用，但不一定适合做高质量人物画像总结
- 如果原始媒体库缺失资源，导出语音 / 视频 / 图片时可能失败
- 画像问答当前是基于画像库检索，不直接回看完整聊天原文
- `src/main.py` 只是手工测试入口，不保证长期稳定

## 开发说明

如果你只想调用业务代码，不走 CLI，建议直接使用：

- [`WechatChatApplication`](src/wechat_tool/services/application.py)
- [`WechatChatExportService`](src/wechat_tool/export/service.py)
- [`WechatContactProfileService`](src/wechat_tool/profile/service.py)
- [`WechatProfileQAService`](src/wechat_tool/profile/qa_service.py)

其中应用层已经统一封装了：

- 聊天导出
- 画像分析
- 画像问答

适合在脚本或测试代码中直接复用。

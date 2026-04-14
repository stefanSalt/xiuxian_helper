# 消息归档支持媒体文本计划（2026-04-14）

## 目标
- 调整消息归档规则：忽略媒体资源本身，但媒体信息要用文字占位后与文本一起归档。
- 保持现有搜索、编辑历史、网页展示逻辑可用。

## 当前现状
- `xiuxian_bot/runtime.py` 中 `_should_archive_plain_text(...)` 仅允许 `message.media is None` 的纯文本消息归档。
- 因此：
  - 纯文本消息会归档
  - 图片/视频/文件 + 文本（caption）不会归档
  - 编辑后加了图片，即使仍有文本，也不会归档该编辑版本

## 计划
- [x] 明确媒体文本归档边界
- [x] 调整 runtime 归档过滤逻辑
- [x] 补充媒体文本 / 编辑场景测试
- [x] 回归验证搜索与页面展示

## 已确认口径
- [x] 媒体消息需要归档，不再因 `message.media` 被整体忽略
- [x] 媒体资源本身不保存，只保存文字占位
- [x] 图像类媒体至少使用 `[image]` 占位
- [x] 原始文本 / caption 继续保留，并与媒体占位一起写入归档文本
- [x] 编辑历史继续保留，每次编辑仍单独形成一个版本
- [x] 纯媒体无文本时，也要记录占位文本
- [x] 采用细分占位：`[image]` / `[video]` / `[voice]` / `[audio]` / `[video_note]` / `[gif]` / `[sticker]` / `[poll]` / `[file]` / `[media]`

## 实施结果
- 已在 `xiuxian_bot/runtime.py` 将归档入口从“只允许纯文本”调整为“统一构造可归档文本”。
- 新增媒体占位提取逻辑：
  - 优先识别 Telethon `Message` 的细分媒体属性
  - 无法细分时回退为 `[media]`
- 当前归档文本规则：
  - 纯文本：原文直接归档
  - 媒体 + 文本：`[占位] + 换行 + 文本`
  - 纯媒体：仅归档 `[占位]`
- 已补充测试：
  - 媒体 + 文本占位
  - 纯媒体占位
  - 编辑后媒体消息归档为 `event_type=edit`
- 已验证：
  - `./venv/bin/python -m unittest tests.test_message_archive`
  - `./venv/bin/python -m unittest discover`

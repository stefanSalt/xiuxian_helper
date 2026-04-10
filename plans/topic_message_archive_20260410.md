# 群话题消息归档与网页检索计划（2026-04-10）

## 目标
- 记录目标群组下**所有话题 / 频道消息**，不再只保留当前聚焦玩法日志。
- 在网页中新增消息归档页面，支持查看与搜索。
- 保持现有自动化逻辑最小改动，不影响插件调度。

## 已确认的现状
- Telegram 消息入口在 `xiuxian_bot/tg_adapter.py`，当前已监听：
  - `events.NewMessage(chats=game_chat_id)`
  - `events.MessageEdited(chats=game_chat_id)`
- 运行时统一消息入口在 `xiuxian_bot/runtime.py` 的 `_on_event(event)`。
- 当前网页已有账号日志页：
  - `/accounts/{account_id}/logs`
- 当前 sqlite 已承载：
  - 账号配置
  - 插件状态
- 当前“业务日志”只是写到 `logs/account_<id>.log`，并不适合做全文检索。

## 已确认需求
- [x] 归档范围：只归档当前 `game_chat_id` 群组内**各个话题**的消息
- [x] 文本范围：只归档**普通文本消息**，**不包含媒体 caption**
- [x] 保留策略：先**永久保留**
- [x] 页面范围：除了按账号查看，还要支持**全局查看**
- [x] 编辑消息：需要**保留编辑历史**

## 调整后的实现方案

### 一、存储模型
新增独立消息归档表，建议放在现有 app sqlite 中：
- `account_id`
- `chat_id`
- `topic_id`
- `message_id`
- `sender_id`
- `sender_name`（若能拿到）
- `raw_text`
- `normalized_text`
- `event_type`（`new` / `edit`）
- `message_ts`
- `captured_at`
- `edit_version`

可选：
- `reply_to_msg_id`
- `is_reply`
- `is_topic_message`
- `sender_name`（若 Telethon 当前事件能稳定拿到）

### 二、采集位置
在 `runtime.py` 的统一 `_on_event` 中，**在 `_in_scope()` 过滤之前**归档消息。

原因：
- 这样可以收集“所有话题消息”
- 又不需要侵入每个插件
- 现有插件仍只处理自己关心的消息

### 三、搜索页面
新增网页页面：
- `/messages`（全局）
- `/accounts/{account_id}/messages`

首期支持：
- 关键词搜索
- 话题 ID 过滤
- 账号过滤
- 发送者过滤
- 事件类型过滤（`new` / `edit`）
- 时间倒序分页

### 四、搜索策略
首期推荐使用 sqlite `LIKE` + 辅助字段：
- `raw_text`：原文展示
- `normalized_text`：用于容错搜索（去空格、OCR 异体、全半角归一）

理由：
- 改动小
- 不引入额外依赖
- 和现有 `normalize_match_text()` 可复用

### 五、编辑消息处理
用户已确认：**保留编辑历史**

推荐做法：
- 单独消息归档表按“事件”存储，不覆盖旧版本
- 同一条 TG 消息多次编辑时，按 `(account_id, message_id, edit_version)` 或自增主键落库
- 列表页默认展示最新版本，也支持查看该消息的历史版本轨迹

不推荐首期就做：
- 删除消息追踪
- 媒体文件归档

## 非目标
- 首期不做图片 / 文件下载存储
- 首期不做 OCR
- 首期不做跨账号聚合大盘
- 首期不做 ES / Whoosh / FTS5 之外的外部搜索服务

## 风险点
- 群消息量大时，表增长会很快，需要确认保留策略。
- 如果“所有消息”包含大量无文本消息，网页价值有限，需要确认是否只归档有文本的消息。
- 论坛话题的 `topic_id` 需要统一抽取逻辑，不能只依赖当前配置里的主话题 `topic_id`。

## 推荐执行清单
- [x] 明确文本范围（是否包含 caption）
- [x] 新增消息归档 repository / sqlite 表
- [x] 在 runtime 统一入口接入归档
- [x] 新增全局消息页与账号消息页
- [x] 补充归档与搜索测试
- [x] 跑定向测试与全量测试

## 待确认问题
1. 归档范围
   - 已确认：只归档**当前 `game_chat_id` 群组内**所有话题消息

2. 文本范围
   - 推荐：首期只归档**有文本内容**的消息
   - 备选：文本 + 媒体标题 / caption

3. 保留策略
   - 已确认：先**永久保留**

4. 页面范围
   - 已确认：需要**全局聚合页**，同时保留按账号查看

5. 编辑消息
   - 已确认：保留每次编辑历史

## 实施结果
- 已新增 `xiuxian_bot/core/message_archive_repository.py`，在现有 sqlite 中持久化消息原文、归一化文本、事件类型与编辑版本。
- 已在 `xiuxian_bot/runtime.py` 的统一事件入口接入归档，并放在 `_in_scope()` 过滤之前，确保旁观话题消息也会落库。
- 已新增网页：
  - `/messages`
  - `/accounts/{account_id}/messages`
- 已在面板与日志页增加消息归档入口。
- 已验证：
  - `./venv/bin/python -m unittest tests.test_message_archive`
  - `./venv/bin/python -m unittest discover`

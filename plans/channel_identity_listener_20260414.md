# 频道身份系统回应监听计划（2026-04-14）

## 目标
- 在保留现有“原 bot 身份回应”识别逻辑的同时，新增对频道身份消息的系统回应监听。
- 新增来源为：`https://t.me/hantianzunhl`
- 目标效果：凡是原来依赖“系统回应”的插件，都能同时识别该频道身份发出的回应消息。

## 当前代码现状
- 统一事件入口位于 `xiuxian_bot/runtime.py`
  - `_on_event()` 构建 `MessageContext` 后分发给所有插件
  - 当前是否进入处理流程，主要依赖：
    - `_in_scope(...)`
    - `ctx.is_reply_to_me`
    - 文本包含自己的名字
- `TGAdapter.build_context()` 当前只返回：
  - `sender_id`
  - `text`
  - `reply_to_msg_id`
  - `is_reply_to_me`
- 当前没有“允许多个系统身份来源”的显式配置字段。

## 风险点
- 频道身份消息在 Telethon 中可能不是普通用户 sender：
  - 可能表现为 `sender_id` 为频道 id
  - 也可能需要读取 `post_author` / `sender.title` / `chat.username`
- 如果仅靠 `is_reply_to_me`，可能漏掉“频道代发但不带普通 sender”的情况。
- 如果改得过宽，可能把群里其他频道转发/代发消息误当系统回应。

## 已确认方案
1. `Config`
   - 增加“额外系统回应来源”配置，支持 username 列表。
   - 默认包含：`hantianzunhl`

2. `TGAdapter`
   - 启动时把配置里的 username 解析为 Telegram 实体 id。
   - `build_context()` 中基于 `sender_id` 判断是否来自额外系统来源。

3. `MessageContext`
   - 最小扩展字段：
     - `is_from_system_identity`
     - `is_system_reply`
   - `is_system_reply` 的口径为：
     - 来自配置的系统来源
     - 且 **@我** 或 **回复我消息**

4. 插件处理
   - 原 bot 的现有逻辑继续保留。
   - 对额外系统来源，只有“@我”或“回复我消息”的系统信息，才作为等价系统回应参与判定。

5. `tests`
   - 增加 TGAdapter / 至少一个依赖“回复我”语义的插件测试
   - 覆盖：
     - 原 bot 回复仍然正常
     - `hantianzunhl` 频道身份消息在“@我 / 回复我”时也能进入处理链路

## 已确认口径
1. 来源范围
   - 已确认：做成**可配置**的多来源 username 列表

2. 匹配口径
   - 已确认：先按 **username** 配置，启动时解析为实体 id 使用

3. 行为口径
   - 已确认：将原 bot 与额外频道来源都视为“系统”
   - 但只监听：
     - **@我** 的系统信息
     - **回复我消息** 的系统信息

## 执行清单
- [x] 确认来源匹配口径
- [x] 扩展上下文字段与配置
- [x] 接入统一系统来源判断
- [x] 补充测试并验证
- [x] 回写计划与交付结果

## 实施结果
- 已在 `xiuxian_bot/config.py` 新增 `system_reply_source_usernames`，支持在配置 / 网页中填写额外系统来源 username 列表。
- 已在 `xiuxian_bot/web.py` 增加“额外系统来源(username逗号分隔)”配置项。
- 已在 `xiuxian_bot/tg_adapter.py` 实现：
  - 启动时使用 Telethon `get_entity(username)` 解析额外系统来源 id
  - `build_context()` 中补充：
    - `is_from_system_identity`
    - `is_system_reply`
- 已在 `xiuxian_bot/core/contracts.py` 增加 `MessageContext.is_effective_reply`，统一表示：
  - 回复我消息
  - 或系统来源（原 bot / 额外频道身份）且 @我 / 回复我
- 已更新受影响插件：
  - `biguan.py`
  - `chuangta.py`
  - `yuanying.py`
  - `xinggong.py`
  - `lingxiaogong.py`
- 已验证：
  - `./venv/bin/python -m unittest tests.test_tg_adapter tests.test_lingxiaogong tests.test_config tests.test_multi_account tests.test_biguan tests.test_chuangta tests.test_xinggong tests.test_yuanying`
  - `./venv/bin/python -m unittest discover`

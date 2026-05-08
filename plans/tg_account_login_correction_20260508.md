# TG 新账号登录纠偏计划 - 2026-05-08

## 问题
- 已提交的 `feat(web): add telegram account login` 理解成了“已有游戏账号登录对应 TG session”。
- 正确需求是：在“新增账号”入口中添加 TG 登录，用 TG 登录创建新的 Telegram session，并生成一个新的账号记录，之后在账号列表中管理。

## 正确目标
- “新增账号”提供 TG 登录创建入口。
- 用户通过 TG 手机号验证码/2FA 登录一个新的 TG 账号。
- 登录成功后创建新的 Telethon session。
- 同时在账号列表中创建一个新的账号记录。
- 新账号默认不自动启动，用户可在账号列表继续编辑游戏配置、启用、启动。
- `TG API ID`、`TG API HASH` 属于同一个 Telegram 应用，只在应用级配置一次，不在每个 TG 账号里重复填写。
- `game_chat_id`、`topic_id` 属于同一个游戏群/话题范围，也只在应用级配置一次，不在每个账号里重复填写。
- 应用级共享设置需要能在 Web 页中修改并持久保存。

## 默认流程
- 账号列表保留“新增账号”。
- 新增账号页增加“通过 TG 登录新增”入口。
- TG 新账号登录页需要填写：
  - 账号名称
  - Session 名称
  - 手机号
- TG 新账号登录页不填写：
  - TG API ID / HASH
  - 游戏群 Chat ID
  - 话题 TOPIC_ID
- 点击发送验证码后：
  - 用应用级共享的 TG API 信息和 Session 名称创建 Telethon client。
  - 发送验证码并暂存 `phone_code_hash`。
- 提交验证码后：
  - 验证成功则创建账号记录。
  - 若需要 2FA，则进入密码步骤。
- 提交 2FA 密码成功后创建账号记录。
- 新账号默认 `enabled=False`，不会自动启动。
- 新账号的共享配置由应用级配置注入：
  - `tg_api_id=SystemConfig.tg_api_id`
  - `tg_api_hash=SystemConfig.tg_api_hash`
  - `game_chat_id=SystemConfig.game_chat_id`
  - `topic_id=SystemConfig.topic_id`
  - `send_to_topic=SystemConfig.send_to_topic`
- 新账号仍保留账号自身配置：
  - `tg_session_name`
  - `my_name=""`
  - 其他插件默认关闭或沿用现有默认值。
- 登录成功后跳转到新账号编辑页，让用户补齐游戏名、身份、插件等账号自身配置。
- 账号列表提供“应用设置”入口，用于修改 TG API、游戏群、话题、是否发送到话题和系统来源。
- 保存应用设置后同步更新现有账号的共享字段，并触发已启用账号重新同步。
- 如果配置的话题已关闭，普通指令会回退到群组根聊天发送，避免 `TOPIC_CLOSED` 无限重试；显式回复某条消息的动作不回退。

## 需要改动
- [x] 在 `SystemConfig` 增加应用级共享配置来源：
  - `TG_API_ID`
  - `TG_API_HASH`
  - `GAME_CHAT_ID`
  - `TOPIC_ID`
  - `SEND_TO_TOPIC`
  - `SYSTEM_REPLY_SOURCE_USERNAMES`
- [x] 新增 `app_settings` 持久化表，Web 保存的应用设置优先于 `.env` 默认值。
- [x] 新增 Web 应用设置页：
  - `GET /settings`
  - `POST /settings`
- [x] 账号表单不再展示/要求重复填写 `tg_api_id`、`tg_api_hash`、`game_chat_id`、`topic_id`、`send_to_topic`、`system_reply_source_usernames`，创建/编辑账号时统一从 `SystemConfig` 注入。
- [x] 保存应用设置后同步现有账号配置，并触发账号运行状态重新同步。
- [x] 对普通话题发送增加 `TOPIC_CLOSED` 兜底，回退到群组根聊天发送。
- [x] 调整 TG 登录路由，从 `/accounts/{id}/tg-login` 改为新增账号级流程：
  - `GET /accounts/new/tg-login`
  - `POST /accounts/new/tg-login/send-code`
  - `POST /accounts/new/tg-login/verify-code`
  - `POST /accounts/new/tg-login/verify-password`
- [x] 临时登录状态不再绑定已有账号 ID，而是绑定一次性 flow ID。
- [x] 登录成功时创建新的账号记录。
- [x] TG 登录服务不再依赖账号 `Config` 提供 API 信息，改为显式接收应用级 `api_id/api_hash`。
- [x] 移除账号列表中每个已有账号的 `TG 登录` 按钮。
- [x] 新增账号页增加“通过 TG 登录新增”入口。
- [x] 更新 TG 登录模板，使其展示新账号创建表单，而不是已有账号信息。
- [x] 更新测试：
  - 新增账号页显示 TG 登录入口。
  - 未登录管理员不能访问 TG 新账号登录。
  - 发送验证码不会创建账号。
  - 验证码成功后创建 disabled 账号并跳转编辑页。
  - 新 TG 账号记录会继承应用级 TG API 和游戏群/话题配置。
  - 2FA 成功后创建 disabled 账号并跳转编辑页。
  - 账号列表不再显示已有账号级 `TG 登录` 操作。
  - 账号创建/编辑页不再要求重复填写 TG API、游戏群、话题。
- [x] 运行完整测试。

## 安全
- 手机号、验证码、2FA 密码不写入数据库。
- 临时登录状态只保存在 Web 内存。
- API HASH 仍会随账号 `Config` 保存一份用于现有运行时兼容，但来源只有应用级 `SystemConfig`，表单不再让每个账号重复录入。

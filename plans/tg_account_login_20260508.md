# TG 账号登录功能计划 - 2026-05-08

## 目标
- 在账号管理中增加 Telegram 账号登录能力。
- 用户能在“新增账号”或账号列表中发起 TG 登录。
- 登录完成后生成/复用该账号的 Telethon session，之后可在账号列表中启动、停止、编辑和管理。

## 依据
- 当前账号配置已经包含：
  - `tg_api_id`
  - `tg_api_hash`
  - `tg_session_name`
- 运行时会用 `tg_session_name` 创建 `TelegramClient`。
- Telethon 官方文档确认：
  - `send_code_request(phone)` 用于发送登录验证码。
  - `sign_in(phone, code, phone_code_hash=...)` 用于验证码登录。
  - 若启用两步验证，会出现需要密码的流程，再使用 `sign_in(password=...)` 完成。

## 默认流程
- 新增账号表单仍保留现有完整配置入口。
- 在账号列表增加“TG 登录”操作。
- 对未登录或需要重新登录的账号，进入 TG 登录页面：
  - 第一步：填写手机号，点击发送验证码。
  - 第二步：填写验证码，提交登录。
  - 第三步：如果 Telegram 要求 2FA，填写密码完成登录。
- 登录成功后：
  - 使用该账号配置中的 `tg_session_name` 作为 session 名称。
  - 若配置了 `SESSION_ROOT_DIR`，沿用现有 `_resolve_session_name()` 的路径规则。
  - 回到账号列表，账号可直接启动。

## 修改范围
- [x] 新增 TG 登录服务/辅助模块，封装 Telethon 登录会话：
  - 创建 client。
  - 发送验证码。
  - 暂存 `phone_code_hash`。
  - 验证 code。
  - 处理 2FA password。
  - 断开 client。
- [x] 在 Web 增加路由：
  - `GET /accounts/{id}/tg-login`
  - `POST /accounts/{id}/tg-login/send-code`
  - `POST /accounts/{id}/tg-login/verify-code`
  - `POST /accounts/{id}/tg-login/verify-password`
- [x] 新增 TG 登录模板页面。
- [x] 在账号列表操作区增加“TG 登录”入口。
- [x] 登录成功后不自动启动账号，只返回账号列表，避免误启动未确认配置。
- [x] 增加测试：
  - 账号列表显示 TG 登录入口。
  - 未登录用户不能访问登录路由。
  - 发送验证码会调用登录服务并保存临时登录状态。
  - 验证码成功后回到账号列表。
  - 2FA 场景会进入密码步骤。
- [x] 运行相关测试和完整测试。

## 安全与状态
- 不把验证码、手机号、2FA 密码写入数据库。
- 临时登录状态只保存在 Web app 内存中：
  - account_id
  - phone
  - phone_code_hash
  - session_name
- 2FA 密码只用于一次请求，不落盘。
- API hash 仍来自账号配置，不额外展示或记录。

## 待确认
- [x] 登录成功后不自动启动账号。
- [x] 新增账号保存后自动跳转到 TG 登录页面。

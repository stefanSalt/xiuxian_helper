# 自定义随机文本发送插件计划（2026-05-12）

## 背景
- 当前系统已有多个定时插件，会通过统一调度器发送固定命令。
- 用户希望维护一组文本，让系统按随机时间挑选一条发送。
- 该功能只能作为可配置的自定义文本/公告发送器实现，不实现规避平台或游戏风控的伪装策略。

## 目标
- 新增插件 `random_text`，启用后从配置文本列表中随机选择一条发送。
- 支持随机发送间隔，避免固定秒数：最小间隔、最大间隔可配置。
- 支持每日最大发送次数，防止刷屏。
- 支持通过 Web 页面配置，并允许身份级覆盖。
- 不主动切换身份；只在系统因其他正常插件切到某个身份后，顺势插入该身份配置的随机文本。
- 复用现有发送链路和多身份运行框架。

## 推荐配置项
- `enable_random_text`: 是否启用随机文本发送，默认关闭。
- `random_text_messages`: 文本列表，一行一条，空行忽略。
- `random_text_min_interval_seconds`: 最小发送间隔，推荐默认 1800 秒。
- `random_text_max_interval_seconds`: 最大发送间隔，推荐默认 7200 秒。
- `random_text_daily_limit`: 每日最大发送次数，推荐默认 6。

## 方案
- [x] 在 `Config` 增加随机文本插件配置字段。
- [x] 新建 `xiuxian_bot/plugins/random_text.py`，提供身份内的随机文本候选与冷却/每日上限判断。
- [x] 插件不在 `bootstrap()` 中主动调度发送，不触发身份切换。
- [x] 在身份切换后的正常发送流程中增加顺势插入点：
  - 当前身份已有正常插件命令发送成功后，检查该身份 `random_text` 是否可发送。
  - 可发送时从该身份文本列表中随机选一条，追加发送。
  - 不因为随机文本改变当前身份，也不单独切换回/切换到任何身份。
- [x] 插件发送前检查配置：
  - 未启用、文本列表为空、每日次数已满时不发送。
  - 距离上次发送未达到 `[min,max]` 随机冷却时不发送。
  - 每日计数跨天自动重置。
- [x] 将插件加入 `runtime.build_plugins()`。
- [x] Web 表单增加“随机文本”配置区。
- [x] 增加单元测试：
  - 空文本不发送。
  - 启用后不会 bootstrap 主动调度。
  - 正常插件命令在某身份发送后，可顺势追加该身份配置的随机文本。
  - 每日上限生效。
  - 表单配置能写入并被身份级覆盖。

## 边界
- 不自动生成聊天内容，只发送用户配置的文本。
- 不根据系统检测结果动态调整内容。
- 不绕过已有全局发送限速和插件发送限速。
- 不默认启用。
- 不主动发起身份切换；随机文本只跟随当前已经切到的身份。

## 已确认
- 文本列表按“一行一条”配置。
- 默认间隔采用 30 分钟到 2 小时。
- 每日上限默认 6 条。
- 随机文本不主动切换身份，只在其他正常命令切换到对应身份并发送后顺势插入。

## 验证
- [x] `./venv/bin/python -m unittest tests.test_random_text tests.test_config tests.test_multi_account.TestWebApp.test_login_and_create_account tests.test_multi_account.TestRunnerManager.test_account_runner_switches_identity_before_sending_due_action`
- [x] `./venv/bin/python -m unittest`
- [x] `git diff --check`

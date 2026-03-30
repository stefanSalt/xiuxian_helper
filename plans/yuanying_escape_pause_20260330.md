# 元婴遁逃全局暂停计划（2026-03-30）

## 目标
- [x] 当 `.探寻裂缝` 回包命中 `元婴遁逃` + `虚弱期` 时，暂停该账号的全部自动指令发送。
- [x] 暂停状态需要持久化，避免重启后自动恢复误发。
- [x] 恢复必须由人工触发，不做自动恢复。
- [x] 状态切换后要立刻阻断后续动作与发送，避免继续误发。

## 用户确认
- [x] 手动恢复方式：复用网页 `启动` 按钮。
- [x] 网页状态明确显示 `元婴遁逃暂停中`。

## 实际改动
- [x] `xiuxian_bot/plugins/yuanying.py`
  - 新增 `escape_pause_active / escape_pause_reason` 持久化字段。
  - 命中 `元婴遁逃` + `虚弱期` 时写入暂停状态。
  - 暴露 `runtime_pause_reason()` 与 `clear_runtime_pause()` 给 runtime 调用。
- [x] `xiuxian_bot/runtime.py`
  - `AccountRunner` 启动时可选择清除暂停状态（仅人工恢复时启用）。
  - `_send()` / `_execute_action()` / `_on_event()` 接入全局暂停检查。
  - 命中暂停后切换 runner 状态为 `paused`，并立即 `cancel_all()` 取消已排队任务。
- [x] `xiuxian_bot/web.py`
  - 网页 `启动` 操作改为人工恢复入口，调用 `clear_runtime_pause=True`。
- [x] `xiuxian_bot/templates/dashboard.html`
  - `paused` 时按钮文案显示为 `恢复`。
  - 运行状态栏直接展示 pause message。
- [x] `tests/test_yuanying.py`
  - 覆盖裂缝命中元婴遁逃后的暂停状态。
  - 覆盖人工恢复时清理 pause 与进度状态。
- [x] `tests/test_state_persistence.py`
  - 覆盖 pause 状态持久化恢复。
- [x] `tests/test_multi_account.py`
  - 覆盖运行时“同一条消息触发暂停后立刻抑制 action/send”。
  - 修复 web 测试：改为手动 lifespan + `httpx.ASGITransport`，避免 `TestClient` 挂起与真实 runner 干扰。

## 验证结果
- [x] `./venv/bin/python -m unittest tests.test_yuanying tests.test_state_persistence tests.test_multi_account`
- [x] `timeout 60 ./venv/bin/python -m unittest discover`

## 结论
- 元婴遁逃虚弱期现在会把账号切到全局暂停态。
- 暂停一旦生效，会立即阻断后续自动动作和发送。
- 重启不会自动解除暂停；只有网页手动 `恢复` 才会清除该状态。

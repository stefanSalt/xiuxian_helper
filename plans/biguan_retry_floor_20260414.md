# 闭关修炼保底重试计划（2026-04-14）

## 目标
- 解决 `.闭关修炼` 指令偶发丢回包后，自动闭关链路永久停住的问题。
- 增加一个固定保底：发送 `.闭关修炼` 后，若 15 分钟内没有收到闭关相关回包，则自动重试一次。

## 已知边界
- 用户已确认：闭关修炼的系统最大响应间隔按 15 分钟处理。
- 本次只做闭关链路保底重试，不扩展到其他插件。
- 需要考虑重启恢复，避免等待中的保底计时丢失。

## 执行清单
- [x] 梳理当前闭关状态机与持久化字段
- [x] 增加闭关等待回包的保底计时
- [x] 收到有效闭关回包时清理等待状态
- [x] 补充持久化与超时重试测试
- [x] 全量回归并交付

## 实施结果
- 已在 `xiuxian_bot/plugins/biguan.py` 增加闭关回包等待状态 `pending_feedback_deadline_at`，并写入状态库。
- 每次自动发送 `.闭关修炼` 后，都会启动 15 分钟保底 watchdog；若期间未收到有效闭关回包，则自动再次发送 `.闭关修炼`。
- 收到以下有效闭关回包时，会立即清理等待状态：
  - 闭关冷却被重置
  - `打坐调息 N 分钟`
  - `灵气尚未平复`
- 已补充测试：
  - `tests/test_biguan.py`：超时自动重试、有效回包后旧 watchdog 不再误重试
  - `tests/test_state_persistence.py`：重启后恢复等待中的 watchdog
- 已验证：
  - `./venv/bin/python -m unittest tests.test_biguan tests.test_state_persistence`
  - `./venv/bin/python -m unittest discover`

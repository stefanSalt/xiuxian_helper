# 主魂/化身网页 UI 配置计划（2026-04-21）

## 目标
- 不再要求用户手写 `identity_profiles_json`。
- 在账号编辑页用表格表单配置主魂与化身。
- 身份基础字段和每个化身的覆盖配置都通过 UI 操作。
- 保持底层存储结构不变，仍生成 `identity_profiles`。
- 兼容旧 JSON 配置，打开编辑页时自动渲染为 UI 行。

## 方案
- [x] 后端模板值增加 `identity_profiles` 列表。
- [x] 表单 POST 支持 `identity_*` 多值字段组装身份列表。
- [x] 模板将 `身份组 JSON` 替换为身份配置表格。
- [x] 每行支持 key、类型、游戏名、切换目标、显示名、TG username、游戏 ID。
- [x] 每行支持常用插件覆盖 UI：闭关、小药园、星宫、元婴、闯塔、凌霄宫、宗门。
- [x] 前端加最小 JS：新增一行、删除一行、保留至少一行主魂。
- [x] 测试覆盖新增账号/编辑账号能通过 UI 字段保存身份与覆盖配置。

## 交互约定
- 主魂行 `kind=main`，切换目标默认 `主魂`。
- 化身行 `kind=avatar`，切换目标默认等于游戏名。
- 覆盖配置使用三态下拉：不覆盖 / 开启 / 关闭。
- 如果以后需要覆盖具体时间/间隔，再按插件分区扩展，不在本次范围内。

## 验证
- `./venv/bin/python -m unittest tests.test_multi_account.TestWebApp.test_login_and_create_account`
- `./venv/bin/python -m unittest discover -s tests`

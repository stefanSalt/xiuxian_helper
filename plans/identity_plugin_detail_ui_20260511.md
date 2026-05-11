# 多身份插件详细配置 UI 计划（2026-05-11）

## 背景
- 当前 `IdentityProfile.config_overrides` 底层已支持任意配置项覆盖。
- 网页只渲染 `IDENTITY_OVERRIDE_FIELDS`，每个身份只能选择插件开关的 `不覆盖/开启/关闭`。
- 账号编辑页所有身份纵向堆叠，身份多时页面过长。

## 目标
- 每个身份独立显示为 tab，避免一页堆叠太多身份配置。
- 每个身份可配置插件详细参数，而不仅是开启/关闭。
- 保持现有账号全局配置和旧 `config_overrides` 数据兼容。
- 主魂仍固定为第一身份，化身可新增/删除。

## 方案
- [x] 梳理可覆盖字段：复用 `FORM_SECTIONS` 中的插件相关字段，排除账号基础、TG/API、身份切换、全局发送等账号级字段。
- [x] 后端生成 `identity_plugin_sections`，每个字段带 `name/label/type/section`。
- [x] 表单解析支持每个身份的详细覆盖字段：
  - checkbox 三态：`inherit/on/off`。
  - text/number 三态：空值或 inherit 表示不覆盖，有值表示覆盖。
  - 保留当前 `config_overrides` 中已有的开关覆盖。
- [x] 模板将身份区改为 tab：
  - 顶部 tab 列表展示主魂/化身 label。
  - 每个 tab 内包含身份基础字段、插件开关、插件详细参数。
  - 新增化身时动态追加 tab 和面板。
- [x] 新增/更新测试：
  - 保存身份详细覆盖后，`identity_profiles[*].config_overrides` 包含具体字段。
  - 编辑页能回显身份级详细覆盖。
  - 旧的开关覆盖仍可保存并运行时生效。

## 边界
- 不改插件运行逻辑；插件仍通过 `Config.apply_identity()` 接收覆盖后的配置。
- 不把 TG API、游戏群、话题、账号发送限速等账号级配置放入身份覆盖。
- 不改变已有账号配置 JSON 结构，只扩展 `config_overrides` 内容。

## 验证
- [x] `./venv/bin/python -m unittest tests.test_multi_account.TestWebApp.test_login_and_create_account`
- [x] `./venv/bin/python -m unittest tests.test_multi_account tests.test_config`
- [x] `./venv/bin/python -m unittest`
- [x] `git diff --check`

## 待确认
1. 身份级详细配置是否覆盖所有插件参数，还是只覆盖已启用插件的参数。
2. text/number 字段的“不覆盖”交互，是使用空值表示，还是增加显式三态控件。
3. 身份 tab 是否默认只展开当前选中的身份，保存失败后是否保持上次选中 tab。

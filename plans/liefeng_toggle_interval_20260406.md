# 探寻裂缝开关 + 时间间隔配置排查计划（2026-04-06）

## 目标
- [x] 给“探寻裂缝”增加独立开关。
- [x] 修复网页修改时间间隔后，运行时仍沿用旧调度的问题。
- [x] 保持最小修改，不影响已有元婴/星宫其他逻辑。

## 排查结论
- 网页“保存配置”链路本身是通的：
  - `web.py::_build_config_from_form()` 会读取表单字段
  - `AccountRepository.update_account()` 会把新配置写回 sqlite
  - `RunnerManager.sync_account()` 会重启账号 runner
- 真正的问题在于“插件恢复状态优先级高于新配置”：
  - `yuanying.py` 会恢复 `_liefeng_blocked_until / _chuqiao_blocked_until`
  - `xinggong.py` 会恢复 `_next_poll_at / _wenan_next_at`
  - `garden.py` 也有同类 `next_poll_at` 恢复逻辑
- 结果：即使网页里把时间间隔改短，runner 重启后仍会先等“旧的下一次触发时间”到期，表现成“还是以前的间隔”。

## 用户确认
1. [x] “探寻裂缝开关”做成 **只控制 `.探寻裂缝`，不影响 `.元婴出窍`**。
2. [x] 时间间隔修复按 **网页保存后，若相关间隔配置变更，则立即按新配置重新调度** 处理。

## 实际改动
- [x] `config.py`
  - 新增 `enable_yuanying_liefeng` 配置项，默认开启。
- [x] `web.py`
  - 网页表单增加“自动探寻裂缝”开关。
  - 编辑账号保存时，对比旧配置与新配置；若相关间隔配置变更，则清理对应插件的调度型持久化状态。
- [x] `yuanying.py`
  - 探寻裂缝支持独立开关，仅影响 `.探寻裂缝` 与失败重试，不影响 `.元婴出窍`。
  - 新增 `liefeng_block_source`，区分“普通轮询/真实冷却/虚弱期”。
  - 配置变更时仅清理“普通轮询”或旧版未标记来源的裂缝等待状态，避免破坏明确的真实冷却与虚弱暂停。
- [x] 调度状态修复范围
  - `garden.next_poll_at`
  - `xinggong.next_poll_at`
  - `xinggong.wenan_next_at`
  - `yuanying.liefeng_blocked_until`

## 验证
- [x] `./venv/bin/python -m unittest tests.test_yuanying tests.test_multi_account tests.test_state_persistence`
- [x] `timeout 60 ./venv/bin/python -m unittest discover`

## 结果
- 网页现在可以单独关闭“自动探寻裂缝”，同时保留元婴出窍逻辑。
- 网页保存新的时间间隔后，不会再长期沿用旧调度状态。
- 对于元婴裂缝，明确的真实冷却/虚弱期会尽量保留，不会被配置修改直接冲掉。

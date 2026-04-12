# 消息归档保留策略计划（2026-04-11）

## 目标
- 控制消息归档 sqlite 的持续增长，避免 `APP_DB_PATH` 因 `message_archive` 表无限膨胀。
- 在不破坏现有消息检索价值的前提下，给归档增加可配置保留策略。

## 当前现状
- 归档数据存放在 `APP_DB_PATH` 对应 sqlite 的 `message_archive` 表中。
- 当前每条纯文本话题消息都会入库，且保留编辑历史。
- 当前没有任何：
  - 按天数清理
  - 按条数清理
  - 按大小清理
  - 手动压缩 / VACUUM
- 你实测：
  - 昨天 sqlite 大小只有几百 KB
  - 今天已到约 `19 MB`
- 因此当前主要增长源已经明确是**消息归档**，不是运行日志。

## 推荐方案

### 一、保留策略类型
- 推荐：**按天数保留**
- 理由：
  - 最符合“最近一段时间可查”的使用习惯
  - 比按大小清理更稳定，不会忽然删太多
  - 比按条数更容易理解

### 二、默认保留范围
- 推荐：**保留最近 30 天**
- 清理范围：删除 `captured_at` 早于阈值的消息归档

### 三、执行时机
- 推荐：**每天 1 次轻量清理**
- 触发方式推荐：
  - Web / Runner 启动时先做一次
  - 然后每天定时做一次

### 四、落点
- `xiuxian_bot/core/message_archive_repository.py`
  - 新增清理方法
  - 新增统计方法时可顺带返回清理前后条数（如需要）
- `xiuxian_bot/config.py`
  - 新增系统级配置项，例如：
    - `MESSAGE_ARCHIVE_RETENTION_DAYS`
    - `MESSAGE_ARCHIVE_CLEANUP_ENABLED`
- `xiuxian_bot/runtime.py` 或 `xiuxian_bot/web.py`
  - 启动时触发一次清理
- `README.md`
  - 补充消息归档保留策略说明

## 不推荐的首期方案
- 不推荐：按 sqlite 文件大小硬砍
  - 原因：不可控，容易一次删太多
- 不推荐：按条数保留
  - 原因：群活跃波动大，实际保留时间不可预期
- 不推荐：本次先做复杂分表 / 冷归档
  - 原因：超出最小修改原则

## 已确认口径
1. 保留策略
   - 已确认：**按天数保留**

2. 默认天数
   - 已确认：**30 天**

3. 执行方式
   - 已确认：**启动时清一次 + 每天自动清一次**

4. Web 能力
   - 已确认：**先只做自动保留，不额外做“手动清理按钮”**

5. 空间回收
   - 已确认：**清理后尝试执行 `VACUUM`**

## 执行清单
- [x] 确认保留口径
- [x] 新增归档清理配置
- [x] 实现仓储清理逻辑
- [x] 接入自动清理触发点
- [x] 补测试并验证

## 实施结果
- 已在 `xiuxian_bot/core/message_archive_repository.py` 新增按北京时间自然日清理旧归档的能力，支持返回清理前后条数，并在删除后按配置尝试 `VACUUM`。
- 已在 `xiuxian_bot/config.py` 新增系统级配置：
  - `MESSAGE_ARCHIVE_CLEANUP_ENABLED`
  - `MESSAGE_ARCHIVE_RETENTION_DAYS`
  - `MESSAGE_ARCHIVE_VACUUM_ENABLED`
- 已在 `xiuxian_bot/web.py` 接入维护流程：
  - Web 启动时先执行一次归档清理
  - 运行期间每 24 小时自动清理一次
  - 服务关闭时取消后台维护任务
- 已在 `README.md` 补充消息归档保留策略与相关环境变量说明。
- 已补充并通过验证：
  - `./venv/bin/python -m unittest tests.test_message_archive tests.test_config`
  - `./venv/bin/python -m unittest discover`

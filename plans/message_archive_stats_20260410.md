# 消息归档统计展示计划（2026-04-10）

## 目标
- 为现有消息归档页面增加轻量统计展示，帮助判断归档规模与增长速度。
- 保持最小修改原则，不引入额外依赖，不改动现有归档主流程。

## 当前现状
- 现有消息归档仓储已支持：
  - `count_messages(...)`
  - 条件搜索 / 分页
- 现有页面：
  - `/messages`
  - `/accounts/{account_id}/messages`
- 当前页面只展示列表总数，不展示容量或时间窗口统计。

## 推荐方案

### 一、统计展示位置
- 推荐：直接放在消息归档页顶部：
  - 全局页 `/messages`
  - 账号页 `/accounts/{account_id}/messages`
- 首期不额外改 dashboard，避免首页信息过重。

### 二、推荐统计项
- 总条数
- 今日新增（按北京时间自然日）
- 近 7 日新增
- 近 30 日新增
- 当前 sqlite 文件大小

### 三、统计口径
- 推荐：统计卡片基于“当前页面范围”计算，而**不跟随搜索筛选变化**
  - 全局页：统计整个消息归档表
  - 账号页：统计该账号范围
- 搜索结果总数仍沿用当前已有的 `total` 字段。

### 四、实现落点
- `xiuxian_bot/core/message_archive_repository.py`
  - 新增统计查询方法
- `xiuxian_bot/web.py`
  - 注入统计数据到消息页模板
- `xiuxian_bot/templates/messages.html`
  - 增加统计卡片
- `xiuxian_bot/static/style.css`
  - 增加轻量样式

### 五、验证
- 补充仓储 / Web 定向测试
- 跑定向测试与全量测试

## 待确认问题
1. 展示位置
   - 已确认：只放在消息页顶部，不额外放首页
2. 统计项
   - 已确认：总条数 / 今日新增 / 近7日 / 近30日 / sqlite 文件大小
3. 统计口径
   - 已确认：按页面范围统计，不跟随当前搜索筛选

## 执行清单
- [x] 确认统计项与展示口径
- [x] 新增仓储统计查询
- [x] 接入消息页展示
- [x] 补测试并验证

## 实施结果
- 已在 `xiuxian_bot/core/message_archive_repository.py` 新增统计模型与查询方法，支持：
  - 总条数
  - 今日新增（北京时间自然日）
  - 近 7 日新增
  - 近 30 日新增
- 已在 `xiuxian_bot/web.py` 的消息页渲染逻辑中注入统计数据与 sqlite 文件大小。
- 已在 `xiuxian_bot/templates/messages.html` 顶部增加统计卡片。
- 已在 `xiuxian_bot/static/style.css` 增加统计卡片样式。
- 已验证：
  - `./venv/bin/python -m unittest tests.test_message_archive`
  - `./venv/bin/python -m unittest discover`

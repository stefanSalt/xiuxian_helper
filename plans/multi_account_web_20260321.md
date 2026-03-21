# 多账户 + 网页配置改造计划（2026-03-21）

## 目标
- 将当前“单 Telegram 账户 + 单份 .env 配置 + 单实例运行”的架构，升级为“多账户并行运行 + 网页管理配置”。
- 保持现有插件逻辑尽量可复用，避免直接重写所有玩法插件。
- 为后续账号启停、插件开关、日志查看、状态恢复留出标准化接口。

## 当前架构问题
- `Config` 把 `tg_api_id / tg_api_hash / tg_session_name / game_chat_id / topic_id / my_name` 全部绑定为全局单例。
- `app.py` 的 `run()` 只会启动 1 个 `TGAdapter + ReliableSender + Scheduler + Dispatcher + 插件集合`。
- `TGAdapter` 只持有 1 个 `TelegramClient`。
- 当前 sqlite 状态存储已经是按 `plugin` 分 namespace，但还没有按 `account_id` 隔离。
- `.env` 方式不适合网页动态修改，也不适合多账户并行管理。

## 目标架构（建议最小可行方案）

### 一、拆成两层配置
1. 系统配置（全局）
- Web 服务监听地址/端口
- 管理员登录信息
- 全局数据库路径
- 日志根目录

2. 账户配置（每个账号一份）
- Telegram 登录信息：`api_id / api_hash / session_name`
- 游戏定位：`game_chat_id / topic_id / my_name / send_to_topic`
- 各插件开关
- 各插件参数（种子、启阵时间、闯塔时间等）
- 账户启用状态

### 二、核心运行单元改成 AccountRunner
新增一个类似 `AccountRunner` 的运行单元，每个账号独立持有：
- `AccountConfig`
- `TGAdapter`
- `ReliableSender`
- `Scheduler`
- `Dispatcher`
- 插件实例集合
- 该账号专属的 state store namespace

这样每个账号就是一套独立 runtime，互不影响。

### 三、状态持久化改成 account + plugin 双维度
当前表结构应升级为至少支持：
- `account_id`
- `plugin`
- `state_json`
- `updated_at`

否则多账号会互相覆盖同名插件状态。

### 四、网页配置层
建议增加一个轻量 Web 管理端，首期只做服务端渲染页面，不急着上前后端分离。

推荐首期能力：
- 账户列表
- 新增/编辑账户
- 启用/停用账户
- 编辑插件开关和参数
- 查看每个账号的最近日志
- 查看运行状态（已启动/未启动/登录失效）

## 推荐技术路线（最小改动）
- Web：FastAPI + Jinja2 模板（推荐）
- 数据库：沿用 sqlite
- ORM/数据访问：首期可先用标准 `sqlite3` 或轻量 ORM；不建议一开始就把项目整体 ORM 化
- 运行模型：**单进程内管理多个 AccountRunner**（推荐首期）

原因：
- 你现在本来就是单进程异步架构，先把“1 套 runtime”推广成“N 套 runtime”即可
- 不必立即上 Celery / Redis / 多进程 supervisor
- 先把数据模型和账户边界理顺，后面再考虑拆 worker

## 代码改造顺序
1. 抽离 `AccountConfig` / `SystemConfig`
2. 抽离 `build_plugins(account_config, logger)`
3. 抽离 `AccountRunner`
4. 升级 `state_store` 为按 `account_id + plugin` 保存
5. 增加 `account_repository`（从 sqlite 读取账户配置）
6. 新增 `runner_manager`（负责启动/停止多个账号）
7. 新增 `webapp`（FastAPI）
8. 用网页替代 `.env` 里原有账户级配置

## 非目标
- 首期不做复杂 RBAC
- 首期不做前后端分离 SPA
- 首期不做分布式 worker
- 首期不改写各玩法插件核心业务逻辑，只做依赖注入和配置来源改造

## 需要确认
1. Web 形式
- a. FastAPI + 服务端渲染页面（推荐）
- b. 前后端分离（API + Vue/React）

2. 账户运行模型
- a. 单进程内多 AccountRunner（推荐）
- b. 每个账号一个独立子进程

3. 登录与权限
- a. 先做最简单的单管理员密码登录（推荐）
- b. 暂时不做登录，只监听本机

4. 首期网页功能范围
- a. 账户增删改查 + 启停 + 插件配置 + 日志查看（推荐）
- b. 只做配置编辑，不做在线启停

5. 旧 `.env` 迁移策略
- a. 保留 `.env` 仅放系统级配置，账号配置迁到 sqlite（推荐）
- b. 继续把账号配置留在 `.env`，网页只做展示

## 执行进度
- [x] 拆分 `SystemConfig` 与账号级 `Config`
- [x] 将 `plugin_state` 升级为 `account_id + plugin` 双维度存储
- [x] 新增 `AccountRepository` 管理 sqlite 账号配置
- [x] 新增 `AccountRunner` / `RunnerManager`，支持单进程内多账号运行
- [x] 新增 FastAPI Web 管理端（登录、账号 CRUD、启停、日志查看）
- [x] 保留旧 `.env` -> sqlite 首账号自动迁移能力
- [x] 修复启阵冷却恢复回归问题，并补充多账户/网页相关测试
- [x] 更新 `README.md`、`.env.example`、`requirements.txt`

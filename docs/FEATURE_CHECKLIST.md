# StoryForge 功能清单

## 功能总览

| # | 功能域 | 功能 | 状态 | 说明 |
|---|--------|------|------|------|
| 1 | 项目管理 | 创建项目 | ✅ 已实现 | idea + title + genre + target_length |
| 2 | 项目管理 | 项目列表 | ✅ 已实现 | 按更新时间排序 |
| 3 | 项目管理 | 项目详情 | ✅ 已实现 | 含阶段、章节游标、协作者 |
| 4 | 项目管理 | 更新项目信息 | ✅ 已实现 | PATCH /api/projects/{id} |
| 5 | 项目管理 | 项目摘要 | ✅ 已实现 | 任务统计 + LLM 用量 + 最新事件 + 失败摘要 |
| 6 | 流水线 | 首轮生成 | ✅ 已实现 | brief → outline → chapter → review |
| 7 | 流水线 | 下一章 | ✅ 已实现 | 基于大纲生成下一章节流水线 |
| 8 | 流水线 | 批量章节推进 | ✅ 已实现 | 排队到目标章节 |
| 9 | 流水线 | 大纲自动扩展 | ✅ 已实现 | 章节用完时自动追加新章节 |
| 10 | 流水线 | Review 拒绝重写 | ✅ 已实现 | 拒绝后自动排队 chapter_rewrite |
| 11 | 资产 | 创建资产 | ✅ 已实现 | POST /api/projects/{id}/assets |
| 12 | 资产 | 资产列表 | ✅ 已实现 | GET /api/projects/{id}/assets |
| 13 | 资产 | 按类型获取最新资产 | ✅ 已实现 | GET /api/projects/{id}/assets/{type} |
| 14 | 资产 | 资产版本列表 | ✅ 已实现 | GET /api/projects/{id}/assets/{type}/versions |
| 15 | 资产 | 编辑资产（新版本） | ✅ 已实现 | PUT /api/projects/{id}/assets/{asset_id} |
| 16 | 资产 | 版本 Diff | ✅ 已实现 | POST /api/projects/{id}/assets/{type}/diff |
| 17 | 资产 | 乐观并发控制 | ✅ 已实现 | base_version 可选，冲突返回 409 |
| 18 | 资产 | 编辑审计 | ✅ 已实现 | actor_id / actor_name / change_note |
| 19 | 资产 | 章节导航 | ✅ 已实现 | GET /api/projects/{id}/chapters |
| 20 | 任务 | 任务列表 | ✅ 已实现 | GET /api/projects/{id}/tasks |
| 21 | 任务 | 创建任务 | ✅ 已实现 | POST /api/projects/{id}/tasks |
| 22 | 任务 | 队列处理（单个） | ✅ 已实现 | POST /api/projects/{id}/workers/process-next |
| 23 | 任务 | 队列处理（批量） | ✅ 已实现 | POST /api/projects/{id}/workers/drain |
| 24 | 导出 | Markdown 导出 | ✅ 已实现 | text/markdown content-type |
| 25 | 导出 | 纯文本导出 | ✅ 已实现 | text/plain content-type |
| 26 | 导出 | 起点格式导出 | ✅ 已实现 | qidian.txt |
| 27 | 导出 | 晋江格式导出 | ✅ 已实现 | jinjiang.txt |
| 28 | 导出 | 章节号过滤 | ✅ 已实现 | 跳过非正整数的 chapter_number |
| 29 | 协作 | 协作者列表 | ✅ 已实现 | GET /api/projects/{id}/collaborators |
| 30 | 协作 | 添加/更新协作者 | ✅ 已实现 | POST /api/projects/{id}/collaborators |
| 31 | 质量 | 内容质量评分 | ✅ 已实现 | ContentQualityEngine |
| 32 | 质量 | 本地回退评分 | ✅ 已实现 | 不依赖 LLM 的启发式规则 |
| 33 | 质量 | A/B 实验记录 | ✅ 已实现 | 预留质量实验统计接口 |
| 34 | LLM | 多 Provider 路由 | ✅ 已实现 | Anthropic / OpenAI 配置切换 |
| 35 | LLM | Skip LLM 回退模式 | ✅ 已实现 | STORYFORGE_SKIP_LLM=1 |
| 36 | LLM | LLM 用量统计 | ✅ 已实现 | total_tokens / input_tokens / output_tokens |
| 37 | 可观测 | 项目摘要 API | ✅ 已实现 | 任务统计 + LLM 用量 + 最新事件 |
| 38 | 可观测 | 任务状态追踪 | ✅ 已实现 | 状态机 + 进度 + 步骤 |
| 39 | 可观测 | 事件记录与回放 | ✅ 已实现 | 持久化事件 + 游标 |
| 40 | Web UI | 三栏工作台 | ✅ 已实现 | 项目列表 / 编辑器 / 辅助面板 |
| 41 | Web UI | 项目创建与选择 | ✅ 已实现 | 表单 + 列表 |
| 42 | Web UI | 资产浏览与编辑 | ✅ 已实现 | 版本 + 结构化数据 |
| 43 | Web UI | 章节导航 | ✅ 已实现 | 审核状态徽章 |
| 44 | Web UI | 大纲展示（Arc 分组） | ✅ 已实现 | 按 Arc 分组渲染 |
| 45 | Web UI | 任务队列展示 | ✅ 已实现 | 最新 12 条，状态徽章 |
| 46 | Web UI | 自动轮询（2s） | ✅ 已实现 | 项目数据自动刷新 |
| 47 | Web UI | 导出下载 | ✅ 已实现 | Markdown / Text |
| 48 | Web UI | 暗色主题 | ✅ 已实现 | 默认暗色 |
| 49 | Web UI | 响应式布局 | ✅ 已实现 | 三栏 → 两栏 → 单栏 |
| 50 | CLI | 创建项目 | ✅ 已实现 | `storyforge create --idea "..."` |
| 51 | CLI | 启动流水线 | ✅ 已实现 | `storyforge start <id> --run` |
| 52 | CLI | 查看状态 | ✅ 已实现 | `storyforge status <id>` |
| 53 | CLI | 章节列表 | ✅ 已实现 | `storyforge chapters <id>` |
| 54 | CLI | 批量推进 | ✅ 已实现 | `storyforge loop <id> --target N --run` |
| 55 | CLI | 处理队列 | ✅ 已实现 | `storyforge drain <id>` |
| 56 | CLI | 阅读章节 | ✅ 已实现 | `storyforge read <id> <chapter>` |
| 57 | CLI | 启动服务 | ✅ 已实现 | `storyforge serve [--port N] [--no-browser]` |
| 58 | 持久化 | InMemory 存储 | ✅ 已实现 | 开发/测试用 |
| 59 | 持久化 | SQLite 存储 | ✅ 已实现 | 默认生产存储 |
| 60 | 安全 | XSS 防护 | ✅ 已实现 | escapeHtml 全部动态插入 |

---

## 未实现功能（Backlog）

| # | 功能域 | 功能 | 优先级 | 说明 |
|---|--------|------|--------|------|
| U1 | 协作 | 实时协作编辑 | P2 | WebSocket 级实时同步 |
| U2 | 协作 | 角色权限控制 | ~~P1~~ ✅ 已实现 | API 级别 viewer 禁止写/创建/管理协作者，author 独占管理权 |
| U3 | 资产 | 资产删除 | ~~P2~~ ✅ 已实现 | 软删除 + 回收站面板 + 恢复功能 |
| U4 | 资产 | 资产版本回滚 | ~~P2~~ ✅ 已实现 | 基于旧版本内容创建新版本，前端版本选择器 + 回滚按钮 |
| U5 | 资产 | 版本 Diff 可视化 | ~~P2~~ ✅ 已实现 | 前端"查看差异"按钮 + 右侧面板展示 unified diff |
| U6 | LLM | 风格学习 | P2 | 学习作者写作风格并应用到生成 |
| U7 | LLM | 模型自动选择 | ~~P2~~ ✅ 已实现 | 按任务类型路由模型，STORYFORGE_MODEL_ROUTING 环境变量配置 |
| U8 | Web UI | 键盘快捷键 | ~~P2~~ ✅ 已实现 | Cmd/Ctrl+S 保存、Cmd/Ctrl+Enter 首轮、N 下一章、D 队列、R 刷新 |
| U9 | Web UI | 成功消息自动消失 | ~~P2~~ ✅ 已实现 | 3 秒后自动清除，新消息替换旧消息时取消旧计时器 |
| U10 | Web UI | ARIA 无障碍增强 | ~~P3~~ ✅ 已实现 | 12 个 aria-label + 3 个 aria-live 区域 |
| U11 | Web UI | 高对比度模式 | ~~P3~~ ✅ 已实现 | 手动切换 + prefers-contrast 媒体查询自动适配 |
| U12 | 导出 | 起点/晋江格式 Web 端导出 | ~~P2~~ ✅ 已实现 | 工具栏新增"导出起点"和"导出晋江"按钮，调用已有后端 API |
| U13 | 流水线 | 多流水线并行 | P3 | 一个项目多分支生成 |
| U14 | 可观测 | SSE 实时事件推送 | ~~P1~~ ✅ 已实现 | 替代轮询的实时方案，EventSource 接入 + 30s 兜底刷新 |
| U15 | 安全 | 用户认证系统 | ~~P1~~ ✅ 已实现 | API Token 认证：生成/验证/吊销，Authorization: Bearer 头 |
| U16 | 安全 | 速率限制 | ~~P2~~ ✅ 已实现 | 滑动窗口限流（60 req/min），429 响应 + X-RateLimit-Remaining 头 |
| U17 | 持久化 | 云端存储 | P3 | S3 / 云数据库 |

---

## 已验证测试覆盖

| 测试文件 | 测试数 | 覆盖范围 |
|----------|--------|----------|
| test_app.py | 28 | API 端点烟测：项目/资产/任务/导出/协作/并发 |
| test_cli.py | 10 | CLI 命令：create/start/status/chapters/drain/read/serve |
| test_closed_loop_safety.py | 6 | 首轮生成安全：任务链、幂等、资产完整 |
| test_llm.py | 5 | LLM 客户端：多 Provider、Skip LLM、Usage 统计 |
| test_multi_chapter.py | 7 | 多章节流水线：大纲扩展、版本保护、Review 重写 |
| test_observability.py | 2 | 项目摘要、质量统计 |
| test_prompts.py | 8 | Prompt 构建：brief/outline/chapter/review/rewrite |
| test_quality.py | 18 | 质量引擎：结构/一致性/评分/实验 |
| test_recovery.py | 2 | 任务恢复：重试/取消 |
| test_runtime.py | 38 | 运行时：worker/claim/lease/audit/severity |
| test_state_machine.py | 2 | 状态机：流转/验证 |
| **总计** | **126** | **100% 通过** |

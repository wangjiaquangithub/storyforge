# StoryForge

AI 协作长篇网文生产工作台。面向职业网文作者和小型内容工作室，提供从创意到导出的完整生产流。

## 核心特性

- **项目驱动**：每个创意是一个项目，独立管理生命周期
- **资产工作台**：brief、大纲、章节、角色卡、世界观等均可创建、编辑、版本追溯
- **自动化流水线**：创意 → brief → 大纲 → 章节 → review → 导出
- **多 Provider LLM**：支持 Anthropic 与 OpenAI，并可切到 `STORYFORGE_SKIP_LLM=1` 的本地回退模式
- **质量与协作基础设施**：项目摘要、质量实验统计、协作者列表、支持可选 `base_version` 的资产乐观并发控制
- **平台化导出**：支持 Markdown、纯文本、起点格式、晋江格式
- **一键启动**：`storyforge serve` 自动打开浏览器工作台

## 安装

```bash
pip install -e .
```

如需 LLM 生成（可选），在 `.env` 中配置：

```bash
# 通用 key，或使用 provider 专属 key
STORYFORGE_API_KEY=
STORYFORGE_LLM_PROVIDER=anthropic
STORYFORGE_ANTHROPIC_API_KEY=
STORYFORGE_OPENAI_API_KEY=
STORYFORGE_MODEL=claude-sonnet-4-6-20250514

# CI / 本地演示可关闭真实 LLM 调用
STORYFORGE_SKIP_LLM=0
```

完整变量示例见 `.env.example`。

## 快速开始

```bash
# 启动服务并自动打开浏览器
storyforge serve

# 或者仅启动 API（不打开浏览器）
storyforge serve --no-browser

# 指定端口
storyforge serve --port 9000
```

在浏览器工作台中：
1. 输入核心创意，点击**创建项目**
2. 点击**首轮生成**，系统自动完成 brief → 大纲 → 第一章 → review
3. 在资产面板查看、编辑生成的内容，并保存为新版本
4. 点击**下一章**继续推进章节流水线
5. 按需导出 Markdown 或 Text

## CLI 命令速览

```bash
storyforge create --idea "一个失势太子在残破灵械之城中重建王朝"
storyforge start <project_id> --run
storyforge status <project_id>
storyforge chapters <project_id>
storyforge loop <project_id> --target 10 --run
storyforge drain <project_id>
storyforge read <project_id> 1
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端工作台 |
| GET | `/health` | 健康检查 |
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/projects/{id}` | 项目详情 |
| PATCH | `/api/projects/{id}` | 更新项目基础信息 |
| GET | `/api/projects/{id}/summary` | 项目执行摘要与质量统计 |
| GET | `/api/projects/{id}/assets` | 资产列表 |
| POST | `/api/projects/{id}/assets` | 创建资产 |
| PUT | `/api/projects/{id}/assets/{asset_id}` | 编辑资产并保存为新版本 |
| GET | `/api/projects/{id}/assets/{type}` | 最新某类型资产 |
| GET | `/api/projects/{id}/assets/{type}/versions` | 某类型资产全部版本 |
| POST | `/api/projects/{id}/assets/{type}/diff` | 最新两个版本 diff |
| GET | `/api/projects/{id}/collaborators` | 协作者列表 |
| POST | `/api/projects/{id}/collaborators` | 添加 / 更新协作者 |
| POST | `/api/projects/{id}/runs/first-loop` | 首轮生成 |
| POST | `/api/projects/{id}/runs/next-chapter` | 追加下一章 |
| POST | `/api/projects/{id}/runs/chapter-loop` | 批量排队到目标章节 |
| GET | `/api/projects/{id}/chapters` | 章节导航摘要 |
| POST | `/api/projects/{id}/export` | 导出项目（`markdown` / `text` / `qidian` / `jinjiang`） |
| POST | `/api/projects/{id}/workers/drain` | 处理队列 |

## 已验证工作流

- 本地 HTTP 烟测已覆盖：创建项目、首轮生成、项目摘要、资产列表、章节列表、Markdown/Text 导出、章节资产保存新版本。
- 浏览器工作台已人工/自动验收黄金路径：加载页面、创建项目、首轮生成、查看大纲/任务/资产、保存资产新版本、导出 Markdown/Text。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

使用 `uv` 时可运行：

```bash
uv run --extra dev pytest
uv run storyforge serve --no-browser --port 9001
```

## 架构

```
CLI ──> FastAPI ──> ClosedLoopService ──> StoryForgeGenerators
                              │                  │
                        StoryForgeStore     ContentQualityEngine
                              │
                      (InMemory / SQLite)
```

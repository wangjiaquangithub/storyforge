# StoryForge 业务架构

## 1. 一句话定位

StoryForge 是面向职业网文作者和小型内容工作室的 **AI 协作长篇网文生产工作台**，提供从创意到导出的完整、可控、可追溯的生产流水线。

---

## 2. 核心业务实体

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│ Project  │────>│  Asset  │     │  Task   │────>│  Event  │
│  项目    │     │  资产    │     │  任务    │     │  事件    │
└────┬────┘     └─────────┘     └─────────┘     └─────────┘
     │
     │  contains
     ▼
┌─────────┐     ┌────────────┐
│  Config  │     │Collaborator│
│  配置    │     │  协作者    │
└─────────┘     └────────────┘
```

### 2.1 Project（项目）

- 每个创意是一个项目，独立管理生命周期
- 项目有阶段（phase）：`idle → briefing → outlining → drafting → reviewing → completed`
- 项目有目标章节数（`target_length`）、题材（`genre`）、标题（`title`）、核心创意（`idea`）
- 项目有协作者列表，每个协作者有角色（`author`/`editor`/`viewer`）

### 2.2 Asset（资产）

- 项目生产过程中产生的所有内容资产
- 资产类型：`brief`、`outline`、`chapter`、`world`、`characters`、`rules`、`timeline`、`review_note`、`continuity_note`、`chapter_summary`
- 资产支持版本追溯（version number 递增）
- 资产有来源（`source`）：`human`（人工创建/编辑）或 `system`（AI 生成）
- 资产包含人类可读正文（`content`）和结构化数据（`structured_data`）
- 资产支持乐观并发控制（`base_version`），防止多人同时编辑覆盖

### 2.3 Task（任务）

- 项目中的每个生成/处理步骤是一个任务
- 任务类型：`brief_generation`、`outline_generation`、`chapter_generation`、`chapter_review`、`outline_expansion`、`chapter_rewrite`
- 任务有状态机：`queued → running → completed / failed / waiting_retry / cancelled`
- 任务可以链接成流水线（`parent_task_id`）
- 任务有进度（`progress` 0.0 → 1.0）和当前步骤（`current_step`）

### 2.4 Event（事件）

- 任务状态变更的持久化记录
- 按时间排序，支持游标回放
- 最新事件消息展示在项目概览中

### 2.5 Config（配置）

- 系统默认 → 项目覆盖 → 任务冻结快照
- 支持 LLM 模型选择、温度、重试次数等参数
- 配置在任务执行时冻结为快照，不因后续修改影响运行中任务

### 2.6 Collaborator（协作者）

- 项目级别的角色分配
- 角色：`author`（作者）、`editor`（编辑）、`viewer`（观察者）
- 编辑资产时记录操作者（`actor_id`/`actor_name`）

---

## 3. 业务流水线

### 3.1 首轮生成（First Loop）

```
idea ──> brief_generation ──> outline_generation ──> chapter_generation ──> chapter_review
           ↓                       ↓                      ↓                    ↓
         brief asset            outline asset          chapter asset        review_note asset
```

- 用户输入核心创意
- 系统按顺序生成 brief → outline → 第一章 → review
- 每个步骤是独立任务，可追踪状态和重试

### 3.2 章节续写（Next Chapter）

```
已完成第 N 章 ──> brief_gen(N+1) ──> outline_gen(N+1) ──> chapter_gen(N+1) ──> chapter_review(N+1)
```

- 基于已有大纲中的章节信息，生成下一章
- 如果大纲章节用完，自动扩展大纲（追加新章节）
- 同样经过 review 门控

### 3.3 章节批量推进（Chapter Loop）

- 批量排队从当前章节到目标章节的全部流水线
- 每章 4 个任务（brief → outline → chapter → review）
- 适合"我要写到第 10 章"的场景

### 3.4 Review 拒绝与重写（Review Reject → Rewrite）

- review 不通过时，自动生成 `chapter_rewrite` 任务
- 重写任务携带 review 反馈（问题列表）
- 重写后重新进入 review

---

## 4. 业务参与者

| 角色 | 描述 |
|------|------|
| 作者 | 项目创建者，拥有完整编辑/生成/导出权限 |
| 编辑 | 被邀请的协作者，可编辑资产、保存新版本 |
| 观察者 | 只读权限，查看项目状态和资产 |
| AI Agent | 生成 brief、outline、chapter、review 内容 |
| 系统 | 任务调度、状态机流转、事件记录 |

---

## 5. 业务约束

- 每个项目只有一条生成流水线，不存在并行多条
- 资产版本只能递增，不能回退
- 资产编辑是可选的乐观并发控制（`base_version` 为空时跳过）
- 所有章节生成都在后台异步执行，不阻塞 UI
- 导出只使用最新的章节版本
- 大纲扩展不会修改旧版本的结构（deep copy 保护）

---

## 6. 商业模式

| 维度 | 当前 | 未来 |
|------|------|------|
| 部署 | 本地/自建 | 可托管 SaaS |
| 用户 | 个人作者 | 工作室多用户协作 |
| 计费 | 自带 LLM key | 平台代管 LLM 按量计费 |
| 存储 | 本地 SQLite | 云端持久化 |

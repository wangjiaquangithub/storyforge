# StoryForge UI 设计规范

版本：V1.0 | 日期：2026-04-26

---

## 1. 设计哲学

**界面作为背景消失。** 创作工具的唯一主角是文字。所有视觉手段都服务于一个目标：让用户忘记界面的存在。

三条约束：
- **沉浸感** — 不用装饰性效果抢注意力
- **确定性** — 状态始终可见，不让用户猜
- **克制** — 每个视觉元素必须有功能理由，没有就删掉

---

## 2. 色彩系统

### 2.1 色值

| 变量 | 色值 | 用途 |
|------|------|------|
| `--bg` | `#0D0D10` | 全局底色，极低饱和蓝紫调，非纯黑 |
| `--surface` | `#16161B` | 面板、卡片背景 |
| `--surface-hover` | `#1F1F26` | 悬浮态背景 |
| `--elevated` | `#24242E` | 弹窗、下拉背景 |
| `--text` | `#E8ECF1` | 主文字，对比度 ≥ 87% |
| `--text-secondary` | `#8D97A8` | 次要文字，对比度 ≥ 4.5% |
| `--muted` | `#5A6478` | 弱提示文字 |
| `--line` | `rgba(255, 255, 255, 0.06)` | 弱分割线 |
| `--line-strong` | `rgba(255, 255, 255, 0.12)` | 强分割线，焦点态 |

### 2.2 功能色

| 用途 | 色值 | 说明 |
|------|------|------|
| 强调 | `#7C6AED` | 主品牌色，蓝紫 |
| 成功 | `#34D399` | 低饱和绿 |
| 警告 | `#FBBF24` | 低饱和黄 |
| 错误 | `#F87171` | 低饱和红 |

### 2.3 渐变

唯一允许使用渐变的场景：**Primary 按钮**

```css
linear-gradient(135deg, #7C3AED, #06B6D4)
```

其他地方禁用渐变。

### 2.4 噪点纹理

全屏叠加一层 2% 不透明度的噪点，增加实体感，消除纯色数字感。

```css
body::after {
  content: "";
  position: fixed;
  inset: 0;
  opacity: 0.02;
  pointer-events: none;
  background-image: url("data:image/svg+xml,..."); /* SVG 噪点 */
}
```

---

## 3. 排版

### 3.1 字体

| 场景 | 字体 |
|------|------|
| 系统界面 | `Inter, -apple-system, PingFang SC, sans-serif` |
| 编辑器正文 | 系统默认无衬线体，用户可切换宋体 |
| 代码/JSON | `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` |

### 3.2 字阶

| 层级 | 字号 | 字重 | 用途 |
|------|------|------|------|
| H1 | 24px | 700 | 页面标题 |
| H2 | 18px | 600 | 卡片/面板标题 |
| H3 | 15px | 600 | 小节标题 |
| Body | 16px | 400 | 编辑器正文 |
| Small | 13px | 400 | 按钮、表单 |
| Meta | 12px | 400 | badge、时间戳 |
| Label | 11px | 700, uppercase, letter-spacing 0.06em | 段标题 |

### 3.3 行高

| 场景 | 值 |
|------|-----|
| 编辑器正文 | `1.8` |
| 系统界面 | `1.6` |
| 列表项 | `1.4` |

### 3.4 编辑器限宽

编辑器内容最大宽度 `900px`，两侧自动居中。避免行长过长影响阅读。

```css
.editor-content { max-width: 900px; margin: 0 auto; }
```

---

## 4. 间距与圆角

### 4.1 间距

基于 4px 网格：

| Token | 值 | 用途 |
|-------|----|------|
| `--space-1` | 4px | badge 内边距 |
| `--space-2` | 8px | 列表项间隙 |
| `--space-3` | 12px | 卡片内间距 |
| `--space-4` | 16px | 面板内区块间距 |
| `--space-5` | 24px | 顶栏内边距 |

统一规则：
- 面板内 `gap: 20px`
- 卡片内 `gap: 12px`
- 列表项之间 `gap: 4px`

### 4.2 圆角

| 元素 | 圆角 |
|------|------|
| 按钮 | 8px |
| 输入框/选择器 | 8px |
| 卡片 | 12px |
| 弹窗 | 12px |
| badge | 999px |

---

## 5. 布局架构

### 5.1 三栏工作台

```
┌──────────────────────────────────────────────────────────┐
│  Topbar (sticky, height 56px)                             │
├─────────┬─────────────────────────────┬──────────────────┤
│ Nav     │      Main                   │      Assistant    │
│ 72px    │      flex: 1, max 900px     │      320px        │
│ 图标栏  │      编辑器/项目概览         │      章节/大纲/任务│
├─────────┴─────────────────────────────┴──────────────────┤
│ 响应式：                                                    │
│ ≤1200px → 两栏（Nav+Main / Assistant 独立行）              │
│ ≤840px  → 单栏堆叠                                         │
└──────────────────────────────────────────────────────────┘
```

**各栏职责：**

| 栏 | 宽度 | 职责 | 不放什么 |
|----|------|------|---------|
| Nav | 72px | 项目切换图标、设置入口 | 文字列表、详情 |
| Main | flex (编辑器限宽 900px) | 项目概览、资产编辑器、消息 | 导航列表 |
| Assistant | 320px | 章节导航、大纲预览、任务队列、评论 | 创建表单、编辑器 |

### 5.2 面板

- 面板间分割线：`1px solid var(--line)`
- 面板内 `padding: 20px`
- 面板内容 `flex-direction: column; gap: 20px`
- 面板独立滚动 `overflow: auto`

### 5.3 容器与卡片

```css
/* 卡片 — 有边框，但用弱色 */
background: var(--surface);
border: 1px solid var(--line);
border-radius: 12px;
padding: 16px;
```

**原则：** 卡片保留边框，但用 `rgba(255,255,255,0.06)` 弱色。边框的作用是区分层级，不是装饰。去掉边框反而会让界面糊在一起。

**悬浮层（弹窗、下拉）：**
```css
background: var(--elevated);
border: 1px solid rgba(255, 255, 255, 0.1);
border-radius: 12px;
backdrop-filter: blur(20px); /* 仅用于悬浮层，不用于面板 */
```

---

## 6. 组件规范

### 6.1 按钮

| 类型 | 样式 | 用途 |
|------|------|------|
| Primary | 渐变背景 + 白色文字 + 无描边 | 推进流程的唯一主操作 |
| Ghost | 透明背景 + `var(--line-strong)` 边框 | 次要操作 |
| Icon | 透明背景 + 悬浮显示 `var(--surface-hover)` + 点击缩放 0.95 | 工具按钮 |
| Danger-text | 透明背景 + 红色文字 + 无描边 | 删除等危险操作 |

**规则：**
- 一个操作区只能有一个 Primary 按钮
- Primary 按钮文案不超过 6 个中文字
- 按钮默认 `padding: 8px 14px`, `font-size: 13px`, `border-radius: 8px`
- Small 变体 `padding: 5px 10px`, `font-size: 12px`

### 6.2 表单控件

```css
background: var(--elevated);
border: 1px solid var(--line);
border-radius: 8px;
padding: 9px 12px;
color: var(--text);
```

**焦点态：**
```css
border-color: var(--line-strong);
box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.15);
```

**规则：**
- textarea 默认 `resize: vertical; min-height: 96px`
- 编辑器正文 `min-height: 420px`
- JSON 编辑区 `font-family: monospace; min-height: 180px`

**AI 生成态：** 当 AI 正在生成内容时，输入框底部出现 2px 线性流光进度条：

```css
.ai-progress-bar {
  height: 2px;
  background: linear-gradient(90deg, #7C3AED, #06B6D4, #7C3AED);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite linear;
}
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
```

### 6.3 列表项

```css
background: transparent;
border: none;
border-radius: 8px;
padding: 10px 12px;
transition: background 0.12s ease;
```

**交互态：**
- hover: `background: var(--surface-hover)`
- active（选中）: `background: rgba(124, 58, 237, 0.1)`

**结构：**
- `.list-title`：`font-weight: 500; font-size: 13px; line-height: 1.4`
- `.list-meta`：`font-size: 12px; color: var(--text-secondary); display: flex; gap: 8px`
- 用 `<button>` 元素，支持键盘导航

**列表项无描边。** 用 hover 和选中态区分，不用边框。

### 6.4 Badge

```css
display: inline-flex;
align-items: center;
gap: 4px;
font-size: 11px;
padding: 2px 7px;
border-radius: 999px;
background: var(--surface);
color: var(--text-secondary);
```

**变体：**
- `.ok`: `background: rgba(52, 211, 153, 0.12); color: #6ee7b7`
- `.warn`: `background: rgba(251, 191, 36, 0.12); color: #fde68a`
- `.danger`: `background: rgba(248, 113, 113, 0.12); color: #fca5a5`

**无描边。** 用半透明背景色区分语义。

### 6.5 空状态

```css
padding: 48px 24px;
text-align: center;
color: var(--text-secondary);
border: 1px dashed var(--line);
border-radius: 16px;
background: transparent;
```

**文案规则：** 不说"还没有 X"，说"先 Y，然后 Z"。

### 6.6 消息反馈

```css
padding: 10px 14px;
border-radius: 8px;
border: none;
background: var(--surface);
font-size: 13px;
```

**变体：**
- `.error`: `background: rgba(248, 113, 113, 0.08); color: #fca5a5`
- `.success`: `background: rgba(52, 211, 153, 0.08); color: #6ee7b7`

**规则：** 成功消息 3 秒自动消失，带句号。错误消息不加句号，保持简短。

### 6.7 统计卡片

```css
padding: 16px;
border-radius: 12px;
background: var(--surface);
text-align: center;
```

**内容：** 数值 `22px, font-weight: 600`，标签 `11px, color: var(--text-secondary), uppercase, letter-spacing: 0.04em`

**无描边。** 靠背景色与整体区分。

### 6.8 进度条

```css
/* 轨道 */
height: 3px;
background: rgba(255, 255, 255, 0.04);
border-radius: 2px;

/* 填充 */
background: var(--ok) / var(--warn) / var(--danger);
```

### 6.9 顶栏

```css
height: 56px;
padding: 0 24px;
background: var(--bg);
position: sticky;
top: 0;
z-index: 5;
```

底部细线：`::after { height: 1px; background: var(--line); }`

标题 `20px, font-weight: 600, letter-spacing: -0.01em`

---

## 7. 交互规范

### 7.1 状态反馈循环

```
用户操作 → API 请求 → 成功：刷新数据 + 成功消息
                             → 失败：错误消息 + 连接状态变红
```

- 错误必须在 UI 上可见，不能只在 console 输出
- 连接状态在顶栏 badge 中始终可见

### 7.2 AI 生成规范

- **流式渲染：** 文字由 `var(--muted)` 渐变为 `var(--text)`，禁止全屏 loading
- **建议标记：** AI 建议以"侧边高亮"出现，不用内联弹窗截断打字流
- **生成指示器：** 编辑器底部显示微型进度条 + "AI 生成中"

### 7.3 键盘快捷键

| 快捷键 | 操作 |
|--------|------|
| Cmd/Ctrl + S | 保存当前资产新版本 |
| Cmd/Ctrl + Enter | 运行首轮生成 |
| N | 下一章 |
| D | 处理队列 |
| R | 刷新全部 |

### 7.4 SSE 连接指示

顶栏右上角 badge：

| 状态 | 表现 |
|------|------|
| Connected | 绿色呼吸灯动画（opacity 0.6 → 1 循环） |
| Reconnecting | 琥珀色常亮 |
| Disconnected | 红色常亮 |

---

## 8. 响应式

| 断点 | 行为 |
|------|------|
| >1200px | 三栏（72px / flex / 320px） |
| 840px–1200px | 两栏（72px+Main / Assistant 独立行） |
| <840px | 单栏堆叠 |

- 编辑器 `max-width: 900px`，居中
- 每个面板独立滚动
- 移动端保证功能可用，不保证体验最优

---

## 9. 文案规范

### 9.1 中文优先

- 界面文字全部使用简体中文
- 技术术语保留英文原文：brief、outline、asset、chapter
- 标点使用全角：句号 `。`、冒号 `：`、顿号 `、`

### 9.2 按钮文案

- 动词 + 名词结构，不超过 6 个中文字
- 导出按钮可缩短为缩写：`MD` / `TXT` / `起点` / `晋江`

### 9.3 中文映射表

| 枚举 | 界面文字 | 枚举 | 界面文字 |
|------|---------|------|---------|
| brief | 项目概要 | accepted | 已接受 |
| world | 世界观 | queued | 排队中 |
| characters | 角色卡 | running | 运行中 |
| rules | 规则集 | waiting_retry | 等待重试 |
| timeline | 时间线 | failed | 失败 |
| outline | 大纲 | cancelled | 已取消 |
| chapter | 章节 | completed | 已完成 |
| review_note | 审查意见 | intake | 创意录入 |
| continuity_note | 连续性记录 | briefing | 概要生成 |
| chapter_summary | 章节摘要 | outlining | 大纲生成 |
| brief_generation | Brief 生成 | drafting | 章节撰写 |
| outline_generation | 大纲生成 | review | 审查修改 |
| chapter_generation | 章节生成 | publishing | 发布准备 |
| chapter_review | 章节审查 | | |
| export | 导出 | | |

### 9.4 错误消息

- 格式：`{具体问题描述}。`
- 不说"请求失败"，说"无法连接到 StoryForge API。"

---

## 10. 文件与实现规则

### 10.1 前端文件结构

```
frontend/
  index.html  # 唯一 HTML 文件，包含全部 CSS 和 JS
```

- 不使用 CSS 框架、预处理器、CSS-in-JS
- 不使用 JS 框架，原生 `fetch` + `async/await`
- 所有动态 HTML 经过 `escapeHtml()`
- 状态管理用单一 `state` 对象

### 10.2 CSS 规则

- 使用 CSS 变量统一管理颜色和间距
- 媒体查询直接写在 `<style>` 底部

### 10.3 JavaScript 规则

- 渲染函数纯函数：输入 state，输出 DOM
- 事件绑定用 `addEventListener`，不用 inline `onclick`
- 用户输入在 `withUiError` 中统一处理错误

---

## 11. 设计检查清单

### 视觉
- [ ] 新颜色是否使用了 CSS 变量？
- [ ] 对比度是否满足 WCAG AA（≥ 4.5:1）？
- [ ] 圆角是否符合组件层级规则？
- [ ] 间距是否是 4px 的倍数？

### 布局
- [ ] 新组件放在正确的栏中吗？
- [ ] 编辑器是否限宽 900px 居中？
- [ ] 面板是否独立滚动？

### 交互
- [ ] 用户操作后有反馈吗？
- [ ] 错误是否在 UI 上可见？
- [ ] AI 生成是否用流式渲染而非全屏 loading？

### 文案
- [ ] 界面文字是简体中文吗？
- [ ] 枚举值是否通过中文映射表转换？
- [ ] 错误消息是否具体？

### 代码
- [ ] 动态 HTML 是否经过 `escapeHtml()`？
- [ ] 是否引入不必要的依赖？

---

## 12. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| V1.0 | 2026-04-26 | 初始版本。合并 v1 规范与 v2 增量，统一色彩系统、组件规范、交互规则 |

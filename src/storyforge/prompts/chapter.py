from __future__ import annotations


def build_chapter_messages(
    project_title: str,
    brief_summary: str,
    chapter_number: int,
    chapter_title: str,
    chapter_summary: str,
    max_words: int = 2000,
    previous_chapters: list[dict[str, str]] | None = None,
    style_profile: dict | None = None,
    rules: list[dict] | None = None,
) -> list[dict]:
    system = (
        "你是资深网络小说作者，擅长写引人入胜的长篇连载。"
        "你的写作风格：\n"
        "- 人物内心冲突丰富，情感描写细腻\n"
        "- 紧张氛围逐步升级，节奏紧凑\n"
        "- 对话生动自然，符合角色性格\n"
        "- 清晰的因果推进，每个场景都有明确目的\n"
        "- 章末留有悬念或转折，吸引读者继续阅读\n\n"
        "请创作完整的章节正文，不是提纲或片段。"
        "直接进入场景，不要概述性叙述。"
    )
    user = (
        f"项目: {project_title}\n\n"
        f"故事背景:\n{brief_summary}\n\n"
    )
    if previous_chapters:
        user += "前文章节摘要（保持连贯性）:\n"
        for prev in previous_chapters:
            ch_num = prev.get("chapter_number", "?")
            ch_summary = prev.get("summary", prev.get("content", "")[:200])
            user += f"- 第 {ch_num} 章: {ch_summary}\n"
        user += "\n"
    if style_profile:
        user += "风格约束:\n"
        if style_profile.get("voice"):
            user += f"- 叙事声音: {style_profile['voice']}\n"
        strengths = style_profile.get("strengths", [])
        if strengths:
            user += f"- 保持优势: {'、'.join(str(item) for item in strengths)}\n"
        sensory_keywords = style_profile.get("sensory_keywords", [])
        if sensory_keywords:
            user += f"- 感官关键词: {'、'.join(str(item) for item in sensory_keywords[:6])}\n"
        avoid = style_profile.get("avoid", [])
        if avoid:
            user += f"- 避免: {'、'.join(str(item) for item in avoid)}\n"
        user += "\n"
    if rules:
        user += "写作规则（必须遵守）:\n"
        for rule in rules:
            user += f"- {rule['name']}: {rule['description']}\n"
        user += "\n"
    user += (
        f"第 {chapter_number} 章: {chapter_title}\n"
        f"本章核心 beat: {chapter_summary}\n\n"
        f"请写一章完整的小说正文，目标约 {max_words} 字。\n"
        "要求:\n"
        "- 与前文保持角色、事件、时间线的连贯性\n"
        "- 直接进入场景，不要概述前情\n"
        "- 包含主角的具体行动、对话和内心活动\n"
        "- 场景之间有明确的因果链\n"
        "- 章末留下明确的悬念或转折\n"
        "- 不要返回 JSON，直接输出小说正文\n"
        "- 中文写作，语言流畅自然"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

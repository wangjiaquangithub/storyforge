from __future__ import annotations


def build_rewrite_messages(
    chapter_content: str,
    review_issues: list[str],
    brief_summary: str,
    chapter_summary: str,
    style_profile: dict | None = None,
) -> list[dict[str, str]]:
    system = (
        "你是专业网络小说作者。你的章节已被编辑审核，需要根据反馈进行修改。"
        "保持原有故事线和风格，逐条解决编辑提出的问题。"
        "优先修复开篇钩子弱、冲突目标不清、赌注不足、摘要化叙述、缺少反转、章末钩子弱等审美问题。"
        "输出修改后的完整中文章节正文，不要解释修改内容。"
        "除非用户明确要求外语，否则所有正文和叙述都必须使用中文。"
    )
    issues_text = "\n".join(f"- {issue}" for issue in review_issues)
    user = (
        f"项目摘要:\n{brief_summary}\n\n"
        f"本章节核心 beat:\n{chapter_summary}\n\n"
    )
    if style_profile:
        user += "风格约束:\n"
        if style_profile.get("voice"):
            user += f"- 叙事声音: {style_profile['voice']}\n"
        strengths = style_profile.get("strengths", [])
        if strengths:
            user += f"- 保持优势: {'、'.join(str(item) for item in strengths)}\n"
        avoid = style_profile.get("avoid", [])
        if avoid:
            user += f"- 避免: {'、'.join(str(item) for item in avoid)}\n"
        user += "\n"
    user += (
        f"当前章节全文:\n{chapter_content}\n\n"
        f"编辑反馈:\n{issues_text}\n\n"
        "请返回修改后的完整章节正文。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

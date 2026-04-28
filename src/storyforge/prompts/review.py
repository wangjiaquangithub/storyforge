from __future__ import annotations


def build_review_messages(
    chapter_content: str,
    brief_summary: str,
    outline_summary: str,
    review_policy: str = "default",
    quality_experiment: str | None = None,
    quality_variant: str | None = None,
) -> list[dict]:
    review_style = "严格审查" if review_policy == "strict" else "审查"
    system = (
        "你是资深小说主编，负责最终审稿。"
        f"你需要从以下维度{review_style}章节内容:\n"
        "- continuity（连贯性）: 是否与 brief 的前提和设定一致，人物名称/性格是否前后一致\n"
        "- motivation（动机）: 主角的行为动机是否清晰可信，是否有无理由的行为\n"
        "- escalation（升级）: 本章是否有明确的剧情推进和压力升级，不是原地踏步\n"
        "- quality（质量）: 文字是否流畅，是否有明显逻辑漏洞、重复句式或冗长段落\n"
        "- pacing（节奏）: 是否对话和场景分布合理，不过于平淡或过于密集\n\n"
        "如果所有章节通过则 approved=true，否则列出具体问题（具体到可操作）。"
    )
    user = (
        f"故事背景:\n{brief_summary}\n\n"
        f"大纲要求:\n{outline_summary}\n\n"
        f"审稿策略: {review_policy}\n"
        f"实验标识: {quality_experiment or 'none'}\n"
        f"实验变体: {quality_variant or 'none'}\n\n"
        f"章节正文:\n{chapter_content}\n\n"
        "请返回纯 JSON，不要额外解释。格式如下:\n"
        '{\n'
        '  "approved": true,\n'
        '  "checks": ["continuity", "motivation", "escalation", "quality", "pacing"],\n'
        '  "issues": ["如果有问题，在这里列出具体问题；没有问题则留空数组"]\n'
        '}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

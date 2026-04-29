from __future__ import annotations

from storyforge.domain.models import Project


def build_brief_messages(project: Project) -> list[dict]:
    system = (
        "你是资深小说策划编辑，擅长从创意种子中提炼结构化项目 brief。"
        "brief 应包含：引人入胜的标题、精准的类型定位、主角的核心动机和主要冲突。"
        "同时必须提炼开篇钩子、类型卖点、主角欲望、外部压迫和不可逆赌注，回答读者为什么要继续看。"
        "目标是为后续大纲和章节写作提供有网文吸引力的创作指南。"
        "所有输出字段的内容必须使用中文，不要使用英文标签或英文正文，除非用户创意本身包含不可翻译的专有名词。"
    )
    user_parts = [f"创意种子: {project.idea}"]
    if project.genre:
        user_parts.append(f"类型: {project.genre}")
    if project.audience:
        user_parts.append(f"目标读者: {project.audience}")
    if project.target_length:
        user_parts.append(f"目标章节数: {project.target_length}")
    user_parts.append(
        "请返回纯 JSON，不要额外解释。格式如下:\n"
        '{"title": "故事标题（有吸引力，符合类型风格）", '
        '"genre": "精准类型定位（如：都市异能/升级流/群像）", '
        '"summary": "200字以内的故事摘要，必须包含：开篇钩子、主角姓名、核心欲望、类型卖点、主要冲突、不可逆赌注和连载看点"}'
    )
    user = "\n".join(user_parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

from __future__ import annotations

from storyforge.domain.models import Project


def build_brief_messages(project: Project) -> list[dict]:
    system = (
        "你是资深小说策划编辑，擅长从创意种子中提炼结构化项目 brief。"
        "brief 应包含：引人入胜的标题、精准的类型定位、主角的核心动机和主要冲突。"
        "目标是为后续大纲和章节写作提供清晰的创作指南。"
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
        '"summary": "200字以内的故事摘要，必须包含：主角姓名、核心动机、主要冲突、不可逆转的赌注"}'
    )
    user = "\n".join(user_parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

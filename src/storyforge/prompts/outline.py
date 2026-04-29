from __future__ import annotations

from storyforge.domain.models import Asset


def build_outline_messages(project_title: str, brief_content: str, story_bible: str = "") -> list[dict]:
    system = (
        "你是资深故事结构设计师，擅长为网络小说设计紧凑的弧结构大纲。"
        "你需要根据项目 brief 设计 3 章的结构化大纲（默认归入第一卷）。"
        "每章要求：\n"
        "- 明确的章节标题（有吸引力，符合类型风格，最好自带悬念或爽点承诺）\n"
        "- 核心情节 beat（2-3句话，包含本章诱因、冲突升级、反转/代价和章末钩子）\n"
        "- 章节之间必须有清晰的因果推进关系，不能只是平铺直叙地继续发生\n"
        "- 第一章建立开场钩子和正面冲突，第二章压力升级并出现反转，第三章兑现阶段爽点/代价并抛出更大悬念\n"
        "请返回包含 arcs 数组和扁平 chapters 数组的 JSON 结构。"
        "所有 title、summary 等可见内容必须使用中文，不要输出英文标题或英文摘要。"
    )
    user = (
        f"项目: {project_title}\n\n"
        f"故事摘要:\n{brief_content}\n\n"
    )
    if story_bible:
        user += f"冻结生产资产（必须遵守并在章节因果中体现）:\n{story_bible}\n\n"
    user += (
        "请返回纯 JSON，不要额外解释。格式如下:\n"
        '{\n'
        '  "arcs": [\n'
        '    {\n'
        '      "arc_number": 1,\n'
        '      "title": "弧一标题",\n'
        '      "chapters": [\n'
        '        {"chapter_number": 1, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"},\n'
        '        {"chapter_number": 2, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"},\n'
        '        {"chapter_number": 3, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"}\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "chapters": [\n'
        '    {"chapter_number": 1, "arc_number": 1, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"},\n'
        '    {"chapter_number": 2, "arc_number": 1, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"},\n'
        '    {"chapter_number": 3, "arc_number": 1, "title": "章节标题", "summary": "核心情节 beat（2-3句话）"}\n'
        '  ]\n'
        '}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_outline_expansion_messages(
    project_title: str,
    existing_outline_content: str,
    from_chapter: int,
    chapters_to_add: int = 3,
) -> list[dict]:
    system = (
        "你是专业故事结构设计师。现有大纲已涵盖前面的章节，现在需要在末尾追加新章节。"
        "新章节应与现有大纲保持风格一致，并推动故事继续发展。"
        "每章应包含明确的章节标题和核心情节 beat（2-3句话）。"
        "新增章节不能只是继续推进故事，必须写出新目标、阻力、代价、反转或章末钩子。"
        "每章需包含 arc_number 字段，默认归入第一卷。"
        "所有 title、summary 等可见内容必须使用中文，不要输出英文标题或英文摘要。"
    )
    user = (
        f"项目: {project_title}\n\n"
        f"现有大纲:\n{existing_outline_content}\n\n"
        f"请从第 {from_chapter} 章开始，追加 {chapters_to_add} 章新内容。\n"
        "返回纯 JSON，只包含新增的章节，不要重复已有章节。格式如下:\n"
        '{\n'
        '  "chapters": [\n'
        f'    {{"chapter_number": {from_chapter}, "arc_number": 1, "title": "章节标题", "summary": "核心情节 beat"}},\n'
        f'    {{"chapter_number": {from_chapter + 1}, "arc_number": 1, "title": "章节标题", "summary": "核心情节 beat"}}\n'
        '  ]\n'
        '}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

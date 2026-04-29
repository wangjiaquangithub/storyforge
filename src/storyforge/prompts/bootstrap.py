from __future__ import annotations

from storyforge.domain.models import Project


def build_asset_bootstrap_messages(project: Project, brief_content: str) -> list[dict]:
    system = (
        "你是职业网文工作室的故事圣经架构师，负责把项目 brief 拆成可执行的长篇生产资产。"
        "请输出严格 JSON，包含 world、characters、rules、timeline、style_profile、foreshadowing 六个对象。"
        "每个对象都必须能直接指导后续大纲和章节写作，不要写空泛说明。"
        "所有可见内容必须使用中文。"
    )
    user = (
        f"项目创意:\n{project.idea}\n\n"
        f"项目 brief:\n{brief_content}\n\n"
        "请返回纯 JSON，不要额外解释。格式如下:\n"
        "{\n"
        "  \"world\": {\"summary\": \"世界设定\", \"forces\": [\"势力\"], \"constraints\": [\"空间/时代/资源约束\"]},\n"
        "  \"characters\": {\"lead\": {\"name\": \"主角\", \"desire\": \"核心欲望\", \"weakness\": \"弱点\"}, \"antagonists\": [\"对手\"], \"relationships\": [\"关系张力\"]},\n"
        "  \"rules\": {\"genre_rules\": [\"题材规则\"], \"power_limits\": [\"能力边界\"], \"avoid\": [\"禁用套路\"]},\n"
        "  \"timeline\": {\"premise\": \"开篇前提\", \"beats\": [\"关键节点\"], \"current_state\": \"当前时间线状态\"},\n"
        "  \"style_profile\": {\"voice\": \"叙事人称\", \"rhythm\": \"节奏\", \"sensory_keywords\": [\"感官词\"], \"avoid\": [\"禁忌写法\"]},\n"
        "  \"foreshadowing\": {\"items\": [{\"title\": \"伏笔\", \"promise\": \"读者承诺\", \"expected_resolution\": \"回收预期\", \"status\": \"planted\"}]}\n"
        "}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

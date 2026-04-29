"""Tests for prompt template functions."""

from storyforge.domain.models import Project
from storyforge.prompts import (
    build_brief_messages,
    build_outline_messages,
    build_outline_expansion_messages,
    build_chapter_messages,
    build_review_messages,
    build_rewrite_messages,
)


def test_build_brief_messages():
    project = Project(
        idea="一个人发现自己活在别人的梦里",
        genre="超现实悬疑",
        audience="中文网文读者",
    )
    messages = build_brief_messages(project)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "一个人发现自己活在别人的梦里" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "开篇钩子" in prompt_text
    assert "类型卖点" in prompt_text
    assert "主角欲望" in prompt_text
    assert "不可逆赌注" in prompt_text
    assert "连载看点" in prompt_text


def test_build_outline_messages():
    messages = build_outline_messages("梦行者", "这里是项目概要")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "梦行者" in messages[1]["content"]
    assert "这里是项目概要" in messages[1]["content"]
    assert "arcs" in messages[1]["content"]
    assert "arc_number" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "章节标题（有吸引力" in prompt_text
    assert "冲突升级" in prompt_text
    assert "反转/代价" in prompt_text
    assert "章末钩子" in prompt_text
    assert "更大悬念" in prompt_text


def test_build_outline_expansion_messages():
    messages = build_outline_expansion_messages(
        project_title="梦行者",
        existing_outline_content="第 1 章：开端\n第 2 章：升级\n第 3 章：突破",
        from_chapter=4,
        chapters_to_add=3,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "从第 4 章开始" in messages[1]["content"]
    assert "追加 3 章" in messages[1]["content"]
    assert "第 1 章" in messages[1]["content"]
    assert "arc_number" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "新目标" in prompt_text
    assert "阻力" in prompt_text
    assert "代价" in prompt_text
    assert "反转" in prompt_text
    assert "章末钩子" in prompt_text


def test_build_chapter_messages():
    messages = build_chapter_messages(
        project_title="梦行者",
        brief_summary="一部关于梦境的超现实悬疑故事",
        chapter_number=1,
        chapter_title="醒来之刻",
        chapter_summary="主角进入梦境",
        max_words=4096,
    )

    assert len(messages) == 2
    assert "梦行者" in messages[1]["content"]
    assert "醒来之刻" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "开篇要有钩子" in prompt_text
    assert "阻力和代价" in prompt_text
    assert "中段出现反转" in prompt_text
    assert "章末留下明确的悬念" in prompt_text
    assert "不要写成大纲扩写" in prompt_text
    assert "不要概述性叙述" in prompt_text


def test_build_chapter_messages_with_previous_context():
    messages = build_chapter_messages(
        project_title="梦行者",
        brief_summary="一部关于梦境的超现实悬疑故事",
        chapter_number=4,
        chapter_title="梦境加深",
        chapter_summary="谜团变得更加危险",
        max_words=4096,
        previous_chapters=[
            {"chapter_number": "1", "title": "醒来之刻", "summary": "主角进入梦境", "content_preview": "前 300 字..."},
            {"chapter_number": "2", "title": "梦境崩塌", "summary": "梦境开始崩塌", "content_preview": "前 300 字..."},
            {"chapter_number": "3", "title": "重建规则", "summary": "梦境规则重建", "content_preview": "前 300 字..."},
        ],
    )

    assert len(messages) == 2
    assert "前文章节摘要" in messages[1]["content"]
    assert "第 1 章" in messages[1]["content"]
    assert "第 3 章" in messages[1]["content"]
    assert "连贯性" in messages[1]["content"]


def test_build_chapter_messages_with_style_profile():
    messages = build_chapter_messages(
        project_title="梦行者",
        brief_summary="一部关于梦境的超现实悬疑故事",
        chapter_number=4,
        chapter_title="梦境加深",
        chapter_summary="谜团变得更加危险",
        max_words=4096,
        style_profile={
            "voice": "贴近角色的第三人称",
            "strengths": ["紧张感强", "感官细节突出"],
            "avoid": ["平铺直叙"],
        },
    )

    assert "风格约束" in messages[1]["content"]
    assert "贴近角色的第三人称" in messages[1]["content"]
    assert "紧张感强" in messages[1]["content"]
    assert "平铺直叙" in messages[1]["content"]



def test_build_review_messages():
    messages = build_review_messages(
        chapter_content="这里是完整章节正文",
        brief_summary="项目概要",
        outline_summary="大纲节拍",
        review_policy="strict",
        quality_experiment="review-ab-v1",
        quality_variant="strict",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "严格审查" in messages[0]["content"]
    assert "这里是完整章节正文" in messages[1]["content"]
    assert "review-ab-v1" in messages[1]["content"]
    assert "strict" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "hook（钩子）" in prompt_text
    assert "stakes（赌注）" in prompt_text
    assert "reversal（反转）" in prompt_text
    assert "scene_prose（场景化正文）" in prompt_text
    assert "payoff_or_cliffhanger" in prompt_text
    assert "不要写“审美不好”" in prompt_text


def test_build_rewrite_messages():
    messages = build_rewrite_messages(
        chapter_content="原始章节正文",
        review_issues=["节奏太慢", "角色动机不清晰"],
        brief_summary="项目概要",
        chapter_summary="章节节拍",
        style_profile={"voice": "贴近角色的第三人称", "strengths": ["紧张感强"], "avoid": ["平铺直叙"]},
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "原始章节正文" in messages[1]["content"]
    assert "节奏太慢" in messages[1]["content"]
    assert "角色动机不清晰" in messages[1]["content"]
    assert "风格约束" in messages[1]["content"]
    assert "贴近角色的第三人称" in messages[1]["content"]
    assert "平铺直叙" in messages[1]["content"]
    prompt_text = messages[0]["content"] + messages[1]["content"]
    assert "开篇钩子弱" in prompt_text
    assert "冲突目标不清" in prompt_text
    assert "赌注不足" in prompt_text
    assert "摘要化叙述" in prompt_text
    assert "缺少反转" in prompt_text
    assert "章末钩子弱" in prompt_text

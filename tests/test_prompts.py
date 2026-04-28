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
        idea="A person discovers they live in someone else's dream",
        genre="surreal thriller",
        audience="web novel readers",
    )
    messages = build_brief_messages(project)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "A person discovers they live in someone else's dream" in messages[1]["content"]


def test_build_outline_messages():
    messages = build_outline_messages("Dream Walker", "Brief content here")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Dream Walker" in messages[1]["content"]
    assert "Brief content here" in messages[1]["content"]
    assert "arcs" in messages[1]["content"]
    assert "arc_number" in messages[1]["content"]


def test_build_outline_expansion_messages():
    messages = build_outline_expansion_messages(
        project_title="Dream Walker",
        existing_outline_content="Chapter 1: The Beginning\nChapter 2: The Middle\nChapter 3: The End",
        from_chapter=4,
        chapters_to_add=3,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "从第 4 章开始" in messages[1]["content"]
    assert "追加 3 章" in messages[1]["content"]
    assert "Chapter 1" in messages[1]["content"]
    assert "arc_number" in messages[1]["content"]


def test_build_chapter_messages():
    messages = build_chapter_messages(
        project_title="Dream Walker",
        brief_summary="A surreal thriller about dreams",
        chapter_number=1,
        chapter_title="The Awakening",
        chapter_summary="The protagonist enters a dream",
        max_words=4096,
    )

    assert len(messages) == 2
    assert "Dream Walker" in messages[1]["content"]
    assert "The Awakening" in messages[1]["content"]


def test_build_chapter_messages_with_previous_context():
    messages = build_chapter_messages(
        project_title="Dream Walker",
        brief_summary="A surreal thriller about dreams",
        chapter_number=4,
        chapter_title="The Deepening",
        chapter_summary="The mystery grows darker",
        max_words=4096,
        previous_chapters=[
            {"chapter_number": "1", "title": "The Awakening", "summary": "Protagonist enters dream", "content_preview": "First 300 chars..."},
            {"chapter_number": "2", "title": "The Fall", "summary": "Dream collapses", "content_preview": "First 300 chars..."},
            {"chapter_number": "3", "title": "The Rebuild", "summary": "New dream rules", "content_preview": "First 300 chars..."},
        ],
    )

    assert len(messages) == 2
    assert "前文章节摘要" in messages[1]["content"]
    assert "第 1 章" in messages[1]["content"]
    assert "第 3 章" in messages[1]["content"]
    assert "连贯性" in messages[1]["content"]


def test_build_chapter_messages_with_style_profile():
    messages = build_chapter_messages(
        project_title="Dream Walker",
        brief_summary="A surreal thriller about dreams",
        chapter_number=4,
        chapter_title="The Deepening",
        chapter_summary="The mystery grows darker",
        max_words=4096,
        style_profile={
            "voice": "closer third person",
            "strengths": ["tight tension", "sensory detail"],
            "avoid": ["flat exposition"],
        },
    )

    assert "风格约束" in messages[1]["content"]
    assert "closer third person" in messages[1]["content"]
    assert "tight tension" in messages[1]["content"]
    assert "flat exposition" in messages[1]["content"]



def test_build_review_messages():
    messages = build_review_messages(
        chapter_content="Full chapter text here",
        brief_summary="Brief summary",
        outline_summary="Outline beat",
        review_policy="strict",
        quality_experiment="review-ab-v1",
        quality_variant="strict",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "严格审查" in messages[0]["content"]
    assert "Full chapter text here" in messages[1]["content"]
    assert "review-ab-v1" in messages[1]["content"]
    assert "strict" in messages[1]["content"]


def test_build_rewrite_messages():
    messages = build_rewrite_messages(
        chapter_content="Original chapter text",
        review_issues=["Pacing is too slow", "Character motivation unclear"],
        brief_summary="Brief summary",
        chapter_summary="Chapter beat",
        style_profile={"voice": "closer third person", "strengths": ["tight tension"], "avoid": ["flat exposition"]},
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Original chapter text" in messages[1]["content"]
    assert "Pacing is too slow" in messages[1]["content"]
    assert "Character motivation unclear" in messages[1]["content"]
    assert "风格约束" in messages[1]["content"]
    assert "closer third person" in messages[1]["content"]
    assert "flat exposition" in messages[1]["content"]

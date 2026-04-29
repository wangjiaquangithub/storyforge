"""Tests for ContentQualityEngine and review integration."""

import pytest

from storyforge.config import LlmConfig
from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project
from storyforge.execution.generators import StoryForgeGenerators
from storyforge.execution.quality import ContentQualityEngine
from storyforge.execution.rules import Rule, RuleLayer, seed_universal_rules, seed_genre_rules
from storyforge.execution.store import InMemoryStoryForgeStore


def _repeat_sentence(sentence: str, times: int) -> str:
    return " ".join([sentence] * times)


def test_skip_llm_generators_default_to_chinese_assets() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(
        Project(
            idea="废土修理师在机械遗迹中重建失落城邦",
            target_length=6,
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    brief = store.save_asset(generators.generate_brief(project))
    outline = store.save_asset(generators.generate_outline(project, brief))
    chapter = store.save_asset(generators.generate_chapter(project, outline, brief, 1))
    review = generators.generate_review(project, chapter, 1)

    assert "标题：" in brief.content
    assert "类型：玄幻" in brief.content
    assert "目标读者：中文网文读者" in brief.content
    assert "故事框架：" in brief.content
    assert "Title:" not in brief.content
    assert "Summary:" not in brief.content
    assert "项目：" in outline.content
    assert "第 1 卷大纲" in outline.content
    assert "第 1 章：" in outline.content
    assert "Chapter" not in outline.content
    assert chapter.content.startswith("第 1 章：")
    assert "“退后。”" in chapter.content
    assert "否则" in chapter.content
    assert "代价" in chapter.content
    assert "沈砚" in chapter.content
    assert "陆青鸢" in chapter.content
    assert "下一刻" in chapter.content
    assert "身后" in chapter.content
    assert "本章核心并不是等待命运降临" not in chapter.content
    assert brief.structured_data["summary"] not in chapter.content
    assert "主角" not in chapter.content
    assert "对手" not in chapter.content
    assert "Core beat" not in chapter.content
    assert review.content.startswith("第 1 章审稿结果：")
    assert "下一步：" in review.content
    assert "Next step" not in review.content


def test_repetition_detects_exact_repeats():
    engine = ContentQualityEngine()
    repeated = _repeat_sentence("The protagonist walked through the dark corridor.", 3)
    content = f"{repeated} Then something moved."
    checks = engine.analyze(content)
    repetition = next(c for c in checks if c.name == "repetition")
    assert not repetition.passed
    assert any("句子重复" in issue for issue in repetition.issues)


def test_repetition_passes_on_diverse_text():
    engine = ContentQualityEngine()
    content = (
        "Alice opened the door and stepped inside. "
        "The room was warm, lit by a single lamp on the desk. "
        "She noticed a letter tucked under the glass. "
        "It bore no stamp, no return address, just her name in unfamiliar handwriting. "
        "She unfolded it carefully and began to read."
    )
    checks = engine.analyze(content)
    repetition = next(c for c in checks if c.name == "repetition")
    assert repetition.passed


def test_structure_detects_too_short():
    engine = ContentQualityEngine()
    checks = engine.analyze("Too short.")
    structure = next(c for c in checks if c.name == "structure")
    assert not structure.passed
    assert any("章节篇幅过短" in issue for issue in structure.issues)


def test_structure_detects_single_long_paragraph():
    engine = ContentQualityEngine()
    long_text = "word " * 300
    checks = engine.analyze(long_text)
    structure = next(c for c in checks if c.name == "structure")
    assert not structure.passed


def test_structure_passes_well_formed_chapter():
    engine = ContentQualityEngine()
    content = "\n\n".join(
        [f"This is paragraph {i} with some unique content about {chr(ord('a') + i % 26)}." * 10 for i in range(8)]
    )
    checks = engine.analyze(content)
    structure = next(c for c in checks if c.name == "structure")
    assert structure.passed


def test_pacing_detects_no_dialogue():
    engine = ContentQualityEngine()
    content = " ".join(["The protagonist walked forward through the dark corridor, feeling the cold wind against their face as they moved ever deeper into the unknown territory ahead."] * 20)
    checks = engine.analyze(content)
    pacing = next(c for c in checks if c.name == "pacing")
    assert not pacing.passed


def test_pacing_passes_with_dialogue():
    engine = ContentQualityEngine()
    content = (
        '"What are you doing here?" she asked.\n'
        '"Waiting," he replied.\n'
        '"For what?"\n'
        '"For you to arrive."\n'
    )
    checks = engine.analyze(content)
    pacing = next(c for c in checks if c.name == "pacing")
    assert pacing.passed


def test_cross_chapter_continuity_passes():
    engine = ContentQualityEngine()
    previous = ["Alice and Bob walked through the garden. Charlie waited by the gate."]
    current = "Alice met Charlie near the fountain. Bob joined them shortly."
    checks = engine.analyze(current, previous_chapters=previous)
    continuity = next(c for c in checks if c.name == "cross_chapter_continuity")
    assert continuity.passed


def test_cross_chapter_continuity_detects_dropped_names():
    engine = ContentQualityEngine()
    previous = [
        "Elizabeth and Jonathan argued about the inheritance. Margaret stood silently. "
        "Thomas left the room. Rebecca followed him."
    ]
    current = "The weather was nice that day."
    checks = engine.analyze(current, previous_chapters=previous)
    continuity = next(c for c in checks if c.name == "cross_chapter_continuity")
    assert not continuity.passed
    assert any("多数角色名未出现" in issue for issue in continuity.issues)


def test_analyze_returns_all_checks():
    engine = ContentQualityEngine()
    content = "Alice walked. " * 30
    checks = engine.analyze(content)
    names = [c.name for c in checks]
    assert "repetition" in names
    assert "structure" in names
    assert "pacing" in names
    assert "character_continuity" in names
    assert "foreshadowing" in names
    assert "ai_trace" in names
    assert "outline_deviation" in names
    assert "emotional_arc" in names
    assert "web_novel_aesthetics" in names
    assert "production_artifacts" in names


def test_analyze_includes_cross_chapter_when_previous_provided():
    engine = ContentQualityEngine()
    checks = engine.analyze("Some text.", previous_chapters=["Previous chapter content."])
    names = [c.name for c in checks]
    assert "cross_chapter_continuity" in names


def test_web_novel_aesthetics_detects_summary_like_chinese_text() -> None:
    engine = ContentQualityEngine()
    content = (
        "主角来到城市，了解了这里的背景和规则。故事继续推进，他开始思考未来应该如何行动。"
        "这一章主要介绍世界观，也说明了人物关系和后续发展方向。"
    )

    checks = engine.analyze(content)
    aesthetic = next(c for c in checks if c.name == "web_novel_aesthetics")

    assert not aesthetic.passed
    assert "缺少明确赌注：读者看不出失败会失去什么" in aesthetic.issues
    assert "场景化不足：需要用动作、对话和感官细节推进正文" in aesthetic.issues
    assert "缺少章末钩子：结尾没有新危机、反转或未解问题" in aesthetic.issues
    assert aesthetic.details["has_stakes"] is False
    assert aesthetic.details["has_scene_prose"] is False


def test_web_novel_aesthetics_passes_scene_driven_chinese_text() -> None:
    engine = ContentQualityEngine()
    content = (
        "门外的警报刚响，冷雨就打进破窗，金属地面泛起寒意。沈砚攥紧筹码，知道一旦交出去，最后的机会就会失去。\n\n"
        "“退后。”陆青鸢把灯光压到他脸上，声音里带着威胁，“否则，你守着的人会先死。”\n\n"
        "沈砚却没有退，脚步声逼近时反而把伤口按在墙上的旧锁孔。下一刻，屏幕上出现真正的债主名字，新的门在他身后打开。"
    )

    checks = engine.analyze(content)
    aesthetic = next(c for c in checks if c.name == "web_novel_aesthetics")

    assert aesthetic.passed
    assert aesthetic.issues == []
    assert aesthetic.details["has_hook"] is True
    assert aesthetic.details["has_conflict"] is True
    assert aesthetic.details["has_stakes"] is True
    assert aesthetic.details["has_reversal"] is True
    assert aesthetic.details["has_scene_prose"] is True
    assert aesthetic.details["has_ending_hook"] is True


def test_production_artifacts_blocks_placeholder_names_and_internal_markers() -> None:
    engine = ContentQualityEngine()
    content = (
        "第 1 章：危机开场\n\n"
        "门外的警报刚响第一声，冷雨就顺着裂开的窗缝打进来。主角攥紧最后一枚筹码，知道一旦交出去就会失去所有。\n\n"
        "本章核心并不是等待命运降临，而是把选择推到眼前。\n\n"
        "【修订强化】对手站在灯下，要求他交出筹码。\n"
        "【对应指令】针对问题修订：情感曲线过于平坦"
    )

    checks = engine.analyze(content)
    artifacts = next(c for c in checks if c.name == "production_artifacts")

    assert artifacts.passed is False
    assert any("内部写作/修订标记" in issue for issue in artifacts.issues)
    assert any("占位角色称呼" in issue for issue in artifacts.issues)


def test_production_artifacts_allows_natural_role_words_in_scene_prose() -> None:
    engine = ContentQualityEngine()
    content = (
        "门外的警报刚响，冷雨就打进破窗。沈砚看见反派派来的枪手堵住巷口，知道一旦退让就会失去最后机会。\n\n"
        "“别动。”陆青鸢把灯光压低，“你的对手不是我，是站在屏幕后面的债主。”\n\n"
        "沈砚没有退。下一刻，真正的债主名字在屏幕上亮起，新的影子站在他身后。"
    )

    checks = engine.analyze(content)
    artifacts = next(c for c in checks if c.name == "production_artifacts")

    assert artifacts.passed is True
    assert artifacts.issues == []


def test_reaudit_fails_when_revision_does_not_reduce_existing_issues() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="废土追逃", brief="记忆代价"))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))
    revised = Asset(
        project_id=project.project_id,
        asset_type=AssetType.chapter,
        content=(
            "门外的警报刚响，冷雨打进破窗，金属地面泛起寒意。沈砚攥紧筹码，知道一旦交出去，妹妹留下的录音就会被抹掉。\n\n"
            "“退后。”陆青鸢把灯光压到他脸上，声音里带着威胁，“否则，你守着的维修棚会被封死。”\n\n"
            "沈砚没有退，脚步声逼近时把伤口按在旧锁孔。下一刻，真正的城邦印记亮起，第三个人的影子站在他身后。"
        ),
        structured_data={"chapter_number": 1},
    )
    previous_validation = Asset(
        project_id=project.project_id,
        asset_type=AssetType.validation_report,
        structured_data={
            "issues": ["情感曲线过于平坦，缺乏起伏"],
            "blocking_issues": [],
            "failed_checks": ["emotional_arc"],
            "checks": {"emotional_arc": {"passed": False, "issues": ["情感曲线过于平坦，缺乏起伏"], "details": {}}},
        },
    )
    previous_audit = Asset(project_id=project.project_id, asset_type=AssetType.audit_report, structured_data={"kind": "audit"})
    revision_delta = Asset(project_id=project.project_id, asset_type=AssetType.revision_delta)

    reaudit = generators.generate_reaudit_report(project, revised, revision_delta, previous_validation, previous_audit, 1, [])

    assert reaudit.structured_data["passed"] is False
    assert reaudit.structured_data["improved"] is False
    assert reaudit.structured_data["remaining_issues"] == ["情感曲线过于平坦，缺乏起伏"]


def test_final_chapter_rejects_internal_revision_markers() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="废土追逃", brief="记忆代价"))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))
    revised = Asset(
        project_id=project.project_id,
        asset_type=AssetType.chapter,
        content="第 1 章\n\n【修订强化】沈砚补强冲突。\n【对应指令】针对问题修订。",
        structured_data={"chapter_number": 1},
    )
    reaudit = Asset(project_id=project.project_id, asset_type=AssetType.audit_report, structured_data={"kind": "re_audit", "passed": True, "chapter_number": 1})

    with pytest.raises(ValueError, match="内部写作"):
        generators.generate_final_chapter(project, revised, reaudit, 1)


def test_final_chapter_requires_passed_reaudit_report() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="废土追逃", brief="记忆代价"))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))
    revised = Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="第 1 章\n\n沈砚在雨里完成选择。", structured_data={"chapter_number": 1})
    failed_reaudit = Asset(project_id=project.project_id, asset_type=AssetType.audit_report, structured_data={"kind": "re_audit", "passed": False, "chapter_number": 1})

    with pytest.raises(ValueError, match="Re-audit did not pass"):
        generators.generate_final_chapter(project, revised, failed_reaudit, 1)


def test_generator_load_asset_by_id_uses_store_lookup():
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, content="brief"))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    loaded = generators._load_asset_by_id(asset.asset_id)

    assert loaded is not None
    assert loaded.asset_id == asset.asset_id



def test_style_profile_prefers_human_revisions() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="He moved fast. The corridor narrowed. The scene ended on a hard beat.",
            structured_data={"chapter_number": 1, "title": "Ch1", "summary": "beat"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="human",
            content="He lingered in the corridor, noticing the rust, the breath in his chest, and the dread under every sound.",
            structured_data={"chapter_number": 1, "title": "Ch1", "summary": "beat"},
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id)

    assert profile["preferred_source"] == "human"
    assert profile["sample_count"] == 1
    assert "锈迹" in " ".join(profile["sensory_keywords"])



def test_style_profile_uses_only_human_samples_when_available() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Metal smoke shadow metal smoke shadow.",
            structured_data={"chapter_number": 1, "title": "Ch1", "summary": "beat"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="human",
            content="He noticed the rust on the rail and the breath trembling in his chest.",
            structured_data={"chapter_number": 1, "title": "Ch1", "summary": "beat"},
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id)

    assert "锈迹" in profile["sensory_keywords"]
    assert "金属" not in profile["sensory_keywords"]



def test_style_profile_uses_latest_three_chapters_in_chapter_order() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    for chapter_number, content in [
        (3, "He smelled rust in the rain."),
        (1, "The room was cold."),
        (4, "His breath stalled in the dark."),
        (2, "Smoke curled above the lamp."),
    ]:
        store.save_asset(
            Asset(
                project_id=project.project_id,
                asset_type=AssetType.chapter,
                source="human",
                content=content,
                structured_data={"chapter_number": chapter_number, "title": f"Ch{chapter_number}", "summary": "beat"},
            )
        )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id)

    assert profile["sample_count"] == 3
    assert "寒意" not in profile["sensory_keywords"]



def test_fallback_review_approves_when_structure_passes_even_if_quality_warns():
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    brief = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.brief,
            content="brief",
            source="system",
            structured_data={"summary": "summary"},
        )
    )
    outline = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            content="outline",
            source="system",
            structured_data={
                "chapters": [{"chapter_number": 1, "title": "Ch1", "summary": "beat"}],
                "brief_ref": brief.asset_id,
            },
        )
    )
    noisy_content = " ".join([
        "The protagonist walked forward through the dark corridor, feeling the cold wind against their face as they moved ever deeper into the unknown territory ahead."
    ] * 20)
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content=noisy_content,
            structured_data={
                "chapter_number": 1,
                "title": "Ch1",
                "summary": "beat",
                "outline_ref": outline.asset_id,
                "brief_ref": brief.asset_id,
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    review = generators.generate_review(project, chapter, 1)

    assert review.structured_data["approved"] is True
    assert any(issue for issue in review.structured_data["issues"])
    assert review.structured_data["quality_details"]["pacing"]["passed"] is False
    assert "quality:pacing" not in review.structured_data["checks"]
    assert "quality:pacing" in review.structured_data["failed_checks"]



def test_fallback_review_rejects_when_structure_fails():
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Some body text that is long enough to avoid trivial emptiness. " * 5,
            structured_data={
                "chapter_number": 1,
                "title": "Ch1",
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    review = generators.generate_review(project, chapter, 1)

    assert review.structured_data["approved"] is False
    assert "章节缺少大纲引用" in review.structured_data["issues"]
    assert "章节缺少项目概要引用" in review.structured_data["issues"]



def test_review_asset_includes_experiment_metadata() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Some body text that is long enough to avoid trivial emptiness. " * 5,
            structured_data={
                "chapter_number": 1,
                "title": "Ch1",
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    review = generators.generate_review(
        project,
        chapter,
        1,
        config=ConfigSnapshot(
            review_policy="strict",
            values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"},
        ),
    )

    assert review.structured_data["review_policy"] == "strict"
    assert review.structured_data["quality_experiment"] == "review-ab-v1"
    assert review.structured_data["quality_variant"] == "strict"


def test_store_save_and_get_rules():
    store = InMemoryStoryForgeStore()
    rule = Rule(layer=RuleLayer.universal, name="test rule", description="a test rule")
    saved = store.save_rule(rule)
    assert saved.rule_id == rule.rule_id

    rules = store.get_rules()
    assert len(rules) == 1
    assert rules[0].name == "test rule"


def test_store_filter_rules_by_layer():
    store = InMemoryStoryForgeStore()
    store.save_rule(Rule(layer=RuleLayer.universal, name="u1", description="universal"))
    store.save_rule(Rule(layer=RuleLayer.genre, genre="玄幻", name="g1", description="genre"))
    store.save_rule(Rule(layer=RuleLayer.custom, project_id="p1", name="c1", description="custom"))

    universal = store.get_rules(layer="universal")
    assert len(universal) == 1
    assert universal[0].layer == RuleLayer.universal


def test_store_filter_rules_by_project():
    store = InMemoryStoryForgeStore()
    store.save_rule(Rule(layer=RuleLayer.custom, project_id="p1", name="p1-rule", description="for p1"))
    store.save_rule(Rule(layer=RuleLayer.custom, project_id="p2", name="p2-rule", description="for p2"))

    rules = store.get_rules(project_id="p1")
    assert len(rules) == 1
    assert rules[0].name == "p1-rule"


def test_store_delete_rule_disables():
    store = InMemoryStoryForgeStore()
    rule = store.save_rule(Rule(layer=RuleLayer.universal, name="to delete", description="will be deleted"))
    store.delete_rule(rule.rule_id)

    rules = store.get_rules()
    assert len(rules) == 0


def test_store_seed_universal_rules():
    store = InMemoryStoryForgeStore()
    rules = store.seed_universal_rules()
    assert len(rules) >= 3

    # Idempotent
    rules2 = store.seed_universal_rules()
    assert len(rules2) == len(rules)


def test_seed_functions_return_rules():
    universal = seed_universal_rules()
    assert all(isinstance(r, Rule) for r in universal)
    assert all(r.layer == RuleLayer.universal for r in universal)

    genre = seed_genre_rules()
    assert all(isinstance(r, Rule) for r in genre)
    assert all(r.layer == RuleLayer.genre for r in genre)
    assert {r.genre for r in genre} == {"玄幻", "言情", "仙侠", "科幻"}


def test_generator_resolve_rules():
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", genre="玄幻"))
    store.seed_universal_rules()
    store.save_rule(Rule(layer=RuleLayer.genre, genre="玄幻", name="玄幻规则", description="玄幻专属规则"))
    store.save_rule(Rule(layer=RuleLayer.genre, genre="科幻", name="科幻规则", description="不应被解析"))

    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))
    rules = generators._resolve_rules(project)

    # Universal rules + 玄幻 genre rules, but not 科幻
    names = [r["name"] for r in rules]
    assert "玄幻规则" in names
    assert "科幻规则" not in names


def test_review_asset_includes_rule_audit_results() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    rule = store.save_rule(
        Rule(
            layer=RuleLayer.custom,
            project_id=project.project_id,
            name="禁用词",
            description="不得出现 ForbiddenTerm",
        )
    )
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="This chapter includes ForbiddenTerm in the draft.",
            structured_data={
                "chapter_number": 1,
                "title": "Ch1",
                "summary": "beat",
                "brief_ref": "brief_1",
                "outline_ref": "outline_1",
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    review = generators.generate_review(project, chapter, 1)

    audits = review.structured_data["rule_audits"]
    assert audits == [
        {
            "rule_id": rule.rule_id,
            "name": "禁用词",
            "layer": "custom",
            "passed": False,
            "issues": ["规则“禁用词”禁止出现：ForbiddenTerm"],
        }
    ]
    assert "rule:禁用词" in review.structured_data["failed_checks"]
    assert review.structured_data["approved"] is False


def test_review_context_uses_chapter_branch_only() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Alice Bob Charlie Diana main-only context.",
            structured_data={"chapter_number": 1, "title": "Main 1"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            branch="main",
            structured_data={"chapters": [{"chapter_number": 2, "summary": "伏笔:main-secret。"}]},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.characters,
            branch="main",
            structured_data={"characters": [{"name": "Alice", "traits": "main-only"}]},
        )
    )
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Alt branch chapter content with enough words to produce a review. " * 8,
            structured_data={
                "chapter_number": 2,
                "title": "Alt 2",
                "summary": "alt beat",
                "brief_ref": "brief_alt",
                "outline_ref": "outline_alt",
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    review = generators.generate_review(project, chapter, 2)

    quality_details = review.structured_data["quality_details"]
    assert "cross_chapter_continuity" not in quality_details
    assert quality_details["foreshadowing"]["passed"] is True
    assert quality_details["character_continuity"]["passed"] is True


def test_style_profile_filters_by_branch() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", source="human", content="rust smoke metal main branch", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="cold rain breath alt branch", structured_data={"chapter_number": 1}))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert "寒意" in profile["sensory_keywords"]
    assert "锈迹" not in profile["sensory_keywords"]


def test_style_profile_applies_locked_override_asset_by_branch() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="cold rain breath alt branch", structured_data={"chapter_number": 1}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.rules,
            branch="alt",
            source="human",
            structured_data={
                "kind": "style_profile",
                "voice": "locked close third",
                "avoid": ["平铺直叙"],
                "sensory_keywords": ["ash", "rain"],
                "locked_fields": ["voice", "avoid", "sensory_keywords"],
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert profile["voice"] == "locked close third"
    assert profile["avoid"] == ["平铺直叙"]
    assert profile["sensory_keywords"] == ["ash", "rain"]
    assert profile["sample_count"] == 1


def test_style_profile_keeps_locked_override_when_samples_are_blank() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="   ", structured_data={"chapter_number": 1}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.rules,
            branch="alt",
            source="human",
            structured_data={
                "kind": "style_profile",
                "voice": "locked close third",
                "locked_fields": ["voice"],
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert profile["voice"] == "locked close third"
    assert profile["locked_fields"] == ["voice"]


def test_style_profile_extracts_chinese_sensory_keywords() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea"))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="human",
            content="雨声压低了呼吸，锈迹和烟气贴着墙面蔓延。下一刻，新的影子站在身后。",
            structured_data={"chapter_number": 1},
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id)

    assert "雨声" in profile["sensory_keywords"]
    assert "锈迹" in profile["sensory_keywords"]
    assert "章末钩子明确" in profile["strengths"]



def test_style_profile_ignores_deleted_chapter_samples() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="cold rain breath", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="rust smoke metal", structured_data={"chapter_number": 2}, is_deleted=True))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert "寒意" in profile["sensory_keywords"]
    assert "锈迹" not in profile["sensory_keywords"]
    assert profile["sample_count"] == 1

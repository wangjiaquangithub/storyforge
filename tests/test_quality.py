"""Tests for ContentQualityEngine and review integration."""

from storyforge.config import LlmConfig
from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project
from storyforge.execution.generators import StoryForgeGenerators
from storyforge.execution.quality import ContentQualityEngine
from storyforge.execution.rules import Rule, RuleLayer, seed_universal_rules, seed_genre_rules
from storyforge.execution.store import InMemoryStoryForgeStore


def _repeat_sentence(sentence: str, times: int) -> str:
    return " ".join([sentence] * times)


def test_repetition_detects_exact_repeats():
    engine = ContentQualityEngine()
    repeated = _repeat_sentence("The protagonist walked through the dark corridor.", 3)
    content = f"{repeated} Then something moved."
    checks = engine.analyze(content)
    repetition = next(c for c in checks if c.name == "repetition")
    assert not repetition.passed
    assert any("repeated" in issue.lower() for issue in repetition.issues)


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
    assert any("too short" in issue.lower() for issue in structure.issues)


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
    assert any("absent" in issue.lower() for issue in continuity.issues)


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


def test_analyze_includes_cross_chapter_when_previous_provided():
    engine = ContentQualityEngine()
    checks = engine.analyze("Some text.", previous_chapters=["Previous chapter content."])
    names = [c.name for c in checks]
    assert "cross_chapter_continuity" in names


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
    assert "rust" in " ".join(profile["sensory_keywords"])



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

    assert "rust" in profile["sensory_keywords"]
    assert "metal" not in profile["sensory_keywords"]



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
    assert "cold" not in profile["sensory_keywords"]



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
    assert "chapter has no outline reference" in review.structured_data["issues"]
    assert "chapter has no brief reference" in review.structured_data["issues"]



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
            "issues": ["Rule '禁用词' forbids term: ForbiddenTerm"],
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

    assert "cold" in profile["sensory_keywords"]
    assert "rust" not in profile["sensory_keywords"]


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
                "avoid": ["flat exposition"],
                "sensory_keywords": ["ash", "rain"],
                "locked_fields": ["voice", "avoid", "sensory_keywords"],
            },
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert profile["voice"] == "locked close third"
    assert profile["avoid"] == ["flat exposition"]
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


def test_style_profile_ignores_deleted_chapter_samples() -> None:
    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="idea", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="cold rain breath", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="rust smoke metal", structured_data={"chapter_number": 2}, is_deleted=True))
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    profile = generators._build_style_profile(project.project_id, branch="alt")

    assert "cold" in profile["sensory_keywords"]
    assert "rust" not in profile["sensory_keywords"]
    assert profile["sample_count"] == 1

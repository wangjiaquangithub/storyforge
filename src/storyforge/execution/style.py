"""Style profile extraction from existing chapter assets."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from storyforge.domain.models import Asset, AssetType
from storyforge.execution.store import StoryForgeStore

STYLE_PROFILE_FIELDS = {"voice", "strengths", "avoid", "sensory_keywords"}


def _latest_style_override(store: StoryForgeStore, project_id: str, *, branch: str | None = None) -> Asset | None:
    overrides = [
        asset
        for asset in store.list_assets(project_id, AssetType.rules, branch=branch)
        if not asset.is_deleted and asset.structured_data.get("kind") == "style_profile"
    ]
    if not overrides:
        return None
    return max(overrides, key=lambda asset: (asset.version, asset.updated_at, asset.created_at))


def _merge_locked_override(profile: dict[str, Any], override: Asset | None) -> dict[str, Any]:
    if override is None:
        return profile
    locked_fields = [field for field in override.structured_data.get("locked_fields", []) if field in STYLE_PROFILE_FIELDS]
    merged = dict(profile)
    for field in locked_fields:
        if field in override.structured_data:
            merged[field] = override.structured_data[field]
    merged["locked_fields"] = locked_fields
    merged["branch"] = override.branch
    merged["override_asset_id"] = override.asset_id
    return merged


def build_style_profile(store: StoryForgeStore, project_id: str, *, branch: str | None = None) -> dict[str, Any]:
    """Analyze recent chapter assets to extract a style profile.

    Returns a dict with voice, strengths, avoid list, and sensory keywords.
    Empty dict if no chapters exist for the project.
    """
    override = _latest_style_override(store, project_id, branch=branch)
    latest_by_chapter: dict[int, Any] = {}
    for asset in store.list_assets(project_id, AssetType.chapter, branch=branch):
        if asset.is_deleted:
            continue
        chapter_number = asset.structured_data.get("chapter_number", 0)
        if not isinstance(chapter_number, int) or chapter_number <= 0:
            continue
        existing = latest_by_chapter.get(chapter_number)
        if existing is None or asset.version > existing.version:
            latest_by_chapter[chapter_number] = asset

    if not latest_by_chapter:
        return _merge_locked_override({}, override)

    ordered_samples = [asset for _, asset in sorted(latest_by_chapter.items())]
    samples = ordered_samples[-3:]
    human_samples = [asset for asset in samples if asset.source == "human"]
    effective_samples = human_samples or samples
    texts = [asset.content for asset in effective_samples if asset.content.strip()]
    if not texts:
        return _merge_locked_override({}, override)

    preferred_source = "human" if human_samples else effective_samples[-1].source
    lower_text = " ".join(texts).lower()
    sensory_matches = re.findall(
        r"\b(?:rust|smoke|blood|cold|heat|breath|shadow|light|scent|metal|rain)\b",
        lower_text,
    )
    sensory_keywords = [word for word, _ in Counter(sensory_matches).most_common(6)]

    strengths: list[str] = []
    if any('"' in text or "“" in text or "”" in text for text in texts):
        strengths.append("dialogue presence")
    if sensory_keywords:
        strengths.append("sensory detail")
    if any(len(text.split()) > 80 for text in texts):
        strengths.append("expanded introspection")

    avoid: list[str] = []
    if all('"' not in text and "“" not in text and "”" not in text for text in texts):
        avoid.append("flat exposition")

    voice = (
        "closer third person"
        if any(word in lower_text for word in ["he ", "she ", "his ", "her "])
        else "immersive narrative"
    )
    return _merge_locked_override(
        {
            "preferred_source": preferred_source,
            "sample_count": len(texts),
            "voice": voice,
            "strengths": strengths,
            "avoid": avoid,
            "sensory_keywords": sensory_keywords,
        },
        override,
    )

from __future__ import annotations

import copy
from typing import Any

from storyforge.config import LlmConfig, load_llm_config
from storyforge.domain.models import (
    Asset,
    AssetType,
    ConfigSnapshot,
    Project,
    ProjectPhase,
    TaskRecord,
    utc_now,
)
from storyforge.execution.quality import ContentQualityCheck, ContentQualityEngine
from storyforge.execution.store import StoryForgeStore
from storyforge.execution.style import build_style_profile as _build_style_profile
from storyforge.llm import LlmClient, LlmSkipError, LlmUsage
from storyforge.prompts import (
    build_brief_messages,
    build_chapter_messages,
    build_outline_expansion_messages,
    build_outline_messages,
    build_review_messages,
    build_rewrite_messages,
)


class StoryForgeGenerators:
    """Content generation boundary.

    Each method receives the minimum project/asset/config inputs it needs,
    builds an Asset, and returns it.  The caller (ClosedLoopService) is
    responsible for saving the asset, updating project state, and recording events.
    """

    def __init__(self, store: StoryForgeStore, llm_config: LlmConfig | None = None) -> None:
        self.store = store
        self._llm_config = llm_config or load_llm_config()
        self._llm_client: LlmClient | None = None
        if not self._llm_config.skip_llm:
            try:
                self._llm_client = LlmClient(self._llm_config)
            except ValueError:
                # API key missing, fall back to local generators
                self._llm_client = None

    # ------------------------------------------------------------------
    # Resolve helpers
    # ------------------------------------------------------------------

    def _resolve_input_asset(
        self,
        task: TaskRecord,
        asset_type: AssetType,
        *,
        required: bool = True,
    ) -> Asset | None:
        """Resolve an input asset for *task* from explicit refs or latest fallback."""
        for ref_id in task.input_asset_refs:
            asset = self._load_asset_by_id(ref_id)
            if asset is not None and asset.asset_type == asset_type:
                return asset
        latest = self.store.get_latest_asset(task.project_id, asset_type)
        if latest is not None and not required:
            return latest
        if latest is not None:
            return latest
        if required:
            raise ValueError(f"Required input asset {asset_type.value} not found for task {task.task_id}")
        return None

    def _load_asset_by_id(self, asset_id: str) -> Asset | None:
        return self.store.get_asset_by_id(asset_id)

    # ------------------------------------------------------------------
    # Generators
    # ------------------------------------------------------------------

    def generate_brief(self, project: Project) -> Asset:
        if self._llm_client is not None:
            messages = build_brief_messages(project)
            max_tokens = max(self._llm_config.max_tokens, 2048)
            data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
            title = data.get("title", _suggest_title(project.idea))
            genre = data.get("genre", project.genre or "progression fantasy")
            summary = data.get("summary", f"{project.idea.strip()}")
        else:
            title = project.title or _suggest_title(project.idea)
            genre = project.genre or "progression fantasy"
            summary = (
                f"{project.idea.strip()} The story follows a protagonist forced to rebuild identity, alliances, "
                "and personal power while the world keeps escalating the cost of every choice."
            )
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.brief,
            source="system",
            content=(
                f"Title: {title}\n"
                f"Genre: {genre}\n"
                f"Audience: {project.audience or 'web novel readers'}\n"
                f"Summary: {summary}\n"
                "Story frame:\n"
                "- Establish the protagonist's fracture and desire.\n"
                "- Escalate external pressure and irreversible stakes.\n"
                "- Force growth through conflict, debt, and consequence."
            ),
            structured_data={
                "title": title,
                "genre": genre,
                "summary": summary,
                "target_length": project.target_length or 12,
            },
        )

    def generate_outline(self, project: Project, brief_asset: Asset) -> Asset:
        if self._llm_client is not None:
            project_title = project.title or brief_asset.structured_data.get("title", "Untitled Project")
            messages = build_outline_messages(project_title, brief_asset.content)
            max_tokens = max(self._llm_config.max_tokens, 4096)
            data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
            raw_chapters = data.get("chapters", [])
            raw_arcs = data.get("arcs", [])
            # Backwards compatibility: if LLM returned only flat chapters, normalize
            if not raw_arcs and raw_chapters:
                raw_arcs = [{
                    "arc_number": 1,
                    "title": "Arc 1",
                    "chapters": raw_chapters,
                }]
                for ch in raw_chapters:
                    ch.setdefault("arc_number", 1)
            chapters = raw_chapters
            arcs = raw_arcs
        else:
            chapters = [
                {
                    "chapter_number": 1,
                    "arc_number": 1,
                    "title": "The Fracture Opens",
                    "summary": "The protagonist is forced to confront the first irreversible consequence of the idea premise.",
                },
                {
                    "chapter_number": 2,
                    "arc_number": 1,
                    "title": "Debt Collects",
                    "summary": "Early gains create new pressure, and hidden opposition makes the cost visible.",
                },
                {
                    "chapter_number": 3,
                    "arc_number": 1,
                    "title": "A New Rule Is Paid For",
                    "summary": "A breakthrough arrives only after the protagonist sacrifices certainty and comfort.",
                },
            ]
            arcs = [{
                "arc_number": 1,
                "title": "The Fracture Opens",
                "chapters": chapters,
            }]
        project_title = project.title or brief_asset.structured_data.get("title", "Untitled Project")
        content_lines = [f"Project: {project_title}"]
        for arc in arcs:
            arc_title = arc.get("title", f"Arc {arc['arc_number']}")
            content_lines.append(f"\nArc {arc['arc_number']} Outline: {arc_title}")
            for ch in arc.get("chapters", []):
                content_lines.append(f"Chapter {ch['chapter_number']}: {ch['title']} — {ch['summary']}")
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            source="system",
            content="\n".join(content_lines),
            structured_data={
                "chapters": chapters,
                "arcs": arcs,
                "brief_ref": brief_asset.asset_id,
            },
        )

    def expand_outline(self, project: Project, existing_outline_asset: Asset, from_chapter: int, chapters_to_add: int = 3) -> Asset:
        """Append new chapters to an existing outline."""
        if self._llm_client is not None:
            project_title = project.title or existing_outline_asset.structured_data.get("title", project.title or "Untitled Project")
            messages = build_outline_expansion_messages(
                project_title,
                existing_outline_asset.content,
                from_chapter=from_chapter,
                chapters_to_add=chapters_to_add,
            )
            max_tokens = max(self._llm_config.max_tokens, 4096)
            data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
            new_chapters = data.get("chapters", [])
        else:
            new_chapters = [
                {
                    "chapter_number": from_chapter + i,
                    "arc_number": 1,
                    "title": f"Continuation {from_chapter + i}",
                    "summary": f"The story continues from chapter {from_chapter + i}.",
                }
                for i in range(chapters_to_add)
            ]

        # Merge new chapters into existing outline data
        existing_chapters = existing_outline_asset.structured_data.get("chapters", [])
        existing_arcs = existing_outline_asset.structured_data.get("arcs", [])

        # Ensure arc_number on new chapters (backward compat)
        for ch in new_chapters:
            ch.setdefault("arc_number", 1)

        merged_chapters = copy.deepcopy(existing_chapters) + copy.deepcopy(new_chapters)

        # Append new chapters to the last arc (or create Arc 1 if no arcs exist)
        merged_arcs = copy.deepcopy(existing_arcs)
        if merged_arcs:
            last_arc = merged_arcs[-1]
            if "chapters" not in last_arc:
                last_arc["chapters"] = []
            last_arc["chapters"].extend(copy.deepcopy(new_chapters))
        else:
            merged_arcs = [{
                "arc_number": 1,
                "title": "Arc 1",
                "chapters": copy.deepcopy(new_chapters),
            }]

        new_content = existing_outline_asset.content + "\n" + "\n".join(
            f"Chapter {chapter['chapter_number']}: {chapter['title']} — {chapter['summary']}"
            for chapter in new_chapters
        )

        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            source="system",
            content=new_content,
            structured_data={
                "chapters": merged_chapters,
                "arcs": merged_arcs,
                "brief_ref": existing_outline_asset.structured_data.get("brief_ref", ""),
            },
        )

    def generate_chapter(
        self,
        project: Project,
        outline_asset: Asset,
        brief_asset: Asset,
        chapter_number: int,
        *,
        rewrite_context: dict[str, Any] | None = None,
    ) -> Asset:
        chapter_entry = next(
            (item for item in outline_asset.structured_data.get("chapters", []) if item.get("chapter_number") == chapter_number),
            None,
        )
        if chapter_entry is None:
            raise ValueError(f"Outline is missing chapter {chapter_number}")
        project_title = project.title or brief_asset.structured_data.get("title", "Untitled Project")
        brief_summary = brief_asset.structured_data.get("summary", project.brief)
        chapter_title = chapter_entry["title"]
        chapter_summary = chapter_entry["summary"]
        style_profile = self._build_style_profile(project.project_id, branch=outline_asset.branch)
        rules = self._resolve_rules(project)

        if rewrite_context is not None:
            original_content = rewrite_context.get("chapter_content", "")
            review_issues = rewrite_context.get("review_issues", [])
            if self._llm_client is not None and original_content and review_issues:
                max_words = self._llm_config.max_tokens
                messages = build_rewrite_messages(
                    chapter_content=original_content,
                    review_issues=review_issues,
                    brief_summary=brief_summary,
                    chapter_summary=chapter_summary,
                    style_profile=style_profile,
                )
                content = self._llm_client.generate_text(messages, max_tokens=max_words)
            else:
                content = (
                    f"Chapter {chapter_number}: {chapter_title} (revised)\n\n"
                    f"{original_content}\n\n[Revisions applied based on review feedback]"
                )
        elif self._llm_client is not None:
            max_words = self._llm_config.max_tokens
            previous_context = self._load_previous_chapter_context(project.project_id, chapter_number, branch=outline_asset.branch)
            messages = build_chapter_messages(
                project_title=project_title,
                brief_summary=brief_summary,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                chapter_summary=chapter_summary,
                max_words=max_words,
                previous_chapters=previous_context,
                style_profile=style_profile,
                rules=rules,
            )
            content = self._llm_client.generate_text(messages)
        else:
            content = (
                f"Chapter {chapter_number}: {chapter_title}\n\n"
                f"{brief_summary}\n\n"
                "The protagonist enters the scene carrying a private agenda and a public weakness. "
                "Pressure arrives faster than expected, forcing an early decision with visible cost.\n\n"
                f"Core beat: {chapter_summary}\n\n"
                "By the end of the chapter, the old equilibrium is broken and the next conflict is unavoidable."
            )
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content=content,
            structured_data={
                "chapter_number": chapter_number,
                "title": chapter_title,
                "summary": chapter_summary,
                "outline_ref": outline_asset.asset_id,
                "brief_ref": brief_asset.asset_id,
                "is_rewrite": rewrite_context is not None,
            },
        )

    def generate_spot_fix(
        self,
        project: Project,
        chapter_asset: Asset,
        paragraph_indices: list[int],
        fix_instruction: str,
    ) -> Asset:
        """Apply targeted paragraph-level fixes to a chapter."""
        paragraphs = chapter_asset.content.split("\n\n")
        target_paragraphs = [paragraphs[i] for i in paragraph_indices if i < len(paragraphs)]
        targets_text = "\n".join(f"段落 {i+1}: {target_paragraphs[j]}" for j, i in enumerate(paragraph_indices) if i < len(paragraphs))

        if self._llm_client is not None:
            system = (
                "你是资深编辑，负责对小说章节的指定段落进行定点修改。"
                "严格按照用户指令修改目标段落，保持其余内容不变。"
            )
            user = (
                f"章节标题: {chapter_asset.structured_data.get('title', '')}\n\n"
                f"修改指令:\n{fix_instruction}\n\n"
                f"目标段落:\n{targets_text}\n\n"
                "请输出修改后的段落内容，不要解释。"
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            fixed_content = self._llm_client.generate_text(messages, max_tokens=self._llm_config.max_tokens)
        else:
            fixed_content = f"[定点修复] 按指令修改了段落: {', '.join(str(i+1) for i in paragraph_indices)}\n{targets_text}"

        # Rebuild chapter with fixed paragraphs
        for idx, paragraph in zip(paragraph_indices, fixed_content.split("\n\n") if "\n\n" in fixed_content else [fixed_content]):
            if idx < len(paragraphs):
                paragraphs[idx] = paragraph

        new_content = "\n\n".join(paragraphs)
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content=new_content,
            structured_data={
                **chapter_asset.structured_data,
                "is_spot_fix": True,
                "original_asset_id": chapter_asset.asset_id,
                "spot_fix_paragraphs": paragraph_indices,
                "fix_instruction": fix_instruction,
                "spot_fix_status": "candidate",
            },
        )

    def get_total_usage(self) -> LlmUsage:
        if self._llm_client is not None:
            return self._llm_client.get_total_usage()
        return LlmUsage()

    def generate_review(
        self,
        project: Project,
        chapter_asset: Asset,
        chapter_number: int,
        *,
        config: ConfigSnapshot | None = None,
    ) -> Asset:
        config = config or ConfigSnapshot()
        chapter_data = chapter_asset.structured_data
        brief_ref = chapter_data.get("brief_ref", "")
        outline_ref = chapter_data.get("outline_ref", "")

        # Run rule-based quality checks regardless of LLM availability
        quality_engine = ContentQualityEngine()
        previous_chapters = self._collect_previous_chapter_content(project.project_id, chapter_number, branch=chapter_asset.branch)
        outline_desc = self._get_outline_chapter_desc(project.project_id, chapter_number, branch=chapter_asset.branch)
        char_assets = self._get_character_assets(project.project_id, branch=chapter_asset.branch)
        quality_checks = quality_engine.analyze(
            chapter_asset.content,
            previous_chapters=previous_chapters or None,
            outline_chapter_desc=outline_desc,
            character_assets=char_assets,
            llm_client=self._llm_client,
        )
        rule_audits = self._audit_rules(project, chapter_asset.content)
        failed_rule_checks = [f"rule:{audit['name']}" for audit in rule_audits if not audit["passed"]]
        rule_issues = [issue for audit in rule_audits for issue in audit["issues"]]
        foreshadowing_issues = self._audit_overdue_foreshadowing(project.project_id, chapter_number, branch=chapter_asset.branch)
        failed_foreshadowing_checks = ["foreshadowing:overdue"] if foreshadowing_issues else []

        llm_review = self._try_llm_review(chapter_asset, chapter_number, brief_ref, outline_ref, config)

        # Merge LLM and quality results
        if llm_review is not None:
            all_issues = list(llm_review["issues"])
            passed_checks = list(llm_review["checks"])
            failed_checks: list[str] = []
            for qc in quality_checks:
                check_name = f"quality:{qc.name}"
                if qc.passed:
                    passed_checks.append(check_name)
                else:
                    failed_checks.append(check_name)
                    all_issues.extend(qc.issues)
            all_issues.extend(rule_issues)
            all_issues.extend(foreshadowing_issues)
            failed_checks.extend(failed_rule_checks)
            failed_checks.extend(failed_foreshadowing_checks)
            return self._build_review_asset(
                project,
                chapter_asset,
                chapter_number,
                llm_review["approved"] and not failed_rule_checks and not failed_foreshadowing_checks,
                passed_checks,
                all_issues,
                failed_checks=failed_checks,
                quality_details={qc.name: {"passed": qc.passed, "details": qc.details} for qc in quality_checks},
                rule_audits=rule_audits,
                review_policy=config.review_policy,
                quality_experiment=str(config.values.get("quality_experiment") or "") or None,
                quality_variant=str(config.values.get("quality_variant") or "") or None,
            )

        # Fallback: structural checks determine approval; quality is advisory
        struct_checks, struct_issues = self._run_structural_checks(chapter_asset, chapter_number)
        approved = len(struct_issues) == 0
        advisory_issues: list[str] = []
        passed_quality_checks: list[str] = []
        failed_quality_checks: list[str] = []
        for qc in quality_checks:
            check_name = f"quality:{qc.name}"
            if qc.passed:
                passed_quality_checks.append(check_name)
            else:
                failed_quality_checks.append(check_name)
                advisory_issues.extend(qc.issues)
        all_checks = struct_checks + passed_quality_checks
        all_issues = struct_issues + advisory_issues + rule_issues + foreshadowing_issues
        return self._build_review_asset(
            project,
            chapter_asset,
            chapter_number,
            approved and not failed_rule_checks and not failed_foreshadowing_checks,
            all_checks,
            all_issues,
            failed_checks=failed_quality_checks + failed_rule_checks + failed_foreshadowing_checks,
            quality_details={qc.name: {"passed": qc.passed, "details": qc.details} for qc in quality_checks},
            rule_audits=rule_audits,
            review_policy=config.review_policy,
            quality_experiment=str(config.values.get("quality_experiment") or "") or None,
            quality_variant=str(config.values.get("quality_variant") or "") or None,
        )

    def _try_llm_review(
        self,
        chapter_asset: Asset,
        chapter_number: int,
        brief_ref: str,
        outline_ref: str,
        config: ConfigSnapshot,
    ) -> dict[str, Any] | None:
        """Attempt LLM-based review. Returns None if LLM is unavailable or refs missing."""
        if self._llm_client is None or not brief_ref or not outline_ref:
            return None
        brief_asset = self._load_asset_by_id(brief_ref)
        outline_asset = self._load_asset_by_id(outline_ref)
        if brief_asset is None or outline_asset is None:
            return None
        outline_entry = next(
            (item for item in outline_asset.structured_data.get("chapters", []) if item.get("chapter_number") == chapter_number),
            None,
        )
        outline_summary = outline_entry.get("summary", "") if outline_entry else ""
        if not outline_summary:
            return None
        brief_summary = brief_asset.structured_data.get("summary", "")
        messages = build_review_messages(
            chapter_content=chapter_asset.content,
            brief_summary=brief_summary,
            outline_summary=outline_summary,
            review_policy=config.review_policy,
            quality_experiment=str(config.values.get("quality_experiment") or "") or None,
            quality_variant=str(config.values.get("quality_variant") or "") or None,
        )
        max_tokens = max(self._llm_config.max_tokens, 2048)
        data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
        return {
            "approved": data.get("approved", True),
            "checks": data.get("checks", []),
            "issues": data.get("issues", []),
        }

    def _run_structural_checks(
        self,
        chapter_asset: Asset,
        chapter_number: int,
    ) -> tuple[list[str], list[str]]:
        """Fallback structural checks when LLM is unavailable."""
        checks_passed: list[str] = []
        issues: list[str] = []
        chapter_data = chapter_asset.structured_data
        outline_ref = chapter_data.get("outline_ref", "")
        brief_ref = chapter_data.get("brief_ref", "")
        if outline_ref:
            checks_passed.append("outline_binding")
        else:
            issues.append("chapter has no outline reference")
        if chapter_data.get("chapter_number") == chapter_number:
            checks_passed.append("chapter_number_match")
        else:
            issues.append(f"chapter number mismatch: expected {chapter_number}, got {chapter_data.get('chapter_number')}")
        if brief_ref:
            checks_passed.append("brief_binding")
        else:
            issues.append("chapter has no brief reference")
        if chapter_data.get("summary"):
            checks_passed.append("escalation_present")
        else:
            issues.append("chapter has no summary / escalation beat")
        return checks_passed, issues

    def _collect_previous_chapter_content(self, project_id: str, up_to_chapter: int, *, branch: str | None = None) -> list[str]:
        """Gather content from chapters 1..(n-1) for continuity checks."""
        chapters: list[tuple[int, str]] = []
        for asset in self.store.list_assets(project_id, AssetType.chapter, branch=branch):
            ch_num = asset.structured_data.get("chapter_number", 0)
            if 0 < ch_num < up_to_chapter:
                chapters.append((ch_num, asset.content))
        chapters.sort(key=lambda item: item[0])
        return [content for _, content in chapters]

    def _get_outline_chapter_desc(self, project_id: str, chapter_number: int, *, branch: str | None = None) -> str | None:
        """Get the outline description for a specific chapter number."""
        outline = self.store.get_latest_asset(project_id, AssetType.outline, branch=branch)
        if outline is None:
            return None
        chapters = outline.structured_data.get("chapters", [])
        arcs = outline.structured_data.get("arcs", [])
        # Check flat chapters first
        for ch in chapters:
            if ch.get("chapter_number") == chapter_number:
                return ch.get("description") or ch.get("summary") or None
        # Check arcs
        for arc in arcs:
            for ch in arc.get("chapters", []):
                if ch.get("chapter_number") == chapter_number:
                    return ch.get("description") or ch.get("summary") or None
        return None

    def _get_character_assets(self, project_id: str, *, branch: str | None = None) -> list[dict]:
        """Extract character asset structured data."""
        char_asset = self.store.get_latest_asset(project_id, AssetType.characters, branch=branch)
        if char_asset is None:
            return []
        characters = char_asset.structured_data.get("characters", [])
        if isinstance(characters, list):
            return characters
        return []

    def _audit_overdue_foreshadowing(self, project_id: str, chapter_number: int, *, branch: str | None = None) -> list[str]:
        issues: list[str] = []
        for asset in self.store.list_assets(project_id, AssetType.continuity_note, branch=branch):
            data = asset.structured_data
            if asset.is_deleted or data.get("kind") != "foreshadowing" or data.get("status") in {"resolved", "dropped"}:
                continue
            expected = data.get("expected_resolution_chapter")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected >= chapter_number:
                continue
            title = str(data.get("title") or asset.content or asset.asset_id)
            issues.append(f"Foreshadowing overdue: {title}")
        return issues

    def _resolve_rules(self, project: "Project") -> list[dict[str, Any]]:
        genre = getattr(project, "genre", None) or ""
        universal = self.store.get_rules(layer="universal")
        genre_rules = self.store.get_rules(layer="genre", genre=genre) if genre else []
        custom = self.store.get_rules(project_id=project.project_id)
        seen = set()
        result = []
        for r in [*universal, *genre_rules, *custom]:
            if r.rule_id not in seen:
                seen.add(r.rule_id)
                result.append({"name": r.name, "description": r.description})
        return result

    def _audit_rules(self, project: Project, content: str) -> list[dict[str, Any]]:
        genre = getattr(project, "genre", None) or ""
        rules = [
            *self.store.get_rules(layer="universal"),
            *(self.store.get_rules(layer="genre", genre=genre) if genre else []),
            *self.store.get_rules(project_id=project.project_id),
        ]
        seen = set()
        audits = []
        for rule in rules:
            if rule.rule_id in seen:
                continue
            seen.add(rule.rule_id)
            issues: list[str] = []
            prefix = "不得出现 "
            if rule.description.startswith(prefix):
                term = rule.description[len(prefix):].strip()
                if term and term in content:
                    issues.append(f"Rule '{rule.name}' forbids term: {term}")
            audits.append({
                "rule_id": rule.rule_id,
                "name": rule.name,
                "layer": rule.layer.value,
                "passed": not issues,
                "issues": issues,
            })
        return audits

    def _build_style_profile(self, project_id: str, *, branch: str | None = None) -> dict[str, Any]:
        return _build_style_profile(self.store, project_id, branch=branch)

    def _load_previous_chapter_context(self, project_id: str, up_to_chapter: int, *, branch: str | None = None) -> list[dict[str, str]]:
        """Load the last 3 previous chapters as structured context for generation."""
        chapters: list[dict[str, str]] = []
        for asset in self.store.list_assets(project_id, AssetType.chapter, branch=branch):
            ch_num = asset.structured_data.get("chapter_number", 0)
            if 0 < ch_num < up_to_chapter:
                chapters.append({
                    "chapter_number": str(ch_num),
                    "title": asset.structured_data.get("title", ""),
                    "summary": asset.structured_data.get("summary", ""),
                    "content_preview": asset.content[:300],
                })
        # Keep the last 3 chapters
        chapters.sort(key=lambda c: int(c["chapter_number"]))
        return chapters[-3:]

    def _build_review_asset(
        self,
        project: Project,
        chapter_asset: Asset,
        chapter_number: int,
        approved: bool,
        checks: list[str],
        issues: list[str],
        *,
        failed_checks: list[str] | None = None,
        quality_details: dict[str, Any] | None = None,
        rule_audits: list[dict[str, Any]] | None = None,
        review_policy: str = "default",
        quality_experiment: str | None = None,
        quality_variant: str | None = None,
    ) -> Asset:
        status_text = "approved for continuation" if approved else f"flagged: {', '.join(issues[:3])}"
        failed_checks = failed_checks or []
        content = (
            f"Review for chapter {chapter_number}: {status_text}.\n"
            f"Checks passed: {', '.join(checks) if checks else 'none'}.\n"
        )
        if failed_checks:
            content += f"Checks failed: {', '.join(failed_checks)}.\n"
        if issues:
            content += f"Issues: {'; '.join(issues)}.\n"
        content += "Next action: continue with the next chapter queue item or refine chapter-level style rules."
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            source="system",
            content=content,
            structured_data={
                "chapter_number": chapter_number,
                "approved": approved,
                "chapter_ref": chapter_asset.asset_id,
                "chapter_version": chapter_asset.version,
                "review_policy": review_policy,
                "quality_experiment": quality_experiment,
                "quality_variant": quality_variant,
                "checks": checks,
                "failed_checks": failed_checks,
                "issues": issues,
                "quality_details": quality_details,
                "rule_audits": rule_audits or [],
            },
        )


def _suggest_title(idea: str) -> str:
    normalized = " ".join(idea.split())
    if not normalized:
        return "Untitled Project"
    if " " not in normalized:
        return normalized[:12]
    words = normalized.split()
    return " ".join(word.capitalize() for word in words[:4])

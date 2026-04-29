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
from storyforge.execution.quality import ContentQualityCheck, ContentQualityEngine, find_production_artifact_issues
from storyforge.execution.store import StoryForgeStore
from storyforge.execution.style import build_style_profile as _build_style_profile
from storyforge.llm import LlmClient, LlmSkipError, LlmUsage
from storyforge.prompts import (
    build_asset_bootstrap_messages,
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
            genre = data.get("genre", project.genre or "玄幻")
            summary = data.get("summary", f"{project.idea.strip()}")
        else:
            title = project.title or _suggest_title(project.idea)
            genre = project.genre or "玄幻"
            summary = (
                f"{project.idea.strip()}。开篇钩子从一次无法回头的危机切入，主角带着强烈欲望被外部势力逼到台前，"
                "必须在暴露弱点和失去最后筹码之间做选择。每一次胜利都会换来更高代价，新的敌人、规则反转和未解悬念持续推动读者追看。"
            )
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.brief,
            source="system",
            content=(
                f"标题：{title}\n"
                f"类型：{genre}\n"
                f"目标读者：{project.audience or '中文网文读者'}\n"
                f"摘要：{summary}\n"
                "故事框架：\n"
                "- 用开篇钩子直接暴露主角欲望、弱点和外部压迫。\n"
                "- 让冲突升级到必须选择的赌注，胜利也要付出代价。\n"
                "- 通过反转、爽点兑现和章末悬念推动主角成长。"
            ),
            structured_data={
                "title": title,
                "genre": genre,
                "summary": summary,
                "target_length": project.target_length or 12,
            },
        )

    def generate_asset_bootstrap(self, project: Project, brief_asset: Asset) -> list[Asset]:
        if self._llm_client is not None:
            messages = build_asset_bootstrap_messages(project, brief_asset.content)
            max_tokens = max(self._llm_config.max_tokens, 4096)
            data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
        else:
            data = self._fallback_bootstrap_data(project, brief_asset)
        return [
            self._build_bootstrap_asset(project, AssetType.world, data.get("world", {}), brief_asset),
            self._build_bootstrap_asset(project, AssetType.characters, data.get("characters", {}), brief_asset),
            self._build_bootstrap_asset(project, AssetType.rules, data.get("rules", {}), brief_asset),
            self._build_bootstrap_asset(project, AssetType.timeline, data.get("timeline", {}), brief_asset),
            self._build_bootstrap_asset(project, AssetType.style_profile, data.get("style_profile", {}), brief_asset),
            self._build_bootstrap_asset(project, AssetType.foreshadowing, data.get("foreshadowing", {}), brief_asset),
        ]

    def _fallback_bootstrap_data(self, project: Project, brief_asset: Asset) -> dict[str, Any]:
        title = project.title or brief_asset.structured_data.get("title", "未命名项目")
        lead_name, judge_name = _fallback_character_names(project, title)
        return {
            "world": {
                "summary": f"《{title}》发生在资源稀缺、旧秩序崩塌后的高压世界，城邦、遗迹和审判体系共同挤压底层主角。",
                "forces": ["掌控资源的城邦高层", "追捕异常力量的审判者", "在废墟中求生的底层群体"],
                "constraints": ["力量使用必须付出记忆或关系代价", "公开暴露能力会引来审判", "每次胜利都会改变势力平衡"],
            },
            "characters": {
                "lead": {"name": lead_name, "desire": "夺回选择命运的主动权", "weakness": "害怕失去仅剩的重要之人"},
                "antagonists": [{"name": judge_name, "role": "城邦审判官", "desire": "追回神格碎片并确认被抹去的旧情"}, "隐藏债主", "掌控规则的城邦议会"],
                "relationships": [f"{lead_name}与{judge_name}存在被遗忘的旧情", "同伴既是支撑也是软肋"],
            },
            "rules": {
                "genre_rules": ["每章必须有明确目标、阻力、代价和章末钩子", "能力兑现要伴随更高风险"],
                "power_limits": ["力量不能无代价解决问题", "记忆损耗会影响人物关系和判断"],
                "avoid": ["不要摘要式讲述", "不要让主角无阻力开挂"],
            },
            "timeline": {
                "premise": "主角在危机中获得异常筹码，并被外部势力逼入不可回头的选择。",
                "beats": ["危机开场", "规则反转", "代价兑现", "更大敌人现身"],
                "current_state": "第一章前，主角尚未理解筹码真正代价。",
            },
            "style_profile": {
                "voice": "贴近角色的第三人称",
                "rhythm": "快节奏场景推进，动作、对话和内心压迫交替出现",
                "sensory_keywords": ["冷雨", "金属", "警报", "血腥", "脚步", "灯光"],
                "avoid": ["平铺直叙", "机械总结句", "说明文式世界观介绍"],
            },
            "foreshadowing": {
                "items": [
                    {"title": "真正的债主", "promise": "主角以为敌人在正面，真正操盘者藏在身后", "expected_resolution": "第三章后继续升级", "status": "planted"},
                    {"title": "被遗忘的关系", "promise": "追捕者和主角有旧关系", "expected_resolution": "中期揭示", "status": "planted"},
                ]
            },
        }

    def _build_bootstrap_asset(self, project: Project, asset_type: AssetType, data: dict[str, Any], brief_asset: Asset) -> Asset:
        content = _render_bootstrap_content(asset_type, data)
        return Asset(
            project_id=project.project_id,
            asset_type=asset_type,
            source="system",
            content=content,
            structured_data={**data, "brief_ref": brief_asset.asset_id, "kind": asset_type.value},
        )

    def generate_outline(self, project: Project, brief_asset: Asset, bootstrap_assets: list[Asset] | None = None) -> Asset:
        bootstrap_assets = bootstrap_assets or []
        story_bible = _format_story_bible(bootstrap_assets)
        if self._llm_client is not None:
            project_title = project.title or brief_asset.structured_data.get("title", "未命名项目")
            messages = build_outline_messages(project_title, brief_asset.content, story_bible=story_bible)
            max_tokens = max(self._llm_config.max_tokens, 4096)
            data = self._llm_client.generate_json(messages, max_tokens=max_tokens)
            raw_chapters = data.get("chapters", [])
            raw_arcs = data.get("arcs", [])
            # Backwards compatibility: if LLM returned only flat chapters, normalize
            if not raw_arcs and raw_chapters:
                raw_arcs = [{
                    "arc_number": 1,
                    "title": "第一卷",
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
                    "title": "危机开场",
                    "summary": "开场钩子直接把主角推入正面冲突：他想守住最后筹码，却被迫当众暴露弱点；如果退让，就会失去翻身机会。章末出现第一个规则反转。",
                },
                {
                    "chapter_number": 2,
                    "arc_number": 1,
                    "title": "反转加压",
                    "summary": "主角刚拿到阶段性爽点，对手就用更狠的阻力逼他付出代价；原以为可靠的线索突然反转，新的赌注把他推向更危险的选择。",
                },
                {
                    "chapter_number": 3,
                    "arc_number": 1,
                    "title": "钩子未断",
                    "summary": "主角以明确代价换来第一次兑现，但真正的敌人借机现身；旧危机看似解决，章末钩子却揭开更大的秘密和下一轮压迫。",
                },
            ]
            arcs = [{
                "arc_number": 1,
                "title": "危机开场",
                "chapters": chapters,
            }]
        project_title = project.title or brief_asset.structured_data.get("title", "未命名项目")
        content_lines = [f"项目：{project_title}"]
        for arc in arcs:
            arc_title = arc.get("title", f"第 {arc['arc_number']} 卷")
            content_lines.append(f"\n第 {arc['arc_number']} 卷大纲：{arc_title}")
            for ch in arc.get("chapters", []):
                content_lines.append(f"第 {ch['chapter_number']} 章：{ch['title']} —— {ch['summary']}")
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            source="system",
            content="\n".join(content_lines),
            structured_data={
                "chapters": chapters,
                "arcs": arcs,
                "brief_ref": brief_asset.asset_id,
                "bootstrap_refs": [asset.asset_id for asset in bootstrap_assets],
            },
        )

    def expand_outline(self, project: Project, existing_outline_asset: Asset, from_chapter: int, chapters_to_add: int = 3) -> Asset:
        """Append new chapters to an existing outline."""
        if self._llm_client is not None:
            project_title = project.title or existing_outline_asset.structured_data.get("title", project.title or "未命名项目")
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
                    "title": "新局压境",
                    "summary": f"主角在第 {from_chapter + i} 章获得新目标，却立刻遭遇更强阻力；为了守住上一章的成果，他必须付出代价，并在章末撞见新的钩子。",
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
                "title": "第一卷",
                "chapters": copy.deepcopy(new_chapters),
            }]

        new_content = existing_outline_asset.content + "\n" + "\n".join(
            f"第 {chapter['chapter_number']} 章：{chapter['title']} —— {chapter['summary']}"
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
        bootstrap_assets: list[Asset] | None = None,
    ) -> Asset:
        chapter_entry = next(
            (item for item in outline_asset.structured_data.get("chapters", []) if item.get("chapter_number") == chapter_number),
            None,
        )
        if chapter_entry is None:
            raise ValueError(f"大纲缺少第 {chapter_number} 章")
        project_title = project.title or brief_asset.structured_data.get("title", "未命名项目")
        brief_summary = brief_asset.structured_data.get("summary", project.brief)
        chapter_title = chapter_entry["title"]
        chapter_summary = chapter_entry["summary"]
        bootstrap_assets = bootstrap_assets or []
        story_bible = _format_story_bible(bootstrap_assets)
        style_profile = self._build_style_profile(project.project_id, branch=outline_asset.branch)
        style_asset = next((asset for asset in bootstrap_assets if asset.asset_type == AssetType.style_profile), None)
        if style_asset is not None:
            style_profile = {**style_profile, **style_asset.structured_data}
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
                    f"第 {chapter_number} 章：{chapter_title}（修订版）\n\n"
                    f"{original_content}\n\n【已根据审稿反馈完成修订】"
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
                story_bible=story_bible,
            )
            content = self._llm_client.generate_text(messages)
        else:
            characters_asset = next((asset for asset in bootstrap_assets if asset.asset_type == AssetType.characters), None)
            lead_name, judge_name = _character_names_from_asset(characters_asset, project, project_title)
            content = _fallback_chapter_content(chapter_number, chapter_title, chapter_summary, lead_name, judge_name)
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
                "bootstrap_refs": [asset.asset_id for asset in bootstrap_assets],
                "is_rewrite": rewrite_context is not None,
            },
        )

    def generate_validation_report(self, project: Project, chapter_asset: Asset, chapter_number: int, context_assets: list[Asset]) -> Asset:
        checks = self._quality_checks(project, chapter_asset, chapter_number)
        blocking_checks = {"structure", "pacing", "web_novel_aesthetics", "production_artifacts", "outline_deviation", "cross_chapter_continuity"}
        failed_checks = [check for check in checks if not check.passed]
        blocking_issues = [issue for check in failed_checks if check.name in blocking_checks for issue in check.issues]
        issues = [issue for check in failed_checks for issue in check.issues]
        passed = not blocking_issues
        content = _render_validation_content(chapter_number, passed, blocking_issues, issues)
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.validation_report,
            source="system",
            content=content,
            structured_data={
                "chapter_number": chapter_number,
                "passed": passed,
                "blocking_issues": blocking_issues,
                "issues": issues,
                "failed_checks": [check.name for check in failed_checks],
                "checks": {check.name: {"passed": check.passed, "issues": check.issues, "details": check.details} for check in checks},
                "chapter_ref": chapter_asset.asset_id,
                "context_refs": [asset.asset_id for asset in context_assets],
            },
        )

    def generate_audit_report(self, project: Project, chapter_asset: Asset, validation_asset: Asset, chapter_number: int, context_assets: list[Asset]) -> Asset:
        validation = validation_asset.structured_data
        blocking_issues = list(validation.get("blocking_issues", []))
        issues = list(validation.get("issues", []))
        scores = _audit_scores(validation.get("checks", {}))
        revision_instructions = [_instruction_for_issue(issue) for issue in issues[:8]]
        passed = not blocking_issues
        content = _render_audit_content(chapter_number, passed, scores, blocking_issues, revision_instructions)
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.audit_report,
            source="system",
            content=content,
            structured_data={
                "chapter_number": chapter_number,
                "passed": passed,
                "scores": scores,
                "blocking_issues": blocking_issues,
                "improvements": [issue for issue in issues if issue not in blocking_issues],
                "revision_instructions": revision_instructions,
                "chapter_ref": chapter_asset.asset_id,
                "validation_ref": validation_asset.asset_id,
                "context_refs": [asset.asset_id for asset in context_assets],
            },
        )

    def generate_revision(self, project: Project, chapter_asset: Asset, validation_asset: Asset, audit_asset: Asset, chapter_number: int, context_assets: list[Asset]) -> list[Asset]:
        instructions = audit_asset.structured_data.get("revision_instructions", [])
        if self._llm_client is not None and instructions:
            brief_asset = next((asset for asset in context_assets if asset.asset_type == AssetType.brief), None)
            outline_asset = next((asset for asset in context_assets if asset.asset_type == AssetType.outline), None)
            brief_summary = brief_asset.structured_data.get("summary", project.brief) if brief_asset else project.brief
            chapter_summary = chapter_asset.structured_data.get("summary", "")
            messages = build_rewrite_messages(
                chapter_content=chapter_asset.content,
                review_issues=[str(item) for item in instructions],
                brief_summary=brief_summary,
                chapter_summary=chapter_summary,
                style_profile=self._build_style_profile(project.project_id, branch=chapter_asset.branch),
            )
            revised_content = self._llm_client.generate_text(messages, max_tokens=self._llm_config.max_tokens)
        else:
            revised_content = _fallback_revised_content(chapter_asset.content, instructions)
        revised = Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content=revised_content,
            structured_data={
                **chapter_asset.structured_data,
                "chapter_number": chapter_number,
                "is_revision": True,
                "draft_ref": chapter_asset.asset_id,
                "validation_ref": validation_asset.asset_id,
                "audit_ref": audit_asset.asset_id,
                "context_refs": [asset.asset_id for asset in context_assets],
            },
        )
        revised_ref = revised.asset_id
        delta = Asset(
            project_id=project.project_id,
            asset_type=AssetType.revision_delta,
            source="system",
            content=_render_revision_delta_content(chapter_number, instructions),
            structured_data={
                "chapter_number": chapter_number,
                "draft_ref": chapter_asset.asset_id,
                "revised_ref": revised_ref,
                "validation_ref": validation_asset.asset_id,
                "audit_ref": audit_asset.asset_id,
                "resolved_issues": list(audit_asset.structured_data.get("blocking_issues", [])),
                "instructions_applied": instructions,
            },
        )
        return [revised, delta]

    def generate_reaudit_report(self, project: Project, revised_asset: Asset, revision_delta: Asset, previous_validation: Asset, previous_audit: Asset, chapter_number: int, context_assets: list[Asset]) -> Asset:
        checks = self._quality_checks(project, revised_asset, chapter_number)
        blocking_checks = {"structure", "pacing", "web_novel_aesthetics", "production_artifacts", "outline_deviation", "cross_chapter_continuity"}
        failed_checks = [check for check in checks if not check.passed]
        current_issues = [issue for check in failed_checks for issue in check.issues]
        current_blocking_issues = [issue for check in failed_checks if check.name in blocking_checks for issue in check.issues]
        previous_issues = list(previous_validation.structured_data.get("issues", []))
        previous_blocking_issues = list(previous_validation.structured_data.get("blocking_issues", []))
        previous_failed_checks = {str(item) for item in previous_validation.structured_data.get("failed_checks", []) if str(item)}
        current_failed_checks = {check.name for check in failed_checks}
        unresolved_previous_checks = sorted(previous_failed_checks & current_failed_checks)
        resolved_previous_checks = sorted(previous_failed_checks - current_failed_checks)
        unresolved_previous_issues = _issues_for_checks(previous_validation.structured_data.get("checks", {}), unresolved_previous_checks) or sorted(set(str(issue) for issue in previous_issues) & set(str(issue) for issue in current_issues))
        resolved_previous_issues = _issues_for_checks(previous_validation.structured_data.get("checks", {}), resolved_previous_checks) or sorted(set(str(issue) for issue in previous_issues) - set(str(issue) for issue in current_issues))
        improved = len(current_blocking_issues) < len(previous_blocking_issues) or len(current_issues) < len(previous_issues) or bool(resolved_previous_checks)
        requires_revision = bool(previous_issues or previous_blocking_issues or previous_failed_checks)
        passed = not current_blocking_issues and (not requires_revision or (improved and not unresolved_previous_checks and not unresolved_previous_issues and not current_issues))
        content = _render_reaudit_content(chapter_number, passed, previous_issues, current_issues)
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.audit_report,
            source="system",
            content=content,
            structured_data={
                "kind": "re_audit",
                "chapter_number": chapter_number,
                "passed": passed,
                "improved": improved,
                "previous_issue_count": len(previous_issues),
                "current_issue_count": len(current_issues),
                "previous_blocking_issue_count": len(previous_blocking_issues),
                "current_blocking_issue_count": len(current_blocking_issues),
                "blocking_issues": current_blocking_issues,
                "remaining_issues": current_issues,
                "unresolved_previous_issues": unresolved_previous_issues,
                "resolved_previous_issues": resolved_previous_issues,
                "unresolved_previous_checks": unresolved_previous_checks,
                "resolved_previous_checks": resolved_previous_checks,
                "failed_checks": [check.name for check in failed_checks],
                "checks": {check.name: {"passed": check.passed, "issues": check.issues, "details": check.details} for check in checks},
                "revised_ref": revised_asset.asset_id,
                "revision_delta_ref": revision_delta.asset_id,
                "previous_validation_ref": previous_validation.asset_id,
                "previous_audit_ref": previous_audit.asset_id,
                "context_refs": [asset.asset_id for asset in context_assets],
            },
        )

    def generate_final_chapter(self, project: Project, revised_asset: Asset, reaudit_asset: Asset, chapter_number: int) -> Asset:
        if reaudit_asset.asset_type != AssetType.audit_report or reaudit_asset.structured_data.get("kind") != "re_audit":
            raise ValueError("Final save requires a re-audit report")
        if reaudit_asset.structured_data.get("passed") is not True:
            raise ValueError("Re-audit did not pass; final save is blocked")
        if reaudit_asset.structured_data.get("chapter_number") != chapter_number:
            raise ValueError("Re-audit chapter number does not match final save")
        artifact_issues = find_production_artifact_issues(revised_asset.content)
        if artifact_issues:
            raise ValueError("定稿正文包含内部写作痕迹：" + "；".join(artifact_issues))
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.final_chapter,
            source="system",
            content=revised_asset.content,
            structured_data={
                **revised_asset.structured_data,
                "chapter_number": chapter_number,
                "finalized": True,
                "source_chapter_ref": revised_asset.asset_id,
                "reaudit_ref": reaudit_asset.asset_id,
            },
        )

    def generate_export_candidate(self, project: Project, final_chapter_assets: list[Asset], chapter_number: int) -> Asset:
        latest_by_chapter: dict[int, Asset] = {}
        for asset in final_chapter_assets:
            value = asset.structured_data.get("chapter_number")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                continue
            existing = latest_by_chapter.get(value)
            if existing is None or (asset.version, asset.updated_at, asset.created_at) > (existing.version, existing.updated_at, existing.created_at):
                latest_by_chapter[value] = asset
        missing = [number for number in range(1, chapter_number + 1) if number not in latest_by_chapter]
        ordered_assets = [latest_by_chapter[number] for number in range(1, chapter_number + 1) if number in latest_by_chapter]
        final_refs = [asset.asset_id for asset in ordered_assets]
        content = "\n\n".join(asset.content for asset in ordered_assets)
        blocked_reason = "" if not missing else "缺少连续定稿章节：" + "、".join(str(number) for number in missing)
        return Asset(
            project_id=project.project_id,
            asset_type=AssetType.export_candidate,
            source="system",
            content=content,
            structured_data={
                "chapter_number": chapter_number,
                "ready": not missing and bool(ordered_assets),
                "final_chapter_refs": final_refs,
                "blocked_reason": blocked_reason or ("" if ordered_assets else "缺少已定稿章节"),
            },
        )

    def _quality_checks(self, project: Project, chapter_asset: Asset, chapter_number: int) -> list[ContentQualityCheck]:
        quality_engine = ContentQualityEngine()
        previous_chapters = self._collect_previous_chapter_content(project.project_id, chapter_number, branch=chapter_asset.branch)
        outline_desc = self._get_outline_chapter_desc(project.project_id, chapter_number, branch=chapter_asset.branch)
        char_assets = self._get_character_assets(project.project_id, branch=chapter_asset.branch)
        return quality_engine.analyze(
            chapter_asset.content,
            previous_chapters=previous_chapters or None,
            outline_chapter_desc=outline_desc,
            character_assets=char_assets,
            llm_client=self._llm_client,
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
            issues.append("章节缺少大纲引用")
        if chapter_data.get("chapter_number") == chapter_number:
            checks_passed.append("chapter_number_match")
        else:
            issues.append(f"章节编号不一致：预期 {chapter_number}，实际 {chapter_data.get('chapter_number')}")
        if brief_ref:
            checks_passed.append("brief_binding")
        else:
            issues.append("章节缺少项目概要引用")
        if chapter_data.get("summary"):
            checks_passed.append("escalation_present")
        else:
            issues.append("章节缺少摘要或升级情节")
        return checks_passed, issues

    def _collect_previous_chapter_content(self, project_id: str, up_to_chapter: int, *, branch: str | None = None) -> list[str]:
        """Gather finalized content from chapters 1..(n-1) for continuity checks."""
        latest_by_chapter: dict[int, Asset] = {}
        final_assets = self.store.list_assets(project_id, AssetType.final_chapter, branch=branch)
        source_assets = final_assets or self.store.list_assets(project_id, AssetType.chapter, branch=branch)
        for asset in source_assets:
            if asset.is_deleted or asset.structured_data.get("is_spot_fix") is True:
                continue
            ch_num = asset.structured_data.get("chapter_number", 0)
            if isinstance(ch_num, bool) or not isinstance(ch_num, int) or not 0 < ch_num < up_to_chapter:
                continue
            existing = latest_by_chapter.get(ch_num)
            if existing is None or (asset.version, asset.updated_at, asset.created_at) > (existing.version, existing.updated_at, existing.created_at):
                latest_by_chapter[ch_num] = asset
        return [latest_by_chapter[number].content for number in sorted(latest_by_chapter)]

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
        data = char_asset.structured_data
        characters = data.get("characters", [])
        if isinstance(characters, list):
            return [item for item in characters if isinstance(item, dict)]
        normalized: list[dict] = []
        lead = data.get("lead")
        if isinstance(lead, dict):
            normalized.append(lead)
        antagonists = data.get("antagonists", [])
        if isinstance(antagonists, list):
            normalized.extend({"name": item} if isinstance(item, str) else item for item in antagonists if isinstance(item, (str, dict)))
        relationships = data.get("relationships", [])
        if isinstance(relationships, list) and normalized:
            normalized[0] = {**normalized[0], "relationships": relationships}
        return normalized

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
            issues.append(f"伏笔逾期未回收：{title}")
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
                    issues.append(f"规则“{rule.name}”禁止出现：{term}")
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
        status_text = "已通过，可继续后续章节" if approved else f"需修订：{'；'.join(issues[:3])}"
        failed_checks = failed_checks or []
        content = (
            f"第 {chapter_number} 章审稿结果：{status_text}。\n"
            f"已通过检查：{'、'.join(checks) if checks else '无'}。\n"
        )
        if failed_checks:
            content += f"未通过检查：{'、'.join(failed_checks)}。\n"
        if issues:
            content += f"问题：{'；'.join(issues)}。\n"
        content += "下一步：继续处理下一章任务，或根据问题修订章节与写作规则。"
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
        return "未命名项目"
    if " " not in normalized:
        return normalized[:12]
    return normalized[:24]


def _fallback_character_names(project: Project, title: str) -> tuple[str, str]:
    text = f"{project.idea} {title} {project.genre}"
    if "神格" in text or "审判官" in text or "废土" in text:
        return "沈砚", "陆青鸢"
    if "修理" in text or "机械" in text or "遗迹" in text:
        return "林砚", "祁望"
    if "仙" in text or "修" in text:
        return "许照夜", "闻青岚"
    return "林照", "顾沉舟"


def _character_names_from_asset(characters_asset: Asset | None, project: Project, title: str) -> tuple[str, str]:
    fallback_lead, fallback_judge = _fallback_character_names(project, title)
    if characters_asset is None:
        return fallback_lead, fallback_judge
    data = characters_asset.structured_data
    lead = data.get("lead")
    lead_name = lead.get("name") if isinstance(lead, dict) else ""
    antagonists = data.get("antagonists", [])
    judge_name = ""
    if isinstance(antagonists, list):
        for item in antagonists:
            if isinstance(item, dict):
                name = str(item.get("name") or "")
            else:
                name = str(item or "")
            if name and name not in _PLACEHOLDER_NAMES:
                judge_name = name
                break
    if not isinstance(lead_name, str) or not lead_name or lead_name in _PLACEHOLDER_NAMES:
        lead_name = fallback_lead
    if not judge_name:
        judge_name = fallback_judge
    return lead_name, judge_name


_PLACEHOLDER_NAMES = {"主角", "对手", "男主", "女主", "反派", "配角", "审判官"}


def _fallback_chapter_content(chapter_number: int, chapter_title: str, chapter_summary: str, lead_name: str, judge_name: str) -> str:
    return (
        f"第 {chapter_number} 章：{chapter_title}\n\n"
        f"门外的警报刚响第一声，冷雨就顺着裂开的窗缝打进来，金属地面泛起一层寒意。{lead_name}攥紧那枚黑色碎片，掌心被棱角割开，他知道一旦把它交出去，自己最后一段关于妹妹的记忆也会被城邦收走。\n\n"
        f"“退后。”{judge_name}把审判灯压到他脸上，声音冷得像刀，“否则，维修棚里所有人都会替你偿债。”\n\n"
        f"{lead_name}看见她腕骨内侧那道旧伤，心口忽然空了一下。他明明不认识这个女人，却本能地想把她从雨里拽回来。碎片在血里发烫，低声叫出一个被他忘掉的称呼。\n\n"
        f"他没有退。脚步声逼近时，{lead_name}反而把碎片按进掌心伤口。疼痛炸开的瞬间，审判灯全部熄灭，围住维修棚的枪口同时垂下。代价也在下一息兑现：他记不起妹妹的脸了。\n\n"
        f"{judge_name}的瞳孔终于乱了一瞬。她抬枪指向他，却没有扣下扳机，只低声问：“你连我也忘了？”\n\n"
        f"{lead_name}还没来得及回答，墙上的旧屏幕突然亮起血红字迹：真正的债主已抵达。下一刻，屏幕倒影里，第三个人的影子站在他身后，替他说出了下一句债约。"
    )


def _render_bootstrap_content(asset_type: AssetType, data: dict[str, Any]) -> str:
    labels = {
        AssetType.world: "世界设定",
        AssetType.characters: "角色关系",
        AssetType.rules: "写作规则",
        AssetType.timeline: "时间线",
        AssetType.style_profile: "风格画像",
        AssetType.foreshadowing: "伏笔计划",
    }
    lines = [labels.get(asset_type, asset_type.value)]
    for key, value in data.items():
        lines.append(f"{key}：{_format_value(value)}")
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    if isinstance(value, dict):
        return "；".join(f"{key}={_format_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "、".join(_format_value(item) for item in value)
    return str(value)


def _format_story_bible(assets: list[Asset]) -> str:
    if not assets:
        return ""
    ordered_types = [AssetType.world, AssetType.characters, AssetType.rules, AssetType.timeline, AssetType.style_profile, AssetType.foreshadowing]
    by_type = {asset.asset_type: asset for asset in assets}
    lines: list[str] = []
    for asset_type in ordered_types:
        asset = by_type.get(asset_type)
        if asset is not None:
            lines.append(f"[{asset_type.value}]\n{asset.content}")
    return "\n\n".join(lines)


def _issues_for_checks(checks: Any, check_names: list[str]) -> list[str]:
    if not isinstance(checks, dict):
        return []
    issues: list[str] = []
    for check_name in check_names:
        check_data = checks.get(check_name)
        if not isinstance(check_data, dict):
            continue
        for issue in check_data.get("issues", []):
            issue_text = str(issue)
            if issue_text and issue_text not in issues:
                issues.append(issue_text)
    return issues


def _audit_scores(checks: dict[str, Any]) -> dict[str, int]:
    dimensions = {
        "structure": ["structure"],
        "pacing": ["pacing", "emotional_arc"],
        "continuity": ["character_continuity", "cross_chapter_continuity", "foreshadowing"],
        "aesthetics": ["web_novel_aesthetics", "ai_trace"],
        "outline_alignment": ["outline_deviation"],
    }
    scores: dict[str, int] = {}
    for dimension, names in dimensions.items():
        present = [checks.get(name) for name in names if name in checks]
        if not present:
            scores[dimension] = 5
            continue
        passed = sum(1 for item in present if isinstance(item, dict) and item.get("passed") is True)
        scores[dimension] = max(1, round(passed / len(present) * 5))
    return scores


def _instruction_for_issue(issue: str) -> str:
    if "开篇" in issue:
        return "重写第一段，让异常、压力或危险直接入场。"
    if "赌注" in issue:
        return "补清失败代价，让读者知道主角会失去什么。"
    if "冲突" in issue:
        return "增加可见阻力和正面对抗，避免只写内心或背景。"
    if "章末" in issue:
        return "重写结尾，留下新危机、反转或未解问题。"
    if "场景化" in issue:
        return "用动作、对话和感官细节替代摘要说明。"
    if "大纲" in issue:
        return "把本章关键目标和大纲关键词写进正文行动。"
    return f"针对问题修订：{issue}"


def _render_validation_content(chapter_number: int, passed: bool, blocking_issues: list[str], issues: list[str]) -> str:
    lines = [f"第 {chapter_number} 章验证结果：{'通过' if passed else '阻断'}"]
    if blocking_issues:
        lines.append("阻断项：" + "；".join(blocking_issues))
    if issues:
        lines.append("全部问题：" + "；".join(issues))
    return "\n".join(lines)


def _render_audit_content(chapter_number: int, passed: bool, scores: dict[str, int], blocking_issues: list[str], instructions: list[str]) -> str:
    lines = [f"第 {chapter_number} 章审计结果：{'通过' if passed else '需修订'}"]
    lines.append("维度分：" + "、".join(f"{key}={value}/5" for key, value in scores.items()))
    if blocking_issues:
        lines.append("阻断项：" + "；".join(blocking_issues))
    if instructions:
        lines.append("修订指令：" + "；".join(instructions))
    return "\n".join(lines)


def _fallback_revised_content(content: str, instructions: list[Any]) -> str:
    revised = _clean_internal_prose(content)
    revised = revised.replace("主角", "沈砚").replace("对手", "陆青鸢")
    if "恐惧像铁锈" in revised:
        return revised
    paragraphs = [paragraph for paragraph in revised.split("\n\n") if paragraph.strip()]
    emotional_turn = (
        "雨声里，恐惧像铁锈一样压住他的喉咙。他终于明白，碎片夺走的不是抽象的记忆，"
        "而是一个人活下去时最舍不得松手的温暖；可那句质问又像微弱火光，让他在绝望里抓住了一线希望。"
    )
    if len(paragraphs) >= 2:
        paragraphs.insert(-1, emotional_turn)
    else:
        paragraphs.append(emotional_turn)
    return "\n\n".join(paragraphs)


def _clean_internal_prose(content: str) -> str:
    cleaned_paragraphs: list[str] = []
    banned_fragments = ("【修订强化】", "【对应指令】", "【已根据审稿反馈完成修订】", "本章核心", "对应指令", "编辑反馈", "请返回")
    for paragraph in content.split("\n\n"):
        if any(fragment in paragraph for fragment in banned_fragments):
            continue
        cleaned_paragraphs.append(paragraph.strip())
    return "\n\n".join(paragraph for paragraph in cleaned_paragraphs if paragraph)


def _render_revision_delta_content(chapter_number: int, instructions: list[Any]) -> str:
    lines = [f"第 {chapter_number} 章修订差异"]
    if instructions:
        lines.extend(f"- {item}" for item in instructions)
    else:
        lines.append("- 保持章节目标，补强场景化与章末钩子")
    return "\n".join(lines)


def _render_reaudit_content(chapter_number: int, passed: bool, previous_issues: list[str], current_issues: list[str]) -> str:
    return (
        f"第 {chapter_number} 章复审结果：{'通过' if passed else '仍需修订'}\n"
        f"原问题数：{len(previous_issues)}；当前问题数：{len(current_issues)}"
        + ("\n剩余问题：" + "；".join(current_issues) if current_issues else "")
    )

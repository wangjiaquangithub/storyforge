"""Content quality analysis for chapter review.

Provides heuristic checks that don't require an LLM: repetition,
continuity markers, and structural sanity.  Results feed into the
review asset alongside any LLM-generated feedback.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


_INTERNAL_PROSE_MARKERS = (
    "【修订强化】",
    "【对应指令】",
    "【已根据审稿反馈完成修订】",
    "修订指令",
    "对应指令",
    "编辑反馈",
    "审稿反馈",
    "当前章节全文",
    "请返回",
    "项目摘要",
    "故事框架",
    "本章核心",
    "本章节核心",
    "开篇钩子从",
    "持续推动读者追看",
    "读者追看",
)

_PLACEHOLDER_CHARACTER_LABELS = ("主角", "对手", "男主", "女主", "反派", "配角")
_PLACEHOLDER_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:^|[\n\s，。；：、“”《》（）()【】])(?P<label>主角|对手|男主|女主|反派|配角)(?:[\n\s，。；：、“”《》（）()【】]|$)",
        r"(?P<label>主角|对手|男主|女主|反派|配角)[：:]",
        r"(?:^|[\n\s，。；：、“”《》（）()【】])(?P<label>主角|对手|男主|女主|反派|配角)(?=攥|看|听|说|问|答|走|退|笑|哭|抬|站|冲|按|把|被|在)",
    )
)


def find_production_artifact_issues(content: str, *, brief_summary: str | None = None) -> list[str]:
    issues: list[str] = []
    found_markers = [marker for marker in _INTERNAL_PROSE_MARKERS if marker in content]
    if found_markers:
        issues.append("正文包含内部写作/修订标记：" + "、".join(found_markers[:5]))
    found_placeholders = _find_placeholder_labels(content)
    if found_placeholders:
        issues.append("正文使用占位角色称呼而非具体姓名：" + "、".join(found_placeholders[:5]))
    if brief_summary:
        normalized_summary = _normalize_for_overlap(brief_summary)
        normalized_content = _normalize_for_overlap(content)
        if len(normalized_summary) >= 24 and normalized_summary in normalized_content:
            issues.append("正文直接粘贴项目 brief/摘要，而不是场景化改写")
    return issues


def _normalize_for_overlap(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def _find_placeholder_labels(content: str) -> list[str]:
    labels: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(content):
            label = match.group("label")
            if label not in labels:
                labels.append(label)
    return labels


class ContentQualityCheck:
    """A single quality check with pass/fail result."""

    def __init__(self, name: str, passed: bool, issues: list[str], details: dict[str, Any] | None = None) -> None:
        self.name = name
        self.passed = passed
        self.issues = issues
        self.details = details or {}


class ContentQualityEngine:
    """Rule-based content quality checks for chapter review.

    These are fast, deterministic analyses that complement LLM review:
    - **repetition**: detect repeated phrases / structural redundancy
    - **continuity**: verify basic consistency markers across text
    - **structure**: paragraph length distribution, dialogue ratio
    - **pacing**: scene density estimation
    """

    def __init__(
        self,
        *,
        repetition_threshold: float = 0.4,
        max_sentence_repeat: int = 3,
        max_paragraph_words: int = 500,
    ) -> None:
        self.repetition_threshold = repetition_threshold
        self.max_sentence_repeat = max_sentence_repeat
        self.max_paragraph_words = max_paragraph_words

    def analyze(
        self,
        chapter_content: str,
        *,
        previous_chapters: list[str] | None = None,
        outline_chapter_desc: str | None = None,
        character_assets: list[dict] | None = None,
        llm_client: Any | None = None,
    ) -> list[ContentQualityCheck]:
        """Run all quality checks. Returns a list of checks with pass/fail."""
        checks: list[ContentQualityCheck] = []
        checks.append(self._check_repetition(chapter_content))
        checks.append(self._check_structure(chapter_content))
        checks.append(self._check_pacing(chapter_content))
        checks.append(self._check_character_continuity(chapter_content, character_assets, llm_client))
        checks.append(self._check_foreshadowing(chapter_content, outline_chapter_desc))
        checks.append(self._check_ai_trace(chapter_content, llm_client))
        checks.append(self._check_outline_deviation(chapter_content, outline_chapter_desc))
        checks.append(self._check_emotional_arc(chapter_content, llm_client))
        checks.append(self._check_web_novel_aesthetics(chapter_content))
        checks.append(self._check_production_artifacts(chapter_content))
        if previous_chapters:
            checks.append(self._check_cross_chapter_continuity(chapter_content, previous_chapters))
        return checks

    # ------------------------------------------------------------------
    # Repetition
    # ------------------------------------------------------------------

    def _check_repetition(self, content: str) -> ContentQualityCheck:
        """Detect intra-chapter phrase and sentence repetition."""
        sentences = self._split_sentences(content)
        issues: list[str] = []
        details: dict[str, Any] = {}

        # Exact sentence repeats
        counter = Counter(s.strip().lower() for s in sentences if len(s.strip()) > 10)
        exact_repeats = {text: count for text, count in counter.items() if count >= self.max_sentence_repeat}
        if exact_repeats:
            for text, count in list(exact_repeats.items())[:3]:
                issues.append(f"句子重复 {count} 次：{text[:60]}...")
            details["exact_repeats"] = len(exact_repeats)

        # Phrase-level: ngram overlap (6-grams of words)
        words = content.lower().split()
        if len(words) > 30:
            ngrams = [" ".join(words[i : i + 6]) for i in range(len(words) - 5)]
            ngram_counter = Counter(ngrams)
            total_ngrams = len(ngrams)
            repeated_ngrams = sum(1 for count in ngram_counter.values() if count > 1)
            ratio = repeated_ngrams / total_ngrams if total_ngrams > 0 else 0
            details["phrase_repetition_ratio"] = round(ratio, 3)
            if ratio > self.repetition_threshold:
                issues.append(f"短语重复比例 {ratio:.1%}，超过阈值 {self.repetition_threshold:.0%}")

        passed = len(issues) == 0
        return ContentQualityCheck("repetition", passed, issues, details)

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------

    def _check_structure(self, content: str) -> ContentQualityCheck:
        """Check paragraph lengths, dialogue ratio, basic formatting."""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        issues: list[str] = []
        details: dict[str, Any] = {}

        details["paragraph_count"] = len(paragraphs)

        # Too short overall
        word_count = self._count_words_or_cjk_chars(content)
        details["word_count"] = word_count
        if word_count < 100:
            issues.append(f"章节篇幅过短：当前统计 {word_count} 个词字单位")

        # Very long paragraphs (likely walls of text)
        long_paras = [p for p in paragraphs if self._count_words_or_cjk_chars(p) > self.max_paragraph_words]
        if long_paras:
            issues.append(f"{len(long_paras)} 个段落超过 {self.max_paragraph_words} 个词，可能形成大段文字")

        # Single-paragraph chapters
        if len(paragraphs) <= 1 and word_count > 200:
            issues.append("章节只有一个大段落，阅读节奏可能过于沉闷")

        passed = len(issues) == 0
        return ContentQualityCheck("structure", passed, issues, details)

    # ------------------------------------------------------------------
    # Pacing
    # ------------------------------------------------------------------

    def _check_pacing(self, content: str) -> ContentQualityCheck:
        """Estimate pacing via scene / dialogue markers."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        word_count = self._count_words_or_cjk_chars(content)
        sentences = self._split_sentences(content)
        dialogue_sentences = [s for s in sentences if '"' in s or '“' in s or '”' in s]
        dialogue_ratio = len(dialogue_sentences) / len(sentences) if sentences else 0
        details["dialogue_ratio"] = round(dialogue_ratio, 3)
        details["sentence_count"] = len(sentences)

        # Scene transition markers
        transition_words = ["与此同时", "后来", "随后", "数小时后", "数日后", "突然", "meanwhile", "later", "the next", "hours later", "days later", "suddenly"]
        transitions_found = [w for w in transition_words if w.lower() in content.lower()]
        details["scene_transitions"] = len(transitions_found)

        # No dialogue at all in a substantial chapter
        if word_count > 300 and dialogue_ratio < 0.05:
            issues.append("对话占比过低，章节可能过于偏说明性叙述")

        passed = len(issues) == 0
        return ContentQualityCheck("pacing", passed, issues, details)

    # ------------------------------------------------------------------
    # Cross-chapter continuity
    # ------------------------------------------------------------------

    def _check_cross_chapter_continuity(
        self,
        content: str,
        previous_chapters: list[str],
    ) -> ContentQualityCheck:
        """Basic continuity: named entities from previous chapters should be consistent."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        all_previous = " ".join(previous_chapters)
        previous_names = set(re.findall(r"[A-Z][a-z]{2,}", all_previous))
        current_names = set(re.findall(r"[A-Z][a-z]{2,}", content))

        shared_names = previous_names & current_names
        dropped_names = previous_names - current_names

        details["shared_character_names"] = list(shared_names)[:10]
        details["previous_unique_names"] = len(previous_names)
        details["current_unique_names"] = len(current_names)

        if len(previous_names) > 3:
            drop_ratio = len(dropped_names) / len(previous_names)
            if drop_ratio > 0.7:
                issues.append(
                    f"前文章节中的多数角色名未出现（{len(dropped_names)}/{len(previous_names)}）"
                )

        passed = len(issues) == 0
        return ContentQualityCheck("cross_chapter_continuity", passed, issues, details)

    # ------------------------------------------------------------------
    # Character continuity
    # ------------------------------------------------------------------

    def _check_character_continuity(
        self,
        content: str,
        character_assets: list[dict] | None,
        llm_client: Any | None,
    ) -> ContentQualityCheck:
        """Check character traits consistency against character assets."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        if not character_assets or not llm_client:
            return ContentQualityCheck("character_continuity", True, [], details)

        for char in character_assets:
            name = char.get("name", "")
            traits = char.get("traits", "")
            if name and name.lower() in content.lower():
                details["checked_character"] = name
                if traits:
                    trait_words = [w.strip().lower() for w in re.split(r"[,;、]", traits) if w.strip()]
                    for tw in trait_words[:5]:
                        if tw and tw not in content.lower():
                            issues.append(f"角色 {name} 的关键特征 '{tw}' 未在本章体现")

        passed = len(issues) == 0
        return ContentQualityCheck("character_continuity", passed, issues, details)

    # ------------------------------------------------------------------
    # Foreshadowing
    # ------------------------------------------------------------------

    def _check_foreshadowing(
        self,
        content: str,
        outline_chapter_desc: str | None,
    ) -> ContentQualityCheck:
        """Check if outline foreshadowing markers are mentioned in this chapter."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        if not outline_chapter_desc:
            return ContentQualityCheck("foreshadowing", True, [], details)

        foreshadowing_matches = re.findall(r"伏笔[：:](.+?)[\n。]", outline_chapter_desc)
        if foreshadowing_matches:
            details["foreshadowing_items"] = foreshadowing_matches
            for item in foreshadowing_matches:
                key = item.strip()
                if len(key) > 1 and key not in content:
                    issues.append(f"大纲中伏笔 '{key}' 未在本章提及")

        passed = len(issues) == 0
        return ContentQualityCheck("foreshadowing", passed, issues, details)

    # ------------------------------------------------------------------
    # AI trace detection
    # ------------------------------------------------------------------

    def _check_ai_trace(self, content: str, llm_client: Any | None) -> ContentQualityCheck:
        """Detect AI writing patterns: cliché transitions, repetitive openings, overused qualifiers."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        cliché_transitions = ["与此同时", "就在这时", "突然之间", "毫无疑问", "毋庸置疑", "不可否认"]
        found = [t for t in cliché_transitions if t in content]
        if len(found) > 3:
            issues.append(f"陈词滥调过渡词过多（{len(found)} 次）：{', '.join(found[:3])}")
        details["cliche_transitions"] = found

        repetitive_openings = re.findall(r"^[他她我它他们](?:\w{0,3})?(?:说|走|看|想|听|闻|笑|叹|问|答)", content, re.MULTILINE)
        if len(repetitive_openings) > 5:
            issues.append(f"句首重复模式（{len(repetitive_openings)} 次）")
        details["repetitive_openings"] = len(repetitive_openings)

        overused_qualifiers = ["仿佛", "似乎", "好像", "宛如", "犹如"]
        qualifier_count = sum(content.count(q) for q in overused_qualifiers)
        word_count = self._count_words_or_cjk_chars(content)
        if word_count > 0 and qualifier_count / word_count > 0.03:
            issues.append(f"修饰词密度过高（{qualifier_count}/{word_count} ≈ {qualifier_count/max(word_count,1)*100:.0f}%）")
        details["overused_qualifier_count"] = qualifier_count

        passed = len(issues) == 0
        return ContentQualityCheck("ai_trace", passed, issues, details)

    # ------------------------------------------------------------------
    # Outline deviation
    # ------------------------------------------------------------------

    def _check_outline_deviation(
        self,
        content: str,
        outline_chapter_desc: str | None,
    ) -> ContentQualityCheck:
        """Compare chapter content against outline description keywords."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        if not outline_chapter_desc:
            return ContentQualityCheck("outline_deviation", True, [], details)

        concept_groups = [
            (("开场", "钩子", "危机"), ("门外", "警报", "冷雨", "第一声", "血红字迹")),
            (("冲突", "阻力", "压迫", "对手"), ("审判灯", "偿债", "枪口", "抬枪", "退后", "威胁")),
            (("筹码", "弱点", "退让", "失去", "翻身", "选择"), ("碎片", "妹妹", "记忆", "交出去", "失去", "最后", "没有退")),
            (("代价",), ("代价", "记不起", "忘", "夺走")),
            (("规则", "反转"), ("熄灭", "垂下", "亮起", "真正", "下一刻")),
            (("章末", "敌人", "债主", "秘密", "身后"), ("债主", "身后", "第三个人", "血红字迹", "下一句")),
        ]
        expected_groups = [aliases for triggers, aliases in concept_groups if any(trigger in outline_chapter_desc for trigger in triggers)]
        if expected_groups:
            matched_groups = [aliases for aliases in expected_groups if any(alias in content for alias in aliases)]
            match_ratio = len(matched_groups) / len(expected_groups)
            details["outline_keywords"] = len(expected_groups)
            details["matched_keywords"] = len(matched_groups)
            details["match_ratio"] = round(match_ratio, 2)
            if match_ratio < 0.6 and len(expected_groups) >= 3:
                issues.append(f"章节内容与大纲偏离较大（关键词匹配率 {match_ratio:.0%}）")
            passed = len(issues) == 0
            return ContentQualityCheck("outline_deviation", passed, issues, details)

        keywords = [w for w in re.split(r"[，。；：、\s]+", outline_chapter_desc) if len(w) >= 2]
        if not keywords:
            return ContentQualityCheck("outline_deviation", True, [], details)
        matched = [k for k in keywords if k in content]
        match_ratio = len(matched) / len(keywords)
        details["outline_keywords"] = len(keywords)
        details["matched_keywords"] = len(matched)
        details["match_ratio"] = round(match_ratio, 2)
        if match_ratio < 0.3 and len(keywords) >= 4:
            issues.append(f"章节内容与大纲偏离较大（关键词匹配率 {match_ratio:.0%}）")

        passed = len(issues) == 0
        return ContentQualityCheck("outline_deviation", passed, issues, details)

    # ------------------------------------------------------------------
    # Emotional arc
    # ------------------------------------------------------------------

    def _check_emotional_arc(self, content: str, llm_client: Any | None) -> ContentQualityCheck:
        """Assess emotional flow within chapter: flat, spiked, or natural."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) < 3:
            return ContentQualityCheck("emotional_arc", True, [], {"reason": "段落太少，无法评估"})

        positive_words = ["笑", "喜", "欢", "乐", "开心", "幸福", "温暖", "希望", "激动", "火光", "机会"]
        negative_words = ["哭", "怒", "悲", "恨", "痛苦", "绝望", "恐惧", "愤怒", "悲伤", "孤独", "害怕", "冷", "疼", "失去", "代价", "逼", "死", "毁", "债"]

        emotional_scores: list[float] = []
        for para in paragraphs:
            pos = sum(para.count(w) for w in positive_words)
            neg = sum(para.count(w) for w in negative_words)
            emotional_scores.append(pos - neg)

        variance = sum((s - sum(emotional_scores)/len(emotional_scores))**2 for s in emotional_scores) / len(emotional_scores)
        details["emotional_variance"] = round(variance, 2)

        if variance < 0.1:
            issues.append("情感曲线过于平坦，缺乏起伏")
            details["emotional_state"] = "flat"
        elif variance > 10:
            issues.append("情感波动过于剧烈，可能显得突兀")
            details["emotional_state"] = "spiked"
        else:
            details["emotional_state"] = "natural"

        passed = len(issues) == 0
        return ContentQualityCheck("emotional_arc", passed, issues, details)

    # ------------------------------------------------------------------
    # Web novel aesthetics
    # ------------------------------------------------------------------

    def _check_web_novel_aesthetics(self, content: str) -> ContentQualityCheck:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        first = paragraphs[0] if paragraphs else content[:120]
        ending = content[-160:]
        issues: list[str] = []
        details: dict[str, Any] = {}

        hook_markers = ["开场", "第一眼", "刚", "忽然", "砰", "血", "门", "警报", "危机", "冲", "雨", "冷"]
        conflict_markers = ["冲突", "逼", "拦", "威胁", "敌", "对手", "追", "夺", "不许", "代价", "毁掉"]
        stakes_markers = ["否则", "一旦", "代价", "失去", "死", "毁", "不能", "最后", "再也"]
        reversal_markers = ["却", "反而", "没想到", "真正", "原来", "突然", "背叛", "反转"]
        scene_markers = ["“", "”", "脚步", "呼吸", "雨声", "灯光", "金属", "烟气", "血腥", "寒意", "伤口"]
        ending_markers = ["下一刻", "门外", "身后", "屏幕上", "真正的", "还没结束", "新的", "抬头", "出现", "站在"]

        has_hook = any(marker in first for marker in hook_markers)
        has_conflict = any(marker in content for marker in conflict_markers)
        has_stakes = any(marker in content for marker in stakes_markers)
        has_reversal = any(marker in content for marker in reversal_markers)
        has_scene_prose = any(marker in content for marker in scene_markers)
        has_ending_hook = any(marker in ending for marker in ending_markers)

        details.update({
            "has_hook": has_hook,
            "has_conflict": has_conflict,
            "has_stakes": has_stakes,
            "has_reversal": has_reversal,
            "has_scene_prose": has_scene_prose,
            "has_ending_hook": has_ending_hook,
        })

        if not has_hook:
            issues.append("缺少开篇钩子：前段未形成即时场面、异常或冲突")
        if not has_conflict:
            issues.append("缺少明确冲突：主角没有被可见阻力逼出行动")
        if not has_stakes:
            issues.append("缺少明确赌注：读者看不出失败会失去什么")
        if not has_reversal:
            issues.append("缺少反转或信息揭示：中段压力没有发生变化")
        if not has_scene_prose:
            issues.append("场景化不足：需要用动作、对话和感官细节推进正文")
        if not has_ending_hook:
            issues.append("缺少章末钩子：结尾没有新危机、反转或未解问题")

        return ContentQualityCheck("web_novel_aesthetics", not issues, issues, details)

    def _check_production_artifacts(self, content: str) -> ContentQualityCheck:
        issues = find_production_artifact_issues(content)
        return ContentQualityCheck("production_artifacts", not issues, issues, {"issue_count": len(issues)})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_words_or_cjk_chars(text: str) -> int:
        words = re.findall(r"[A-Za-z0-9_]+", text)
        cjk_chars = re.findall(r"[一-鿿]", text)
        return len(words) + len(cjk_chars)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Simple sentence splitter on terminal punctuation."""
        parts = re.split(r"(?<=[。！？.!?])\s*", text)
        return [p for p in parts if p.strip()]

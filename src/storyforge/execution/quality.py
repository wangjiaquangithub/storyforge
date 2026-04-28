"""Content quality analysis for chapter review.

Provides heuristic checks that don't require an LLM: repetition,
continuity markers, and structural sanity.  Results feed into the
review asset alongside any LLM-generated feedback.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


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
                issues.append(f"Sentence repeated {count}x: {text[:60]}...")
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
                issues.append(f"Phrase repetition ratio {ratio:.1%} exceeds {self.repetition_threshold:.0%}")

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
        word_count = len(content.split())
        details["word_count"] = word_count
        if word_count < 100:
            issues.append(f"Chapter too short: {word_count} words")

        # Very long paragraphs (likely walls of text)
        long_paras = [p for p in paragraphs if len(p.split()) > self.max_paragraph_words]
        if long_paras:
            issues.append(f"{len(long_paras)} paragraph(s) exceed {self.max_paragraph_words} words")

        # Single-paragraph chapters
        if len(paragraphs) <= 1 and word_count > 200:
            issues.append("Chapter appears to be a single large block of text")

        passed = len(issues) == 0
        return ContentQualityCheck("structure", passed, issues, details)

    # ------------------------------------------------------------------
    # Pacing
    # ------------------------------------------------------------------

    def _check_pacing(self, content: str) -> ContentQualityCheck:
        """Estimate pacing via scene / dialogue markers."""
        issues: list[str] = []
        details: dict[str, Any] = {}

        word_count = len(content.split())
        sentences = self._split_sentences(content)
        dialogue_sentences = [s for s in sentences if '"' in s or '“' in s or '”' in s]
        dialogue_ratio = len(dialogue_sentences) / len(sentences) if sentences else 0
        details["dialogue_ratio"] = round(dialogue_ratio, 3)
        details["sentence_count"] = len(sentences)

        # Scene transition markers
        transition_words = ["meanwhile", "later", "the next", "hours later", "days later", "suddenly"]
        transitions_found = [w for w in transition_words if w.lower() in content.lower()]
        details["scene_transitions"] = len(transitions_found)

        # No dialogue at all in a substantial chapter
        if word_count > 300 and dialogue_ratio < 0.05:
            issues.append("Very low dialogue ratio — may be exposition-heavy")

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
                    f"Most character names from previous chapters absent ({len(dropped_names)}/{len(previous_names)})"
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
        word_count = len(content.split())
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

        keywords = [w for w in re.findall(r"[一-鿿]{2,}", outline_chapter_desc) if len(w) > 1]
        if not keywords:
            return ContentQualityCheck("outline_deviation", True, [], details)

        matched = [k for k in keywords if k in content]
        match_ratio = len(matched) / len(keywords) if keywords else 0
        details["outline_keywords"] = len(keywords)
        details["matched_keywords"] = len(matched)
        details["match_ratio"] = round(match_ratio, 2)

        if match_ratio < 0.3 and len(keywords) > 5:
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

        positive_words = ["笑", "喜", "欢", "乐", "开心", "幸福", "温暖", "希望", "激动", "幸福"]
        negative_words = ["哭", "怒", "悲", "恨", "痛苦", "绝望", "恐惧", "愤怒", "悲伤", "孤独"]

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
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Simple sentence splitter on terminal punctuation."""
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p for p in parts if p.strip()]

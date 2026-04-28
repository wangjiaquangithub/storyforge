"""Three-layer rule engine: Universal -> Genre -> Custom."""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class RuleLayer(str, Enum):
    universal = "universal"
    genre = "genre"
    custom = "custom"


class Rule(BaseModel):
    rule_id: str = Field(default_factory=lambda: f"rule_{uuid4().hex[:12]}")
    layer: RuleLayer
    genre: str | None = None
    project_id: str | None = None
    name: str
    description: str
    enabled: bool = True


def seed_universal_rules() -> list[Rule]:
    return [
        Rule(layer=RuleLayer.universal, name="章节长度", description="章节长度应在 1500-3000 字之间"),
        Rule(layer=RuleLayer.universal, name="无作者引用", description="不得出现作者名或现实世界引用"),
        Rule(layer=RuleLayer.universal, name="无现代术语", description="不得出现明显的现代网络用语或术语（除非设定允许）"),
    ]


def seed_genre_rules() -> list[Rule]:
    return [
        Rule(layer=RuleLayer.genre, genre="玄幻", name="力量体系一致", description="力量体系需前后一致，不得随意突破境界"),
        Rule(layer=RuleLayer.genre, genre="玄幻", name="修炼逻辑", description="修炼进度需有合理铺垫，不得无故跳跃"),
        Rule(layer=RuleLayer.genre, genre="言情", name="感情线渐进", description="感情线需循序渐进，不得突兀表白"),
        Rule(layer=RuleLayer.genre, genre="仙侠", name="道法设定一致", description="道法和法宝设定需前后一致"),
        Rule(layer=RuleLayer.genre, genre="科幻", name="科技逻辑", description="科技设定需自洽，不得出现违反物理定律的情节"),
    ]

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Protocol

from storyforge.domain.models import Asset, AssetType, EventRecord, Project, ProjectExecutionClaim, RuntimeAuditRecord, TaskRecord
from storyforge.execution.rules import Rule


class StoryForgeStore(Protocol):
    def create_project(self, project: Project) -> Project: ...
    def save_project(self, project: Project) -> Project: ...
    def get_project(self, project_id: str) -> Project | None: ...
    def list_projects(self) -> list[Project]: ...
    def save_asset(self, asset: Asset, *, expected_version: int | None = None, lineage_origin_id: str | None = None) -> Asset: ...
    def list_assets(self, project_id: str, asset_type: AssetType | None = None, *, branch: str | None = None) -> list[Asset]: ...
    def get_latest_asset(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> Asset | None: ...
    def get_asset_by_id(self, asset_id: str) -> Asset | None: ...
    def list_asset_versions(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> list[Asset]: ...
    def create_task(self, task: TaskRecord) -> TaskRecord: ...
    def save_task(self, task: TaskRecord) -> TaskRecord: ...
    def get_task(self, task_id: str) -> TaskRecord | None: ...
    def list_tasks(self, project_id: str | None = None, *, branch: str | None = None) -> list[TaskRecord]: ...
    def append_event(self, event: EventRecord) -> EventRecord: ...
    def list_events(self, task_id: str) -> list[EventRecord]: ...
    def save_project_claim(self, claim: ProjectExecutionClaim) -> ProjectExecutionClaim: ...
    def get_project_claim(self, project_id: str) -> ProjectExecutionClaim | None: ...
    def list_project_claims(self) -> list[ProjectExecutionClaim]: ...
    def append_runtime_audit(self, record: RuntimeAuditRecord) -> RuntimeAuditRecord: ...
    def list_runtime_audits(self, project_id: str | None = None) -> list[RuntimeAuditRecord]: ...
    def delete_project_claim(self, project_id: str) -> None: ...
    def delete_asset(self, asset_id: str) -> Asset: ...
    def list_trash(self, project_id: str) -> list[Asset]: ...
    def restore_asset(self, asset_id: str) -> Asset: ...
    def save_rule(self, rule: Rule) -> Rule: ...
    def get_rule(self, rule_id: str) -> Rule | None: ...
    def get_rules(self, *, layer: str | None = None, genre: str | None = None, project_id: str | None = None, include_disabled: bool = False) -> list[Rule]: ...
    def delete_rule(self, rule_id: str) -> None: ...
    def seed_universal_rules(self) -> list[Rule]: ...
    def close(self) -> None: ...


class InMemoryStoryForgeStore:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.assets: dict[str, list[Asset]] = defaultdict(list)
        self.tasks: dict[str, TaskRecord] = {}
        self.project_tasks: dict[str, list[str]] = defaultdict(list)
        self.events: dict[str, list[EventRecord]] = defaultdict(list)
        self.project_claims: dict[str, ProjectExecutionClaim] = {}
        self.runtime_audits: list[RuntimeAuditRecord] = []
        self.rules: dict[str, Rule] = {}
        self._universal_seeded: bool = False

    def create_project(self, project: Project) -> Project:
        self.projects[project.project_id] = project
        return project

    def save_project(self, project: Project) -> Project:
        self.projects[project.project_id] = project
        return project

    def get_project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    def list_projects(self) -> list[Project]:
        return list(self.projects.values())

    def save_asset(self, asset: Asset, *, expected_version: int | None = None, lineage_origin_id: str | None = None) -> Asset:
        matching_assets = [existing for existing in self.assets[asset.project_id] if existing.asset_type == asset.asset_type]
        latest_version = 0
        if lineage_origin_id is not None:
            for existing in matching_assets:
                if existing.branch == asset.branch and (existing.asset_id == lineage_origin_id or existing.structured_data.get("origin_asset_id") == lineage_origin_id):
                    latest_version = max(latest_version, existing.version)
        elif matching_assets:
            latest_version = max(existing.version for existing in matching_assets)
        if expected_version is not None and latest_version != expected_version:
            raise ValueError("A newer asset version already exists")
        asset.version = max((existing.version for existing in matching_assets), default=0) + 1
        self.assets[asset.project_id].append(asset)
        return asset

    def list_assets(self, project_id: str, asset_type: AssetType | None = None, *, branch: str | None = None) -> list[Asset]:
        assets = list(self.assets[project_id])
        if asset_type is not None:
            assets = [asset for asset in assets if asset.asset_type == asset_type]
        if branch is not None:
            assets = [asset for asset in assets if asset.branch == branch]
        return assets

    def get_latest_asset(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> Asset | None:
        assets = self.list_assets(project_id, asset_type, branch=branch)
        if not assets:
            return None
        return assets[-1]

    def get_asset_by_id(self, asset_id: str) -> Asset | None:
        best = None
        for assets in self.assets.values():
            for asset in assets:
                if asset.asset_id == asset_id:
                    if best is None or asset.version > best.version:
                        best = asset
        return best

    def list_asset_versions(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> list[Asset]:
        return self.list_assets(project_id, asset_type, branch=branch)

    def create_task(self, task: TaskRecord) -> TaskRecord:
        self.tasks[task.task_id] = task
        self.project_tasks[task.project_id].append(task.task_id)
        return task

    def save_task(self, task: TaskRecord) -> TaskRecord:
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def list_tasks(self, project_id: str | None = None, *, branch: str | None = None) -> list[TaskRecord]:
        if project_id is None:
            tasks = list(self.tasks.values())
        else:
            tasks = [self.tasks[task_id] for task_id in self.project_tasks[project_id]]
        if branch is not None:
            tasks = [task for task in tasks if task.branch == branch]
        return tasks

    def append_event(self, event: EventRecord) -> EventRecord:
        self.events[event.task_id].append(event)
        return event

    def list_events(self, task_id: str) -> list[EventRecord]:
        return list(self.events[task_id])

    def save_project_claim(self, claim: ProjectExecutionClaim) -> ProjectExecutionClaim:
        self.project_claims[claim.project_id] = claim
        return claim

    def get_project_claim(self, project_id: str) -> ProjectExecutionClaim | None:
        return self.project_claims.get(project_id)

    def list_project_claims(self) -> list[ProjectExecutionClaim]:
        return list(self.project_claims.values())

    def append_runtime_audit(self, record: RuntimeAuditRecord) -> RuntimeAuditRecord:
        self.runtime_audits.append(record)
        return record

    def list_runtime_audits(self, project_id: str | None = None) -> list[RuntimeAuditRecord]:
        if project_id is None:
            return list(self.runtime_audits)
        return [record for record in self.runtime_audits if record.project_id == project_id]

    def delete_project_claim(self, project_id: str) -> None:
        self.project_claims.pop(project_id, None)

    def delete_asset(self, asset_id: str) -> Asset:
        for assets in self.assets.values():
            for asset in assets:
                if asset.asset_id == asset_id:
                    asset.is_deleted = True
                    asset.deleted_at = datetime.now(timezone.utc)
                    return asset
        raise ValueError(f"Asset {asset_id} not found")

    def list_trash(self, project_id: str) -> list[Asset]:
        return [asset for asset in self.assets.get(project_id, []) if asset.is_deleted]

    def restore_asset(self, asset_id: str) -> Asset:
        for assets in self.assets.values():
            for asset in assets:
                if asset.asset_id == asset_id:
                    asset.is_deleted = False
                    asset.deleted_at = None
                    return asset
        raise ValueError(f"Asset {asset_id} not found")

    def close(self) -> None:
        return None

    def save_rule(self, rule: Rule) -> Rule:
        self.rules[rule.rule_id] = rule
        return rule

    def get_rule(self, rule_id: str) -> Rule | None:
        return self.rules.get(rule_id)

    def get_rules(self, *, layer: str | None = None, genre: str | None = None, project_id: str | None = None, include_disabled: bool = False) -> list[Rule]:
        results = list(self.rules.values())
        if layer:
            results = [r for r in results if r.layer.value == layer]
        if genre:
            results = [r for r in results if r.genre == genre]
        if project_id:
            results = [r for r in results if r.project_id == project_id]
        if not include_disabled:
            results = [r for r in results if r.enabled]
        return results

    def delete_rule(self, rule_id: str) -> None:
        self.rules.pop(rule_id, None)

    def seed_universal_rules(self) -> list[Rule]:
        if not self._universal_seeded:
            from storyforge.execution.rules import seed_universal_rules
            for rule in seed_universal_rules():
                self.rules[rule.rule_id] = rule
            self._universal_seeded = True
        return [r for r in self.rules.values() if r.layer.value == "universal" and r.enabled]

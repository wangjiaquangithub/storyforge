from __future__ import annotations

import threading
from datetime import timedelta
from queue import Empty, Queue
from typing import Final
from uuid import uuid4

from storyforge.domain.models import ProjectExecutionClaim, RuntimeAuditRecord, utc_now
from storyforge.execution.workflow import ClosedLoopService

_STOP: Final = object()
_DEFAULT_LEASE_SECONDS: Final = 30
_MIN_HEARTBEAT_INTERVAL_SECONDS: Final = 0.1


class WorkerRuntime:
    def __init__(self, workflow: ClosedLoopService) -> None:
        self.workflow = workflow
        self.worker_id = f"worker_{uuid4().hex[:12]}"
        self._queue: Queue[object] = Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._queued_projects: set[str] = set()
        self._lease_seconds = _DEFAULT_LEASE_SECONDS
        self._heartbeat_interval_seconds = max(_MIN_HEARTBEAT_INTERVAL_SECONDS, self._lease_seconds / 3)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="storyforge-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def enqueue_project(self, project_id: str) -> None:
        with self._lock:
            if project_id in self._queued_projects:
                return
            self._queued_projects.add(project_id)
        self._queue.put(project_id)

    def process_project_now(self, project_id: str, *, branch: str | None = None) -> int:
        if not self._acquire_project_claim(project_id):
            return 0
        heartbeat = self._start_claim_heartbeat(project_id)
        try:
            return self.workflow.drain(project_id, branch=branch).processed_count
        finally:
            heartbeat.set()
            self._release_owned_project_claim(project_id)

    def get_project_claim(self, project_id: str) -> ProjectExecutionClaim | None:
        return self.workflow.store.get_project_claim(project_id)

    def list_project_claims(self) -> list[ProjectExecutionClaim]:
        return self.workflow.store.list_project_claims()

    def list_runtime_audits(self, project_id: str | None = None) -> list[RuntimeAuditRecord]:
        return self.workflow.store.list_runtime_audits(project_id)

    def _release_owned_project_claim(self, project_id: str) -> None:
        claim = self.workflow.store.get_project_claim(project_id)
        if claim is not None and claim.worker_id == self.worker_id:
            self.workflow.store.delete_project_claim(project_id)

    def release_project_claim(self, project_id: str, *, force: bool = False) -> bool:
        claim = self.workflow.store.get_project_claim(project_id)
        if claim is None:
            return False
        if not force and claim.worker_id != self.worker_id:
            return False
        stale = claim.lease_expires_at <= utc_now()
        self.workflow.store.delete_project_claim(project_id)
        self.workflow.store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=project_id,
                actor_worker_id=self.worker_id,
                claim_worker_id=claim.worker_id,
                forced=force,
                stale=stale,
                message="Released project execution claim",
            )
        )
        return True

    def release_stale_project_claims(self) -> list[str]:
        now = utc_now()
        released: list[str] = []
        for claim in self.workflow.store.list_project_claims():
            if claim.lease_expires_at <= now:
                self.workflow.store.delete_project_claim(claim.project_id)
                self.workflow.store.append_runtime_audit(
                    RuntimeAuditRecord(
                        action="release_stale_project_claim",
                        project_id=claim.project_id,
                        actor_worker_id=self.worker_id,
                        claim_worker_id=claim.worker_id,
                        forced=True,
                        stale=True,
                        message="Released stale project execution claim",
                    )
                )
                released.append(claim.project_id)
        return released

    def _acquire_project_claim(self, project_id: str) -> bool:
        with self._lock:
            existing = self.workflow.store.get_project_claim(project_id)
            now = utc_now()
            if existing is not None and existing.worker_id != self.worker_id and existing.lease_expires_at > now:
                return False
            claim = ProjectExecutionClaim(
                project_id=project_id,
                worker_id=self.worker_id,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                lease_duration_seconds=float(self._lease_seconds),
                heartbeat_interval_seconds=float(self._heartbeat_interval_seconds),
                last_heartbeat_at=now,
            )
            self.workflow.store.save_project_claim(claim)
            return True

    def _renew_project_claim(self, project_id: str) -> bool:
        with self._lock:
            claim = self.workflow.store.get_project_claim(project_id)
            if claim is None or claim.worker_id != self.worker_id:
                return False
            now = utc_now()
            claim.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            claim.last_heartbeat_at = now
            claim.lease_duration_seconds = float(self._lease_seconds)
            claim.heartbeat_interval_seconds = float(self._heartbeat_interval_seconds)
            self.workflow.store.save_project_claim(claim)
            return True

    def _record_claim_renewal_loss(self, project_id: str) -> None:
        with self._lock:
            claim = self.workflow.store.get_project_claim(project_id)
            if claim is None:
                self.workflow.store.append_runtime_audit(
                    RuntimeAuditRecord(
                        action="claim_heartbeat_lost",
                        project_id=project_id,
                        actor_worker_id=self.worker_id,
                        claim_worker_id=None,
                        forced=False,
                        stale=False,
                        message="Stopped claim heartbeat because the project claim no longer exists",
                    )
                )
                return
            if claim.worker_id != self.worker_id:
                stale = claim.lease_expires_at <= utc_now()
                self.workflow.store.append_runtime_audit(
                    RuntimeAuditRecord(
                        action="claim_heartbeat_lost",
                        project_id=project_id,
                        actor_worker_id=self.worker_id,
                        claim_worker_id=claim.worker_id,
                        forced=False,
                        stale=stale,
                        message="Stopped claim heartbeat because project ownership moved to another worker",
                    )
                )

    def _start_claim_heartbeat(self, project_id: str) -> threading.Event:
        stop_event = threading.Event()

        def beat() -> None:
            while not stop_event.wait(self._heartbeat_interval_seconds):
                if not self._renew_project_claim(project_id):
                    if not stop_event.is_set():
                        self._record_claim_renewal_loss(project_id)
                    return

        threading.Thread(
            target=beat,
            name=f"storyforge-claim-heartbeat-{project_id}",
            daemon=True,
        ).start()
        return stop_event

    def _loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.2)
            except Empty:
                continue
            if item is _STOP:
                break
            if isinstance(item, str):
                try:
                    if self._acquire_project_claim(item):
                        heartbeat = self._start_claim_heartbeat(item)
                        try:
                            self.workflow.drain(item)
                        finally:
                            heartbeat.set()
                            self._release_owned_project_claim(item)
                finally:
                    with self._lock:
                        self._queued_projects.discard(item)

"""
Thread-safe Asynchronous Background Job Queue with Real-time Progress Tracking.
Prevents web timeouts and server freezes during long-running stream slicing and video rendering.
"""

import asyncio
from dataclasses import dataclass, asdict, field
import logging
import threading
import time
from typing import Callable, Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    title: str
    status: str = "pending"  # "pending", "running", "completed", "failed"
    progress: float = 0.0     # 0.0 to 100.0
    stage: str = "Initialized"
    details: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class JobQueue:
    """Singleton background job coordinator."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._init_queue()
        return cls._instance

    def _init_queue(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def create_job(self, job_type: str, title: str, details: Optional[dict] = None) -> JobRecord:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        now = time.time()
        record = JobRecord(
            job_id=job_id,
            job_type=job_type,
            title=title,
            details=details or {},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = record
            self._subscribers[job_id] = []
        logger.info(f"Created job {job_id} ({job_type}): {title}")
        return record

    def update_progress(
        self,
        job_id: str,
        progress: float,
        stage: str,
        details: Optional[dict] = None,
    ):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0.0, min(100.0, float(progress)))
            job.stage = stage
            job.updated_at = time.time()
            if job.status == "pending":
                job.status = "running"
            if details:
                job.details.update(details)
            subscribers = list(self._subscribers.get(job_id, []))

        # Notify event stream subscribers
        msg = job.to_dict()
        for q in subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    def mark_completed(self, job_id: str, result: dict):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "completed"
            job.progress = 100.0
            job.stage = "Completed"
            job.result = result
            job.updated_at = time.time()
            subscribers = list(self._subscribers.get(job_id, []))

        msg = job.to_dict()
        for q in subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                pass
        logger.info(f"Job {job_id} completed successfully")

    def mark_failed(self, job_id: str, error_message: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "failed"
            job.stage = "Failed"
            job.error = error_message
            job.updated_at = time.time()
            subscribers = list(self._subscribers.get(job_id, []))

        msg = job.to_dict()
        for q in subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                pass
        logger.error(f"Job {job_id} failed: {error_message}")

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            sorted_jobs = sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )
            return [j.to_dict() for j in sorted_jobs[:limit]]

    def run_in_background(self, target: Callable, *args, **kwargs) -> threading.Thread:
        """Launch worker function in separate thread."""
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread

    def register_subscriber(self, job_id: str, queue: asyncio.Queue):
        with self._lock:
            if job_id not in self._subscribers:
                self._subscribers[job_id] = []
            self._subscribers[job_id].append(queue)

    def remove_subscriber(self, job_id: str, queue: asyncio.Queue):
        with self._lock:
            if job_id in self._subscribers and queue in self._subscribers[job_id]:
                self._subscribers[job_id].remove(queue)


job_queue = JobQueue()

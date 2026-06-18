"""Identity verification queue with bulk actions and audit logging."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from collections import deque
import json

logger = logging.getLogger(__name__)


class VerificationPriority(Enum):
    """Priority levels for verification tasks."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class VerificationAction(Enum):
    """Types of verification actions."""

    CONFIRM = "confirm"
    REJECT = "reject"
    MERGE = "merge"
    SPLIT = "split"
    RENAME = "rename"


@dataclass
class VerificationTask:
    """A single verification task."""

    task_id: str
    identity_id: Optional[str]
    face_ids: list[str]
    action: VerificationAction
    priority: VerificationPriority
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"  # pending, processing, completed, failed


@dataclass
class AuditLogEntry:
    """An entry in the audit log."""

    timestamp: datetime
    action: VerificationAction
    identity_id: Optional[str]
    face_ids: list[str]
    user: str
    details: dict = field(default_factory=dict)
    undone: bool = False


class VerificationQueue:
    """
    Priority queue for identity verification tasks.

    Features:
    - Priority-based task ordering
    - Bulk actions (merge, split multiple identities)
    - Undo stack (last 10 actions)
    - Audit logging
    """

    def __init__(self, max_undo_stack: int = 10):
        """
        Initialize the verification queue.

        Args:
            max_undo_stack: Maximum number of actions to keep in undo stack.
        """
        self.max_undo_stack = max_undo_stack
        self._queue: deque[VerificationTask] = deque()
        self._undo_stack: deque[AuditLogEntry] = deque(maxlen=max_undo_stack)
        self._audit_log: list[AuditLogEntry] = []
        self._processing = False
        self._task_callbacks: dict[str, Callable] = {}
        self._lock = asyncio.Lock()

    async def add_task(
        self,
        action: VerificationAction,
        face_ids: list[str],
        identity_id: Optional[str] = None,
        priority: VerificationPriority = VerificationPriority.MEDIUM,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Add a verification task to the queue.

        Args:
            action: Type of verification action.
            face_ids: List of face IDs involved.
            identity_id: Optional identity ID.
            priority: Task priority level.
            metadata: Optional additional metadata.

        Returns:
            Task ID for tracking.
        """
        task_id = f"task_{datetime.now(timezone.utc).timestamp()}_{len(self._queue)}"
        
        task = VerificationTask(
            task_id=task_id,
            identity_id=identity_id,
            face_ids=face_ids,
            action=action,
            priority=priority,
            metadata=metadata or {},
        )

        async with self._lock:
            # Insert based on priority (higher priority first)
            inserted = False
            for i, existing_task in enumerate(self._queue):
                if task.priority.value > existing_task.priority.value:
                    self._queue.insert(i, task)
                    inserted = True
                    break
            if not inserted:
                self._queue.append(task)

        logger.info(f"Added verification task {task_id}: {action.value} for {len(face_ids)} faces")
        return task_id

    async def process_queue(
        self,
        handler: Callable[[VerificationTask], bool],
        batch_size: int = 10,
    ) -> int:
        """
        Process tasks from the queue.

        Args:
            handler: Async function to handle each task. Returns True if successful.
            batch_size: Number of tasks to process in one batch.

        Returns:
            Number of tasks processed.
        """
        if self._processing:
            logger.warning("Queue is already being processed")
            return 0

        self._processing = True
        processed_count = 0

        try:
            while len(self._queue) > 0 and processed_count < batch_size:
                async with self._lock:
                    task = self._queue.popleft()

                task.status = "processing"
                logger.debug(f"Processing task {task.task_id}")

                try:
                    success = await handler(task)
                    task.status = "completed" if success else "failed"

                    if success:
                        # Log to audit trail
                        await self._log_action(
                            action=task.action,
                            identity_id=task.identity_id,
                            face_ids=task.face_ids,
                            user="system",
                            details=task.metadata,
                        )
                        processed_count += 1
                    else:
                        # Re-queue with lower priority
                        task.priority = VerificationPriority(
                            max(VerificationPriority.LOW.value, task.priority.value - 1)
                        )
                        async with self._lock:
                            self._queue.append(task)

                except Exception as e:
                    logger.error(f"Task {task.task_id} failed: {e}")
                    task.status = "failed"
                    # Re-queue with lower priority
                    task.priority = VerificationPriority.LOW
                    async with self._lock:
                        self._queue.append(task)

        finally:
            self._processing = False

        return processed_count

    async def _log_action(
        self,
        action: VerificationAction,
        identity_id: Optional[str],
        face_ids: list[str],
        user: str,
        details: Optional[dict] = None,
    ):
        """
        Log an action to the audit trail.

        Args:
            action: Action performed.
            identity_id: Identity affected.
            face_ids: Faces affected.
            user: User who performed the action.
            details: Additional details.
        """
        entry = AuditLogEntry(
            timestamp=datetime.now(timezone.utc),
            action=action,
            identity_id=identity_id,
            face_ids=face_ids,
            user=user,
            details=details or {},
        )

        self._audit_log.append(entry)
        self._undo_stack.append(entry)

        logger.info(f"Audit log: {user} performed {action.value} on identity {identity_id}")

    async def undo_last_action(self) -> Optional[AuditLogEntry]:
        """
        Undo the last verification action.

        Returns:
            The undone audit log entry, or None if nothing to undo.
        """
        if not self._undo_stack:
            logger.warning("No actions to undo")
            return None

        entry = self._undo_stack.pop()
        entry.undone = True

        logger.info(f"Undone action: {entry.action.value} by {entry.user}")
        return entry

    def get_audit_log(
        self,
        limit: int = 100,
        offset: int = 0,
        identity_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Retrieve audit log entries.

        Args:
            limit: Maximum number of entries to return.
            offset: Offset for pagination.
            identity_id: Filter by identity ID.

        Returns:
            List of audit log entries as dictionaries.
        """
        filtered = self._audit_log
        if identity_id:
            filtered = [e for e in filtered if e.identity_id == identity_id]

        # Sort by timestamp descending
        sorted_entries = sorted(filtered, key=lambda x: x.timestamp, reverse=True)
        paginated = sorted_entries[offset : offset + limit]

        return [
            {
                "timestamp": entry.timestamp.isoformat(),
                "action": entry.action.value,
                "identity_id": entry.identity_id,
                "face_ids": entry.face_ids,
                "user": entry.user,
                "details": entry.details,
                "undone": entry.undone,
            }
            for entry in paginated
        ]

    def get_queue_status(self) -> dict:
        """
        Get current queue status.

        Returns:
            Dictionary with queue statistics.
        """
        priority_counts = {p.name: 0 for p in VerificationPriority}
        for task in self._queue:
            priority_counts[task.priority.name] += 1

        return {
            "pending_tasks": len(self._queue),
            "priority_breakdown": priority_counts,
            "processing": self._processing,
            "undo_stack_size": len(self._undo_stack),
            "total_audit_entries": len(self._audit_log),
        }

    async def bulk_merge(
        self,
        source_identity_ids: list[str],
        target_identity_id: str,
        priority: VerificationPriority = VerificationPriority.HIGH,
    ) -> str:
        """
        Create a bulk merge task.

        Args:
            source_identity_ids: Identities to merge into target.
            target_identity_id: Target identity to merge into.
            priority: Task priority.

        Returns:
            Task ID.
        """
        return await self.add_task(
            action=VerificationAction.MERGE,
            face_ids=[],  # Will be populated during processing
            identity_id=target_identity_id,
            priority=priority,
            metadata={"source_ids": source_identity_ids},
        )

    async def bulk_split(
        self,
        identity_id: str,
        face_ids: list[str],
        new_identity_name: Optional[str] = None,
        priority: VerificationPriority = VerificationPriority.MEDIUM,
    ) -> str:
        """
        Create a bulk split task.

        Args:
            identity_id: Identity to split from.
            face_ids: Faces to move to new identity.
            new_identity_name: Optional name for new identity.
            priority: Task priority.

        Returns:
            Task ID.
        """
        return await self.add_task(
            action=VerificationAction.SPLIT,
            face_ids=face_ids,
            identity_id=identity_id,
            priority=priority,
            metadata={"new_identity_name": new_identity_name},
        )

    async def confirm_face(
        self,
        identity_id: str,
        face_id: str,
        priority: VerificationPriority = VerificationPriority.HIGH,
    ) -> str:
        """
        Confirm a face belongs to an identity.

        Args:
            identity_id: Identity to confirm against.
            face_id: Face to confirm.
            priority: Task priority.

        Returns:
            Task ID.
        """
        return await self.add_task(
            action=VerificationAction.CONFIRM,
            face_ids=[face_id],
            identity_id=identity_id,
            priority=priority,
        )

    async def reject_face(
        self,
        identity_id: str,
        face_id: str,
        priority: VerificationPriority = VerificationPriority.HIGH,
    ) -> str:
        """
        Reject a face from an identity.

        Args:
            identity_id: Identity to remove from.
            face_id: Face to reject.
            priority: Task priority.

        Returns:
            Task ID.
        """
        return await self.add_task(
            action=VerificationAction.REJECT,
            face_ids=[face_id],
            identity_id=identity_id,
            priority=priority,
        )

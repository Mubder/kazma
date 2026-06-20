"""Division Sandbox — Isolates data and operations between divisions.

Ensures that operations execute within the correct division context,
with audit logging and cross-division access controls.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from kazma_core.audit_logger import AuditLogger
from kazma_core.rbac import RBACEngine, PermissionResult

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of an operation executed within a division sandbox."""

    success: bool
    division: str
    user_id: str
    result: Any = None
    error: str = ""
    audit_entry_id: str = ""


@dataclass
class CrossDivisionRequest:
    """A request to access a cross-division resource."""

    id: str
    user_id: str
    source_division: str
    target_division: str
    resource: str
    reason: str
    status: str = "pending"  # "pending" | "approved" | "denied"
    created_at: str = ""
    resolved_at: str = ""
    resolved_by: str = ""


class DivisionSandbox:
    """Isolates data and operations between divisions.

    Every operation is executed within a division context. The sandbox
    verifies user access, sets the division scope, and logs the action.

    Args:
        rbac: The RBAC engine for permission checks.
        audit_logger: Optional audit logger for recording access.
    """

    def __init__(
        self,
        rbac: RBACEngine,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.rbac = rbac
        self.audit = audit_logger or AuditLogger()
        self._cross_division_requests: dict[str, CrossDivisionRequest] = {}

    async def execute_in_sandbox(
        self,
        user_id: str,
        division: str,
        operation: Callable,
        resource: str = "*",
        action: str = "read",
        *args: Any,
        **kwargs: Any,
    ) -> SandboxResult:
        """Execute an operation within a division sandbox.

        1. Verify user has access to division
        2. Check permission for the specific operation
        3. Set division context (isolate data access)
        4. Execute operation
        5. Audit log the action

        Args:
            user_id: The user performing the operation.
            division: The division context.
            operation: The async callable to execute.
            resource: The resource being accessed.
            action: The action being performed.
            *args: Positional arguments to pass to the operation.
            **kwargs: Keyword arguments to pass to the operation.

        Returns:
            SandboxResult with success status and operation result.
        """
        # 1. Verify user has access to division
        if not await self.rbac.is_user_in_division(user_id, division):
            audit = await self.audit.log_access_attempt(
                user_id=user_id,
                division=division,
                resource=resource,
                action=action,
                result="denied",
                reason=f"User '{user_id}' not in division '{division}'",
            )
            return SandboxResult(
                success=False,
                division=division,
                user_id=user_id,
                error=f"Access denied: user not in division '{division}'",
                audit_entry_id=audit.id,
            )

        # 2. Check specific permission
        perm = await self.rbac.check_permission(user_id, division, resource, action)
        if not perm.allowed:
            audit = await self.audit.log_access_attempt(
                user_id=user_id,
                division=division,
                resource=resource,
                action=action,
                result="denied",
                reason=perm.reason,
            )
            return SandboxResult(
                success=False,
                division=division,
                user_id=user_id,
                error=perm.reason,
                audit_entry_id=audit.id,
            )

        # 3. Check if approval is required for sensitive operations
        if perm.requires_approval:
            audit = await self.audit.log_access_attempt(
                user_id=user_id,
                division=division,
                resource=resource,
                action=action,
                result="pending_approval",
                reason="Sensitive resource requires admin approval",
            )
            return SandboxResult(
                success=False,
                division=division,
                user_id=user_id,
                error="Requires admin approval for sensitive resource",
                audit_entry_id=audit.id,
            )

        # 4. Execute operation within division context
        try:
            # Bind division context to kwargs for the operation
            kwargs["_division_context"] = division
            kwargs["_user_id_context"] = user_id
            result = await operation(*args, **kwargs)

            # 5. Audit log success
            audit = await self.audit.log_access_attempt(
                user_id=user_id,
                division=division,
                resource=resource,
                action=action,
                result="allowed",
            )

            return SandboxResult(
                success=True,
                division=division,
                user_id=user_id,
                result=result,
                audit_entry_id=audit.id,
            )

        except Exception as exc:
            audit = await self.audit.log_access_attempt(
                user_id=user_id,
                division=division,
                resource=resource,
                action=action,
                result="denied",
                reason=f"Operation failed: {exc}",
            )
            return SandboxResult(
                success=False,
                division=division,
                user_id=user_id,
                error=f"Operation failed: {exc}",
                audit_entry_id=audit.id,
            )

    async def request_cross_division_access(
        self,
        user_id: str,
        source_division: str,
        target_division: str,
        resource: str,
        reason: str,
    ) -> CrossDivisionRequest:
        """Request access to a cross-division resource.

        Creates an authorization request that requires explicit approval
        from admins of both the source and target divisions.

        Args:
            user_id: The user requesting access.
            source_division: The user's home division.
            target_division: The division whose resources are being requested.
            resource: The specific resource being requested.
            reason: Justification for the cross-division access.

        Returns:
            CrossDivisionRequest with status 'pending'.
        """
        # Validate divisions exist
        if source_division not in self.rbac.divisions:
            raise ValueError(f"Unknown source division: {source_division}")
        if target_division not in self.rbac.divisions:
            raise ValueError(f"Unknown target division: {target_division}")

        # Verify user is in source division
        if not await self.rbac.is_user_in_division(user_id, source_division):
            raise PermissionError(f"User '{user_id}' is not in source division '{source_division}'")

        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        request = CrossDivisionRequest(
            id=request_id,
            user_id=user_id,
            source_division=source_division,
            target_division=target_division,
            resource=resource,
            reason=reason,
            status="pending",
            created_at=now,
        )

        self._cross_division_requests[request_id] = request

        # Log the cross-division access request
        await self.audit.log_access_attempt(
            user_id=user_id,
            division=target_division,
            resource=resource,
            action="cross_division_request",
            result="pending_approval",
            reason=f"Cross-division access from {source_division}: {reason}",
            metadata={"request_id": request_id, "source_division": source_division},
        )

        logger.info(
            "Cross-division request %s: %s -> %s for %s",
            request_id[:8], source_division, target_division, resource,
        )
        return request

    async def get_cross_division_request(self, request_id: str) -> CrossDivisionRequest | None:
        """Get a cross-division request by ID."""
        return self._cross_division_requests.get(request_id)

    async def get_pending_requests(self, division: str | None = None) -> list[CrossDivisionRequest]:
        """Get all pending cross-division requests, optionally filtered by target division."""
        requests = [
            r for r in self._cross_division_requests.values()
            if r.status == "pending"
            and (division is None or r.target_division == division)
        ]
        return requests

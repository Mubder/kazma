"""Authorization Flow — Manages authorization requests and approvals.

Handles the full lifecycle of cross-division access requests:
creation, notification, approval/denial, and temporary access grants.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from kazma_core.audit_logger import AuditLogger
from kazma_core.rbac import RBACEngine

logger = logging.getLogger(__name__)


@dataclass
class AuthorizationRequest:
    """A request for cross-division access."""

    id: str
    user_id: str
    source_division: str
    target_division: str
    resource: str
    justification: str
    status: str = "pending"  # "pending" | "approved" | "denied" | "expired"
    created_at: str = ""
    resolved_at: str = ""
    approver_id: str = ""
    duration_hours: int = 24
    expires_at: str = ""
    denial_reason: str = ""
    notified_admins: list[str] = field(default_factory=list)


@dataclass
class ApprovalResult:
    """Result of an approval decision."""

    success: bool
    request_id: str
    message: str = ""
    expires_at: str = ""


@dataclass
class DenialResult:
    """Result of a denial decision."""

    success: bool
    request_id: str
    message: str = ""


class AuthorizationFlow:
    """Manages authorization requests and approvals.

    Handles the lifecycle of cross-division access requests, from creation
    through approval/denial to temporary access grant and expiration.

    Args:
        rbac: The RBAC engine for role management.
        audit_logger: Optional audit logger for recording decisions.
        max_approval_duration_hours: Maximum hours for a temporary access grant.
    """

    def __init__(
        self,
        rbac: RBACEngine,
        audit_logger: AuditLogger | None = None,
        max_approval_duration_hours: int = 24,
    ) -> None:
        self.rbac = rbac
        self.audit = audit_logger or AuditLogger()
        self.max_approval_duration_hours = max_approval_duration_hours
        self._requests: dict[str, AuthorizationRequest] = {}

    async def request_access(
        self,
        user_id: str,
        source_division: str,
        target_division: str,
        resource: str,
        justification: str,
        duration_hours: int = 24,
    ) -> AuthorizationRequest:
        """Request access to a cross-division resource.

        1. Create authorization request
        2. Notify division admins
        3. Wait for approval (with timeout)
        4. Grant temporary access if approved

        Args:
            user_id: The user requesting access.
            source_division: The user's home division.
            target_division: The division whose resources are requested.
            resource: The specific resource being requested.
            justification: Business justification for the access.
            duration_hours: Requested duration of access (capped at max).

        Returns:
            AuthorizationRequest with status 'pending'.
        """
        # Validate divisions
        if target_division not in self.rbac.divisions:
            raise ValueError(f"Unknown target division: {target_division}")

        # Verify user is in source division
        if not await self.rbac.is_user_in_division(user_id, source_division):
            raise PermissionError(f"User '{user_id}' not in source division '{source_division}'")

        # Cap duration
        duration_hours = min(duration_hours, self.max_approval_duration_hours)

        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        request = AuthorizationRequest(
            id=request_id,
            user_id=user_id,
            source_division=source_division,
            target_division=target_division,
            resource=resource,
            justification=justification,
            status="pending",
            created_at=now.isoformat(),
            duration_hours=duration_hours,
            expires_at=(now + timedelta(hours=duration_hours)).isoformat(),
        )

        self._requests[request_id] = request

        # Notify admins (in production, this would send notifications)
        target_div_info = self.rbac.divisions[target_division]
        logger.info(
            "Authorization request %s: user=%s wants '%s' on %s/%s (%s)",
            request_id[:8], user_id, resource, target_division,
            target_div_info.get("name", target_division), justification,
        )

        # Log the request
        await self.audit.log_access_attempt(
            user_id=user_id,
            division=target_division,
            resource=resource,
            action="authorization_request",
            result="pending_approval",
            reason=f"Justification: {justification}",
            metadata={"request_id": request_id, "source_division": source_division},
        )

        return request

    async def approve_request(
        self,
        request_id: str,
        approver_id: str,
        duration_hours: int | None = None,
    ) -> ApprovalResult:
        """Approve a cross-division access request.

        Grants temporary access to the requesting user for the specified
        duration. The approver must be an admin in the target division.

        Args:
            request_id: The request to approve.
            approver_id: The admin approving the request.
            duration_hours: Override for access duration (capped at max).

        Returns:
            ApprovalResult with success status and expiration time.
        """
        request = self._requests.get(request_id)
        if not request:
            return ApprovalResult(
                success=False,
                request_id=request_id,
                message=f"Request '{request_id}' not found",
            )

        if request.status != "pending":
            return ApprovalResult(
                success=False,
                request_id=request_id,
                message=f"Request is already '{request.status}'",
            )

        # Verify approver is admin in target division
        approver_roles = await self.rbac.get_user_roles(approver_id, request.target_division)
        is_admin = any(r["role"] == "admin" for r in approver_roles)
        if not is_admin:
            return ApprovalResult(
                success=False,
                request_id=request_id,
                message=f"Approver '{approver_id}' is not an admin in division '{request.target_division}'",
            )

        # Grant access
        effective_duration = duration_hours or request.duration_hours
        effective_duration = min(effective_duration, self.max_approval_duration_hours)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=effective_duration)

        request.status = "approved"
        request.resolved_at = now.isoformat()
        request.approver_id = approver_id
        request.expires_at = expires.isoformat()

        # Grant a viewer role in the target division
        await self.rbac.assign_role(
            request.user_id,
            request.target_division,
            "viewer",
            granted_by=f"auth_flow:{approver_id}",
        )

        # Log the approval
        await self.audit.log_authorization_decision(
            request_id=request_id,
            approver_id=approver_id,
            decision="approved",
            reason=f"Temporary access granted for {effective_duration}h",
            user_id=request.user_id,
            division=request.target_division,
            resource=request.resource,
        )

        logger.info(
            "Request %s approved by %s (expires %s)",
            request_id[:8], approver_id, expires.isoformat(),
        )

        return ApprovalResult(
            success=True,
            request_id=request_id,
            message=f"Access approved for {effective_duration}h",
            expires_at=expires.isoformat(),
        )

    async def deny_request(
        self,
        request_id: str,
        approver_id: str,
        reason: str,
    ) -> DenialResult:
        """Deny a cross-division access request.

        Args:
            request_id: The request to deny.
            approver_id: The admin denying the request.
            reason: Explanation for the denial.

        Returns:
            DenialResult with success status.
        """
        request = self._requests.get(request_id)
        if not request:
            return DenialResult(
                success=False,
                request_id=request_id,
                message=f"Request '{request_id}' not found",
            )

        if request.status != "pending":
            return DenialResult(
                success=False,
                request_id=request_id,
                message=f"Request is already '{request.status}'",
            )

        # Verify approver is admin in target division
        approver_roles = await self.rbac.get_user_roles(approver_id, request.target_division)
        is_admin = any(r["role"] == "admin" for r in approver_roles)
        if not is_admin:
            return DenialResult(
                success=False,
                request_id=request_id,
                message=f"Approver '{approver_id}' is not an admin in division '{request.target_division}'",
            )

        now = datetime.now(timezone.utc)
        request.status = "denied"
        request.resolved_at = now.isoformat()
        request.approver_id = approver_id
        request.denial_reason = reason

        # Log the denial
        await self.audit.log_authorization_decision(
            request_id=request_id,
            approver_id=approver_id,
            decision="denied",
            reason=reason,
            user_id=request.user_id,
            division=request.target_division,
            resource=request.resource,
        )

        logger.info("Request %s denied by %s: %s", request_id[:8], approver_id, reason)

        return DenialResult(
            success=True,
            request_id=request_id,
            message=f"Request denied: {reason}",
        )

    async def get_request(self, request_id: str) -> AuthorizationRequest | None:
        """Get an authorization request by ID."""
        return self._requests.get(request_id)

    async def get_pending_requests(self, division: str | None = None) -> list[AuthorizationRequest]:
        """Get all pending authorization requests, optionally by division."""
        return [
            r for r in self._requests.values()
            if r.status == "pending"
            and (division is None or r.target_division == division)
        ]

    async def check_expired(self) -> list[str]:
        """Check for and expire any requests past their duration.

        Returns:
            List of request IDs that were expired.
        """
        now = datetime.now(timezone.utc)
        expired = []
        for req in self._requests.values():
            if req.status == "approved":
                if not req.expires_at:
                    continue
                try:
                    expires = datetime.fromisoformat(req.expires_at)
                except (ValueError, TypeError):
                    logger.warning("Request %s has malformed expires_at: %s", req.id[:8], req.expires_at)
                    continue
                if now > expires:
                    req.status = "expired"
                    expired.append(req.id)
                    logger.info("Request %s expired", req.id[:8])
        return expired

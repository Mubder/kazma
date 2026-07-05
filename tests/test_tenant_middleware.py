"""Tests for Multi-Tenant Gateway Middleware.

Verifies:
1. Context-scoped tenant_id extraction from inbound X-Tenant-ID headers.
2. Fallback to cookies and base64 JWT payload decoding.
3. Clean thread/async context boundary reset after request completes.
4. Response cookie propagation of the active tenant.
"""

from __future__ import annotations

import base64
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.tenant_context import get_current_tenant_id
from kazma_ui.auth import create_tenant_middleware

# Initialize a dummy FastAPI app for testing the middleware
app = FastAPI()
app.middleware("http")(create_tenant_middleware())


@app.get("/api/test-tenant")
def read_tenant():
    # Return the currently active tenant in this request's async context
    return {"active_tenant": get_current_tenant_id()}


def create_mock_jwt(tenant_id: str) -> str:
    """Generate a mock base64url-encoded JWT token containing the tenant_id claim."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"tenant_id": tenant_id, "user": "test-user"}
    
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature_b64 = base64.urlsafe_b64encode(b"mock-signature").decode().rstrip("=")
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def test_tenant_middleware_no_tenant():
    """Verify that if no tenant context is passed, get_current_tenant_id returns None."""
    client = TestClient(app)
    response = client.get("/api/test-tenant")
    assert response.status_code == 200
    assert response.json() == {"active_tenant": None}
    assert "X-Tenant-ID" not in response.cookies


def test_tenant_middleware_header():
    """Verify tenant extraction from X-Tenant-ID header."""
    client = TestClient(app)
    
    # 1. Test uppercase header
    response = client.get("/api/test-tenant", headers={"X-Tenant-ID": "tenant-alpha"})
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-alpha"}
    assert response.cookies.get("X-Tenant-ID") == "tenant-alpha"

    # 2. Test lowercase header
    response = client.get("/api/test-tenant", headers={"x-tenant-id": "tenant-beta"})
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-beta"}
    assert response.cookies.get("X-Tenant-ID") == "tenant-beta"


def test_tenant_middleware_cookie():
    """Verify tenant extraction from cookies."""
    client = TestClient(app)
    
    # 1. Test X-Tenant-ID cookie
    client.cookies.set("X-Tenant-ID", "tenant-gamma")
    response = client.get("/api/test-tenant")
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-gamma"}

    # Clear cookie
    client.cookies.clear()

    # 2. Test tenant_id cookie
    client.cookies.set("tenant_id", "tenant-delta")
    response = client.get("/api/test-tenant")
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-delta"}


def test_tenant_middleware_bearer_jwt():
    """Verify tenant extraction from Authorization Bearer JWT."""
    client = TestClient(app)
    jwt_token = create_mock_jwt("tenant-epsilon")
    
    response = client.get(
        "/api/test-tenant",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-epsilon"}
    assert response.cookies.get("X-Tenant-ID") == "tenant-epsilon"


def test_tenant_middleware_cookie_jwt():
    """Verify tenant extraction from JWT passed in cookie."""
    client = TestClient(app)
    jwt_token = create_mock_jwt("tenant-zeta")
    
    # Test cookie named 'jwt'
    client.cookies.set("jwt", jwt_token)
    response = client.get("/api/test-tenant")
    assert response.status_code == 200
    assert response.json() == {"active_tenant": "tenant-zeta"}
    assert response.cookies.get("X-Tenant-ID") == "tenant-zeta"


def test_tenant_context_leak_isolation():
    """Verify that concurrent or subsequent requests do not leak context."""
    client = TestClient(app)
    
    # Request 1 sets tenant-1
    res1 = client.get("/api/test-tenant", headers={"X-Tenant-ID": "tenant-1"})
    assert res1.json() == {"active_tenant": "tenant-1"}

    # Clear cookies so client doesn't resend X-Tenant-ID from cookie
    client.cookies.clear()
    
    # Request 2 (no headers) doesn't see tenant-1 because of clean reset
    res2 = client.get("/api/test-tenant")
    assert res2.json() == {"active_tenant": None}

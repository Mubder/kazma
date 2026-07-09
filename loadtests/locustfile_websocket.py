"""
Locust load test for Kazma WebSocket/SSE and HITL Approval Flow.

Tests:
- WebSocket /ws/swarm/{task_id} - Real-time swarm updates
- SSE /api/swarm/stream/{thread_id} - Server-sent events fallback
- HITL Approval: POST /api/approve/{thread_id}
- HITL WebSocket: /ws/hitl/{thread_id} - Real-time approval notifications

Usage:
    # WebSocket test (requires locust-plugins)
    locust -f loadtests/locustfile_websocket.py --host=http://localhost:8090 --users=100 --spawn-rate=10
    
    # SSE fallback test
    locust -f loadtests/locustfile_websocket.py --host=http://localhost:8090 --users=50 --spawn-rate=5 -H "SSE"
"""

from locust import HttpUser, task, between, events
from locust.exception import StopUser
import random
import uuid
import time
import json
import threading
from typing import Optional

try:
    from locust_plugins.users import WebSocketUser
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    WebSocketUser = HttpUser  # fallback


class SSESwarmUser(HttpUser):
    """Tests SSE (Server-Sent Events) endpoint for swarm updates."""
    
    wait_time = between(3, 10)
    
    def on_start(self):
        self.thread_id = f"sse-loadtest-{uuid.uuid4().hex[:8]}"
        self.active_connections = 0
        self.max_concurrent = 3
    
    @task(5)
    def sse_swarm_stream(self):
        """Connect to SSE endpoint and consume events."""
        if self.active_connections >= self.max_concurrent:
            return
            
        self.active_connections += 1
        try:
            with self.client.get(
                f"/api/swarm/stream/{self.thread_id}",
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
                catch_response=True,
                name="/api/swarm/stream/[thread_id] (SSE)",
                stream=True,
            ) as response:
                if response.status_code == 200:
                    # Consume events for a short duration
                    event_count = 0
                    start_time = time.time()
                    for line in response.iter_lines():
                        if line:
                            event_count += 1
                            if event_count >= 10 or (time.time() - start_time) > 30:
                                break
                    response.success()
                elif response.status_code == 404:
                    # Thread might not have active swarm - that's OK for load test
                    response.success()
                else:
                    response.failure(f"HTTP {response.status_code}")
        finally:
            self.active_connections -= 1
    
    @task(3)
    def sse_multiple_streams(self):
        """Open multiple concurrent SSE connections."""
        for i in range(2):
            thread_id = f"{self.thread_id}-{i}"
            with self.client.get(
                f"/api/swarm/stream/{thread_id}",
                headers={"Accept": "text/event-stream"},
                catch_response=True,
                name="/api/swarm/stream/[thread_id] (SSE multi)",
                stream=True,
            ) as response:
                if response.status_code in (200, 404):
                    # Read a couple events then close
                    count = 0
                    for line in response.iter_lines():
                        if line:
                            count += 1
                            if count >= 3:
                                break
                    response.success()
                else:
                    response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def health_check(self):
        with self.client.get("/health", catch_response=True, name="/health") as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


class HITLApprovalUser(HttpUser):
    """Tests HITL (Human-in-the-Loop) approval flow."""
    
    wait_time = between(2, 8)
    
    def on_start(self):
        self.thread_id = f"hitl-loadtest-{uuid.uuid4().hex[:8]}"
        self.pending_approvals = []
    
    @task(10)
    def trigger_hitl_tool(self):
        """Trigger a tool that requires HITL approval."""
        # Dispatch a task that uses a danger tool (file_write, shell_exec, etc.)
        task_data = {
            "prompt": "Write a test file to /tmp/loadtest_output.txt with content 'load test'",
            "workers": ["coder"],
            "task_type": "SWARM",
            "thread_id": self.thread_id,
            "metadata": {
                "source": "loadtest",
                "hitl_test": True,
                "require_approval": True,
            }
        }
        
        with self.client.post(
            "/api/swarm/dispatch",
            json=task_data,
            catch_response=True,
            name="/api/swarm/dispatch (HITL trigger)",
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    task_id = data.get("task_id") or data.get("thread_id")
                    if task_id:
                        self.pending_approvals.append(task_id)
                    response.success()
                except Exception:
                    response.failure("Invalid JSON")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(8)
    def check_pending_approvals(self):
        """Check for pending HITL approvals."""
        if not self.pending_approvals:
            return
            
        # Check a random pending approval
        thread_id = random.choice(self.pending_approvals)
        
        with self.client.get(
            f"/api/approve/{thread_id}/status",
            catch_response=True,
            name="/api/approve/[thread_id]/status",
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("pending"):
                    self._submit_approval(thread_id)
                response.success()
            elif response.status_code == 404:
                # No pending approval - remove from list
                if thread_id in self.pending_approvals:
                    self.pending_approvals.remove(thread_id)
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    def _submit_approval(self, thread_id: str):
        """Submit approval decision."""
        approved = random.choice([True, True, True, False])  # 75% approve
        
        with self.client.post(
            f"/api/approve/{thread_id}",
            json={"approved": approved, "reason": "Load test decision"},
            catch_response=True,
            name="/api/approve/[thread_id]",
        ) as response:
            if response.status_code == 200:
                if thread_id in self.pending_approvals:
                    self.pending_approvals.remove(thread_id)
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(3)
    def list_approvals(self):
        """List all pending approvals."""
        with self.client.get(
            "/api/approve/pending",
            catch_response=True,
            name="/api/approve/pending",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class WebSocketSwarmUser:
    """WebSocket user for real-time swarm updates.
    
    Requires: pip install locust-plugins
    Usage: locust -f loadtests/locustfile_websocket.py --host=ws://localhost:8090
    """
    
    if WEBSOCKET_AVAILABLE:
        # Only define if websocket support is available
        class WebSocketSwarmUserImpl(WebSocketUser):
            wait_time = between(5, 15)
            host = "ws://localhost:8090"  # WebSocket host
            
            def on_start(self):
                self.thread_id = f"ws-loadtest-{uuid.uuid4().hex[:8]}"
                self.connect_websocket()
            
            def connect_websocket(self):
                """Connect to WebSocket endpoint."""
                ws_url = f"/ws/swarm/{self.thread_id}"
                self.ws = self.client.connect(ws_url)
                
                # Send initial subscription message
                if self.ws:
                    self.ws.send(json.dumps({
                        "type": "subscribe",
                        "thread_id": self.thread_id,
                        "event_types": ["task_started", "worker_progress", "task_completed", "hitl_required"]
                    }))
            
            @task
            def listen_for_updates(self):
                """Listen for WebSocket messages."""
                if not self.ws:
                    self.connect_websocket()
                    return
                
                try:
                    # Wait for message with timeout
                    message = self.ws.recv(timeout=10)
                    if message:
                        data = json.loads(message)
                        # Track message types for metrics
                        msg_type = data.get("type", "unknown")
                        self.environment.events.request.fire(
                            request_type="WS",
                            name=f"ws/swarm/{msg_type}",
                            response_time=0,
                            response_length=len(message),
                            exception=None,
                        )
                except TimeoutError:
                    # No message in 10s - that's OK for load test
                    pass
                except Exception as e:
                    self.environment.events.request.fire(
                        request_type="WS",
                        name="ws/swarm/error",
                        response_time=0,
                        response_length=0,
                        exception=e,
                    )
            
            @task(2)
            def send_ping(self):
                """Send ping to keep connection alive."""
                if self.ws:
                    try:
                        self.ws.send(json.dumps({"type": "ping"}))
                    except Exception:
                        self.connect_websocket()
            
            def on_stop(self):
                if self.ws:
                    self.ws.close()
    else:
        # Placeholder when websocket not available
        class WebSocketSwarmUserImpl(HttpUser):
            abstract = True
            @task
            def placeholder(self):
                pass


# Event hooks
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print(f"[WebSocket/SSE/HITL Load Test] Starting on {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("[WebSocket/SSE/HITL Load Test] Stopped")
    stats = environment.stats
    print(f"  Total Requests: {stats.total.num_requests}")
    print(f"  Failures: {stats.total.num_failures}")
    if stats.total.num_requests > 0:
        print(f"  Avg Response Time: {stats.total.avg_response_time:.0f}ms")
        print(f"  95th Percentile: {stats.total.get_response_time_percentile(0.95):.0f}ms")


# Combined user class for mixed scenario
class MixedRealTimeUser(HttpUser):
    """Mixed real-time workload: SSE + HITL + WebSocket simulation."""
    
    wait_time = between(2, 10)
    
    tasks = {
        SSESwarmUser: 3,
        HITLApprovalUser: 2,
    }
    abstract = True
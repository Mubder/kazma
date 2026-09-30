"""
Locust load test for Kazma WebSocket/SSE and HITL Approval Flow.

Tests:
- WebSocket /ws/dashboard - Real-time dashboard feed (needs locust-plugins)
- SSE /api/swarm/tasks/{task_id}/stream - Server-sent events for a real task
- HITL: GET /api/swarm/tasks/{task_id}, POST /api/swarm/tasks/{task_id}/approve|reject
  for a task paused at a checkpoint, GET /api/pending-approvals

Request shapes come from kazma_api.py; tests/test_loadtest_routes.py checks
them and every path here against the real app.

Usage:
    # WebSocket test (requires locust-plugins)
    locust -f loadtests/locustfile_websocket.py --host=http://localhost:9090 --users=100 --spawn-rate=10
    
    # SSE fallback test
    locust -f loadtests/locustfile_websocket.py --host=http://localhost:9090 --users=50 --spawn-rate=5 -H "SSE"
"""

from locust import HttpUser, task, between, events
import random
import time
import json
from typing import Optional

from kazma_api import PAUSED, TERMINAL, dispatch_body, task_status

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
        self.task_ids: list[str] = []
        self.active_connections = 0
        self.max_concurrent = 3

    def _dispatch(self) -> Optional[str]:
        """Dispatch one background task so there is a real task to stream."""
        with self.client.post(
            "/api/swarm/dispatch",
            json=dispatch_body("Name three uses of server-sent events"),
            catch_response=True,
            name="/api/swarm/dispatch (for SSE)",
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return None
            try:
                task_id = response.json().get("task_id")
            except Exception:
                response.failure("Invalid JSON response")
                return None
            if not task_id:
                response.failure("dispatch returned no task_id")
                return None
            response.success()
            self.task_ids.append(task_id)
            return task_id

    def _task_id(self) -> Optional[str]:
        return random.choice(self.task_ids) if self.task_ids else self._dispatch()

    @task(5)
    def sse_swarm_stream(self):
        """Connect to a real task's SSE stream and consume events."""
        if self.active_connections >= self.max_concurrent:
            return
        task_id = self._task_id()
        if not task_id:
            return

        self.active_connections += 1
        try:
            with self.client.get(
                f"/api/swarm/tasks/{task_id}/stream",
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
                catch_response=True,
                name="/api/swarm/tasks/[task_id]/stream (SSE)",
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
                else:
                    # The stream serves every stored task (finished ones
                    # replay their history), so a real task never 404s.
                    response.failure(f"HTTP {response.status_code}")
        finally:
            self.active_connections -= 1

    @task(3)
    def sse_multiple_streams(self):
        """Open two SSE connections, each on a real task."""
        while len(self.task_ids) < 2:
            if not self._dispatch():
                return
        for task_id in self.task_ids[-2:]:
            with self.client.get(
                f"/api/swarm/tasks/{task_id}/stream",
                headers={"Accept": "text/event-stream"},
                catch_response=True,
                name="/api/swarm/tasks/[task_id]/stream (SSE multi)",
                stream=True,
            ) as response:
                if response.status_code == 200:
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
        self.watched: list[str] = []  # swarm task ids this user dispatched

    @task(10)
    def trigger_task(self):
        """Dispatch a background task; its status is watched below."""
        with self.client.post(
            "/api/swarm/dispatch",
            json=dispatch_body("Write a two-line summary of what HITL means"),
            catch_response=True,
            name="/api/swarm/dispatch (HITL trigger)",
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                task_id = response.json().get("task_id")
            except Exception:
                response.failure("Invalid JSON")
                return
            if not task_id:
                response.failure("dispatch returned no task_id")
                return
            self.watched.append(task_id)
            response.success()

    @task(8)
    def check_task_for_checkpoint(self):
        """Poll a watched task; decide it only when it is paused at a checkpoint.

        A swarm task's approval is a pipeline checkpoint (``paused``), decided
        through the swarm's own route. The chat approve route is for chat
        turns and never matches a swarm task id.
        """
        if not self.watched:
            return
        task_id = random.choice(self.watched)

        with self.client.get(
            f"/api/swarm/tasks/{task_id}",
            catch_response=True,
            name="/api/swarm/tasks/[task_id]",
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                status = task_status(response.json())
            except Exception:
                response.failure("Invalid JSON")
                return
            response.success()
        if status == PAUSED:
            self._decide_checkpoint(task_id)
        elif status in TERMINAL:
            self.watched.remove(task_id)

    def _decide_checkpoint(self, task_id: str):
        """Approve (75%) or reject a task paused at a checkpoint.

        Each route is written out (not built from a variable action) so
        tests/test_loadtest_routes.py can check it exists.
        """
        body = {"reason": "Load test decision"}
        if random.random() < 0.75:
            decision = self.client.post(
                f"/api/swarm/tasks/{task_id}/approve", json=body,
                catch_response=True, name="/api/swarm/tasks/[task_id]/approve",
            )
        else:
            decision = self.client.post(
                f"/api/swarm/tasks/{task_id}/reject", json=body,
                catch_response=True, name="/api/swarm/tasks/[task_id]/reject",
            )
        with decision as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(3)
    def list_approvals(self):
        """List every pending approval (the gate registry, all mechanisms)."""
        with self.client.get(
            "/api/pending-approvals",
            catch_response=True,
            name="/api/pending-approvals",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class WebSocketSwarmUser:
    """WebSocket user for real-time swarm updates.
    
    Requires: pip install locust-plugins
    Usage: locust -f loadtests/locustfile_websocket.py --host=ws://localhost:9090
    """
    
    if WEBSOCKET_AVAILABLE:
        # Only define if websocket support is available
        class WebSocketSwarmUserImpl(WebSocketUser):
            wait_time = between(5, 15)
            host = "ws://localhost:9090"  # WebSocket host
            
            def on_start(self):
                self.ws = None
                self.connect_websocket()

            def connect_websocket(self):
                """Open the real-time dashboard feed (the app's live WebSocket;
                it greets with {"type": "connected"} and streams trace events).
                There is no per-task swarm socket — task events are SSE."""
                self.ws = self.client.connect("/ws/dashboard")
            
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
                            name=f"ws/dashboard/{msg_type}",
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
                        name="ws/dashboard/error",
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
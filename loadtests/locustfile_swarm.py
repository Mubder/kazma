"""
Locust load test for Kazma Swarm Dispatch endpoints.

Tests:
- POST /api/swarm/dispatch - Main swarm dispatch endpoint (background)
- GET  /api/swarm/tasks/{task_id} - Swarm task status polling
- GET  /api/swarm/tasks/{task_id}/stream - SSE task events
- GET  /api/swarm/tasks - Task list

Request shapes come from kazma_api.py; tests/test_loadtest_routes.py checks
them and every path here against the real app.

Usage:
    locust -f loadtests/locustfile_swarm.py --host=http://localhost:9090 --users=50 --spawn-rate=5 --run-time=60s
"""

from locust import HttpUser, task, between, events
import random
import uuid

from kazma_api import DISPATCH_PATTERNS, chat_body, dispatch_body


class SwarmDispatchUser(HttpUser):
    """Simulates a user dispatching swarm tasks and monitoring results."""
    
    wait_time = between(2, 8)  # Wait 2-8 seconds between tasks
    
    # Test data for swarm tasks
    SWARM_TASKS = [
        "Research the latest developments in quantum computing",
        "Write a Python script to scrape product data from an e-commerce site",
        "Analyze the sentiment of customer reviews for product X",
        "Create a marketing plan for a new SaaS product launch",
        "Debug this Python code: def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)",
        "Summarize the key findings from the latest IPCC climate report",
        "Design a database schema for a multi-tenant SaaS application",
        "Write unit tests for a FastAPI authentication middleware",
        "Explain the difference between REST and GraphQL APIs",
        "Generate a Docker Compose file for a microservices architecture",
    ]
    
    def on_start(self):
        """Called when a simulated user starts."""
        self.thread_id = f"loadtest-{uuid.uuid4().hex[:8]}"
        self.session_id = None
        self.authenticated = False
        
        # Try to authenticate/get session
        self._authenticate()
    
    def _authenticate(self):
        """Get a thread ID for testing.

        There is no session-create endpoint — a web chat/swarm thread id is
        minted client-side and passed on each request, so generate one.
        """
        self.session_id = f"loadtest-thread-{uuid.uuid4().hex[:12]}"
    
    @task(10)
    def dispatch_swarm_task(self):
        """Dispatch a swarm task - primary load test."""
        if not self.session_id:
            return
            
        task_data = dispatch_body(
            random.choice(self.SWARM_TASKS),
            pattern=random.choice(DISPATCH_PATTERNS),
        )

        with self.client.post(
            "/api/swarm/dispatch",
            json=task_data,
            catch_response=True,
            name="/api/swarm/dispatch",
        ) as response:
            if response.status_code == 200:
                try:
                    self.last_task_id = response.json().get("task_id")
                except Exception:
                    response.failure("Invalid JSON response")
                    return
                if self.last_task_id:
                    response.success()
                else:
                    response.failure("dispatch returned no task_id")
            elif response.status_code == 429:
                response.failure("Rate limited (429)")
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
    
    @task(5)
    def check_swarm_status(self):
        """Poll swarm task status."""
        if not hasattr(self, 'last_task_id') or not self.last_task_id:
            return
            
        with self.client.get(
            f"/api/swarm/tasks/{self.last_task_id}",
            catch_response=True,
            name="/api/swarm/tasks/[task_id]",
        ) as response:
            # The task store is durable (it survives completion and restart),
            # so a task this user dispatched is never a legitimate 404.
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(3)
    def list_swarm_tasks(self):
        """List recent swarm tasks."""
        with self.client.get(
            "/api/swarm/tasks",
            params={"limit": 20, "thread_id": self.session_id},
            catch_response=True,
            name="/api/swarm/tasks",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(2)
    def health_check(self):
        """Health check endpoint."""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class WebSocketSwarmUser(HttpUser):
    """Simulates WebSocket connections for real-time swarm updates."""
    
    wait_time = between(5, 15)
    
    def on_start(self):
        self.task_id = None
        self.ws = None

    @task
    def websocket_swarm_updates(self):
        """Stream a real task's SSE events (HttpUser has no WebSocket)."""
        if not self.task_id:
            self._dispatch()
        if self.task_id:
            self._test_sse()

    def _dispatch(self):
        """Dispatch one background task so there is a real task to stream."""
        with self.client.post(
            "/api/swarm/dispatch",
            json=dispatch_body("Summarise the benefits of load testing in one line"),
            catch_response=True,
            name="/api/swarm/dispatch (for SSE)",
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                self.task_id = response.json().get("task_id")
            except Exception:
                response.failure("Invalid JSON response")
                return
            if self.task_id:
                response.success()
            else:
                response.failure("dispatch returned no task_id")

    def _test_sse(self):
        """Test Server-Sent Events endpoint as WebSocket alternative."""
        with self.client.get(
            f"/api/swarm/tasks/{self.task_id}/stream",
            headers={"Accept": "text/event-stream"},
            catch_response=True,
            name="/api/swarm/tasks/[task_id]/stream (SSE)",
            stream=True,
        ) as response:
            if response.status_code == 200:
                # Read a few events then close
                count = 0
                for line in response.iter_lines():
                    count += 1
                    if count >= 5:  # Read 5 events then stop
                        break
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class GatewayApiUser(HttpUser):
    """Load test for general gateway API endpoints."""
    
    wait_time = between(1, 5)
    
    @task(5)
    def chat_completion(self):
        """Test the chat endpoint (SSE stream)."""
        with self.client.post(
            "/api/chat/stream",
            json=chat_body(
                "Hello, this is a load test message",
                f"loadtest-{uuid.uuid4().hex[:8]}",
            ),
            headers={"Accept": "text/event-stream"},
            catch_response=True,
            name="/api/chat/stream",
            stream=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(3)
    def list_models(self):
        """List available models."""
        with self.client.get(
            "/api/models",
            catch_response=True,
            name="/api/models",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(2)
    def get_config(self):
        """Get configuration."""
        with self.client.get(
            "/api/settings",
            catch_response=True,
            name="/api/settings",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def metrics_endpoint(self):
        """Prometheus metrics endpoint."""
        with self.client.get(
            "/metrics",
            catch_response=True,
            name="/metrics",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


# Event hooks for custom metrics
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print(f"[Locust] Load test starting on {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("[Locust] Load test stopped")
    # Print summary stats
    stats = environment.stats
    print(f"  Total Requests: {stats.total.num_requests}")
    print(f"  Failures: {stats.total.num_failures}")
    print(f"  Avg Response Time: {stats.total.avg_response_time:.0f}ms")
    print(f"  95th Percentile: {stats.total.get_response_time_percentile(0.95):.0f}ms")
    print(f"  Max Response Time: {stats.total.max_response_time:.0f}ms")


# Custom user class weights for mixed load scenarios
class MixedLoadUser(HttpUser):
    """Mixed workload user - combines swarm, chat, and gateway calls."""
    
    wait_time = between(1, 10)
    
    # Weight distribution: 40% swarm, 30% chat, 20% gateway, 10% admin
    tasks = {
        SwarmDispatchUser: 4,
        GatewayApiUser: 3,
    }
    
    # This class uses task weights from parent classes
    abstract = True
"""
Locust load test for Kazma Swarm Dispatch endpoints.

Tests:
- /api/swarm/dispatch - Main swarm dispatch endpoint
- /api/swarm/status - Swarm task status polling
- WebSocket /ws/swarm/{task_id} - Real-time swarm updates
- HITL approval flow: POST /api/approve/{thread_id}

Usage:
    locust -f loadtests/locustfile_swarm.py --host=http://localhost:8090 --users=50 --spawn-rate=5 --run-time=60s
"""

from locust import HttpUser, task, between, events
from locust.exception import StopUser
import random
import json
import uuid
import time


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
    
    WORKER_POOLS = [
        ["researcher", "analyst", "writer"],
        ["coder", "reviewer", "tester"],
        ["planner", "executor", "critic"],
        ["all"],  # All workers
    ]
    
    TASK_TYPES = ["SWARM", "PIPELINE", "DAG"]
    
    def on_start(self):
        """Called when a simulated user starts."""
        self.thread_id = f"loadtest-{uuid.uuid4().hex[:8]}"
        self.session_id = None
        self.authenticated = False
        
        # Try to authenticate/get session
        self._authenticate()
    
    def _authenticate(self):
        """Attempt to get a session/thread ID for testing."""
        try:
            # Try to create a session or get thread ID
            resp = self.client.post("/api/session/create", json={
                "platform": "web",
                "user_id": f"loadtest_{random.randint(1000, 9999)}",
                "metadata": {"source": "loadtest"}
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self.session_id = data.get("thread_id") or data.get("session_id")
                self.authenticated = True
        except Exception:
            pass
        
        # Fallback: generate our own thread_id
        if not self.session_id:
            self.session_id = f"loadtest-thread-{uuid.uuid4().hex[:12]}"
    
    @task(10)
    def dispatch_swarm_task(self):
        """Dispatch a swarm task - primary load test."""
        if not self.session_id:
            return
            
        task_data = {
            "prompt": random.choice(self.SWARM_TASKS),
            "workers": random.choice(self.WORKER_POOLS),
            "task_type": random.choice(self.TASK_TYPES),
            "thread_id": self.session_id,
            "metadata": {
                "source": "loadtest",
                "user_id": f"loadtest_user_{random.randint(1, 100)}",
            }
        }
        
        with self.client.post(
            "/api/swarm/dispatch",
            json=task_data,
            catch_response=True,
            name="/api/swarm/dispatch",
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    self.last_task_id = data.get("task_id") or data.get("thread_id")
                    response.success()
                except Exception:
                    response.failure("Invalid JSON response")
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
            f"/api/swarm/status/{self.last_task_id}",
            catch_response=True,
            name="/api/swarm/status/[task_id]",
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Task might not exist yet or completed
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
    
    @task(1)
    def hitl_approve(self):
        """Simulate HITL approval flow."""
        if not hasattr(self, 'last_task_id') or not self.last_task_id:
            return
            
        # Randomly approve or deny
        approved = random.choice([True, False])
        
        with self.client.post(
            f"/api/approve/{self.last_task_id}",
            json={"approved": approved, "reason": "Load test approval"},
            catch_response=True,
            name="/api/approve/[thread_id]",
        ) as response:
            if response.status_code in (200, 404):  # 404 if no pending approval
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
        self.thread_id = f"ws-loadtest-{uuid.uuid4().hex[:8]}"
        self.ws = None
    
    @task
    def websocket_swarm_updates(self):
        """Connect to WebSocket and listen for swarm updates."""
        # Note: Locust doesn't have native WebSocket support in HttpUser
        # This would need WebSocketUser from locust-plugins or custom implementation
        # For now, we'll test the SSE endpoint instead
        self._test_sse()
    
    def _test_sse(self):
        """Test Server-Sent Events endpoint as WebSocket alternative."""
        with self.client.get(
            f"/api/swarm/stream/{self.thread_id}",
            headers={"Accept": "text/event-stream"},
            catch_response=True,
            name="/api/swarm/stream/[thread_id] (SSE)",
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
        """Test chat completion endpoint."""
        with self.client.post(
            "/api/chat",
            json={
                "message": "Hello, this is a load test message",
                "thread_id": f"loadtest-{uuid.uuid4().hex[:8]}",
                "model": "gpt-4o-mini",
            },
            catch_response=True,
            name="/api/chat",
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
            "/api/config",
            catch_response=True,
            name="/api/config",
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
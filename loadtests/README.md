# Load Testing Infrastructure

This directory contains load testing scripts for Kazma using **Locust** (Python) and **k6** (JavaScript/Go).

## Quick Start

### Prerequisites

```bash
# Install Locust
pip install locust locust-plugins

# Install k6 (macOS)
brew install k6

# Install k6 (Linux)
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```

### Running Locust Tests

```bash
# Run swarm dispatch load test (headless)
locust -f loadtests/locustfile_swarm.py --host=http://localhost:8090 --users=50 --spawn-rate=5 --run-time=60s --headless

# Run WebSocket/SSE/HITL load test
locust -f loadtests/locustfile_websocket.py --host=http://localhost:8090 --users=30 --spawn-rate=3 --run-time=120s --headless

# Run with Web UI (for debugging)
locust -f loadtests/locustfile_swarm.py --host=http://localhost:8090

# Run using the runner script
python loadtests/run_loadtests.py --scenario=swarm --users=100 --spawn-rate=10 --run-time=120s
```

### Running k6 Tests

```bash
# Run k6 swarm test
k6 run loadtests/k6_swarm.js --vus 50 --duration 60s

# Run with custom host
k6 run loadtests/k6_swarm.js -e BASE_URL=http://staging.kazma.io --vus 100 --duration 5m

# Run with JSON output for CI
k6 run loadtests/k6_swarm.js --out json=results.json --vus 50 --duration 60s
```

## Test Scenarios

### 1. Swarm Dispatch Load Test (`locustfile_swarm.py`)

Tests the core swarm dispatch flow:
- `POST /api/swarm/dispatch` - Dispatch swarm tasks
- `GET /api/swarm/status/{task_id}` - Poll task status
- `GET /api/swarm/tasks` - List tasks
- `POST /api/approve/{thread_id}` - HITL approval flow
- `GET /health` - Health checks

**User Classes:**
- `SwarmDispatchUser` - Primary swarm dispatch workload
- `GatewayApiUser` - General gateway API load
- `MixedLoadUser` - Combined workload (40% swarm, 30% chat, 20% gateway, 10% admin)

### 2. WebSocket/SSE/HITL Test (`locustfile_websocket.py`)

Tests real-time communication:
- `GET /api/swarm/stream/{thread_id}` - SSE stream
- `POST /api/approve/{thread_id}` - HITL approval
- `GET /api/approve/{thread_id}/status` - Check approval status
- WebSocket `/ws/swarm/{task_id}` (requires `locust-plugins`)

**User Classes:**
- `SSESwarmUser` - Server-Sent Events for swarm updates
- `HITLApprovalUser` - Human-in-the-loop approval flow
- `WebSocketSwarmUser` - WebSocket real-time updates (requires `locust-plugins`)
- `MixedRealTimeUser` - Combined SSE + HITL workload

### 3. k6 Test (`k6_swarm.js`)

k6 script with advanced features:
- Swarm dispatch + polling
- SSE stream consumption
- HITL approval flow
- WebSocket connection (requires k6 websocket extension)
- Custom metrics and thresholds
- HTML/JSON/JUnit report generation

## CI Integration

### GitHub Actions

```yaml
# .github/workflows/loadtest.yml
name: Load Tests

on:
  workflow_dispatch:
    inputs:
      scenario:
        description: 'Test scenario'
        required: true
        type: choice
        options: [swarm, websocket, mixed, all]
      users:
        description: 'Number of users'
        required: false
        default: '50'
      duration:
        description: 'Test duration'
        required: false
        default: '60s'

jobs:
  loadtest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install Locust
        run: pip install locust locust-plugins
      
      - name: Start Kazma Server
        run: |
          # Start server in background
          cd kazma-ui && python -m uvicorn app:create_app --factory --host 0.0.0.0 --port 8090 &
          sleep 10
      
      - name: Run Load Test
        run: |
          python loadtests/run_loadtests.py \
            --scenario=${{ github.event.inputs.scenario }} \
            --users=${{ github.event.inputs.users }} \
            --run-time=${{ github.event.inputs.duration }} \
            --headless
      
      - name: Upload Reports
        uses: actions/upload-artifact@v4
        with:
          name: loadtest-reports
          path: loadtest_reports/
```

### k6 Cloud / Grafana Cloud

```bash
# Run with k6 Cloud
k6 cloud loadtests/k6_swarm.js --vus 100 --duration 10m

# Or with Grafana Cloud
K6_CLOUD_TOKEN=<token> k6 run loadtests/k6_swarm.js --out cloud
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KAZMA_HOST` | `http://localhost:8090` | Target host for load tests |
| `KAZMA_WS_HOST` | `ws://localhost:8090` | WebSocket host |
| `KAZMA_API_KEY` | (empty) | API key for authenticated endpoints |

### Locust Configuration

Edit `locustfile_swarm.py` to adjust:
- `wait_time` - Time between user actions
- Task weights (`@task(N)`) - Relative frequency of operations
- Test data (`SWARM_TASKS`, `WORKER_POOLS`, `TASK_TYPES`)

### k6 Thresholds

The k6 script defines pass/fail thresholds:
```javascript
thresholds: {
  http_req_duration: ['p(95)<2000'],  // 95% of requests < 2s
  http_req_failed: ['rate<0.01'],      // Error rate < 1%
  swarm_dispatch_duration: ['p(95)<5000'],
  sse_connection_duration: ['p(95)<10000'],
  hitl_approval_duration: ['p(95)<30000'],
  checks: ['rate>0.95'],               // 95% of checks pass
}
```

## Reports

### Locust Reports
- **HTML Report**: `--html=report.html` - Visual charts and tables
- **CSV Export**: `--csv=results` - Raw data for analysis
- **Stats Summary**: Printed to console on completion

### k6 Reports
- **Console Summary**: Built-in summary with percentiles
- **HTML Report**: `k6 run --out html=report.html ...`
- **JSON**: `k6 run --out json=results.json ...`
- **JUnit XML**: `k6 run --out junit=results.xml ...` (for CI)
- **InfluxDB/Grafana**: `k6 run --out influxdb=... ...`

## Interpreting Results

### Key Metrics

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| `p(95) response time` | < 500ms | 500ms - 2s | > 2s |
| `Error rate` | < 0.1% | 0.1% - 1% | > 1% |
| `Throughput (RPS)` | > 100 | 50 - 100 | < 50 |
| `SSE connection duration` | < 5s | 5s - 15s | > 15s |
| `HITL approval latency` | < 10s | 10s - 30s | > 30s |

### Bottleneck Identification

1. **High dispatch latency** → Check swarm engine, worker pool saturation
2. **SSE disconnects** → Check connection limits, proxy timeouts
3. **HITL timeouts** → Check approval queue, notification delivery
4. **WebSocket failures** → Check connection limits, heartbeat interval
5. **High error rate** → Check logs for 5xx errors, rate limiting

## Scaling Guidelines

| Users | Spawn Rate | Duration | Expected RPS |
|-------|-----------|----------|--------------|
| 10 | 2/s | 60s | ~5 |
| 50 | 5/s | 120s | ~25 |
| 100 | 10/s | 300s | ~50 |
| 500 | 25/s | 600s | ~200 |
| 1000 | 50/s | 1800s | ~400 |

## Troubleshooting

### Locust "Connection refused"
- Ensure Kazma server is running on the target host/port
- Check firewall/security groups
- Verify `--host` parameter includes protocol (http:// or https://)

### k6 "websocket: dial tcp: connection refused"
- WebSocket tests require separate WebSocket server
- Set `WS_URL` environment variable
- Or comment out WebSocket test if not available

### High memory usage
- Reduce `--users` or `--spawn-rate`
- Use `--headless` mode
- For k6, use `--compatibility-mode=base` to disable some features

### Rate limiting (429 responses)
- Reduce spawn rate
- Add `wait_time = between(5, 15)` to user classes
- Check server rate limit configuration
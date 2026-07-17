import http
import ws
import json
import time
import random
from k6 import check, sleep, fail
from k6.http import get, post, websocket
from k6.ws import connect

// Kazma Load Test Script (k6)
// Run: k6 run --vus 50 --duration 60s loadtests/k6_swarm.js

export const options = {
  scenarios: {
    swarm_dispatch: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },  // Ramp up
        { duration: '60s', target: 20 },  // Stay at 20
        { duration: '30s', target: 30 },  // Ramp up to 30
        { duration: '60s', target: 30 },  // Stay at 30
        { duration: '30s', target: 0 },   // Ramp down
      ],
      gracefulRampDown: '10s',
    },
    sse_stream: {
      executor: 'constant-vus',
      vus: 10,
      duration: '3m',
      startTime: '30s',  // Start after swarm ramp-up
    },
    hitl_approval: {
      executor: 'per-vu-iterations',
      vus: 5,
      iterations: 20,
      startTime: '1m',
      maxDuration: '2m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    http_req_failed: ['rate<0.05'],
    ws_connecting: ['p(95)<1000'],
    ws_session_duration: ['p(95)<180000'],
    checks: ['rate>0.95'],
  },
};

const BASE_URL = __ENV.KAZMA_HOST || 'http://localhost:9090';
const WS_URL = __ENV.KAZMA_WS_HOST || 'ws://localhost:9090';

// Test data
const SWARM_TASKS = [
  "Analyze the quarterly sales data and create a summary report",
  "Research competitors' pricing strategies for our product line",
  "Generate a marketing plan for Q3 product launch",
  "Review and optimize the database queries for performance",
  "Create a security audit checklist for the web application",
];

const WORKER_POOLS = [
  ["researcher", "analyst"],
  ["coder", "reviewer"],
  ["planner", "executor"],
  ["architect", "implementer", "tester"],
];

function getAuthHeaders() {
  // Add auth if needed
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${__ENV.KAZMA_API_TOKEN || 'test-token'}`,
  };
}

export function setup() {
  // Verify server is reachable
  const health = get(`${BASE_URL}/health`);
  check(health, { 'health check ok': (r) => r.status === 200 });
  
  // Return test data for VUs
  return {
    baseUrl: BASE_URL,
    wsUrl: WS_URL,
  };
}

export default function (data) {
  const scenario = __ENV.SCENARIO || 'swarm';
  
  switch (scenario) {
    case 'swarm':
      runSwarmDispatch(data);
      break;
    case 'sse':
      runSSEStream(data);
      break;
    case 'hitl':
      runHITLApproval(data);
      break;
    case 'mixed':
      // Randomly pick one
      const pick = randomIntBetween(1, 3);
      if (pick === 1) runSwarmDispatch(data);
      else if (pick === 2) runSSEStream(data);
      else runHITLApproval(data);
      break;
    default:
      runSwarmDispatch(data);
  }
  
  // Think time between iterations
  sleep(randomFloatBetween(1, 5));
}

function runSwarmDispatch(data) {
  const task = SWARM_TASKS[randomIntBetween(0, SWARM_TASKS.length - 1)];
  const workers = WORKER_POOLS[randomIntBetween(0, WORKER_POOLS.length - 1)];
  
  const payload = {
    prompt: task,
    workers: workers,
    type: "fan_out",
    metadata: {
      source_platform: "loadtest",
      source_chat_id: `loadtest-${__VU}`,
      source_user: `loadtest-user-${__VU}`,
    },
  };
  
  const params = { headers: getAuthHeaders() };
  
  // Dispatch swarm task
  const start = new Date();
  const resp = post(`${data.baseUrl}/api/swarm/dispatch`, JSON.stringify(payload), params);
  const duration = new Date() - start;
  
  check(resp, {
    'swarm dispatch status 200': (r) => r.status === 200,
    'swarm dispatch has task_id': (r) => {
      try {
        const body = JSON.parse(r.body);
        return !!body.task_id;
      } catch {
        return false;
      }
    },
    'swarm dispatch response time < 5s': () => duration < 5000,
  });
  
  // If we got a task_id, poll for completion
  if (resp.status === 200) {
    try {
      const body = JSON.parse(resp.body);
      const taskId = body.task_id;
      if (taskId) {
        pollSwarmTask(data.baseUrl, taskId, params);
      }
    } catch (e) {
      // Ignore parse errors
    }
  }
}

function pollSwarmTask(baseUrl, taskId, params) {
  const maxPolls = 30;  // Max 30 seconds
  let polls = 0;
  
  while (polls < maxPolls) {
    sleep(1);
    polls++;
    
    const resp = get(`${baseUrl}/api/swarm/status/${taskId}`, params);
    
    check(resp, {
      'swarm status check ok': (r) => r.status === 200,
    });
    
    if (resp.status !== 200) break;
    
    try {
      const body = JSON.parse(resp.body);
      if (body.status === 'completed' || body.status === 'failed') {
        check(body, {
          'swarm task completed': (b) => b.status === 'completed',
          'swarm has output': (b) => !!b.aggregated_output,
        });
        break;
      }
    } catch (e) {
      break;
    }
  }
}

function runSSEStream(data) {
  // Test SSE endpoint for swarm updates
  const threadId = `loadtest-${__VU}-${__ITER}`;
  const url = `${data.baseUrl}/api/swarm/stream/${threadId}`;
  
  const params = { 
    headers: { ...getAuthHeaders(), 'Accept': 'text/event-stream' },
    tags: { name: 'SSE_SwarmStream' },
  };
  
  const resp = get(url, params);
  
  check(resp, {
    'SSE stream connected': (r) => r.status === 200 || r.status === 201,
    'SSE content-type': (r) => r.headers['Content-Type']?.includes('text/event-stream'),
  });
  
  // Read a few events
  if (resp.status === 200) {
    const lines = resp.body.split('\n');
    let eventCount = 0;
    for (const line of lines) {
      if (line.startsWith('data:')) {
        eventCount++;
        if (eventCount >= 3) break;
      }
    }
    check(null, { 'SSE received events': () => eventCount > 0 });
  }
  
  sleep(randomFloatBetween(5, 15));
}

function runHITLApproval(data) {
  // First, trigger a task that requires HITL approval
  const payload = {
    prompt: "Execute a dangerous operation: delete all files in /tmp/test",
    workers: ["executor"],
    type: "fan_out",
    metadata: {
      source_platform: "loadtest",
      source_chat_id: `hitl-${__VU}`,
      source_user: `hitl-user-${__VU}`,
    },
  };
  
  const params = { headers: getAuthHeaders() };
  
  // Dispatch task
  const dispatchResp = post(`${data.baseUrl}/api/swarm/dispatch`, JSON.stringify(payload), params);
  
  check(dispatchResp, {
    'HITL task dispatched': (r) => r.status === 200,
  });
  
  if (dispatchResp.status !== 200) return;
  
  let taskId = null;
  try {
    taskId = JSON.parse(dispatchResp.body).task_id;
  } catch (e) {
    return;
  }
  
  // Poll until HITL required
  let hitlRequired = false;
  for (let i = 0; i < 20; i++) {
    sleep(1);
    const statusResp = get(`${data.baseUrl}/api/swarm/status/${taskId}`, params);
    if (statusResp.status === 200) {
      try {
        const body = JSON.parse(statusResp.body);
        if (body.hitl_required === true) {
          hitlRequired = true;
          break;
        }
        if (body.status === 'completed' || body.status === 'failed') {
          break;
        }
      } catch (e) {}
    }
  }
  
  if (!hitlRequired) {
    check(null, { 'HITL was triggered': () => false });
    return;
  }
  
  // Approve the HITL request
  const approveResp = post(
    `${data.baseUrl}/api/approve/${taskId}`,
    JSON.stringify({ approved: true, reason: "Load test approval" }),
    params
  );
  
  check(approveResp, {
    'HITL approve status 200': (r) => r.status === 200,
    'HITL approve success': (r) => {
      try {
        return JSON.parse(r.body).approved === true;
      } catch {
        return false;
      }
    },
  });
  
  // Wait for completion
  for (let i = 0; i < 30; i++) {
    sleep(1);
    const statusResp = get(`${data.baseUrl}/api/swarm/status/${taskId}`, params);
    if (statusResp.status === 200) {
      try {
        const body = JSON.parse(statusResp.body);
        if (body.status === 'completed') {
          check(body, { 'HITL task completed after approval': (b) => b.status === 'completed' });
          break;
        }
      } catch (e) {}
    }
  }
}

// WebSocket test (separate VU type)
export function wsTest() {
  const wsUrl = `${WS_URL}/ws/swarm/loadtest-${__VU}`;
  
  const params = {
    tags: { name: 'WS_SwarmConnection' },
  };
  
  const response = ws.connect(wsUrl, params, function (socket) {
    socket.on('open', function () {
      // Subscribe to updates
      socket.send(JSON.stringify({
        type: 'subscribe',
        thread_id: `loadtest-${__VU}`,
        event_types: ['task_started', 'worker_progress', 'task_completed', 'hitl_required'],
      }));
      
      // Send ping periodically
      socket.setInterval(function () {
        socket.send(JSON.stringify({ type: 'ping' }));
      }, 10000);
    });
    
    socket.on('message', function (message) {
      const data = JSON.parse(message);
      // Track message types
      console.log(`WS message: ${data.type}`);
    });
    
    socket.on('close', function () {
      console.log('WS connection closed');
    });
    
    socket.setTimeout(function () {
      socket.close();
    }, 120000);  // 2 minutes
  });
  
  check(response, {
    'WS connected': (r) => r && r.status === 101,
  });
}

// Helper functions
function randomIntBetween(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomFloatBetween(min, max) {
  return Math.random() * (max - min) + min;
}

// Teardown
export function teardown(data) {
  console.log('Load test completed');
}
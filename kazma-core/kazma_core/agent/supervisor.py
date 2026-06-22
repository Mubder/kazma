"""Kazma Supervisor — LangGraph Supervisor Pattern for intelligent routing.

The Supervisor acts as the "traffic cop" of the Kazma agent system:
- Receives user prompts and decides the optimal execution path
- Routes simple queries (greetings, casual chat) to fast local LLM
- Routes complex queries (research, file operations, multi-step tasks) to worker nodes
- Enforces Arabic-first responses and cultural context awareness
"""

from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State Definition
# ---------------------------------------------------------------------------


class SupervisorState(TypedDict, total=False):
    """State for the Supervisor workflow.

    Attributes:
        messages: Conversation history with add_messages reducer
        next_node: The next node to route to (simple_llm, worker, or END)
        cultural_context_active: Whether Arabic cultural context is active
        query_complexity: Estimated complexity of the current query (simple/complex)
        routing_reason: Explanation of why the supervisor chose this route
    """

    messages: Annotated[list, add_messages]
    next_node: str
    cultural_context_active: bool
    query_complexity: str
    routing_reason: str


def initial_supervisor_state() -> SupervisorState:
    """Create a fresh initial state for the Supervisor workflow."""
    return SupervisorState(
        messages=[],
        next_node="",
        cultural_context_active=True,
        query_complexity="unknown",
        routing_reason="",
    )


# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------


SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor of Kazma (كاظمه), an autonomous AI agent framework designed for Arabic-first interactions.

Your primary role is to act as an intelligent traffic cop that routes user queries to the most appropriate execution path.

## Routing Decision Criteria:

**SIMPLE PATH (route to "simple_llm"):**
- Greetings and casual conversation (e.g., "شلونك", "مرحبا", "السلام عليكم")
- Simple questions that don't require external tools or research
- Direct requests for information you can answer from your training data
- Emotional support or casual chat
- Quick translations or language explanations

**COMPLEX PATH (route to "worker"):**
- Research tasks requiring web search or document analysis
- File system operations (reading, writing, listing files)
- Multi-step reasoning or complex problem solving
- Code execution or debugging tasks
- Data analysis or calculations
- Tasks requiring tool usage (APIs, databases, external services)
- Requests that need to gather information from multiple sources

## Cultural Context:
- ALWAYS respond in Arabic when the user writes in Arabic
- Detect and respect Kuwaiti/Gulf dialects
- Maintain "Majlis Mode" - culturally appropriate, respectful, and warm
- When cultural_context_active is True, prioritize Arabic responses and cultural awareness

## Output Format:
You must respond with a JSON object containing:
{
  "next_node": "simple_llm" | "worker",
  "query_complexity": "simple" | "complex",
  "routing_reason": "Brief explanation of your routing decision"
}

Be decisive and efficient. Your routing decisions directly impact system performance and user experience."""


SIMPLE_LLM_SYSTEM_PROMPT = """You are Kazma (كاظمه), a helpful AI assistant designed for Arabic-first interactions.

Your role is to provide fast, direct responses to simple queries without using tools.

## Guidelines:
- Respond in the same language and dialect the user uses (Arabic, English, or mixed)
- Be warm, culturally appropriate, and respectful (Majlis Mode)
- Keep responses concise and direct
- Do not attempt to use tools or perform complex operations
- If a query is too complex, politely explain that it needs to be routed to the worker

## Cultural Context:
- Understand Kuwaiti/Gulf Arabic dialects
- Use appropriate honorifics and cultural references
- Maintain a friendly, conversational tone typical of Arabic hospitality"""


WORKER_SYSTEM_PROMPT = """You are the Worker node of Kazma (كاظمه), designed to handle complex multi-step tasks.

Your role is to execute complex operations that may require:
- Tool usage (file operations, web search, APIs)
- Multi-step reasoning and planning
- Research and information gathering
- Code execution and debugging
- Data analysis and processing

## Guidelines:
- Break down complex tasks into clear steps
- Use available tools efficiently
- Report progress clearly
- Handle errors gracefully and provide recovery suggestions
- Always respond in Arabic when the user writes in Arabic
- Maintain cultural awareness (Majlis Mode)

## Tool Usage:
- Only use tools when necessary for the task
- Verify tool results before proceeding
- Log all tool executions for transparency
- Respect file system permissions and security boundaries"""


# ---------------------------------------------------------------------------
# Supervisor Node
# ---------------------------------------------------------------------------


async def supervisor_node(state: SupervisorState) -> dict[str, object]:
    """The Supervisor node - intelligent routing decision maker.

    This node analyzes the user's query and decides whether to route to:
    - simple_llm: For fast, direct responses to simple queries
    - worker: For complex tasks requiring tools and multi-step execution

    Args:
        state: Current workflow state containing messages and context

    Returns:
        Updated state with next_node, query_complexity, and routing_reason
    """
    logger.info("[SUPERVISOR] Evaluating routing decision")

    messages = state.get("messages", [])
    if not messages:
        logger.warning("[SUPERVISOR] No messages in state, defaulting to simple_llm")
        return {
            "next_node": "simple_llm",
            "query_complexity": "simple",
            "routing_reason": "No messages - default to simple path",
        }

    # Get the last user message
    last_message = messages[-1]
    user_content = last_message.get("content", "").lower() if isinstance(last_message, dict) else str(last_message)

    logger.debug("[SUPERVISOR] Analyzing query: %s", user_content[:100])

    # Simple query indicators
    simple_indicators = [
        "شلونك", "شخبارك", "مرحبا", "السلام", "اهلا", "هلا",
        "how are you", "hello", "hi", "hey",
        "شكرا", "thanks", "thank you",
        "مع السلامة", "bye", "goodbye",
        "صباح", "مساء", "morning", "evening",
    ]

    # Complex query indicators
    complex_indicators = [
        "بحث", "search", "find", "look up",
        "ملف", "file", "read", "write", "save",
        "كود", "code", "برمجة", "programming",
        "تحليل", "analyze", "analysis",
        "حساب", "calculate", "compute",
        "قاعدة بيانات", "database", "api",
        "تنزيل", "download", "upload",
        "تقرير", "report", "document",
        "قائمة", "list", "directory",
    ]

    # Determine complexity
    is_simple = any(indicator in user_content for indicator in simple_indicators)
    is_complex = any(indicator in user_content for indicator in complex_indicators)

    # Length-based heuristic (very short queries are often simple)
    is_short = len(user_content.split()) < 5

    # Decision logic
    if is_complex:
        next_node = "worker"
        complexity = "complex"
        reason = "Query contains complex task indicators requiring tools or multi-step execution"
    elif is_simple or is_short:
        next_node = "simple_llm"
        complexity = "simple"
        reason = "Query is a simple greeting or short interaction suitable for direct response"
    else:
        # Default to worker for ambiguous queries (safer to have tools available)
        next_node = "worker"
        complexity = "complex"
        reason = "Query is ambiguous - defaulting to worker path for safety"

    # Check if query is in Arabic to activate cultural context
    has_arabic = any("\u0600" <= char <= "\u06FF" for char in user_content)
    cultural_active = state.get("cultural_context_active", True) and has_arabic

    logger.info(
        "[SUPERVISOR] Routing decision: next_node=%s, complexity=%s, reason=%s",
        next_node,
        complexity,
        reason,
    )

    return {
        "next_node": next_node,
        "query_complexity": complexity,
        "routing_reason": reason,
        "cultural_context_active": cultural_active,
    }


# ---------------------------------------------------------------------------
# Simple LLM Node
# ---------------------------------------------------------------------------


async def simple_llm_node(state: SupervisorState) -> dict[str, object]:
    """The Simple LLM node - fast responses for simple queries.

    This node handles greetings, casual conversation, and simple questions
    without invoking tools or complex reasoning.

    Args:
        state: Current workflow state

    Returns:
        Updated state with assistant response added to messages
    """
    logger.info("[SIMPLE_LLM] Processing simple query")

    messages = state.get("messages", [])
    if not messages:
        logger.warning("[SIMPLE_LLM] No messages in state")
        return {"messages": []}

    # In a real implementation, this would call the LLM provider
    # For now, we provide a placeholder response
    last_message = messages[-1]
    user_content = last_message.get("content", "") if isinstance(last_message, dict) else str(last_message)

    # Simple response logic (placeholder - would use actual LLM in production)
    has_arabic = any("\u0600" <= char <= "\u06FF" for char in user_content)

    if has_arabic:
        response = "أهلاً وسهلاً! أنا كاظمه، مساعدك الذكي. كيف يمكنني مساعدتك اليوم؟"
    else:
        response = "Hello! I'm Kazma, your intelligent assistant. How can I help you today?"

    logger.info("[SIMPLE_LLM] Generated response: %s", response[:100])

    # Add assistant response to messages
    from langchain_core.messages import AIMessage

    ai_message = AIMessage(content=response)
    return {"messages": [ai_message]}


# ---------------------------------------------------------------------------
# Worker Node
# ---------------------------------------------------------------------------


async def worker_node(state: SupervisorState) -> dict[str, object]:
    """The Worker node - handles complex multi-step tasks.

    This node is responsible for:
    - Tool execution (file operations, web search, APIs)
    - Multi-step reasoning and planning
    - Research and information gathering
    - Complex problem solving

    Args:
        state: Current workflow state

    Returns:
        Updated state with worker results added to messages
    """
    logger.info("[WORKER] Processing complex task")

    messages = state.get("messages", [])
    if not messages:
        logger.warning("[WORKER] No messages in state")
        return {"messages": []}

    last_message = messages[-1]
    user_content = last_message.get("content", "") if isinstance(last_message, dict) else str(last_message)

    has_arabic = any("\u0600" <= char <= "\u06FF" for char in user_content)

    # Placeholder response - in production, this would:
    # 1. Analyze the task
    # 2. Plan the execution steps
    # 3. Invoke tools as needed
    # 4. Aggregate results
    # 5. Generate a comprehensive response

    if has_arabic:
        response = "سأقوم بمعالجة طلبك المعقد. هذا يتطلب استخدام الأدوات والتنفيذ متعدد الخطوات. (Processing complex task - this requires tool usage and multi-step execution.)"
    else:
        response = "I will process your complex request. This requires tool usage and multi-step execution."

    logger.info("[WORKER] Generated response: %s", response[:100])

    # Add assistant response to messages
    from langchain_core.messages import AIMessage

    ai_message = AIMessage(content=response)
    return {"messages": [ai_message]}


# ---------------------------------------------------------------------------
# Routing Logic
# ---------------------------------------------------------------------------


def route_after_supervisor(state: SupervisorState) -> str:
    """Conditional routing after the Supervisor node.

    Args:
        state: Current workflow state

    Returns:
        Next node name ("simple_llm", "worker", or END)
    """
    next_node = state.get("next_node", "")
    logger.debug("[ROUTE] Supervisor decided next_node=%s", next_node)

    if next_node == "simple_llm":
        return "simple_llm"
    elif next_node == "worker":
        return "worker"
    else:
        logger.warning("[ROUTE] Unknown next_node '%s', defaulting to END", next_node)
        return END


def route_after_worker(state: SupervisorState) -> str:
    """Conditional routing after the Worker node.

    In a full implementation, this could loop back to the supervisor
    for additional routing decisions. For now, we end the workflow.

    Args:
        state: Current workflow state

    Returns:
        END (terminates the workflow)
    """
    logger.debug("[ROUTE] Worker completed, ending workflow")
    return END


def route_after_simple_llm(state: SupervisorState) -> str:
    """Conditional routing after the Simple LLM node.

    Args:
        state: Current workflow state

    Returns:
        END (terminates the workflow)
    """
    logger.debug("[ROUTE] Simple LLM completed, ending workflow")
    return END


# ---------------------------------------------------------------------------
# Graph Compilation
# ---------------------------------------------------------------------------


def build_supervisor_graph() -> StateGraph:
    """Build and compile the Supervisor workflow graph.

    The graph structure:
    - Entry point: supervisor_node
    - supervisor_node -> simple_llm (for simple queries)
    - supervisor_node -> worker (for complex tasks)
    - simple_llm -> END
    - worker -> END

    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("[GRAPH] Building Supervisor workflow graph")

    # Create the graph with SupervisorState
    graph = StateGraph(SupervisorState)

    # Add nodes
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("simple_llm", simple_llm_node)
    graph.add_node("worker", worker_node)

    # Set entry point
    graph.set_entry_point("supervisor")

    # Add conditional edges from supervisor
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "simple_llm": "simple_llm",
            "worker": "worker",
            END: END,
        },
    )

    # Add edges from leaf nodes to END
    graph.add_edge("simple_llm", END)
    graph.add_edge("worker", END)

    # Compile the graph
    compiled_graph = graph.compile()

    logger.info("[GRAPH] Supervisor workflow graph compiled successfully")
    return compiled_graph


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


async def run_supervisor_workflow(
    user_message: str,
    cultural_context_active: bool = True,
) -> SupervisorState:
    """Run the Supervisor workflow with a user message.

    Args:
        user_message: The user's input message
        cultural_context_active: Whether to activate Arabic cultural context

    Returns:
        Final state after workflow execution
    """
    logger.info("[WORKFLOW] Starting Supervisor workflow with message: %s", user_message[:100])

    from langchain_core.messages import HumanMessage

    # Initialize state
    initial_state = initial_supervisor_state()
    initial_state["messages"] = [HumanMessage(content=user_message)]
    initial_state["cultural_context_active"] = cultural_context_active

    # Build and run the graph
    graph = build_supervisor_graph()

    # Run the workflow
    final_state = await graph.ainvoke(initial_state)

    logger.info("[WORKFLOW] Supervisor workflow completed")
    return final_state


# ---------------------------------------------------------------------------
# Module Exports
# ---------------------------------------------------------------------------


__all__ = [
    "SupervisorState",
    "initial_supervisor_state",
    "supervisor_node",
    "simple_llm_node",
    "worker_node",
    "route_after_supervisor",
    "route_after_worker",
    "route_after_simple_llm",
    "build_supervisor_graph",
    "run_supervisor_workflow",
    "SUPERVISOR_SYSTEM_PROMPT",
    "SIMPLE_LLM_SYSTEM_PROMPT",
    "WORKER_SYSTEM_PROMPT",
]

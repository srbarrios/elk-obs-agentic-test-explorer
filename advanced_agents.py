"""
Advanced Testing Agents for Elastic Observability
Implements specialized agents for fuzzing, integrity checking, autonomous exploration, and AI evaluation.
"""

import operator
from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langchain.agents import create_agent
from playwright.async_api import Page

from custom_tools import get_screenshot_tool
from ai_assistant_tools import get_ai_assistant_interaction_tool


# ---------------------------------------------------------
# State Definition
# ---------------------------------------------------------
class AdvancedAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_agent: str


# ---------------------------------------------------------
# Advanced Swarm Graph Builder
# ---------------------------------------------------------
def build_advanced_graph(base_tools: list, active_page: Page, checkpointer, kibana_url: str):
    """
    Builds a swarm graph with advanced testing agents:
    - Fuzzer Agent: Generates and injects malformed payloads
    - Auditor Agent: Cross-checks data integrity
    - Explorer Agent: Autonomous UI exploration with chaos testing
    - Evaluator Agent: Tests the Elastic AI Assistant
    """
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)

    # Initialize page-aware tools
    bug_screenshot = get_screenshot_tool(page=active_page)
    ai_assistant_tool = get_ai_assistant_interaction_tool(page=active_page)

    # Import advanced tools
    from fuzzing_tools import (
        generate_malformed_otel_payloads,
        inject_telemetry_to_elastic,
        inject_and_track_payload,
        verify_payload_integrity
    )
    from ai_assistant_tools import (
        generate_ai_assistant_questions,
        validate_esql_query,
        evaluate_ai_assistant_accuracy
    )

    # Tool bundles for different agents
    fuzzer_tools = [
        generate_malformed_otel_payloads,
        inject_telemetry_to_elastic,
        bug_screenshot
    ]

    auditor_tools = [
        inject_and_track_payload,
        verify_payload_integrity,
        bug_screenshot
    ]

    explorer_tools = base_tools + [bug_screenshot]

    evaluator_tools = [
        generate_ai_assistant_questions,
        ai_assistant_tool,
        validate_esql_query,
        evaluate_ai_assistant_accuracy,
        bug_screenshot
    ]

    # --- Agent System Prompts ---

    fuzzer_prompt = SystemMessage(content=f"""You are the Chaos Testing Agent with the persona of a "Chaotic System".

Your mission: Generate and inject malformed OpenTelemetry payloads to test Elastic Observability's resilience.

WORKFLOW:
1. Use 'generate_malformed_otel_payloads' to create 10-20 corrupted log/trace payloads
   - Request diverse mutation types: SQL injection, type mismatches, invalid timestamps, etc.

2. For each generated payload, use 'inject_telemetry_to_elastic' to send it to the Elastic APM server
   - Analyze the response carefully

3. Evaluate the results:
   - PASS: HTTP 400 (graceful rejection) or 202 (accepted but validated)
   - FAIL: HTTP 500 (crash), timeout, or improper acceptance of dangerous payloads

4. If you find a FAIL case, use 'capture_bug_screenshot' immediately to document it

5. At the end, summarize:
   - Total payloads tested
   - Success rate of graceful error handling
   - Critical vulnerabilities found (500 errors, crashes)

IMPORTANT RULES:
- NEVER stop after the first failure - test ALL generated payloads
- Document EVERY crash with a screenshot
- Be systematic: test logs first, then traces
- Your goal is to BREAK things safely to find resilience gaps
""")

    auditor_prompt = SystemMessage(content=f"""You are the Data Integrity Auditor Agent.

Your mission: Verify that Elastic Observability preserves data integrity during ingestion and indexing.

WORKFLOW:
1. Generate a complex, multi-field telemetry payload with:
   - Nested objects
   - Arrays of metrics
   - Special characters in string fields
   - High-precision numeric values
   - Custom attributes

2. Use 'inject_and_track_payload' with a unique tracking ID
   - This stores the original payload for later comparison

3. Wait for indexing to complete (suggest 5-10 seconds)

4. Query Elasticsearch/Kibana to retrieve the indexed document
   - Use the tracking ID in your query
   - Extract the full document JSON

5. Use 'verify_payload_integrity' to compare original vs retrieved
   - The tool will perform deep field-by-field comparison

6. If differences are found:
   - Use 'capture_bug_screenshot' to document the discrepancy
   - Analyze if it's a bug or expected transformation (e.g., timestamp normalization)

7. Repeat for 5-10 different payload structures

CRITICAL: Pay special attention to:
- Numeric precision loss
- Timestamp format changes
- Field truncation
- Missing nested objects
- Type conversions
""")

    explorer_prompt = SystemMessage(content=f"""You are the Autonomous Explorer Agent with the persona of an "SRE Investigating an Incident".

Kibana instance: {kibana_url}

Your mission: Randomly explore Kibana's Observability features looking for bugs, crashes, timeouts, and UI errors.

EXPLORATION STRATEGY:
1. Start at a random Observability app (Logs, APM, Metrics, Alerts)

2. Perform chaotic interactions:
   - Click random filters (host.name, service.name, etc.)
   - Change time ranges rapidly (Last 15 min → Last 7 days → Custom range)
   - Add multiple table columns
   - Expand/collapse detail views
   - Navigate between cross-linked data (APM trace → related logs)
   - Scroll through large data tables

3. Watch for ERROR SIGNALS:
   - Infinite loading spinners (wait > 30 seconds)
   - "Shard failed" or "Request timeout" errors
   - HTTP 500 error messages
   - Blank/white screens
   - Browser console errors (check with browser tools)

4. When you find an error:
   - Use 'capture_bug_screenshot' IMMEDIATELY
   - Extract any error text from the DOM
   - Note the exact steps that led to the error

5. Continue exploring for 10-15 different interaction paths

IMPORTANT:
- Be genuinely random - don't follow predictable patterns
- Test edge cases: extreme time ranges, many filters combined
- Intentionally stress-test the UI by rapid clicking
- Your goal is to find the breaking points
""")

    evaluator_prompt = SystemMessage(content=f"""You are the AI Assistant Evaluator Agent.

Kibana instance: {kibana_url}

Your mission: Test the Elastic AI Assistant by generating questions, submitting them, and validating the responses.

WORKFLOW:
1. Use 'generate_ai_assistant_questions' to create 10-15 test questions
   - Specify a realistic scenario (e.g., 'slow_checkout_service', 'memory_leak_investigation')
   - Request a mix of complexity levels

2. Navigate to the Elastic AI Assistant in Kibana
   - Usually accessible via a chat icon or dedicated page
   - Use browser tools to locate the assistant

3. For each generated question:
   - Use 'submit_question_to_ai_assistant' to ask it
   - The tool will extract the response and any generated ES|QL query

4. For responses with ES|QL queries:
   - Use 'validate_esql_query' to check syntax and semantics
   - Verify the query actually addresses the question

5. If validation fails:
   - Use 'capture_bug_screenshot' to document the hallucination
   - Note the specific issue (wrong index, bad aggregation, etc.)

6. After testing all questions:
   - Use 'evaluate_ai_assistant_accuracy' to get an overall score
   - Summarize patterns in failures (e.g., "struggles with time-range queries")

EVALUATION CRITERIA:
- Syntax correctness of ES|QL queries
- Semantic appropriateness (does it answer the question?)
- Absence of hallucinations (made-up field names, wrong indices)
- Response clarity and helpfulness

CRITICAL: Document ALL failures with screenshots and detailed analysis.
""")

    # --- Create Agents ---
    fuzzer_agent = create_agent(llm, tools=fuzzer_tools, system_prompt=fuzzer_prompt)
    auditor_agent = create_agent(llm, tools=auditor_tools, system_prompt=auditor_prompt)
    explorer_agent = create_agent(llm, tools=explorer_tools, system_prompt=explorer_prompt)
    evaluator_agent = create_agent(llm, tools=evaluator_tools, system_prompt=evaluator_prompt)

    # --- Node Wrappers ---
    async def fuzzer_node(state: AdvancedAgentState):
        return {"messages": (await fuzzer_agent.ainvoke(state))["messages"]}

    async def auditor_node(state: AdvancedAgentState):
        return {"messages": (await auditor_agent.ainvoke(state))["messages"]}

    async def explorer_node(state: AdvancedAgentState):
        return {"messages": (await explorer_agent.ainvoke(state))["messages"]}

    async def evaluator_node(state: AdvancedAgentState):
        return {"messages": (await evaluator_agent.ainvoke(state))["messages"]}

    async def supervisor_node(state: AdvancedAgentState) -> dict:
        supervisor_prompt = (
            "You are the Advanced Testing Orchestrator. Decide which specialized agent should work next.\n\n"
            "Available agents:\n"
            "- 'fuzzer_agent': Tests payload resilience with malformed data\n"
            "- 'auditor_agent': Verifies data integrity during ingestion\n"
            "- 'explorer_agent': Autonomously explores Kibana UI for bugs\n"
            "- 'evaluator_agent': Tests the Elastic AI Assistant\n\n"
            "If the mission goal is achieved, respond with 'FINISH'."
        )
        routing_llm = llm.with_structured_output(
            schema={
                "type": "object",
                "properties": {
                    "next": {
                        "type": "string",
                        "enum": ["fuzzer_agent", "auditor_agent", "explorer_agent", "evaluator_agent", "FINISH"]
                    }
                }
            }
        )
        decision = await routing_llm.ainvoke(
            [SystemMessage(content=supervisor_prompt)] + state["messages"]
        )
        return {"next_agent": decision["next"]}

    # --- Build Graph ---
    workflow = StateGraph(AdvancedAgentState)
    workflow.add_node("Supervisor", supervisor_node)
    workflow.add_node("fuzzer_agent", fuzzer_node)
    workflow.add_node("auditor_agent", auditor_node)
    workflow.add_node("explorer_agent", explorer_node)
    workflow.add_node("evaluator_agent", evaluator_node)

    for agent in ["fuzzer_agent", "auditor_agent", "explorer_agent", "evaluator_agent"]:
        workflow.add_edge(agent, "Supervisor")

    workflow.add_conditional_edges(
        "Supervisor",
        lambda state: state["next_agent"],
        {
            "fuzzer_agent": "fuzzer_agent",
            "auditor_agent": "auditor_agent",
            "explorer_agent": "explorer_agent",
            "evaluator_agent": "evaluator_agent",
            "FINISH": END
        }
    )

    workflow.set_entry_point("Supervisor")
    return workflow.compile(checkpointer=checkpointer)
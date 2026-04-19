import operator
from typing import Annotated, Any, Dict, List, Sequence, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langchain.agents import create_agent
from playwright.async_api import Page

from agentic_explorer.tools.common.custom_tools import get_visual_validation_tool, get_screenshot_tool
from agentic_explorer.tools.browser.engine import (
    get_browser_command_tool,
    get_dom_snapshot_tool,
    get_code_generator_tool,
    get_action_tape,
)

# ---------------------------------------------------------
# State Definition
# ---------------------------------------------------------
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_agent: str
    # Immutable chronological log of deterministic browser commands.
    action_tape: Annotated[List[Dict[str, Any]], operator.add]

# ---------------------------------------------------------
# Swarm Graph Builder
# ---------------------------------------------------------
def build_graph(base_tools: list, active_page: Page, checkpointer, kibana_url: str):
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0) 

    # Initialize page-aware tools
    vision_validation = get_visual_validation_tool(page=active_page)
    bug_screenshot = get_screenshot_tool(page=active_page)

    # Record-and-Translate deterministic hands
    execute_browser_command = get_browser_command_tool(page=active_page)
    get_dom_snapshot = get_dom_snapshot_tool(page=active_page)
    generate_reproduction_spec = get_code_generator_tool(kibana_url=kibana_url)

    # NOTE: base_tools historically carried raw PlayWrightBrowserToolkit tools.
    # In the Record-and-Translate architecture, agents MUST NOT drive the browser
    # directly — they emit JSON intents via `execute_browser_command` instead.
    # We therefore only pass through non-browser base tools (docs / skills / MCP).
    non_browser_base_tools = [
        tool_obj for tool_obj in base_tools
        if getattr(tool_obj, "name", "") not in {
            "click_element", "navigate_browser", "previous_webpage",
            "extract_text", "extract_hyperlinks", "get_elements",
            "current_webpage",
        }
    ]

    # Bundle tools for specific agents
    dom_tools = non_browser_base_tools + [
        execute_browser_command,
        get_dom_snapshot,
        bug_screenshot,
        generate_reproduction_spec,
    ]
    visual_tools = dom_tools + [vision_validation]

    global_qa_rule = (
        " ARCHITECTURE — RECORD & TRANSLATE: You are the *brain*. You do NOT touch the browser directly."
        " To interact with Kibana you MUST emit strict JSON commands to 'execute_browser_command'."
        " Use 'get_dom_snapshot' to inspect the page before choosing a selector."
        " Every command is appended to an immutable Action Tape. Supported actions:"
        " navigate, click, fill, press, select_option, hover, wait_for, scroll, extract_text, snapshot."
        " Example: execute_browser_command({\"action\":\"click\",\"selector\":\"[data-test-subj='logsStreamTab']\"})."
        " BEFORE planning, you MUST use your documentation tools (MCP)"
        " to look up the expected behaviors for the module you are testing. Do not guess."
        " IMPORTANT: If you discover any UI error, missing element, tool failure, or visual anomaly,"
        " you MUST (1) invoke 'capture_bug_screenshot' to save visual evidence, then"
        " (2) invoke 'generate_reproduction_spec' so the Action Tape is translated into a"
        " runnable Playwright .spec.ts that the developer can execute locally."
    )

    # --- Agent Definitions ---
    logs_prompt = SystemMessage(content=(
        f"You are the Kibana Logs QA Analyst. The Kibana instance is located at {kibana_url}."
        "Test Log stream auto-refresh, KQL search bar behavior, log detail flyouts, and highlight rendering. "
        "Drive the UI exclusively through 'execute_browser_command' JSON intents." + global_qa_rule
    ))
    logs_agent = create_agent(llm, tools=dom_tools, system_prompt=logs_prompt)

    apm_prompt = SystemMessage(content=(
        f"You are the Kibana APM QA Analyst. The Kibana instance is located at {kibana_url}."
        "Test Service Maps (complex SVG/Canvas rendering), Trace timeline visualizations, and waterfall views. "
        "You MUST invoke 'analyze_visual_state' to validate the graphical rendering." + global_qa_rule
    ))
    apm_agent = create_agent(llm, tools=visual_tools, system_prompt=apm_prompt)

    metrics_prompt = SystemMessage(content=(
        f"You are the Kibana Metrics QA Analyst. The Kibana instance is located at {kibana_url}. Your focus is infrastructure monitoring. "
        "Explore Metrics Explorer charts, Inventory views (waffle maps/groupings), and time-series data. "
        "Use 'analyze_visual_state' to validate complex chart rendering and waffle map layouts." + global_qa_rule
    ))
    metrics_agent = create_agent(llm, tools=visual_tools, system_prompt=metrics_prompt)

    synthetics_prompt = SystemMessage(content=(
        f"You are the Kibana Synthetics & Uptime QA Analyst. The Kibana instance is located at {kibana_url}."
        "Validate Monitor status grids, geographical availability maps, and step-by-step journey playbacks. "
        "Use 'analyze_visual_state' to verify map pins and journey visualizations." + global_qa_rule
    ))
    synthetics_agent = create_agent(llm, tools=visual_tools, system_prompt=synthetics_prompt)

    alerting_prompt = SystemMessage(content=(
        f"You are the Kibana Alerting & Rules QA Analyst. The Kibana instance is located at {kibana_url}."
        "Test Rule creation wizards, threshold slider inputs, and alert notification flyouts. "
        "Interact only by emitting JSON intents to 'execute_browser_command'." + global_qa_rule
    ))
    alerting_agent = create_agent(llm, tools=dom_tools, system_prompt=alerting_prompt)

    agent_registry = {
        "logs_agent": logs_agent,
        "apm_agent": apm_agent,
        "metrics_agent": metrics_agent,
        "synthetics_agent": synthetics_agent,
        "alerting_agent": alerting_agent,
    }
    agent_names = tuple(agent_registry.keys())

    # --- Node Wrappers ---
    async def _run(agent, state: AgentState, config=None):
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "default") if config else "default"
        before = len(get_action_tape(thread_id))
        out = await agent.ainvoke(state, config=config) if config else await agent.ainvoke(state)
        new_tape = list(get_action_tape(thread_id)[before:])
        return {"messages": out["messages"], "action_tape": new_tape}

    def _make_node(agent):
        async def _node(state: AgentState, config=None):
            return await _run(agent, state, config)

        return _node

    node_functions = {
        name: _make_node(agent)
        for name, agent in agent_registry.items()
    }

    async def supervisor_node(state: AgentState) -> dict:
        available_agents = ", ".join(f"'{name}'" for name in agent_names)
        supervisor_prompt = (
            "You are the QA Orchestrator. Decide who should test next based on the task. "
            f"Available agents: {available_agents}. "
            "If the overarching goal of the prompt is achieved, respond with 'FINISH'."
        )
        routing_llm = llm.with_structured_output(
            schema={"type": "object", "properties": {"next": {"type": "string", "enum": [*agent_names, "FINISH"]}}}
        )
        decision = await routing_llm.ainvoke([SystemMessage(content=supervisor_prompt), *list(state["messages"])])
        return {"next_agent": decision["next"]}

    # --- Graph Compilation ---
    workflow = StateGraph(AgentState)  # type: ignore[arg-type]
    workflow.add_node("Supervisor", supervisor_node)  # type: ignore[arg-type]
    for name, node_fn in node_functions.items():
        workflow.add_node(name, node_fn)  # type: ignore[arg-type]

    for agent in agent_names:
        workflow.add_edge(agent, "Supervisor")

    route_map = {name: name for name in agent_names}
    route_map["FINISH"] = END

    workflow.add_conditional_edges(
        "Supervisor",
        lambda state: state["next_agent"],
        route_map,
    )
    workflow.set_entry_point("Supervisor")
    return workflow.compile(checkpointer=checkpointer)

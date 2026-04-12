import operator
from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langchain.agents import create_agent
from playwright.async_api import Page

from custom_tools import get_visual_validation_tool, get_screenshot_tool

# ---------------------------------------------------------
# State Definition
# ---------------------------------------------------------
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_agent: str

# ---------------------------------------------------------
# Swarm Graph Builder
# ---------------------------------------------------------
def build_graph(base_tools: list, active_page: Page, checkpointer, kibana_url: str):
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0) 
    
    # Initialize page-aware tools
    vision_validation = get_visual_validation_tool(page=active_page)
    bug_screenshot = get_screenshot_tool(page=active_page)
    
    # Bundle tools for specific agents
    dom_tools = base_tools + [bug_screenshot]
    visual_tools = dom_tools + [vision_validation]

    global_qa_rule = (
        " BEFORE planning, you MUST use your documentation tools (MCP) "
        "to look up the expected behaviors for the module you are testing. Do not guess."
        " IMPORTANT: If you discover any UI error, missing element, tool failure, or visual anomaly, "
        "you MUST invoke the 'capture_bug_screenshot' tool to save evidence before continuing or finishing."
    )

    # --- Agent Definitions ---
    logs_prompt = SystemMessage(content=(
        f"You are the Kibana Logs QA Analyst. The Kibana instance is located at {kibana_url}."
        "Test Log stream auto-refresh, KQL search bar behavior, log detail flyouts, and highlight rendering. "
        "Interact using browser tools and extract text to verify UI state." + global_qa_rule
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
        "Rely heavily on browser tools to type into inputs, move sliders, and extract DOM text to verify wizards." + global_qa_rule
    ))
    alerting_agent = create_agent(llm, tools=dom_tools, system_prompt=alerting_prompt)

    # --- Node Wrappers ---
    async def logs_node(state: AgentState): return {"messages": (await logs_agent.ainvoke(state))["messages"]}
    async def apm_node(state: AgentState): return {"messages": (await apm_agent.ainvoke(state))["messages"]}
    async def metrics_node(state: AgentState): return {"messages": (await metrics_agent.ainvoke(state))["messages"]}
    async def synthetics_node(state: AgentState): return {"messages": (await synthetics_agent.ainvoke(state))["messages"]}
    async def alerting_node(state: AgentState): return {"messages": (await alerting_agent.ainvoke(state))["messages"]}

    async def supervisor_node(state: AgentState) -> dict:
        supervisor_prompt = (
            "You are the QA Orchestrator. Decide who should test next based on the task. "
            "Available agents: 'logs_agent', 'apm_agent', 'metrics_agent', 'synthetics_agent', 'alerting_agent'. "
            "If the overarching goal of the prompt is achieved, respond with 'FINISH'."
        )
        routing_llm = llm.with_structured_output(
            schema={"type": "object", "properties": {"next": {"type": "string", "enum": ["logs_agent", "apm_agent", "metrics_agent", "synthetics_agent", "alerting_agent", "FINISH"]}}}
        )
        decision = await routing_llm.ainvoke([SystemMessage(content=supervisor_prompt)] + state["messages"])
        return {"next_agent": decision["next"]}

    # --- Graph Compilation ---
    workflow = StateGraph(AgentState)
    workflow.add_node("Supervisor", supervisor_node)
    workflow.add_node("logs_agent", logs_node)
    workflow.add_node("apm_agent", apm_node)
    workflow.add_node("metrics_agent", metrics_node)
    workflow.add_node("synthetics_agent", synthetics_node)
    workflow.add_node("alerting_agent", alerting_node)

    for agent in ["logs_agent", "apm_agent", "metrics_agent", "synthetics_agent", "alerting_agent"]:
        workflow.add_edge(agent, "Supervisor")

    workflow.add_conditional_edges(
        "Supervisor",
        lambda state: state["next_agent"],
        {
            "logs_agent": "logs_agent", "apm_agent": "apm_agent",
            "metrics_agent": "metrics_agent", "synthetics_agent": "synthetics_agent",
            "alerting_agent": "alerting_agent", "FINISH": END
        }
    )
    workflow.set_entry_point("Supervisor")
    return workflow.compile(checkpointer=checkpointer)

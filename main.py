import asyncio
import os
import argparse
import yaml
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from playwright.async_api import async_playwright

from langchain_core.globals import set_verbose
set_verbose(True)

# --- Import our custom modules ---
from custom_tools import get_elastic_mcp_doc_tools, fetch_elastic_agent_skill
from agents import build_graph
from advanced_agents import build_advanced_graph

load_dotenv()
KIBANA_BASE_URL = os.getenv("KIBANA_URL", "http://localhost:5601")

async def main():
    if not os.getenv("GOOGLE_API_KEY"):
        raise ValueError("Please set your GOOGLE_API_KEY environment variable.")

    parser = argparse.ArgumentParser(description="Run Agentic Exploratory Tests")
    parser.add_argument("--missions", type=str, required=True, help="Path to the YAML missions file")
    parser.add_argument("--headed", action="store_true", help="Run browser with visible UI")
    parser.add_argument("--clear-memory", action="store_true", help="Delete the previous SQLite memory database")
    args = parser.parse_args()

    with open(args.missions, 'r') as f:
        config_data = yaml.safe_load(f)
        missions = config_data.get("missions", [])

    if not missions:
        print("❌ No missions found in YAML. Exiting.")
        return

    if args.clear_memory:
        print("\n🧹 Cleaning up previous memory files...")
        for mem_file in ["agent_memory.sqlite", "agent_memory.sqlite-wal", "agent_memory.sqlite-shm"]:
            if os.path.exists(mem_file):
                os.remove(mem_file)
                print(f"  - Deleted {mem_file}")

    print("📖 Connecting to Elastic Docs MCP Server...")
    doc_tools = await get_elastic_mcp_doc_tools()
    skill_tools = [fetch_elastic_agent_skill]
    
    print("⚙️ Initializing Authenticated Browser and Persistent Database...")
    async with async_playwright() as p, AsyncSqliteSaver.from_conn_string("agent_memory.sqlite") as memory_saver:
        browser = await p.chromium.launch(headless=not args.headed, args=["--start-maximized"])
        
        if not os.path.exists("auth.json"):
            context = await browser.new_context(no_viewport=True)
        else:
            context = await browser.new_context(storage_state="auth.json", no_viewport=True)
        
        context.set_default_timeout(5000)
        context.set_default_navigation_timeout(15000)

        active_page = await context.new_page()

        # Monkey-patching for self-healing
        toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
        browser_tools = toolkit.get_tools()
        for t in browser_tools:
            original_arun = t._arun
            async def safe_arun(*args, orig=original_arun, **kwargs):
                try:
                    return await orig(*args, **kwargs)
                except Exception as e:
                    return f"Action Failed! Error: {str(e)}. Try a different strategy."
            t._arun = safe_arun

        print("👥 Compiling LangGraph Swarms...")
        base_tools = doc_tools + skill_tools + browser_tools

        # Build both standard and advanced graphs
        standard_app = build_graph(base_tools, active_page, memory_saver, KIBANA_BASE_URL)
        advanced_app = build_advanced_graph(base_tools, active_page, memory_saver, KIBANA_BASE_URL)

        for mission in missions:
            thread_id = str(mission["thread_id"])
            prompt = mission["prompt"]

            # Detect mission type based on thread_id keywords
            advanced_keywords = ["fuzzing", "integrity", "explorer", "ai_assistant", "chaos", "auditor", "evaluator"]
            is_advanced_mission = any(keyword in thread_id.lower() for keyword in advanced_keywords)

            mission_type = "ADVANCED" if is_advanced_mission else "STANDARD"
            app = advanced_app if is_advanced_mission else standard_app

            print(f"\n{'='*60}\n🚀 STARTING MISSION [{mission_type}]: {thread_id}\n{'='*60}")

            os.makedirs(f"report_{thread_id}", exist_ok=True)
            with open(f"report_{thread_id}/traces.log", "w", encoding="utf-8") as f:
                f.write(f"=== TRACES: {thread_id} ===\n")

            run_config = {"configurable": {"thread_id": thread_id}}
            existing_state = await app.aget_state(run_config)
            
            stream_input = {"messages": [HumanMessage(content=prompt)], "next_agent": ""} if not existing_state.values else None
            
            # Execute Mission
            max_retries = 5
            base_delay = 2

            for attempt in range(max_retries):
                try:
                    async for output in app.astream(stream_input, config=run_config, stream_mode="updates"):
                        for node_name, state_update in output.items():
                            header = f"\n{'='*40}\n🔄 STATE UPDATE FROM: {node_name}\n{'='*40}\n"
                            print(header)

                            if "messages" in state_update and state_update["messages"]:
                                messages = state_update["messages"] if isinstance(state_update["messages"], list) else [state_update["messages"]]

                                with open(f"report_{thread_id}/traces.log", "a", encoding="utf-8") as trace_file:
                                    trace_file.write(header)
                                    for msg in messages:
                                        if isinstance(msg.content, list):
                                            for block in msg.content:
                                                if isinstance(block, dict) and "extras" in block:
                                                    del block["extras"]
                                        msg.pretty_print()
                                        trace_file.write(msg.pretty_repr() + "\n")

                    # If we finish the stream successfully, break out of retry loop
                    break
                except Exception as e:
                    # Check for 503 Unavailable or general API transient errors
                    if "503" in str(e) or "UNAVAILABLE" in str(e).upper():
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"\n⚠️ Encountered transient error (503). Retrying in {delay} seconds (Attempt {attempt+1}/{max_retries})...")
                            await asyncio.sleep(delay)
                            # On retry, we resume from the checkpoint, so stream_input is None
                            stream_input = None
                        else:
                            print(f"\n❌ Failed after {max_retries} attempts.")
                            raise
                    else:
                        # Reraise if it's not a transient 503 error
                        raise

            # Generate Report (Using clean transcript pattern)
            print(f"\n📝 Generating Mission Report for {thread_id}...")
            final_state = await app.aget_state(run_config)
            mission_history = final_state.values.get("messages", [])
            
            transcript_lines = []
            for msg in mission_history:
                text_content = "".join([b.get("text", "") for b in msg.content if isinstance(b, dict) and "text" in b]) if isinstance(msg.content, list) else str(msg.content)
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    text_content += f" [Action: Used tools -> {', '.join([tc['name'] for tc in msg.tool_calls])}]"
                if text_content.strip():
                    transcript_lines.append(f"{msg.type.upper()}: {text_content.strip()}")
            
            clean_transcript = "\n".join(transcript_lines)

            report_llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)
            report_instruction = HumanMessage(content=(
                f"You are the Lead QA Engineer. Review the following agent test transcript for mission '{thread_id}'.\n\n"
                f"--- TEST TRANSCRIPT ---\n{clean_transcript}\n-----------------------\n\n"
                "Write a concise report formatted in Markdown. Include ONLY these sections:\n"
                "- **Mission ID & Objective**\n"
                "- **Actions Taken** (Brief summary)\n"
                "- **Issues Found** (Any UI errors, visual anomalies, or tool failures)\n"
                "- **Final Status** (PASS or FAIL based on whether the objective was achieved)\n\n"
                "Output ONLY plain Markdown text."
            ))
            
            for attempt in range(max_retries):
                try:
                    report_response = await report_llm.ainvoke([report_instruction])
                    break
                except Exception as e:
                    if "503" in str(e) or "UNAVAILABLE" in str(e).upper():
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"\n⚠️ Report generation transient error. Retrying in {delay} seconds...")
                            await asyncio.sleep(delay)
                        else:
                            print(f"\n❌ Report generation failed after {max_retries} attempts.")
                            raise
                    else:
                        raise

            clean_report_text = report_response.content
            if isinstance(clean_report_text, list):
                clean_report_text = "".join([
                    block.get("text", "") for block in clean_report_text if isinstance(block, dict)
                ])

            with open(f"report_{thread_id}/test_report.md", "w", encoding="utf-8") as report_file:
                report_file.write(f"\n{clean_report_text}\n\n---\n")
        
        print("\n✅ All missions completed!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())


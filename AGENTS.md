# AGENTS.md 

## Project Overview
This repository is an Agentic Test Explorer PoC designed for automated, autonomous exploratory QA of Elastic Kibana's Observability features. It runs on an async Python runtime utilizing a LangGraph supervisor-worker swarm architecture, Playwright for browser automation, and Gemini models. 

## Architecture & Swarm Pattern
The system employs a Supervisor-Worker pattern implemented with LangGraph, where a Supervisor node routes tasks with structured enum outputs to specialized agents and loops workers back to itself until a `FINISH` state is reached.

### Agent Types
The `main.py` script automatically compiles either a standard or advanced graph based on `thread_id` keywords (e.g., `fuzzing`, `integrity`, `explorer`, `ai_assistant`, `chaos`, `auditor`, `evaluator`) defined in the mission YAML.

* **Standard QA Agents** (`agents.py`): The standard graph splits tools by modality.
    * **DOM-focused tools**: Used by the `logs_agent` (tests log streams, KQL search) and `alerting_agent` (tests rule wizards, threshold inputs).
    * **Visual validation tools**: Requires the `analyze_visual_state` tool. Used by the `apm_agent` (service maps, trace timelines), `metrics_agent` (explorer charts, waffle maps), and `synthetics_agent` (status grids, geo maps).
* **Advanced Testing Agents** (`advanced_agents.py`): Specialized for edge-case scenarios.
    * `fuzzer_agent`: A chaotic persona that generates malformed OTEL payloads and tests system resilience.
    * `auditor_agent`: A data integrity verifier that injects complex payloads and performs deep comparisons to detect corruption.
    * `explorer_agent`: An SRE persona that randomly explores the UI to find timeouts and crashes.
    * `evaluator_agent`: Generates test questions for the AI Assistant and validates generated ES|QL queries.

### State Management
* **Persistent Memory**: State is persisted via an SQLite checkpointer (`agent_memory.sqlite`), keyed by the `thread_id`.
* **Mission Isolation**: Each mission has a unique `thread_id` to isolate its memory; reusing a thread ID resumes the prior context.

## Custom Tools & Integrations
* **Browser Tools**: Standard Playwright tools for DOM interactions are monkey-patched in `main.py` (lines 71-77) to return recoverable errors instead of raising exceptions, enabling self-healing behavior.
* **MCP (Model Context Protocol) Tools**: Elastic docs are fetched via `https://www.elastic.co/docs/_mcp/`. A local skill loader also expects skills to be located at `./agent-skills/skills/<skill_name>/SKILL.md`.
* **Vision & Screenshots**: The screenshot tool captures full-page bug evidence and is thread-aware via `RunnableConfig`. The `analyze_visual_state` tool uses the Gemini vision model to validate UI rendering.
* **Advanced Tools**: 
    * APM ingestion requires the `ELASTIC_APM_SERVER_URL`. Tools include payload mutation and integrity cross-checking.
    * ES|QL validation requires the `ELASTICSEARCH_URL` and Kibana credentials to validate syntax and semantic correctness via the AI Assistant.

## Running the System

### Initial Setup
1.  **Install dependencies**: Requires `langchain`, `langchain-google-genai`, `langgraph`, `playwright`, `python-dotenv`, `pyyaml`, `pillow`, and `langchain-mcp-adapters`.
2.  **Install browser**: Run `playwright install chromium`.
3.  **Environment Variables**: Create a `.env` file requiring keys like `GOOGLE_API_KEY`, `KIBANA_URL`, `KIBANA_USERNAME`, `KIBANA_PASSWORD`, `ELASTICSEARCH_URL`, and `ELASTIC_APM_SERVER_URL`.
4.  **Authenticate**: Run `python auth_setup.py` for one-time Kibana authentication.

### Developer Workflows
Execute missions defined in YAML format (e.g., `missions/smoke.yaml` or `missions/advanced_all.yaml`):
* **Standard Run**: `python main.py --missions missions/smoke.yaml`
* **Headed Mode** (Debugging): `python main.py --missions missions/obs_metrics_inventory.yaml --headed`
* **Clear Memory**: `python main.py --missions missions/smoke.yaml --clear-memory`

## Output Artifacts
Every mission generates artifacts localized in a `report_<thread_id>/` directory.
* `traces.log`: Full message history with tool calls and responses.
* `test_report.md`: An LLM-generated summary detailing actions, issues, and status.
* `screenshots/`: Full-page screenshots captured when bugs are detected.

## Conventions When Changing Code
* **Global QA Rule**: This is strictly enforced in `agents.py`—agents must look up expected behavior in MCP docs first, never guess, and capture screenshot evidence immediately on failures.
* **Agent Modification**: If adding a new agent, you must update the system prompt, tool bundle, node wrapper function, supervisor enum, and conditional routing table.
* **Mission Modifications**: If adding a new advanced mission category, update both the mission `thread_id` naming and the `advanced_keywords` in `main.py`.
* **LLM Configuration**: The system currently runs on `gemini-3.1-flash-lite-preview` with `temperature=0` for reasoning, vision, and report generation.
* **Tool Outputs**: Keep tool outputs as machine-consumable strings or JSON, as many agent prompts expect parseable responses.
* **Selectors**: Be cautious with `ai_assistant_tools.py` selectors; comments flag them as placeholders, so always verify them against the live Kibana DOM. Ensure the browser context accounts for a 5s default timeout and 15s navigation timeout.
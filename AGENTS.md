# AGENTS.md 

## Project Overview
This repository is an Agentic Test Explorer Proof-of-Concept (PoC) designed for automated, autonomous exploratory QA of Elastic Kibana's Observability features. It runs on an async Python runtime utilizing a LangGraph supervisor-worker swarm architecture, Playwright for browser automation, and Gemini models.

## Architecture & Swarm Pattern
The system employs a Supervisor-Worker pattern implemented with LangGraph, where a Supervisor node routes tasks with structured enum outputs to specialized agents and loops workers back to itself until a `FINISH` state is reached.

### Record-and-Translate Browser Engine
A core architectural principle is the **brain/hands separation** implemented in `src/agentic_explorer/tools/browser/engine.py`:

* **Brain** — LangGraph agents emit strict JSON intents (e.g., `{"action":"click","selector":"[data-test-subj='logsStreamTab']"}`).
* **Hands** — The deterministic engine parses, validates, and executes each command with Playwright.
* **Action Tape** — Every command is appended to a per-thread, immutable `action_tape.jsonl` log stored in `report_<thread_id>/`.
* **Reproduction** — When a bug is found, `generate_reproduction_spec` translates the Action Tape into a runnable `reproduction_*.spec.ts` Playwright test file.

Supported JSON actions: `navigate`, `click`, `fill`, `press`, `select_option`, `hover`, `wait_for`, `scroll`, `extract_text`, `snapshot`.

### Agent Types
The `main.py` script automatically compiles either a standard or advanced graph based on `thread_id` keywords (e.g., `fuzzing`, `integrity`, `explorer`, `ai_assistant`, `chaos`, `auditor`, `evaluator`) defined in the mission YAML.

* **Standard QA Agents** (`src/agentic_explorer/orchestration/standard_graph.py`): The standard graph splits tools by modality.
    * **DOM-focused tools**: `execute_browser_command`, `get_dom_snapshot`, `capture_bug_screenshot`, `generate_reproduction_spec`, plus MCP/skill tools. Used by the `logs_agent` (tests log streams, KQL search) and `alerting_agent` (tests rule wizards, threshold inputs).
    * **Visual validation tools**: All DOM tools plus `analyze_visual_state`. Used by the `apm_agent` (service maps, trace timelines), `metrics_agent` (explorer charts, waffle maps), and `synthetics_agent` (status grids, geo maps).
* **Advanced Testing Agents** (`src/agentic_explorer/orchestration/advanced_graph.py`): Specialized for edge-case scenarios.
    * `fuzzer_agent`: A chaotic persona that generates malformed OTEL payloads and tests system resilience.
    * `auditor_agent`: A data integrity verifier that injects complex payloads and performs deep comparisons to detect corruption.
    * `explorer_agent`: An SRE persona that randomly explores the UI to find timeouts and crashes. Uses the full Record-and-Translate engine.
    * `evaluator_agent`: Generates test questions for the AI Assistant and validates generated ES|QL queries.

### State Management
* **Persistent Memory**: State is persisted via an SQLite checkpointer (`agent_memory.sqlite`), keyed by the `thread_id`.
* **Mission Isolation**: Each mission has a unique `thread_id` to isolate its memory; reusing a thread ID resumes the prior context.
* **`AgentState` / `AdvancedAgentState`**: Both state TypedDicts carry `messages`, `next_agent`, `step_count`, and `action_tape` (an append-only list of recorded browser commands).

## Custom Tools & Integrations
* **Record-and-Translate Browser Tools** (`src/agentic_explorer/tools/browser/engine.py`):
    * `execute_browser_command` — dispatches a JSON intent to Playwright, records to the Action Tape, and returns the resulting DOM snapshot.
    * `get_dom_snapshot` — read-only Accessibility Tree / JS DOM digest; does **not** write to the tape.
    * `generate_reproduction_spec` — translates the Action Tape into a `reproduction_*.spec.ts` Playwright script.
    * Raw `PlayWrightBrowserToolkit` tools (`click_element`, `navigate_browser`, etc.) are **filtered out** from agents; they exist only for the monkey-patch self-healing mechanism.
* **MCP (Model Context Protocol) Tools**: Elastic docs are fetched via `https://www.elastic.co/docs/_mcp/`. A local skill loader also expects skills to be located at `./agent-skills/skills/<skill_name>/SKILL.md`.
* **Vision & Screenshots**: The screenshot tool captures full-page bug evidence and is thread-aware via `RunnableConfig`. The `analyze_visual_state` tool uses the Gemini vision model to validate UI rendering.
* **Advanced Tools**: 
    * APM ingestion requires the `ELASTIC_APM_SERVER_URL`. Tools include payload mutation and integrity cross-checking.
    * ES|QL validation requires the `ELASTICSEARCH_URL` and Kibana credentials to validate syntax and semantic correctness via the AI Assistant.

## Running the System

### Initial Setup
1.  **Install dependencies**: `pip install -r requirements.txt` (or `uv sync`). Key packages: `langchain`, `langchain-google-genai`, `langgraph`, `playwright`, `python-dotenv`, `pyyaml`, `pillow`, `langchain-mcp-adapters`, `langgraph-checkpoint-sqlite`, `aiosqlite`, `httpx`.
2.  **Install browser**: Run `playwright install chromium`.
3.  **Environment Variables**: Create a `.env` file requiring keys like `GOOGLE_API_KEY`, `KIBANA_URL`, `KIBANA_USERNAME`, `KIBANA_PASSWORD`, `ELASTICSEARCH_URL`, and `ELASTIC_APM_SERVER_URL`.
4.  **Authenticate**: Run `agent-auth` for one-time Kibana authentication (saves `auth.json`).

### Developer Workflows
Execute missions defined in YAML format (e.g., `missions/smoke.yaml` or `missions/advanced_all.yaml`):
* **Standard Run**: `agent-explorer --missions missions/smoke.yaml`
* **Headed Mode** (Debugging): `agent-explorer --missions missions/obs_metrics_inventory.yaml --headed`
* **Clear Memory**: `agent-explorer --missions missions/smoke.yaml --clear-memory`
* **Custom Step Limit**: `agent-explorer --missions missions/smoke.yaml --max-steps 50` (default: 30; supervisor resets to Kibana homepage on limit and tries a new strategy)

## Output Artifacts
Every mission generates artifacts localized in a `report_<thread_id>/` directory:
* `traces.log`: Full message history with tool calls and responses.
* `test_report.md`: An LLM-generated summary detailing actions, issues, Action Tape stats, and status.
* `action_tape.jsonl`: Immutable, line-delimited JSON log of every deterministic browser command executed (used for reproduction).
* `reproduction_*.spec.ts`: Auto-generated Playwright TypeScript test files. Run with `npx playwright test reproduction_*.spec.ts --headed`.
* `screenshots/`: Full-page screenshots captured when bugs are detected.

## Conventions When Changing Code
* **Global QA Rule**: This is strictly enforced in `src/agentic_explorer/orchestration/standard_graph.py` and `advanced_graph.py` — agents must look up expected behavior in MCP docs first, never guess, and capture screenshot evidence + call `generate_reproduction_spec` immediately on failures.
* **Selector Policy (Engine-Enforced)**: `execute_browser_command` rejects brittle selectors at runtime. Priority order for selectors:
    1. `data-test-subj` attributes → `[data-test-subj='myButton']`
    2. ARIA labels / roles → `[aria-label='Search']`, `role='dialog'`
    3. Semantic HTML / visible text → `button:has-text('Save')`, `text='Apply'`
    * **Forbidden**: XPath (`//div`), positional CSS (`:nth-child(3)`, `div > span:nth-of-type(2)`). Always call `get_dom_snapshot` first.
* **Agent Modification**: If adding a new agent, you must update the system prompt, tool bundle, node wrapper function, supervisor enum, and conditional routing table.
* **Mission Modifications**: If adding a new advanced mission category, update both the mission `thread_id` naming and the `ADVANCED_KEYWORDS` tuple in `main.py`.
* **LLM Configuration**: The system currently runs on `gemini-3.1-flash-lite-preview` with `temperature=0` for reasoning, vision, and report generation.
* **Tool Outputs**: Keep tool outputs as machine-consumable strings or JSON, as many agent prompts expect parseable responses.
* **Selectors**: Be cautious with `src/agentic_explorer/tools/ai_assistant/tools.py` selectors; comments flag them as placeholders, so always verify them against the live Kibana DOM. Ensure the browser context accounts for a 5s default timeout and 15s navigation timeout.

# 🤖 Elastic Observability Agentic Test Explorer

An autonomous, AI-driven exploratory Quality Assurance (QA) framework designed to intelligently explore, test, and validate Elastic Kibana's Observability modules. 


Powered by a **LangGraph Swarm** architecture, **Playwright**, and **Google Gemini 3.1 Flash Lite Preview**, this Proof of Concept (PoC) goes beyond static testing. It dynamically routes tasks, explores the Kibana DOM, visually validates complex charts, fuzzes telemetry ingestion endpoints, self-heals from UI errors, proactively queries documentation via MCP, and writes its own Markdown executive test reports.

---

## 🏗️ Architecture

The framework is built on a **Supervisor-Worker Swarm** pattern. Based on the mission type (determined by the `thread_id` keyword), the system spins up either a **Standard** or **Advanced** routing graph.


```mermaid
graph TD
    User[User / CI] -->|YAML Missions| Main(main.py)
    
    Main -->|Standard Missions| S_Supervisor{QA Supervisor}
    Main -->|Advanced Missions| A_Supervisor{Adv. Supervisor}
    Main -->|Checkpoints| DB[(SQLite Memory)]

    subgraph Standard QA Swarm
        S_Supervisor -->|Routes| S_Logs[Logs Agent]
        S_Supervisor -->|Routes| S_APM[APM Agent]
        S_Supervisor -->|Routes| S_Metrics[Metrics Agent]
        S_Supervisor -->|Routes| S_Synth[Synthetics Agent]
        S_Supervisor -->|Routes| S_Alert[Alerting Agent]
        
        S_Logs --> S_Supervisor
        S_APM --> S_Supervisor
        S_Metrics --> S_Supervisor
        S_Synth --> S_Supervisor
        S_Alert --> S_Supervisor
    end

    subgraph Advanced Testing Swarm
        A_Supervisor -->|Routes| A_Fuzzer[Fuzzer Agent]
        A_Supervisor -->|Routes| A_Auditor[Auditor Agent]
        A_Supervisor -->|Routes| A_Explorer[Explorer Agent]
        A_Supervisor -->|Routes| A_Eval[Evaluator Agent]

        A_Fuzzer --> A_Supervisor
        A_Auditor --> A_Supervisor
        A_Explorer --> A_Supervisor
        A_Eval --> A_Supervisor
    end

    Standard QA Swarm --> Tools[Tools & APIs]
    Advanced Testing Swarm --> Tools

    subgraph Integrations
        Tools -->|DOM & Screenshots| PW[Playwright]
        Tools -->|Visual Validation| Vision[Gemini Vision]
        Tools -->|Behaviors| MCP[Elastic Docs MCP]
        Tools -->|Query & Test| KIB[Kibana / Elasticsearch]
        Tools -->|Ingest Payload| APM[APM Server]
    end
```

### Architecture Details
1. **Mission Dispatcher (`main.py`)**: Loads `missions/*.yaml` files and automatically provisions the correct graph network based on naming conventions. 
2. **Supervisor-Worker Flow**: A Supervisor node dynamically evaluates the workspace state and dispatches control to specialized worker nodes (e.g., Logs, APM, Fuzzer). 
3. **Tool Modality**: Agents access bound tools—Playwright for DOM operations, Gemini Vision for perceptual validation, and MCP (`elastic-docs`) to look up expected capabilities and avoid hallucinations.
4. **State & Memory (`agent_memory.sqlite`)**: An asynchronous SQLite checkpointer remembers agent states, allowing a reused `thread_id` to resume precisely where it left off.

---

## ✨ Key Features

* **Multi-Agent Swarm**: Uses a routing model to distribute tasks among highly specialized AI personas depending on standard UI testing or advanced chaos/fuzzing goals.
* **Self-Healing Browser Execution**: Playwright actions are monkey-patched to catch uncaught exceptions. If a selector changes, the error is fed back to the agent as natural language so it can adapt and try a new strategy instead of crashing.
* **Visual Validation**: Agents can take screenshots of complex Canvas/SVG elements (like Service Maps) and use Gemini Vision to analyze them for rendering anomalies.
* **Elastic MCP Integration**: Agents proactively query the Elastic Docs via the Model Context Protocol (MCP) to learn UI paths *before* executing actions.
* **Deep Telemetry & AI Evaluation**: Advanced swarms can inject malformed telemetry payloads and directly evaluate the Elastic AI Assistant's ES|QL generation capabilities.
* **Automated Artifact Generation**: Every test generates an isolated folder containing raw execution traces, bug screenshots, and an executive Markdown report.

---

## 🛠️ Setup & Prerequisites

### 1. Dependencies
Ensure you have Python 3.11+ installed. It is highly recommended to use a virtual environment.

```bash
python -m venv venv
source venv/bin/activate

# Install dependencies (ensure langchain, langgraph, playwright, google-genai are included)
pip install -r requirements.txt

# Install the Playwright Chromium browser
playwright install chromium
```

### 2. Environment Variables
Create a `.env` file in the root directory and add your API keys and Elastic environment details:

```env
GOOGLE_API_KEY="your_gemini_api_key_here"
KIBANA_URL="http://localhost:5601"
KIBANA_USERNAME="elastic"
KIBANA_PASSWORD="changeme"
ELASTICSEARCH_URL="http://localhost:9200"
ELASTIC_APM_SERVER_URL="http://localhost:8200"
ELASTIC_APM_SECRET_TOKEN=""
```

### 3. Authenticate Kibana
Initialize a reusable `auth.json` cookie file. This allows headless testing without requiring the agents to process the login screen on every run.

```bash
python auth_setup.py
```

---

## 🚀 Defining Missions & Usage

### Creating a Mission
Create YAML files (e.g., in a `missions/` directory) defining specific UI tests. Each mission needs a unique `thread_id` (for persistent memory routing) and a `prompt`.

```yaml
# missions/smoke.yaml
missions:
  - thread_id: "obs_logs_exploration_01"
    prompt: >
      Navigate to Observability Logs. Perform a KQL search for 'error'. 
      Verify the detail flyout renders correctly and highlights the search term.
```

### Running the Framework
Execute your test suite by pointing the main orchestrator to your mission file:

**Run a standard functional smoke test:**
```bash
python main.py --missions missions/smoke.yaml
```

**Run advanced/chaotic missions (Fuzzing, Integrity, AI Evaluation):**
```bash
python main.py --missions missions/advanced_all.yaml
```

**Run with a visible UI (Headed Mode) - Great for debugging:**
```bash
python main.py --missions missions/smoke.yaml --headed
```

**Clear agent memory to restart fresh:**
```bash
python main.py --missions missions/smoke.yaml --clear-memory
```

---

## 📂 Project Structure

* `main.py`: The core CLI entry point, swarm graph compiler, and orchestrator.
* `agents.py`: Swarm setup and state definitions for Standard functional QA agents.
* `advanced_agents.py`: Swarm setup for Chaos, Evaluator, and Fuzzing agents.
* `custom_tools.py`: Tool factory for visual validation, screenshot logic, and MCP/Skill connectors.
* `ai_assistant_tools.py`: Tools for deep ES|QL parsing and AI Assistant evaluation.
* `fuzzing_tools.py`: LLM-driven anomaly injections targeting the APM server schema.
* `auth_setup.py`: Utility script to save Kibana session state.
* `missions/`: Directory containing declarative `.yaml` files establishing test goals per thread.
* `report_<thread_id>/`: Generated artifact folders containing outputs for each specific run.

---

## 📊 Test Artifacts

For every mission executed, the framework generates a dedicated `report_<thread_id>` directory. Inside, you will find:
1. **`traces.log`**: A complete, human-readable audit trail of every thought, plan, and tool invocation the agent performed.
2. **`test_report.md`**: A concise executive summary generated by the AI detailing the objective, actions taken, bugs found, and a final PASS/FAIL status.
3. **`/screenshots/`**: High-resolution image evidence of any UI bugs, missing elements, or visual anomalies discovered by the agents.

---

## 🤖 Guide for Autonomous Agents
If you are an AI coding assistant contributing to this repository, please review the rules defined in `AGENTS.md` to understand conventions regarding execution flow, new agent registration, and tool behavior.
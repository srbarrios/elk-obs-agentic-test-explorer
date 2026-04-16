# Advanced Testing Scenarios - Agentic Test Explorer

This document describes the 4 advanced testing scenarios implemented with specialized LLM agents.

## 🎯 Overview

Traditional testing focuses on the "happy path" - perfect inputs, expected user flows, and standard configurations. These advanced scenarios use LLM agents to test what humans struggle to cover at scale: chaos, malformed data, autonomous exploration, and AI-testing-AI.

---

## 1. 🔥 Telemetry Fuzzing with LLM Agents

### The Problem
Testing that Elastic handles *perfect* logs is easy. Testing how it responds to malformed JSON, SQL injection in log messages, APM traces with future timestamps, or corrupted data structures requires generating thousands of edge-case variations - impractical for manual testing.

### The Agentic Solution
**Agent**: `fuzzer_agent` (Chaotic System Persona)

**Strategy**:
1. Agent receives standard OpenTelemetry schema (logs/traces)
2. LLM generates 500+ corrupted payload variations:
   - SQL injection: `service.name = "'; DROP TABLE logs;--"`
   - Type mismatches: `timestamp` as string instead of int64
   - Invalid dates: timestamps in year 2050, negative durations
   - Malformed JSON: broken UTF-8, circular references
3. Agent injects each payload via Elastic APM Server API
4. Autonomous evaluation of responses:
   - ✅ **PASS**: HTTP 400 (graceful rejection with error message)
   - ⚠️ **INVESTIGATE**: HTTP 202 (accepted - validate it's sanitized)
   - ❌ **FAIL**: HTTP 500 (crash), timeout, or silent corruption

### Value
The agent not only breaks things - it evaluates **resilience quality**. Finding a crash (500 error) is critical; finding improper acceptance of malicious payloads is a security issue.

### Running
```bash
python main.py --missions missions/fuzzing_telemetry.yaml
```

**Tools Used**:
- `generate_malformed_otel_payloads` - LLM-driven mutation engine
- `inject_telemetry_to_elastic` - API injection with response classification
- `capture_bug_screenshot` - Evidence capture for crashes

---

## 2. 🔍 Data Integrity Cross-Checking (Auditor Agent)

### The Problem
When Elastic ingests terabytes of logs, ingest pipelines (Grok patterns, processors) can silently mutate or drop fields. Examples:
- Numeric precision loss: `3.141592653589793` → `3.14159`
- Timestamp conversions: `2024-01-15T10:30:45.123Z` → `1705318245000`
- Field truncation: 10,000-character log message → 5,000 characters
- Nested object flattening: `{"user": {"profile": {"name": "X"}}}` → lost structure

Traditional tests check for field *existence*, not field *integrity*.

### The Agentic Solution
**Agent**: `auditor_agent` (Data Integrity Verifier)

**Workflow**:
1. Generate complex, high-precision payload with:
   - Deeply nested objects (5+ levels)
   - High-precision floats (15+ decimals)
   - Large arrays (100+ elements)
   - Special characters (unicode, null bytes, quotes)
2. Inject with unique `tracking_id` (stored in memory for comparison)
3. Wait for indexing (~10 seconds)
4. Query Elasticsearch to retrieve indexed document
5. **Deep field-by-field comparison**:
   - Original JSON vs retrieved JSON
   - Detect: type changes, truncation, missing fields, value mutations
6. If differences found: screenshot + detailed diff report

### Value
Catches subtle bugs that pass functional tests but corrupt production data. For example: discovering that a Grok pattern rounds metric values incorrectly, causing drift in monitoring dashboards.

### Running
```bash
python main.py --missions missions/integrity_verification.yaml
```

**Tools Used**:
- `inject_and_track_payload` - Injects with tracking ID, stores original in cache
- `verify_payload_integrity` - Recursive JSON diff with detailed error reporting

---

## 3. 🤖 Autonomous Explorer Agent (SRE Persona)

### The Problem
Kibana has infinite combinations of filters, time ranges, cross-app navigation paths. Manual testers follow documented flows. Real users:
- Rapidly add/remove 5 filters while data is loading
- Switch from "Last 15 minutes" to "Last 90 days" on high-cardinality indices
- Click from APM trace → related logs → back to metrics
- Expand 10 detail flyouts simultaneously

These chaotic patterns reveal bugs: infinite spinners, shard failures, race conditions, memory leaks.

### The Agentic Solution
**Agent**: `explorer_agent` (Autonomous SRE)

**Exploration Strategy**:
The agent embodies an SRE under pressure, randomly exploring with **no predefined script**:
1. Navigate to random Observability app (Logs, APM, Metrics, Alerts, Synthetics)
2. Perform chaotic interactions:
   - Add 5 filters simultaneously
   - Change time range *during* data loading
   - Sort tables by different columns rapidly
   - Cross-navigate: APM → Logs → Metrics
   - Test extreme ranges: custom range from 2000-2050
3. **Error detection**:
   - Infinite spinners (>30 seconds with no data)
   - "Shard failed" or "Request timeout" errors
   - HTTP 500 internal errors
   - Blank/white screens
   - Browser console JavaScript errors

### Value
Finds bugs that only appear under stress or unusual interaction sequences. Example: clicking "related logs" while a trace is still loading causes a race condition → blank screen.

### Running
```bash
python main.py --missions missions/autonomous_exploration.yaml --headed
```
*(Use `--headed` to watch the chaos unfold)*

**Tools Used**:
- Standard Playwright browser tools (clicks, navigation, extraction)
- `capture_bug_screenshot` - Documents every error with full context

---

## 4. 🧠 AI Assistant Evaluation (Testing AI with AI)

### The Context
Elastic has an **AI Assistant** that answers questions in natural language:
- User: *"Why is the checkout service slow?"*
- Assistant: Generates ES|QL query + explanation

**How do you test an AI to ensure it's not hallucinating?** Use AI agents to audit it.

### The Agentic Solution
**Agents**: `evaluator_agent` + Question Generator + ES|QL Validator

**Evaluation Loop**:
1. **Question Generation**: Agent creates 100 realistic SRE questions
   - Scenario-based: `slow_checkout_service`, `memory_leak`, `trace_analysis`
   - Complexity levels: low/medium/high
   - Example: *"Which services have high error rates AND latency > 500ms in us-east-1?"*

2. **Submission**: Agent navigates to Kibana AI Assistant UI, submits each question, extracts:
   - Response text
   - Generated ES|QL query

3. **Validation**:
   - **Syntax check**: Send ES|QL to Elasticsearch `_query` endpoint
   - **Semantic check**: LLM evaluates if query actually answers the question
   - **Hallucination detection**: Check for fake field names, wrong indices

4. **Evaluation Report**:
   - Accuracy score: `12/15 passed = 80%`
   - Failure patterns: *"Assistant struggles with multi-index joins"*
   - Screenshots of hallucinations

### Value
Ensures the AI Assistant is production-ready. Catches:
- Syntax errors in generated queries
- Semantic mismatches (query doesn't answer the question)
- Hallucinations (inventing field names like `service.fake_metric`)

### Running
```bash
python main.py --missions missions/ai_assistant_evaluation.yaml
```

**Tools Used**:
- `generate_ai_assistant_questions` - LLM creates test questions
- `submit_question_to_ai_assistant` - Interacts with Kibana UI (page-aware)
- `validate_esql_query` - Dual validation: Elasticsearch API + LLM semantic check
- `evaluate_ai_assistant_accuracy` - Calculates metrics and identifies patterns

---

## 🚀 Running All Advanced Scenarios

**Single command for all 4 scenarios**:
```bash
python main.py --missions missions/advanced_all.yaml
```

**Individual scenarios**:
```bash
# Fuzzing
python main.py --missions missions/fuzzing_telemetry.yaml

# Integrity
python main.py --missions missions/integrity_verification.yaml

# Autonomous exploration (visible browser recommended)
python main.py --missions missions/autonomous_exploration.yaml --headed

# AI Assistant evaluation
python main.py --missions missions/ai_assistant_evaluation.yaml
```

---

## 📊 Expected Outputs

Each mission generates a `report_<thread_id>/` directory:
- `traces.log` - Full agent conversation with tool calls
- `test_report.md` - Executive summary with findings
- `screenshots/` - Evidence for every bug found

**Example Findings**:
- **Fuzzing**: *"17/20 payloads gracefully rejected. 3 caused HTTP 500 errors - screenshots captured."*
- **Integrity**: *"Field `metrics.duration` lost 5 decimal places of precision. Original: 3.141592653589793, Retrieved: 3.14159"*
- **Explorer**: *"Found infinite spinner when switching time range during log loading. Steps to reproduce documented."*
- **AI Evaluator**: *"10/15 queries passed. Assistant hallucinated field name `host.fake_metric` in 3 queries."*

---

## 🔧 Configuration

Add to `.env`:
```bash
# Required for all scenarios
GOOGLE_API_KEY=<your-gemini-api-key>
KIBANA_URL=http://localhost:5601
KIBANA_USERNAME=elastic
KIBANA_PASSWORD=<password>

# Required for fuzzing + integrity scenarios
ELASTIC_APM_SERVER_URL=http://localhost:8200
ELASTIC_APM_SECRET_TOKEN=<optional>

# Required for AI Assistant + integrity scenarios
ELASTICSEARCH_URL=http://localhost:9200
```

---

## 🎓 Key Takeaways

These advanced scenarios represent a paradigm shift from **scripted testing** to **agentic testing**:

1. **Fuzzing**: LLM generates mutations humans wouldn't think of
2. **Integrity**: Agents compare data at scale, catching silent corruption
3. **Autonomous Exploration**: Agents explore chaotically, finding edge-case bugs
4. **AI Evaluation**: Using AI to audit AI - the only scalable way to test generative features

**Bottom line**: Humans define *what to test*. Agents determine *how to break it*.

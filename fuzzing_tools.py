"""
Fuzzing and Chaos Testing Tools for Elastic Observability
Provides tools for payload mutation, injection, and validation.
"""

import json
import random
import string
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

# --- LLM for Payload Generation ---
fuzzing_llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.9)

# --- OpenTelemetry Schema Templates ---
OTEL_LOG_TEMPLATE = {
    "resourceLogs": [{
        "resource": {
            "attributes": [
                {"key": "service.name", "value": {"stringValue": "checkout-service"}},
                {"key": "service.version", "value": {"stringValue": "1.2.3"}},
                {"key": "host.name", "value": {"stringValue": "prod-server-01"}}
            ]
        },
        "scopeLogs": [{
            "scope": {"name": "otel.logs"},
            "logRecords": [{
                "timeUnixNano": "1618317040000000000",
                "severityNumber": 9,
                "severityText": "INFO",
                "body": {"stringValue": "Transaction completed successfully"},
                "attributes": [
                    {"key": "http.status_code", "value": {"intValue": 200}},
                    {"key": "user.id", "value": {"stringValue": "usr_12345"}}
                ]
            }]
        }]
    }]
}

OTEL_TRACE_TEMPLATE = {
    "resourceSpans": [{
        "resource": {
            "attributes": [
                {"key": "service.name", "value": {"stringValue": "api-gateway"}},
                {"key": "deployment.environment", "value": {"stringValue": "production"}}
            ]
        },
        "scopeSpans": [{
            "scope": {"name": "otel.traces"},
            "spans": [{
                "traceId": "5b8efff798038103d269b633813fc60c",
                "spanId": "eee19b7ec3c1b174",
                "name": "/api/checkout",
                "kind": 1,
                "startTimeUnixNano": "1618317040000000000",
                "endTimeUnixNano": "1618317041000000000",
                "attributes": [
                    {"key": "http.method", "value": {"stringValue": "POST"}},
                    {"key": "http.status_code", "value": {"intValue": 200}}
                ]
            }]
        }]
    }]
}

# ---------------------------------------------------------
# Fuzzing Tool: LLM-Driven Payload Mutation
# ---------------------------------------------------------
@tool
async def generate_malformed_otel_payloads(schema_type: str, mutation_count: int = 10) -> str:
    """
    Generates malformed OpenTelemetry payloads using LLM-based fuzzing.

    Args:
        schema_type: Either 'logs' or 'traces'
        mutation_count: Number of malformed variants to generate (default: 10)

    Returns:
        JSON string containing array of mutated payloads with descriptions
    """
    template = OTEL_LOG_TEMPLATE if schema_type == "logs" else OTEL_TRACE_TEMPLATE

    prompt = f"""You are a chaos testing expert. Generate {mutation_count} MALFORMED variations of this OpenTelemetry {schema_type} payload.

Base payload:
{json.dumps(template, indent=2)}

Apply these mutation strategies (use different combinations):
1. SQL injection in string fields (e.g., "'; DROP TABLE logs;--")
2. Invalid timestamps (future dates, negative values, malformed format)
3. Type mismatches (string instead of int, array instead of object)
4. Missing required fields
5. Extremely large field values (10MB strings)
6. Special characters and unicode exploits
7. Nested object corruption
8. Invalid enum values

IMPORTANT: Return ONLY a JSON array where each element has:
- "mutation_type": brief description of the attack
- "payload": the corrupted payload object

Example format:
[
  {{"mutation_type": "SQL injection in service.name", "payload": {{...}}}},
  {{"mutation_type": "Negative timestamp", "payload": {{...}}}}
]
"""

    try:
        response = await fuzzing_llm.ainvoke(prompt)
        # Extract JSON from response
        content = response.content

        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and "text" in part:
                    text_parts.append(part["text"])
            content = "".join(text_parts)

        # Try to parse as JSON directly
        try:
            mutations = json.loads(content)
            return json.dumps(mutations, indent=2)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
                mutations = json.loads(json_str)
                return json.dumps(mutations, indent=2)
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
                mutations = json.loads(json_str)
                return json.dumps(mutations, indent=2)
            else:
                return f"Error: Could not parse LLM response as JSON. Raw response: {content}"
    except Exception as e:
        return f"Error generating mutations: {str(e)}"


# ---------------------------------------------------------
# Injection Tool: Send Payloads to Elastic API
# ---------------------------------------------------------
@tool
async def inject_telemetry_to_elastic(payload_json: str, endpoint_type: str = "logs") -> str:
    """
    Injects telemetry payload to Elastic Observability and validates response.

    Args:
        payload_json: JSON string of the payload to inject
        endpoint_type: 'logs' or 'traces' (determines OTel endpoint)

    Returns:
        Status report with HTTP code, response body, and verdict (PASS/FAIL)
    """
    elastic_url = os.getenv("ELASTIC_APM_SERVER_URL", "http://localhost:8200")
    elastic_token = os.getenv("ELASTIC_APM_SECRET_TOKEN", "")

    endpoint_map = {
        "logs": f"{elastic_url}/v1/logs",
        "traces": f"{elastic_url}/v1/traces"
    }

    url = endpoint_map.get(endpoint_type)
    if not url:
        return f"Error: Invalid endpoint_type '{endpoint_type}'. Use 'logs' or 'traces'."

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {elastic_token}" if elastic_token else ""
    }

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON payload - {str(e)}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)

            # Determine verdict
            if response.status_code == 400:
                verdict = "PASS (Graceful rejection)"
            elif response.status_code == 202 or response.status_code == 200:
                verdict = "PASS (Accepted - check if it should have been rejected)"
            elif response.status_code >= 500:
                verdict = "FAIL (Internal server error - crash detected)"
            else:
                verdict = f"UNKNOWN (HTTP {response.status_code})"

            return json.dumps({
                "endpoint": url,
                "http_status": response.status_code,
                "verdict": verdict,
                "response_body": response.text[:500],  # Truncate large responses
                "response_headers": dict(response.headers)
            }, indent=2)

        except httpx.TimeoutException:
            return json.dumps({
                "endpoint": url,
                "verdict": "FAIL (Timeout - possible crash or hang)",
                "error": "Request timed out after 30 seconds"
            }, indent=2)
        except Exception as e:
            return json.dumps({
                "endpoint": url,
                "verdict": "FAIL (Connection error)",
                "error": str(e)
            }, indent=2)


# ---------------------------------------------------------
# Cross-Checking Tool: Inject and Verify Data Integrity
# ---------------------------------------------------------
@tool
async def inject_and_track_payload(payload_json: str, tracking_id: str) -> str:
    """
    Injects a payload with a unique tracking ID for later verification.
    Stores the original payload in memory for cross-checking.

    Args:
        payload_json: The payload to inject (must include tracking_id in attributes)
        tracking_id: Unique identifier to find this payload later

    Returns:
        Confirmation with tracking ID and injection status
    """
    # Store in a simple in-memory cache (production would use Redis/DB)
    global _payload_cache
    if "_payload_cache" not in globals():
        _payload_cache = {}

    try:
        payload = json.loads(payload_json)
        _payload_cache[tracking_id] = {
            "original_payload": payload,
            "injected_at": datetime.utcnow().isoformat(),
            "verified": False
        }

        # Inject to Elastic
        result = await inject_telemetry_to_elastic.ainvoke({
            "payload_json": payload_json,
            "endpoint_type": "logs"
        })

        return f"Tracking ID '{tracking_id}' registered.\nInjection result:\n{result}"
    except Exception as e:
        return f"Error during injection: {str(e)}"


@tool
async def verify_payload_integrity(tracking_id: str, retrieved_json: str) -> str:
    """
    Compares the originally injected payload with what was retrieved from Elasticsearch.
    Reports any field mutations, truncations, or data loss.

    Args:
        tracking_id: The ID used during injection
        retrieved_json: The JSON document retrieved from Elasticsearch

    Returns:
        Detailed diff report showing any data integrity issues
    """
    global _payload_cache
    if "_payload_cache" not in globals() or tracking_id not in _payload_cache:
        return f"Error: No original payload found for tracking_id '{tracking_id}'"

    original = _payload_cache[tracking_id]["original_payload"]

    try:
        retrieved = json.loads(retrieved_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid retrieved JSON - {str(e)}"

    # Deep comparison
    differences = []

    def compare_values(path: str, orig: Any, retr: Any):
        if type(orig) != type(retr):
            differences.append(f"{path}: TYPE MISMATCH - Original: {type(orig).__name__}, Retrieved: {type(retr).__name__}")
        elif isinstance(orig, dict):
            for key in orig:
                if key not in retr:
                    differences.append(f"{path}.{key}: MISSING in retrieved data")
                else:
                    compare_values(f"{path}.{key}", orig[key], retr[key])
            for key in retr:
                if key not in orig:
                    differences.append(f"{path}.{key}: EXTRA field (not in original)")
        elif isinstance(orig, list):
            if len(orig) != len(retr):
                differences.append(f"{path}: ARRAY LENGTH MISMATCH - Original: {len(orig)}, Retrieved: {len(retr)}")
            else:
                for i, (o, r) in enumerate(zip(orig, retr)):
                    compare_values(f"{path}[{i}]", o, r)
        elif orig != retr:
            differences.append(f"{path}: VALUE MISMATCH - Original: {orig}, Retrieved: {retr}")

    compare_values("root", original, retrieved)

    _payload_cache[tracking_id]["verified"] = True

    if not differences:
        return f"✓ INTEGRITY CHECK PASSED: Tracking ID '{tracking_id}' - No differences detected"
    else:
        diff_report = "\n".join([f"  - {d}" for d in differences[:50]])  # Limit to 50 diffs
        return f"✗ INTEGRITY CHECK FAILED: Tracking ID '{tracking_id}'\n\nDifferences found ({len(differences)} total):\n{diff_report}"
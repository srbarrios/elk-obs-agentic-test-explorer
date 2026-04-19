"""
AI Assistant Evaluation Tools
Tools for testing Elastic AI Assistant with LLM-generated questions and ES|QL validation.
"""

import json
from typing import Dict, Any
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from playwright.async_api import Page
import httpx
from dotenv import load_dotenv
import os
from agentic_explorer.utils.llm_json import parse_json_from_llm

load_dotenv()

# --- LLM for Question Generation and Evaluation ---
evaluation_llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.7)
validator_llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)

# ---------------------------------------------------------
# Question Generation Tool
# ---------------------------------------------------------
@tool
async def generate_ai_assistant_questions(scenario: str, question_count: int = 10) -> str:
    """
    Generates complex questions to test the Elastic AI Assistant.

    Args:
        scenario: Context for questions (e.g., 'slow_checkout_service', 'high_cpu_alerts', 'missing_logs')
        question_count: Number of questions to generate

    Returns:
        JSON array of questions with expected query patterns
    """
    prompt = f"""You are an SRE troubleshooting an observability incident. Generate {question_count} realistic questions that an engineer would ask the Elastic AI Assistant.

Scenario: {scenario}

The questions should:
1. Cover different complexity levels (simple to multi-step)
2. Require correlation across logs, metrics, and traces
3. Test time-range handling and aggregations
4. Include questions that might expose hallucination (ambiguous requests)

Format each as:
- "question": the natural language question
- "expected_query_type": the type of ES|QL query expected (e.g., "aggregation", "filter", "join", "time_series")
- "complexity": "low", "medium", or "high"

Return ONLY a JSON array of question objects.

Example:
[
  {{
    "question": "Why is the checkout service responding slowly since 2pm?",
    "expected_query_type": "time_series_filter",
    "complexity": "medium"
  }},
  {{
    "question": "Which hosts have CPU above 80% and memory errors in the last hour?",
    "expected_query_type": "aggregation_with_filter",
    "complexity": "high"
  }}
]
"""

    try:
        response = await evaluation_llm.ainvoke(prompt)
        questions = parse_json_from_llm(response.content)
        return json.dumps(questions, indent=2)
    except Exception as e:
        return f"Error generating questions: {str(e)}"


# ---------------------------------------------------------
# ES|QL Query Validator Tool
# ---------------------------------------------------------
@tool
async def validate_esql_query(query: str, question_context: str) -> str:
    """
    Validates an ES|QL query for syntax correctness and semantic appropriateness.

    Args:
        query: The ES|QL query to validate
        question_context: The original question that prompted this query

    Returns:
        Validation report with syntax check, semantic analysis, and potential issues
    """
    elasticsearch_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    elastic_user = os.getenv("KIBANA_USERNAME", "elastic")
    elastic_pass = os.getenv("KIBANA_PASSWORD", "")

    # Step 1: Syntax validation via Elasticsearch _query endpoint
    syntax_result = await _check_esql_syntax(query, elasticsearch_url, elastic_user, elastic_pass)

    # Step 2: Semantic validation via LLM
    semantic_result = await _check_esql_semantics(query, question_context)

    return json.dumps({
        "query": query,
        "syntax_validation": syntax_result,
        "semantic_validation": semantic_result,
        "overall_verdict": "PASS" if syntax_result["valid"] and semantic_result["appropriate"] else "FAIL"
    }, indent=2)


async def _check_esql_syntax(query: str, es_url: str, user: str, password: str) -> Dict[str, Any]:
    """Internal: Validates ES|QL syntax by sending to Elasticsearch."""
    headers = {"Content-Type": "application/json"}
    auth = (user, password) if user and password else None

    # Use ES|QL _query endpoint with validate parameter
    payload = {
        "query": query
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        try:
            # Try dry-run execution (limit 0 to avoid actual data retrieval)
            response = await client.post(
                f"{es_url}/_query",
                json=payload,
                headers=headers,
                auth=auth
            )

            if response.status_code == 200:
                return {"valid": True, "message": "Syntax is valid"}
            else:
                error_detail = response.json().get("error", {})
                return {
                    "valid": False,
                    "error_type": error_detail.get("type", "unknown"),
                    "error_message": error_detail.get("reason", response.text)
                }
        except httpx.TimeoutException:
            return {"valid": False, "error_type": "timeout", "error_message": "Query validation timed out"}
        except Exception as e:
            return {"valid": False, "error_type": "connection_error", "error_message": str(e)}


async def _check_esql_semantics(query: str, question: str) -> Dict[str, Any]:
    """Internal: Uses LLM to check if query semantically matches the question."""
    prompt = f"""You are an Elasticsearch expert. Evaluate if this ES|QL query correctly answers the user's question.

Question: {question}

Query:
{query}

Analyze:
1. Does the query target the right indices/data streams?
2. Are the filters appropriate for the question?
3. Are the aggregations/groupings correct?
4. Does it handle the time range properly?
5. Are there any logic errors or potential hallucinations?

Respond ONLY with a JSON object:
{{
  "appropriate": true/false,
  "issues": ["list", "of", "issues"],
  "confidence": "high/medium/low"
}}
"""

    try:
        response = await validator_llm.ainvoke(prompt)
        result = parse_json_from_llm(response.content)
        return result
    except Exception as e:
        return {
            "appropriate": False,
            "issues": [f"Evaluation error: {str(e)}"],
            "confidence": "low"
        }


# ---------------------------------------------------------
# AI Assistant Interaction Tool (Page-Aware)
# ---------------------------------------------------------
def get_ai_assistant_interaction_tool(page: Page):
    @tool
    async def submit_question_to_ai_assistant(question: str) -> str:
        """
        Submits a question to the Elastic AI Assistant in Kibana and extracts the response.

        Args:
            question: The natural language question to ask

        Returns:
            JSON containing the assistant's response text and any generated ES|QL query
        """
        try:
            # Locate AI Assistant input (adjust selector based on actual Kibana UI)
            # This is a placeholder - actual selectors need to be verified
            assistant_input_selector = '[data-test-subj="ai-assistant-input"]'
            submit_button_selector = '[data-test-subj="ai-assistant-submit"]'
            response_selector = '[data-test-subj="ai-assistant-response"]'

            # Wait for assistant to be available
            await page.wait_for_selector(assistant_input_selector, timeout=10000)

            # Type question
            await page.fill(assistant_input_selector, question)
            await page.click(submit_button_selector)

            # Wait for response
            await page.wait_for_selector(response_selector, timeout=30000)

            # Extract response text
            response_text = await page.inner_text(response_selector)

            # Try to extract ES|QL query from code blocks
            esql_query = None
            code_block_selector = f'{response_selector} code'
            code_elements = await page.query_selector_all(code_block_selector)

            for element in code_elements:
                code_text = await element.inner_text()
                if "FROM" in code_text and "|" in code_text:  # Basic ES|QL detection
                    esql_query = code_text
                    break

            return json.dumps({
                "question": question,
                "response_text": response_text,
                "generated_query": esql_query,
                "query_detected": esql_query is not None
            }, indent=2)

        except Exception as e:
            return json.dumps({
                "error": f"Failed to interact with AI Assistant: {str(e)}",
                "question": question
            }, indent=2)

    return submit_question_to_ai_assistant


# ---------------------------------------------------------
# Evaluation Orchestration Tool
# ---------------------------------------------------------
@tool
async def evaluate_ai_assistant_accuracy(questions_json: str, responses_json: str) -> str:
    """
    Evaluates the overall accuracy of AI Assistant responses using DeepEval-style metrics.

    Args:
        questions_json: JSON array of original questions with expected query types
        responses_json: JSON array of AI Assistant responses with generated queries

    Returns:
        Evaluation report with accuracy scores and failure analysis
    """
    try:
        questions = json.loads(questions_json)
        responses = json.loads(responses_json)

        if len(questions) != len(responses):
            return "Error: Question and response arrays must have the same length"

        evaluations = []
        pass_count = 0

        for question_item, response_item in zip(questions, responses):
            if not response_item.get("query_detected"):
                evaluations.append({
                    "question": question_item["question"],
                    "verdict": "FAIL",
                    "reason": "No ES|QL query was generated"
                })
                continue

            # Validate the generated query
            validation_result = await validate_esql_query.ainvoke({
                "query": response_item["generated_query"],
                "question_context": question_item["question"]
            })
            validation_data = json.loads(validation_result)

            if validation_data["overall_verdict"] == "PASS":
                pass_count += 1
                evaluations.append({
                    "question": question_item["question"],
                    "verdict": "PASS",
                    "query": response_item["generated_query"]
                })
            else:
                evaluations.append({
                    "question": question_item["question"],
                    "verdict": "FAIL",
                    "query": response_item["generated_query"],
                    "issues": validation_data["semantic_validation"].get("issues", [])
                })

        accuracy = (pass_count / len(questions)) * 100 if questions else 0

        return json.dumps({
            "total_questions": len(questions),
            "passed": pass_count,
            "failed": len(questions) - pass_count,
            "accuracy_percentage": round(accuracy, 2),
            "evaluations": evaluations
        }, indent=2)

    except Exception as e:
        return f"Error during evaluation: {str(e)}"
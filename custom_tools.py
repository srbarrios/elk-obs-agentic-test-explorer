import base64
import os
import io
import time
import re
from PIL import Image

from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from playwright.async_api import Page
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.runnables import RunnableConfig

from dotenv import load_dotenv
load_dotenv()

# --- Vision Model Setup ---
try:
    vision_model = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)
except Exception as e:
    print(f"Error initializing vision model: {e}")
    vision_model = None

# ---------------------------------------------------------
# MCP & Skill Tools
# ---------------------------------------------------------
async def get_elastic_mcp_doc_tools():
    client = MultiServerMCPClient({
        "elastic-docs": {
            "transport": "http",
            "url": "https://www.elastic.co/docs/_mcp/"
        }
    })
    return await client.get_tools()

@tool
def fetch_elastic_agent_skill(skill_name: str) -> str:
    """Reads the SKILL.md from the local elastic/agent-skills repository."""
    #TODO: This needs to be refactored, so we use installed skills.
    try:
        with open(f"./agent-skills/skills/{skill_name}/SKILL.md", "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"Skill documentation for {skill_name} not found."

# ---------------------------------------------------------
# Visual & Page Tools (Tool Factories)
# ---------------------------------------------------------
def get_visual_validation_tool(page: Page):
    @tool
    async def analyze_visual_state(validation_context: str) -> str:
        """
        Captures a screenshot of the current page and uses Gemini to 
        analyze it for visual anomalies based on the provided validation_context.
        """
        if not vision_model:
            return "Error: Visual validation model is not available."

        print(f"Executing visual analysis with context: '{validation_context}'...")
        screenshot_bytes = await page.screenshot(full_page=False)

        try:
            image = Image.open(io.BytesIO(screenshot_bytes))
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        except Exception as e:
            return f"Error during screenshot processing: {e}"

        system_prompt = (
            "You are an expert Kibana QA analyst validating visualizations."
            "Analyze the provided image. Respond only with 'PASS' if the visual state is correct "
            "according to the user's instructions. If there is a rendering issue or visual anomaly, "
            "respond with 'FAIL: ' followed by a detailed description of the error."
        )

        human_prompt = HumanMessage(
            content=[
                {"type": "text", "text": f"Instruction: {validation_context}"},
                {"type": "image_url", "image_url": f"data:image/png;base64,{img_str}"}
            ]
        )

        try:
            response = await vision_model.ainvoke([
                HumanMessage(content=system_prompt), 
                human_prompt
            ])
            return f"Visual Analysis complete. Result: {response.content}"
        except Exception as e:
            return f"Error interacting with the vision model: {e}"
            
    return analyze_visual_state

def get_screenshot_tool(page: Page):
    @tool
    async def capture_bug_screenshot(bug_summary: str, config: RunnableConfig) -> str:
        """
        USE THIS TOOL immediately when you find a bug, missing element, or visual anomaly.
        Provide a short, descriptive bug_summary (e.g., 'missing_kql_bar' or 'apm_waterfall_overlap').
        """
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        screenshot_dir = f"report_{thread_id}/screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', bug_summary)[:30].strip('_')
        filename = f"{screenshot_dir}/bug_{safe_name}_{int(time.time())}.png"

        try:
            await page.screenshot(path=filename, full_page=True)
            return f"Evidence captured! Screenshot successfully saved to {filename}"
        except Exception as e:
            return f"Failed to capture screenshot: {str(e)}"
            
    return capture_bug_screenshot

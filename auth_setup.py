import asyncio
import os
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

async def save_kibana_auth_state():
    KIBANA_URL = os.getenv("KIBANA_URL")
    USERNAME = os.getenv("KIBANA_USERNAME")
    PASSWORD = os.getenv("KIBANA_PASSWORD")

    if not all([KIBANA_URL, USERNAME, PASSWORD]):
        raise ValueError("Missing Kibana credentials! Check your .env file.")

    async with async_playwright() as p:
        # Launch non-headless so you can see it working or handle unexpected prompts
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()

        print("Navigating to Kibana...")
        await page.goto(KIBANA_URL)

        # Wait for the login form to appear
        print("Logging in...")
        await page.wait_for_selector('input[data-test-subj="loginUsername"]')
        
        # Fill credentials (using Kibana's standard data-test-subj selectors)
        await page.fill('input[data-test-subj="loginUsername"]', USERNAME)
        await page.fill('input[data-test-subj="loginPassword"]', PASSWORD)
        await page.click('button[data-test-subj="loginSubmit"]')

        # Wait for the global navigation or home page to confirm successful login
        print("Waiting for dashboard to load...")
        await page.wait_for_selector('a[id="observability-overview"]', timeout=5000)

        # Save the authentication state
        await context.storage_state(path="auth.json")
        print("Authentication state saved successfully to auth.json!")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(save_kibana_auth_state())

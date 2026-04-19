"""
Record-and-Translate Browser Engine
-----------------------------------
Separates the AI's *brain* (LangGraph agents emitting JSON intents) from its
execution *hands* (deterministic Playwright engine).

Workflow:
  1. Agent outputs a strict JSON command via `execute_browser_command`.
  2. This engine parses + validates the command, executes it with Playwright,
     captures a DOM snapshot, and appends an immutable record to the
     per-thread "Action Tape" (in-memory + `report_<thread>/action_tape.jsonl`).
  3. When a bug is detected, `generate_playwright_spec` translates the tape
     into a reproducible `.spec.ts` file.

Supported actions (JSON `action` field):
    navigate      {"url": str}
    click         {"selector": str}
    fill          {"selector": str, "value": str}
    press         {"selector": str, "key": str}
    select_option {"selector": str, "value": str}
    hover         {"selector": str}
    wait_for      {"selector": str, "state"?: "visible"|"hidden"|"attached"}
    scroll        {"selector"?: str, "y"?: int}
    extract_text  {"selector": str}
    snapshot      {}    # DOM-only, no side effect
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from playwright.async_api import Page

# ---------------------------------------------------------
# Per-thread Action Tape store
# ---------------------------------------------------------
_ACTION_TAPES: Dict[str, List[Dict[str, Any]]] = {}


def get_action_tape(thread_id: str) -> List[Dict[str, Any]]:
    return _ACTION_TAPES.setdefault(thread_id, [])


def reset_action_tape(thread_id: str) -> None:
    _ACTION_TAPES[thread_id] = []


def _tape_path(thread_id: str) -> str:
    os.makedirs(f"report_{thread_id}", exist_ok=True)
    return f"report_{thread_id}/action_tape.jsonl"


def _append_tape(thread_id: str, entry: Dict[str, Any]) -> None:
    get_action_tape(thread_id).append(entry)
    try:
        with open(_tape_path(thread_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - best-effort logging
        print(f"[browser_engine] Failed to persist tape entry: {exc}")


# ---------------------------------------------------------
# DOM snapshot extraction
# ---------------------------------------------------------
# Lightweight DOM digest: tag, role, name, id, class, visible text (truncated),
# and a stable CSS selector for interactive / textual elements. This is what
# the agent "sees" instead of raw HTML.
_DOM_SNAPSHOT_JS = r"""
(limit) => {
  const INTERACTIVE = new Set([
    'a','button','input','select','textarea','summary','option','label'
  ]);
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = window.getComputedStyle(el);
    return cs && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
  };
  const cssPath = (el) => {
    if (!(el instanceof Element)) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      let part = el.nodeName.toLowerCase();
      if (el.id) { part += '#' + el.id; parts.unshift(part); break; }
      const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0,2).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.nodeName === el.nodeName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(el)+1})`;
      }
      parts.unshift(part);
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const out = [];
  const all = document.querySelectorAll('*');
  for (const el of all) {
    if (out.length >= limit) break;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    const testSubj = el.getAttribute('data-test-subj');
    const isBtnLike = INTERACTIVE.has(tag) || role === 'button' || role === 'link' || role === 'tab' || role === 'menuitem';
    const textRaw = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    const hasText = textRaw.length > 0 && textRaw.length < 200;
    if (!isBtnLike && !testSubj && !hasText) continue;
    if (!isVisible(el)) continue;
    out.push({
      tag,
      role: role || null,
      id: el.id || null,
      name: el.getAttribute('name') || el.getAttribute('aria-label') || null,
      testSubj: testSubj || null,
      text: hasText ? textRaw.slice(0, 140) : null,
      selector: cssPath(el),
      interactive: isBtnLike
    });
  }
  return {
    url: location.href,
    title: document.title,
    elements: out
  };
}
"""


async def extract_dom_snapshot(page: Page, limit: int = 120) -> Dict[str, Any]:
    """Return a compact DOM digest usable by an LLM."""
    try:
        snap = await page.evaluate(_DOM_SNAPSHOT_JS, limit)
        return snap
    except Exception as exc:
        return {"url": page.url if page else "", "title": "", "elements": [], "error": str(exc)}


def _format_snapshot_for_llm(snap: Dict[str, Any], max_elems: int = 60) -> str:
    lines = [f"URL: {snap.get('url','')}", f"TITLE: {snap.get('title','')}"]
    if snap.get("error"):
        lines.append(f"SNAPSHOT_ERROR: {snap['error']}")
    elements = snap.get("elements", [])[:max_elems]
    for i, e in enumerate(elements):
        marker = "*" if e.get("interactive") else "-"
        label = e.get("testSubj") or e.get("name") or e.get("id") or e.get("text") or ""
        lines.append(
            f"{marker} [{i}] <{e.get('tag','')}> role={e.get('role')} "
            f"sel={e.get('selector')!r} label={label!r}"
        )
    if len(snap.get("elements", [])) > max_elems:
        lines.append(f"... (+{len(snap['elements'])-max_elems} more elements truncated)")
    return "\n".join(lines)


# ---------------------------------------------------------
# Command execution
# ---------------------------------------------------------
ALLOWED_ACTIONS = {
    "navigate", "click", "fill", "press", "select_option", "hover",
    "wait_for", "scroll", "extract_text", "snapshot",
}


@dataclass
class ExecutionResult:
    ok: bool
    action: str
    params: Dict[str, Any]
    started_at: float
    duration_ms: int
    result: Optional[str] = None
    error: Optional[str] = None
    snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_tape_entry(self) -> Dict[str, Any]:
        return {
            "ts": self.started_at,
            "action": self.action,
            "params": self.params,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "error": self.error,
            "page_url": self.snapshot.get("url"),
            "page_title": self.snapshot.get("title"),
        }


async def _dispatch(page: Page, action: str, params: Dict[str, Any]) -> str:
    if action == "navigate":
        await page.goto(params["url"])
        return f"navigated to {params['url']}"
    if action == "click":
        await page.click(params["selector"])
        return f"clicked {params['selector']}"
    if action == "fill":
        await page.fill(params["selector"], params.get("value", ""))
        return f"filled {params['selector']}"
    if action == "press":
        await page.press(params["selector"], params["key"])
        return f"pressed {params['key']} on {params['selector']}"
    if action == "select_option":
        await page.select_option(params["selector"], params["value"])
        return f"selected {params['value']} on {params['selector']}"
    if action == "hover":
        await page.hover(params["selector"])
        return f"hovered {params['selector']}"
    if action == "wait_for":
        await page.wait_for_selector(params["selector"], state=params.get("state", "visible"))
        return f"waited for {params['selector']}"
    if action == "scroll":
        if "selector" in params and params["selector"]:
            await page.eval_on_selector(
                params["selector"], "el => el.scrollIntoView({block:'center'})"
            )
            return f"scrolled to {params['selector']}"
        y = int(params.get("y", 400))
        await page.evaluate("(y)=>window.scrollBy(0,y)", y)
        return f"scrolled window by {y}px"
    if action == "extract_text":
        txt = await page.locator(params["selector"]).first.inner_text()
        return (txt or "").strip()[:4000]
    if action == "snapshot":
        return "snapshot"
    raise ValueError(f"Unsupported action: {action}")


# ---------------------------------------------------------
# Tool factories (page- and thread-aware via RunnableConfig)
# ---------------------------------------------------------
def get_browser_command_tool(page: Page):
    """
    LangChain tool: executes a single strict JSON browser command, records it
    to the immutable Action Tape, and returns a DOM snapshot + result.
    """
    @tool
    async def execute_browser_command(command_json: str, config: RunnableConfig) -> str:
        """Execute ONE browser action described as strict JSON and return the new DOM snapshot.

        The agent is the *brain*: it only emits intents. The engine is the *hands*.

        Argument `command_json` MUST be a JSON object string, for example:
            {"action": "navigate", "url": "https://kibana.example/app/observability"}
            {"action": "click", "selector": "[data-test-subj='logsStreamTab']"}
            {"action": "fill", "selector": "input[aria-label='KQL']", "value": "host.name:*"}
            {"action": "wait_for", "selector": ".euiDataGrid"}
            {"action": "extract_text", "selector": "h1"}
            {"action": "snapshot"}

        Allowed actions: navigate, click, fill, press, select_option, hover,
        wait_for, scroll, extract_text, snapshot.

        The return value is a human-readable block with:
          STATUS, RESULT or ERROR, and a DOM_SNAPSHOT digest.
        Every invocation is appended to the per-thread Action Tape, which is
        later translated into a Playwright .spec.ts script for reproduction.
        """
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        started = time.time()
        try:
            cmd = json.loads(command_json) if isinstance(command_json, str) else dict(command_json)
        except Exception as exc:
            return f"STATUS: ERROR\nERROR: Invalid JSON ({exc}). Expected object with 'action'."
        action = (cmd.get("action") or "").strip()
        if action not in ALLOWED_ACTIONS:
            return (
                f"STATUS: ERROR\nERROR: Unknown action '{action}'. "
                f"Allowed: {sorted(ALLOWED_ACTIONS)}"
            )
        params = {k: v for k, v in cmd.items() if k != "action"}
        err: Optional[str] = None
        res: Optional[str] = None
        try:
            res = await _dispatch(page, action, params)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"

        snap = await extract_dom_snapshot(page)
        duration_ms = int((time.time() - started) * 1000)
        result = ExecutionResult(
            ok=err is None, action=action, params=params,
            started_at=started, duration_ms=duration_ms,
            result=res, error=err, snapshot=snap,
        )
        _append_tape(thread_id, result.to_tape_entry())

        status = "OK" if result.ok else "ERROR"
        body = [
            f"STATUS: {status}",
            f"ACTION: {action} {json.dumps(params, ensure_ascii=False)}",
            f"DURATION_MS: {duration_ms}",
        ]
        if result.ok:
            body.append(f"RESULT: {res}")
        else:
            body.append(f"ERROR: {err}  (Recoverable — try a different selector or action.)")
        body.append("DOM_SNAPSHOT:")
        body.append(_format_snapshot_for_llm(snap))
        return "\n".join(body)

    return execute_browser_command


def get_dom_snapshot_tool(page: Page):
    """Read-only DOM digest tool (does NOT record to the tape)."""
    @tool
    async def get_dom_snapshot() -> str:
        """Return a compact DOM snapshot of the current page (URL, title, interactive
        elements with selectors). Use this to *see* before emitting a command."""
        snap = await extract_dom_snapshot(page)
        return _format_snapshot_for_llm(snap, max_elems=80)
    return get_dom_snapshot


# ---------------------------------------------------------
# Code Generator: Action Tape -> Playwright .spec.ts
# ---------------------------------------------------------
def _ts_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _tape_entry_to_ts(entry: Dict[str, Any]) -> Optional[str]:
    if not entry.get("ok"):
        return f"  // SKIPPED (failed at record time): {entry.get('action')} {json.dumps(entry.get('params'))} -- {entry.get('error')}"
    a = entry["action"]
    p = entry.get("params") or {}
    if a == "navigate":
        return f"  await page.goto('{_ts_escape(p['url'])}');"
    if a == "click":
        return f"  await page.click('{_ts_escape(p['selector'])}');"
    if a == "fill":
        return f"  await page.fill('{_ts_escape(p['selector'])}', '{_ts_escape(str(p.get('value','')))}');"
    if a == "press":
        return f"  await page.press('{_ts_escape(p['selector'])}', '{_ts_escape(p['key'])}');"
    if a == "select_option":
        return f"  await page.selectOption('{_ts_escape(p['selector'])}', '{_ts_escape(str(p['value']))}');"
    if a == "hover":
        return f"  await page.hover('{_ts_escape(p['selector'])}');"
    if a == "wait_for":
        state = p.get("state", "visible")
        return f"  await page.waitForSelector('{_ts_escape(p['selector'])}', {{ state: '{state}' }});"
    if a == "scroll":
        if p.get("selector"):
            return (
                f"  await page.locator('{_ts_escape(p['selector'])}')"
                f".scrollIntoViewIfNeeded();"
            )
        return f"  await page.evaluate(() => window.scrollBy(0, {int(p.get('y', 400))}));"
    if a == "extract_text":
        return (
            f"  const _t = await page.locator('{_ts_escape(p['selector'])}').first().innerText();\n"
            f"  expect(_t.length).toBeGreaterThan(0);"
        )
    if a == "snapshot":
        return "  // snapshot (no-op in replay)"
    return f"  // unsupported action replay: {a}"


def generate_playwright_spec(
    thread_id: str,
    bug_summary: str,
    kibana_url: Optional[str] = None,
    storage_state_path: str = "auth.json",
) -> str:
    """Translate the recorded Action Tape into a runnable Playwright spec file.
    Returns the absolute path of the generated .spec.ts file."""
    tape = get_action_tape(thread_id)
    safe = re.sub(r"[^a-zA-Z0-9]", "_", bug_summary)[:40].strip("_") or "bug"
    out_dir = f"report_{thread_id}"
    os.makedirs(out_dir, exist_ok=True)
    spec_path = os.path.join(out_dir, f"reproduction_{safe}_{int(time.time())}.spec.ts")

    steps: List[str] = []
    for entry in tape:
        line = _tape_entry_to_ts(entry)
        if line:
            steps.append(line)

    header = [
        "/**",
        f" * Auto-generated reproduction for bug: {bug_summary}",
        f" * Mission thread_id: {thread_id}",
        f" * Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        " *",
        " * Prerequisites:",
        " *   npm i -D @playwright/test && npx playwright install chromium",
        f" *   Place an authenticated storage state at ./{storage_state_path}",
        " * Run:",
        " *   npx playwright test " + os.path.basename(spec_path),
        " */",
        "import { test, expect } from '@playwright/test';",
        "",
        f"test.use({{ storageState: '{storage_state_path}' }});",
        "",
        f"test('reproduce: {_ts_escape(bug_summary)}', async ({{ page }}) => {{",
        "  test.setTimeout(120_000);",
    ]
    if kibana_url:
        header.append(f"  // Kibana base URL recorded at capture time: {kibana_url}")
    footer = ["});", ""]
    content = "\n".join(header + steps + footer)
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.abspath(spec_path)


# ---------------------------------------------------------
# Code Generator Tool (callable by agents, e.g. on bug capture)
# ---------------------------------------------------------
def get_code_generator_tool(kibana_url: Optional[str] = None):
    @tool
    async def generate_reproduction_spec(bug_summary: str, config: RunnableConfig) -> str:
        """Translate the immutable Action Tape into a 100%-reproducible
        Playwright .spec.ts script a developer can run locally.

        Call this *immediately after* `capture_bug_screenshot` so every bug has
        both a visual evidence and an executable repro script."""
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        tape = get_action_tape(thread_id)
        if not tape:
            return "No Action Tape recorded yet — nothing to translate."
        try:
            path = generate_playwright_spec(thread_id, bug_summary, kibana_url=kibana_url)
            return f"Reproduction spec generated at {path} ({len(tape)} recorded actions)."
        except Exception as exc:
            return f"Failed to generate reproduction spec: {exc}"
    return generate_reproduction_spec


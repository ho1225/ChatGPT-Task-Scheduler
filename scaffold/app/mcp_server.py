"""MCP server for the task scheduler.

Run as a stdio MCP server:
    python -m app.mcp_server

Or test with the inspector:
    npx @modelcontextprotocol/inspector python -m app.mcp_server
"""

import asyncio
import json
import os
import sys

from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Job, _utcnow
from .scheduler import get_time_bucket, start_scheduler

from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)
log_path = Path(__file__).resolve().parent.parent / "mcp_debug.log"


# ===================================================================
# Tool handlers — pure business logic, sync, take a DB Session
# ===================================================================


def _log_debug(message: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        print(message, file=f)


def _call_openai_for_task_parsing(task_text: str) -> dict | None:
    """Ask an LLM to convert a natural-language request into structured task data."""
    api_key = os.getenv("OPENAI_API_KEY")
    _log_debug(f"APIkey: {api_key}")
    if not api_key:
        _log_debug("OPENAI_API_KEY is not set")
        return None

    try:
        from openai import OpenAI
    except ImportError as exc:
        _log_debug(f"openai package import failed: {exc}")
        return None

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract a concise task description and an ISO-8601 scheduled_at "
                        "time from the user's request. Return JSON with keys description "
                        "and scheduled_at. Use null for scheduled_at when no explicit time is given."
                    ),
                },
                {"role": "user", "content": task_text},
            ],
            temperature=0,
        )

        _log_debug("--- OPENAI RAW RESPONSE ---")
        try:
            _log_debug(json.dumps(response, default=str, indent=2))
        except Exception:
            _log_debug(str(response))
        _log_debug("---------------------------")

        content = ""
        if hasattr(response, "output_text"):
            content = getattr(response, "output_text", "") or ""
        elif hasattr(response, "output"):
            output = getattr(response, "output")
            if isinstance(output, str):
                content = output
            elif isinstance(output, list):
                for item in output:
                    if isinstance(item, dict):
                        item_content = item.get("content")
                        if isinstance(item_content, str):
                            content += item_content
                        elif isinstance(item_content, list):
                            for chunk in item_content:
                                if isinstance(chunk, dict):
                                    text = chunk.get("text")
                                    if isinstance(text, str):
                                        content += text
                                elif isinstance(chunk, str):
                                    content += chunk

        _log_debug(f"Extracted response text: {content!r}")

        if not content:
            return None

        payload = json.loads(content)
        description = payload.get("description")
        scheduled_at = payload.get("scheduled_at")
        if isinstance(description, str) and description.strip():
            return {
                "description": description.strip(),
                "scheduled_at": scheduled_at if isinstance(scheduled_at, str) else None,
            }
    except Exception as exc:
        _log_debug(f"OpenAI parsing failed: {exc}")
        return None

    return None


def parse_task_request(task_text: str, scheduled_at: str | None = None) -> dict:
    """Normalize either structured input or a natural-language request into task fields."""
    if scheduled_at:
        return {"description": task_text.strip(), "scheduled_at": scheduled_at}

    if not task_text or not task_text.strip():
        return {"description": "", "scheduled_at": None}

    parsed = _call_openai_for_task_parsing(task_text)
    with open("mcp_debug.log", "a", encoding="utf-8") as f:
        print(f"Parsed task request: {parsed}", file=f)
    if parsed:
        return parsed

    return {"description": task_text.strip(), "scheduled_at": None}


def handle_create_task(
    db: Session,
    *,
    description: str,
    scheduled_at: str | None = None,
    cron_expression: str | None = None,
) -> dict:
    """Create a new scheduled job."""
    parsed = parse_task_request(description, scheduled_at=scheduled_at)

    description = parsed["description"]
    scheduled_at_value = parsed["scheduled_at"]

    if scheduled_at_value:
        try:
            dt = datetime.fromisoformat(scheduled_at_value.replace("Z", "+00:00"))
        except ValueError:
            dt = _utcnow()
    else:
        dt = _utcnow()

    job = Job(
        description=description,
        scheduled_at=dt,
        time_bucket=get_time_bucket(dt),
        cron_expression=cron_expression,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "status": job.status, "scheduled_at": str(job.scheduled_at)}


def handle_get_status(db: Session, *, job_id: int) -> dict:
    """Get the status of a scheduled job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        return {"error": f"Job {job_id} not found"}
    return {
        "job_id": job.id,
        "description": job.description,
        "status": job.status,
        "scheduled_at": str(job.scheduled_at),
        "result": job.result,
    }


def handle_list_tasks(db: Session) -> dict:
    """List all scheduled jobs."""
    jobs = db.query(Job).order_by(Job.scheduled_at.desc()).all()
    return {
        "jobs": [
            {
                "job_id": j.id,
                "description": j.description,
                "status": j.status,
                "scheduled_at": str(j.scheduled_at),
            }
            for j in jobs
        ]
    }


def handle_cancel_task(db: Session, *, job_id: int) -> dict:
    """Cancel a scheduled job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        return {"error": f"Job {job_id} not found"}
    if job.status in ("completed", "failed"):
        return {"error": f"Cannot cancel job in '{job.status}' state"}
    job.status = "cancelled"
    db.commit()
    return {"job_id": job.id, "status": "cancelled"}


# ===================================================================
# Tool definitions — what Claude / MCP client sees
# (pre-filled — boilerplate for MCP discovery, not the focus)
# ===================================================================

TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="task.create",
        description="Schedule a new task for future execution",
        inputSchema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What the task should do; may be natural language and will be parsed when possible",
                },
                "scheduled_at": {
                    "type": "string",
                    "format": "date-time",
                    "description": "When to run, ISO 8601 format (e.g. 2026-05-03T10:00:00)",
                },
                "cron_expression": {
                    "type": "string",
                    "description": "Optional cron expression for recurring jobs, e.g. 0 9 * * *",
                },
            },
            "required": ["description"],
        },
    ),
    Tool(
        name="task.list",
        description="List all scheduled tasks",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="task.status",
        description="Get the status of a scheduled task by job_id",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "The job ID returned by task.create"},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="task.cancel",
        description="Cancel a scheduled task that hasn't completed yet",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "The job ID to cancel"},
            },
            "required": ["job_id"],
        },
    ),
]


# ===================================================================
# Registry pattern — name to handler dispatch
# ===================================================================
TOOL_REGISTRY: dict = {
    "task.create": handle_create_task,
    "task.list": handle_list_tasks,
    "task.status": handle_get_status,
    "task.cancel": handle_cancel_task,

}


def route_tool_call(tool_name: str, arguments: dict, db: Session) -> dict:
    handler = TOOL_REGISTRY.get(tool_name)
    if handler == None:
        return {"error": f"Unknown tool: {tool_name}"}
    return handler(db, **arguments)


# ===================================================================
# MCP server wiring — boilerplate, do not modify
# ===================================================================

server: Server = Server("task-scheduler")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOL_DEFINITIONS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Async wrapper — runs the sync handler in a thread to avoid blocking the event loop."""
    db = SessionLocal()
    try:
        result = await asyncio.to_thread(route_tool_call, name, arguments or {}, db)
    finally:
        db.close()
    return [TextContent(type="text", text=json.dumps(result, default=str, ensure_ascii=False))]


# ===================================================================
# Entry point — `python -m app.mcp_server`
# ===================================================================


async def main() -> None:
    Base.metadata.create_all(bind=engine)
    start_scheduler()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

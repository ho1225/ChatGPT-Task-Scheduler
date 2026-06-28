## Guided Track Setup

```bash
cd scaffold
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need **Node.js** for `npx` (used by the MCP inspector for verification).

### Files to Fill In

| File                | TODO                                    | Design Decision                                     |
| ------------------- | --------------------------------------- | --------------------------------------------------- |
| `app/scheduler.py`  | `get_time_bucket()` + `find_due_jobs()` | Time bucket partitioning for efficient job scanning |
| `app/mcp_server.py` | `TOOL_REGISTRY` + `route_tool_call()`   | Registry pattern for MCP tool routing               |

### Run and Verify

The prototype is a real MCP stdio server. Verify with the MCP inspector (no Claude needed):

```bash
npx @modelcontextprotocol/inspector python -m app.mcp_server
```

This opens a browser GUI — see `PROMPT.md` Verification section for the full test flow. Once the inspector tests pass, you can optionally connect to Claude Desktop / Claude Code (instructions also in `PROMPT.md`).

Other features

- Connect a real LLM to parse natural language task descriptions before calling `task.create`
- Add recurring job support (cron expressions)

"""
MCP server providing a `next_prompt` tool for agent-user communication.
The agent calls this when done responding. The tool waits for the user's
next message and returns it. The response text is captured from the TUI.
"""

import json
import os
import sys
import tempfile
import time
import uuid

QUESTION_DIR = os.environ.get("MCP_QUESTION_DIR", "/tmp/mcp")
POLL_INTERVAL = 0.5

EXCHANGE_FILE = os.path.join(QUESTION_DIR, "exchange.json")
REPLY_FILE = os.path.join(QUESTION_DIR, "reply.json")


def ensure_dir():
    os.makedirs(QUESTION_DIR, exist_ok=True)


def atomic_write_json(filepath, data):
    dir_name = os.path.dirname(filepath)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_read_json(filepath):
    try:
        with open(filepath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def next_prompt() -> list:
    """Signal done and wait for the user's next message."""
    ensure_dir()

    xid = str(uuid.uuid4())[:8]

    for f in [EXCHANGE_FILE, REPLY_FILE]:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass

    atomic_write_json(EXCHANGE_FILE, {"id": xid})

    while True:
        data = safe_read_json(REPLY_FILE)
        if data and data.get("id") == xid:
            for fp in [EXCHANGE_FILE, REPLY_FILE]:
                try:
                    os.remove(fp)
                except FileNotFoundError:
                    pass

            content = []
            msg = data.get("message", "")
            if msg:
                content.append({"type": "text", "text": msg})

            for img in data.get("images", []):
                content.append({
                    "type": "image",
                    "data": img["data"],
                    "mimeType": img.get("mimeType", "image/png"),
                })

            return content if content else [{"type": "text", "text": ""}]
        time.sleep(POLL_INTERVAL)


TOOLS = {
    "next_prompt": {
        "description": (
            "MANDATORY: Call this at the END of EVERY TURN. NO EXCEPTIONS. "
            "This signals you are done and waits for the user's next message. "
            "The user sees your response via the terminal UI - you don't need to repeat it. "
            "Just call this tool when you're finished. It returns the user's next message. "
            "ALWAYS call this. EVERY turn. Even for short answers. Even on errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
}


def handle_jsonrpc(request):
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "chat", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        tools_list = []
        for name, spec in TOOLS.items():
            tools_list.append(
                {
                    "name": name,
                    "description": spec["description"],
                    "inputSchema": spec["inputSchema"],
                }
            )
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}}

    if method == "tools/call":
        tool_name = request["params"]["name"]

        if tool_name == "next_prompt":
            content = next_prompt()
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": content, "isError": False},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method and method.startswith("notifications/"):
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    ensure_dir()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_jsonrpc(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

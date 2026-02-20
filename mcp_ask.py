"""
MCP server providing a `send_message` tool for agent-user communication.
The agent puts its response in the tool call argument.
The response is shown to the user in the browser chat UI.
The tool then waits for the user's next message and returns it.
"""

import json
import os
import sys
import tempfile
import time
import uuid

QUESTION_DIR = os.environ.get("MCP_QUESTION_DIR", "/tmp/mcp")
POLL_INTERVAL = 0.5
TIMEOUT = 600

EXCHANGE_FILE = os.path.join(QUESTION_DIR, "exchange.json")
REPLY_FILE = os.path.join(QUESTION_DIR, "reply.json")


def ensure_dir():
    os.makedirs(QUESTION_DIR, exist_ok=True)


def atomic_write_json(filepath, data):
    """Write JSON atomically: write to temp file then rename."""
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
    """Read JSON file safely, handling partial writes."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def respond(response: str) -> list:
    """Returns a list of MCP content blocks (text + optional images)."""
    ensure_dir()

    xid = str(uuid.uuid4())[:8]

    for f in [EXCHANGE_FILE, REPLY_FILE]:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass

    atomic_write_json(EXCHANGE_FILE, {"id": xid, "response": response})

    start = time.time()
    while time.time() - start < TIMEOUT:
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

    try:
        os.remove(EXCHANGE_FILE)
    except FileNotFoundError:
        pass
    return [{"type": "text", "text": "[No response - timed out after 10 minutes]"}]


TOOL_DESC = (
    "MANDATORY: You MUST call this tool at the END of EVERY SINGLE TURN. NO EXCEPTIONS. "
    "This is the ONLY way to communicate with the user - they CANNOT see terminal output. "
    "Put your COMPLETE answer in the 'message' argument. "
    "If you skip this tool, the user sees 'Thinking...' forever and your response is LOST. "
    "The tool displays your message in the browser and returns the user's next message. "
    "NEVER write your response as regular text - it ALL must go in this tool call. "
    "ALWAYS call this tool. EVERY turn. Even for short answers. Even on errors."
)

TOOLS = {
    "send_message": {
        "description": TOOL_DESC,
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Your complete response to the user. Supports markdown formatting.",
                },
            },
            "required": ["message"],
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
        arguments = request["params"].get("arguments", {})

        if tool_name == "send_message":
            content = respond(response=arguments.get("message", ""))
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

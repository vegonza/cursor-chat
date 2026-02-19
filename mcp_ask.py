"""
MCP server providing a `respond` tool.
The agent puts its response in the tool call argument.
The response is shown to the user in the browser chat UI.
The tool then waits for the user's next message and returns it.
"""

import json
import os
import sys
import time
import uuid

QUESTION_DIR = os.environ.get("MCP_QUESTION_DIR", "/tmp/mcp")
POLL_INTERVAL = 0.5
TIMEOUT = 600

EXCHANGE_FILE = os.path.join(QUESTION_DIR, "exchange.json")
REPLY_FILE = os.path.join(QUESTION_DIR, "reply.json")


def ensure_dir():
    os.makedirs(QUESTION_DIR, exist_ok=True)


def respond(response: str) -> list:
    """Returns a list of MCP content blocks (text + optional images)."""
    ensure_dir()

    xid = str(uuid.uuid4())[:8]

    for f in [EXCHANGE_FILE, REPLY_FILE]:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass

    with open(EXCHANGE_FILE, "w") as f:
        json.dump({"id": xid, "response": response}, f)

    start = time.time()
    while time.time() - start < TIMEOUT:
        if os.path.exists(REPLY_FILE):
            with open(REPLY_FILE) as f:
                data = json.load(f)
            if data.get("id") == xid:
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


TOOLS = {
    "respond": {
        "description": (
            "Send your response to the user and wait for their next message. "
            "You MUST call this tool at the end of EVERY turn. "
            "Put your complete answer in the 'response' argument. "
            "The response will be displayed in the browser chat interface. "
            "The tool will then wait for the user to type their next message "
            "and return it to you. "
            "IMPORTANT: Always put your full response in the 'response' field. "
            "Do NOT write your response as regular text output - put it ALL in this tool call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "Your complete response to the user's query. Supports markdown formatting.",
                },
            },
            "required": ["response"],
        },
    }
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
                "serverInfo": {"name": "chat-bridge", "version": "1.0.0"},
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

        if tool_name == "respond":
            content = respond(response=arguments.get("response", ""))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": content,
                    "isError": False,
                },
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

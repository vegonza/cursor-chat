import asyncio
import base64
import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import termios

from aiohttp import web

TERMINAL_USER = os.environ.get("TERMINAL_USER", "admin")
TERMINAL_PASSWORD = os.environ["TERMINAL_PASSWORD"]
BASE_PATH = os.environ.get("BASE_PATH", "/terminal").rstrip("/")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MCP_QUESTION_DIR = os.environ.get("MCP_QUESTION_DIR", "/tmp/mcp")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "/workspace/.chat-history.json")


def check_basic_auth(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, password = decoded.split(":", 1)
        return user == TERMINAL_USER and password == TERMINAL_PASSWORD
    except Exception:
        return False


@web.middleware
async def auth_middleware(request, handler):
    if not check_basic_auth(request):
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Terminal"'},
            text="Unauthorized",
        )
    return await handler(request)


async def index_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def ws_handler(request):
    ws = web.WebSocketResponse(protocols=["tty"])
    await ws.prepare(request)

    master_fd, slave_fd = pty.openpty()

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)
        os.execvpe("tmux", ["tmux", "attach", "-t", "agent"], env)

    os.close(slave_fd)

    loop = asyncio.get_event_loop()

    async def pty_to_ws():
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                if not data:
                    break
                await ws.send_bytes(data)
        except (OSError, ConnectionResetError):
            pass

    reader_task = asyncio.create_task(pty_to_ws())

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                payload = msg.json()
                if payload.get("type") == "input":
                    os.write(master_fd, payload["data"].encode("utf-8"))
                elif payload.get("type") == "resize":
                    cols = payload.get("cols", 80)
                    rows = payload.get("rows", 24)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                    os.kill(pid, signal.SIGWINCH)
            elif msg.type == web.WSMsgType.BINARY:
                os.write(master_fd, msg.data)
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                break
    except (OSError, ConnectionResetError):
        pass
    finally:
        reader_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
            os.waitpid(pid, os.WNOHANG)
        except (OSError, ChildProcessError):
            pass

    return ws


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(messages):
    with open(HISTORY_FILE, "w") as f:
        json.dump(messages, f)


def append_message(role, text, images=None):
    msgs = load_history()
    entry = {"role": role, "text": text}
    if images:
        entry["images"] = images
    msgs.append(entry)
    save_history(msgs)


async def history_handler(request):
    return web.json_response(load_history())


async def clear_history_handler(request):
    save_history([])
    return web.json_response({"ok": True})


_last_saved_exchange_id = None


async def exchange_handler(request):
    """Returns the current exchange: agent's response waiting for user reply."""
    global _last_saved_exchange_id
    efile = os.path.join(MCP_QUESTION_DIR, "exchange.json")
    rfile = os.path.join(MCP_QUESTION_DIR, "reply.json")
    if os.path.exists(efile) and not os.path.exists(rfile):
        with open(efile) as f:
            data = json.load(f)
        if data.get("id") != _last_saved_exchange_id:
            _last_saved_exchange_id = data["id"]
            if data.get("response"):
                append_message("agent", data["response"])
        return web.json_response(data)
    return web.json_response(None)


async def reply_handler(request):
    """User sends their reply to the agent."""
    data = await request.json()
    os.makedirs(MCP_QUESTION_DIR, exist_ok=True)
    msg = data.get("message", "")
    images = data.get("images")
    if msg or images:
        append_message("user", msg, images=[{"mimeType": i["mimeType"]} for i in images] if images else None)
    rfile = os.path.join(MCP_QUESTION_DIR, "reply.json")
    with open(rfile, "w") as f:
        json.dump(data, f)
    return web.json_response({"ok": True})


async def kickstart_handler(request):
    """Send the first message by injecting keystrokes into the tmux agent session."""
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "No message"}, status=400)

    efile = os.path.join(MCP_QUESTION_DIR, "exchange.json")
    if os.path.exists(efile):
        return web.json_response({"error": "Exchange already exists"}, status=409)

    def tmux_send_key(key):
        subprocess.run(
            ["tmux", "send-keys", "-t", "agent", key],
            timeout=5,
            capture_output=True,
        )

    def tmux_send_text(text):
        subprocess.run(
            ["tmux", "send-keys", "-t", "agent", "-l", text],
            timeout=5,
            capture_output=True,
        )

    append_message("user", message)
    clean_msg = message.replace("\n", " ")
    tmux_send_text(clean_msg)
    await asyncio.sleep(0.5)

    tmux_send_key("Escape")
    await asyncio.sleep(0.3)
    tmux_send_key("Enter")
    await asyncio.sleep(3)

    for _ in range(90):
        if os.path.exists(efile):
            break
        tmux_send_key("Tab")
        await asyncio.sleep(2)

    return web.json_response({"ok": True})


app = web.Application(middlewares=[auth_middleware])
app.router.add_get(f"{BASE_PATH}", index_handler)
app.router.add_get(f"{BASE_PATH}/", index_handler)
app.router.add_get(f"{BASE_PATH}/ws", ws_handler)
app.router.add_get(f"{BASE_PATH}/api/exchange", exchange_handler)
app.router.add_post(f"{BASE_PATH}/api/reply", reply_handler)
app.router.add_post(f"{BASE_PATH}/api/kickstart", kickstart_handler)
app.router.add_get(f"{BASE_PATH}/api/history", history_handler)
app.router.add_post(f"{BASE_PATH}/api/history/clear", clear_history_handler)
app.router.add_static(f"{BASE_PATH}/static", STATIC_DIR)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=7681)

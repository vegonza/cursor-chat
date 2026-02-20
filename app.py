import asyncio
import base64
import fcntl
import json
import os
import pty
import re
import signal
import struct
import subprocess
import tempfile
import termios

from aiohttp import web


TERMINAL_USER = os.environ.get("TERMINAL_USER", "admin")
TERMINAL_PASSWORD = os.environ["TERMINAL_PASSWORD"]
BASE_PATH = os.environ.get("BASE_PATH", "/terminal").rstrip("/")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MCP_QUESTION_DIR = os.environ.get("MCP_QUESTION_DIR", "/tmp/mcp")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "/workspace/.chat-history.json")


def atomic_write_json(filepath, data):
    """Write JSON atomically: write to temp file then rename."""
    dir_name = os.path.dirname(filepath)
    os.makedirs(dir_name, exist_ok=True)
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
    """Read JSON file safely, handling partial writes or missing files."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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
    resp = web.FileResponse(os.path.join(STATIC_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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
    atomic_write_json(HISTORY_FILE, messages)


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


def _init_last_exchange_id():
    efile = os.path.join(MCP_QUESTION_DIR, "exchange.json")
    data = safe_read_json(efile)
    return data.get("id") if data else None


_last_saved_exchange_id = _init_last_exchange_id()
_agent_processing = False
_user_mode = "chat"


async def exchange_handler(request):
    """Returns the current exchange: agent is done, waiting for user reply."""
    global _last_saved_exchange_id, _agent_processing, _streaming_text, _last_finalized_text
    efile = os.path.join(MCP_QUESTION_DIR, "exchange.json")
    rfile = os.path.join(MCP_QUESTION_DIR, "reply.json")

    if not os.path.exists(efile):
        return web.json_response(None)

    if os.path.exists(rfile):
        return web.json_response(None)

    data = safe_read_json(efile)
    if not data or not data.get("id"):
        return web.json_response(None)

    _agent_processing = False

    # Final capture attempt if streaming didn't catch anything
    if not _streaming_text:
        pane = _capture_pane()
        if pane:
            text = _extract_response(pane)
            if text:
                _streaming_text = text

    if data["id"] != _last_saved_exchange_id:
        _last_saved_exchange_id = data["id"]
        if _streaming_text:
            _last_finalized_text = _streaming_text
            append_message("agent", _streaming_text)
            data["response"] = _streaming_text

    if "response" not in data and _streaming_text:
        data["response"] = _streaming_text

    return web.json_response(data)


async def reply_handler(request):
    """User sends their reply to the agent."""
    global _agent_processing, _streaming_text
    data = await request.json()
    os.makedirs(MCP_QUESTION_DIR, exist_ok=True)
    msg = data.get("message", "")
    images = data.get("images")
    if msg or images:
        append_message(
            "user", msg,
            images=[{"mimeType": i["mimeType"]} for i in images] if images else None,
        )
    if msg:
        data["message"] = msg + SYSTEM_SUFFIX
    rfile = os.path.join(MCP_QUESTION_DIR, "reply.json")
    atomic_write_json(rfile, data)
    _agent_processing = True
    _streaming_text = ""
    return web.json_response({"ok": True})


SYSTEM_SUFFIX = (
    "\n\n[SYSTEM INSTRUCTION — MANDATORY: You MUST end your turn by calling the "
    "`next_prompt` tool from the `chat` MCP server with no arguments. "
    "Do NOT end your turn any other way. Do NOT use AskQuestion. "
    "The user CANNOT reply until you call `next_prompt`. Call it NOW when done.]"
)


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

    global _agent_processing, _streaming_text
    _streaming_text = ""
    append_message("user", message)
    injected = message.replace("\n", " ") + SYSTEM_SUFFIX.replace("\n", " ")
    tmux_send_text(injected)
    await asyncio.sleep(0.5)

    tmux_send_key("Escape")
    await asyncio.sleep(0.3)
    tmux_send_key("Enter")

    _agent_processing = True
    return web.json_response({"ok": True})


async def restart_handler(request):
    """Restart the agent session and clear chat history."""
    global _last_saved_exchange_id, _agent_processing, _streaming_text, _last_finalized_text
    save_history([])
    for f_name in ["exchange.json", "reply.json"]:
        try:
            os.remove(os.path.join(MCP_QUESTION_DIR, f_name))
        except FileNotFoundError:
            pass
    subprocess.run(["tmux", "kill-session", "-t", "agent"], capture_output=True, timeout=5)
    _last_saved_exchange_id = None
    _agent_processing = False
    _streaming_text = ""
    _last_finalized_text = ""
    return web.json_response({"ok": True})


async def mode_handler(request):
    """User switches between chat and terminal mode."""
    global _user_mode
    data = await request.json()
    _user_mode = data.get("mode", "chat")
    return web.json_response({"ok": True, "mode": _user_mode})


_streaming_text = ""
_last_finalized_text = ""
_SUFFIX_END_RE = re.compile(r"Call\s+it\s+NOW\s+when\s+done\.\]")


def _capture_pane():
    """Capture the visible tmux pane content as plain text."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", "agent", "-p"],
            timeout=3, capture_output=True, text=True,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _extract_response(pane_text):
    """Extract the agent's current response from the TUI.

    Strategy: find the input box by locating └ then ┌. The response lives above ┌.

    Top boundary:
      Case 1 (first turn):  the SYSTEM_SUFFIX ending "Call it NOW when done.]"
      Case 2 (later turns): ⬢ completed tool-call line.
    """
    lines = pane_text.split("\n")

    box_bottom = -1
    for i in range(len(lines) - 1, max(len(lines) - 15, -1), -1):
        if "└" in lines[i] and "─" in lines[i]:
            box_bottom = i
            break
    if box_bottom < 0:
        return ""

    box_top = -1
    for i in range(box_bottom - 1, max(box_bottom - 5, -1), -1):
        if "┌" in lines[i] and "─" in lines[i]:
            box_top = i
            break
    if box_top < 0:
        return ""

    response_start = 0
    for i in range(box_top - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("⬡"):
            continue
        if s.startswith("⬢"):
            response_start = i + 1
            break
        if "Cursor Agent" in s or (s.startswith("/") and "/" in s):
            for j in range(i + 1, box_top):
                if _SUFFIX_END_RE.search(lines[j]):
                    response_start = j + 1
                    break
            else:
                msg_started = False
                for j in range(i + 1, box_top):
                    if lines[j].strip():
                        msg_started = True
                    elif msg_started:
                        response_start = j
                        break
            break

    result = []
    for i in range(response_start, box_top):
        s = lines[i].strip()
        if s.startswith("⬡") or s.startswith("⬢"):
            continue
        result.append(lines[i])

    return "\n".join(result).strip()


async def stream_sse_handler(request):
    """SSE endpoint: pushes text deltas as they appear, flushing each write."""
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.enable_chunked_encoding()
    await resp.prepare(request)

    await resp.write(b": connected\n\n")
    await resp.drain()

    sent_len = 0
    idle_ticks = 0
    while True:
        current = _streaming_text
        if len(current) > sent_len:
            delta = current[sent_len:]
            sent_len = len(current)
            await resp.write(f"data: {json.dumps({'delta': delta, 'total': sent_len})}\n\n".encode())
            await resp.drain()
            idle_ticks = 0
        else:
            idle_ticks += 1
        if not _agent_processing:
            if sent_len > 0:
                await resp.write(f"data: {json.dumps({'done': True, 'total': sent_len})}\n\n".encode())
                await resp.drain()
            break
        if idle_ticks > 1200:
            break
        await asyncio.sleep(0.05)

    return resp


def _tmux_tab():
    subprocess.run(
        ["tmux", "send-keys", "-t", "agent", "Tab"],
        timeout=5,
        capture_output=True,
    )


async def stream_capture_loop(app):
    """Background task: capture TUI text every 50ms while agent is processing."""
    global _streaming_text
    while True:
        await asyncio.sleep(0.05)
        if _agent_processing and _user_mode == "chat":
            pane = await asyncio.get_event_loop().run_in_executor(None, _capture_pane)
            if pane:
                text = _extract_response(pane)
                if text and text != _last_finalized_text:
                    _streaming_text = text


async def tab_presser_loop(app):
    """Background task: press Tab when agent is processing (needs tool approvals).
    Only runs in chat mode - in terminal mode the user controls everything directly."""
    efile = os.path.join(MCP_QUESTION_DIR, "exchange.json")
    while True:
        await asyncio.sleep(2)
        if _agent_processing and _user_mode == "chat" and not os.path.exists(efile):
            try:
                _tmux_tab()
            except Exception:
                pass


async def start_background_tasks(app):
    app["tab_presser"] = asyncio.create_task(tab_presser_loop(app))
    app["stream_capture"] = asyncio.create_task(stream_capture_loop(app))


async def cleanup_background_tasks(app):
    for key in ("tab_presser", "stream_capture"):
        if key in app:
            app[key].cancel()
            try:
                await app[key]
            except asyncio.CancelledError:
                pass


app = web.Application(middlewares=[auth_middleware])
app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)
app.router.add_get(f"{BASE_PATH}", index_handler)
app.router.add_get(f"{BASE_PATH}/", index_handler)
app.router.add_get(f"{BASE_PATH}/ws", ws_handler)
app.router.add_get(f"{BASE_PATH}/api/exchange", exchange_handler)
app.router.add_post(f"{BASE_PATH}/api/reply", reply_handler)
app.router.add_post(f"{BASE_PATH}/api/kickstart", kickstart_handler)
app.router.add_get(f"{BASE_PATH}/api/history", history_handler)
app.router.add_post(f"{BASE_PATH}/api/history/clear", clear_history_handler)
app.router.add_post(f"{BASE_PATH}/api/restart", restart_handler)
app.router.add_post(f"{BASE_PATH}/api/mode", mode_handler)
app.router.add_get(f"{BASE_PATH}/api/stream", stream_sse_handler)
app.router.add_static(f"{BASE_PATH}/static", STATIC_DIR)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=7681)

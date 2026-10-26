"""Codex desktop adapter, without a second execution owner.

Native IPC is an internal, version-gated desktop protocol. The connection only
registers as a follower. No rollout, auth file, desktop DB or stdio is opened.
Only normalized visible messages and explicit pending requests leave this module.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import html
import re
import tempfile
import json
import os
import stat
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

MAX_FRAME = 32 * 1024 * 1024
MAX_TEXT = 12000
MAX_MESSAGE_BYTES = 256 * 1024
MESSAGE_MIN_INTERVAL = .5
NOTIFY_DELAY = .12
RECONCILE_DELAYS = (.6, 1.5, 3.0)
SUPPORTED_REQUESTS = {
    "item/commandExecution/requestApproval": "thread-follower-command-approval-decision",
    "item/fileChange/requestApproval": "thread-follower-file-approval-decision",
    "item/tool/requestUserInput": "thread-follower-submit-user-input",
}
DECISION_LABELS = {
    "accept": "允许一次", "acceptForSession": "本会话允许", "decline": "拒绝",
    "cancel": "拒绝并停止当前轮次",
}


class DesktopError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _safe_socket(path: str) -> bool:
    try:
        info = os.lstat(path)
        directory = os.lstat(os.path.dirname(path))
        return (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_ISDIR(directory.st_mode) and directory.st_uid == os.getuid()
                and not directory.st_mode & 0o022)
    except OSError:
        return False


def _visible_messages(result: dict, limit: int) -> list[dict]:
    """Whitelist the read_thread result; never forward arbitrary tool outputs."""
    if not isinstance(result, dict) or not isinstance(result.get("turns"), list):
        raise DesktopError("message_schema", "总控消息格式暂不支持")
    for turn in result["turns"]:
        if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
            raise DesktopError("message_schema", "总控消息格式暂不支持")
        for item in turn["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise DesktopError("message_schema", "总控消息格式暂不支持")
            kind = item["type"]
            if (kind == "agentMessage" and not isinstance(item.get("text"), str)
                    or kind == "userMessage" and not isinstance(item.get("content"), list)
                    or kind.endswith("Message") and kind not in {"agentMessage", "userMessage", "steeringUserMessage"}):
                raise DesktopError("message_schema", "总控消息格式暂不支持")
    messages: list[dict] = []
    for turn in sorted(result["turns"], key=lambda t: t.get("startedAt") or 0):
        if not isinstance(turn, dict):
            continue
        for index, item in enumerate(turn.get("items", [])):
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            text = ""
            images = []
            if kind == "userMessage":
                for block in item.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text += str(block.get("text", "")) + "\n"
                    elif block.get("type") == "localImage" and isinstance(block.get("path"), str):
                        images.append({"_path": block["path"]})
                role = "user"
            elif kind == "agentMessage" and item.get("phase") != "analysis":
                text = str(item.get("text", ""))
                # Only explicit absolute local image Markdown; server validates allowed roots/content.
                for match in re.finditer(r"!\[[^\]\n]*\]\(<?(/[^\n]*?)>?\)", text):
                    image_path = match.group(1)
                    if ("\x00" not in image_path and image_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                            and image_path not in {im["_path"] for im in images}):
                        images.append({"_path": image_path})
                role = "assistant"
            elif (kind == "functionCallOutput" and item.get("namespace") == "codex_app"
                  and item.get("name") in ("send_message_to_thread", "create_thread")):
                output = item.get("output", {})
                raw = output.get("text", "") if isinstance(output, dict) else ""
                if not isinstance(raw, str) or not raw.lstrip().startswith("<codex_delegation>"):
                    continue
                match = re.search(r"<input>(.*?)</input>", raw, re.S)
                if not match:
                    continue
                text = html.unescape(match.group(1).strip())
                role = "user"
            else:
                continue
            if not text.strip() and not images:
                continue
            messages.append({
                "id": str(item.get("id") or f"{turn.get('id', 'turn')}:{index}"),
                "role": role, "text": text.rstrip()[:MAX_TEXT], "images": images,
                "created_at": turn.get("startedAt") or 0,
            })
    messages = messages[-max(1, min(limit, 60)):]
    while len(_json(messages)) > MAX_MESSAGE_BYTES and len(messages) > 1:
        messages.pop(0)
    return messages


class DesktopAdapter:
    def __init__(self, caller_thread_id: str, ipc_path: str | None = None,
                 tools_pipe: str | None = None, max_threads: int = 50):
        self.caller_thread_id = caller_thread_id
        self.ipc_path = ipc_path or str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "ipc/ipc.sock")
        self.tools_pipe = tools_pipe
        self.max_threads = min(50, max(1, max_threads))
        self._callback: Callable[[], Awaitable[None]] | None = None
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._answer_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._client_id = "initializing-client"
        self._epoch = uuid.uuid4().hex
        self._owners: dict[str, str] = {}
        self._native: dict[str, dict] = {}
        self._revisions: dict[str, int] = {}
        self._thread_status: dict[str, Any] = {}
        self._threads: list[dict] = []
        self._catalog: dict[str, dict] = {}
        self._controller_id: str | None = None
        self._messages: list[dict] = []
        self._messages_at = 0.0
        self._messages_dirty = True
        self._message_generation = 0
        self._controller_generation = 0
        self._reconcile_task: asyncio.Task | None = None
        self._reconcile_wake = asyncio.Event()
        self._reconcile_serial = 0
        self._deferred_messages: asyncio.Task | None = None
        self._message_lock = asyncio.Lock()
        self._async_items: dict[str, dict[str, dict]] = {}
        self._async_sent: set[str] = set()
        self._projection_tasks: dict[str, asyncio.Task] = {}
        self._native_changed = asyncio.Condition()
        self._change_task: asyncio.Task | None = None
        self._notify_pending = False
        self._following: set[str] = set()
        self._connected = asyncio.Event()
        self._tools_lock = asyncio.Lock()
        self._last_inventory = 0.0
        self.last_error: str | None = None

    async def start(self, on_change: Callable[[], Awaitable[None]]) -> None:
        if self._tasks:
            return
        self._callback = on_change
        self._closed = False
        self._tasks = [asyncio.create_task(self._connection_loop()),
                       asyncio.create_task(self._inventory_loop())]

    async def close(self) -> None:
        self._closed = True
        self._controller_generation += 1
        self._notify_pending = False
        if self._reconcile_task:
            self._reconcile_task.cancel()
        if self._writer and self._connected.is_set():
            for tid in list(self._following):
                with contextlib.suppress(Exception):
                    await self._follow(tid, False)
        for task in list(self._projection_tasks.values()):
            task.cancel()
        for task in self._tasks:
            task.cancel()
        if self._change_task:
            self._change_task.cancel()
        if self._deferred_messages:
            self._deferred_messages.cancel()
        finishing = list(self._tasks) + list(self._projection_tasks.values())
        finishing += [t for t in (self._change_task, self._deferred_messages, self._reconcile_task) if t]
        for task in finishing:
            if task is not asyncio.current_task():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._tasks = []
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._connected.clear()

    async def _notify(self) -> None:
        if self._closed:
            return
        self._notify_pending = True
        if self._change_task and not self._change_task.done():
            return
        async def run():
            try:
                while not self._closed:
                    await asyncio.sleep(NOTIFY_DELAY)
                    # Events during the debounce are included in this callback;
                    # events during the callback require one trailing drain.
                    self._notify_pending = False
                    if self._callback:
                        with contextlib.suppress(Exception):
                            await self._callback()
                    if not self._notify_pending:
                        break
            finally:
                if self._change_task is asyncio.current_task():
                    self._change_task = None
        self._change_task = asyncio.create_task(run())

    def _native_ready(self, tid: str | None) -> bool:
        return bool(tid and self._connected.is_set() and tid in self._owners
                    and tid in self._revisions and tid in self._following)

    def _queue_reconcile(self) -> None:
        """One resettable quiet-window task, at most three tail refreshes per burst."""
        if self._closed or not self._controller_id:
            return
        self._reconcile_serial += 1
        self._reconcile_wake.set()
        if self._reconcile_task and not self._reconcile_task.done():
            return
        tid, generation = self._controller_id, self._controller_generation
        async def run():
            try:
                while not self._closed and tid == self._controller_id and generation == self._controller_generation:
                    serial = self._reconcile_serial
                    self._reconcile_wake.clear()
                    restart = False
                    for delay in RECONCILE_DELAYS:
                        try:
                            await asyncio.wait_for(self._reconcile_wake.wait(), delay)
                            restart = True
                            break
                        except asyncio.TimeoutError:
                            pass
                        if (self._closed or tid != self._controller_id
                                or generation != self._controller_generation):
                            return
                        if serial != self._reconcile_serial:
                            restart = True
                            break
                        self._messages_dirty = True
                        self._message_generation += 1
                        await self._notify()
                    if not restart:
                        break
            finally:
                if self._reconcile_task is asyncio.current_task():
                    self._reconcile_task = None
        self._reconcile_task = asyncio.create_task(run())

    @staticmethod
    def _message_patch(patch: dict) -> bool:
        path = patch.get("path")
        return bool(isinstance(path, list) and path and (
            path[0] == "threadRuntimeStatus" or path[0] in ("turns", "turnHistory") and (
                any(field in path for field in ("items", "text", "status", "turnId", "turnStartedAtMs"))
                or isinstance(patch.get("value"), (dict, list)))))

    async def _write(self, payload: dict) -> None:
        data = _json(payload)
        if not self._writer or len(data) > MAX_FRAME:
            raise DesktopError("desktop_unavailable", "Codex 桌面连接不可用")
        async with self._write_lock:
            self._writer.write(struct.pack("<I", len(data)) + data)
            await self._writer.drain()

    async def _request(self, method: str, params: dict, owner: str | None = None,
                       version: int = 1, timeout: float = 7) -> dict:
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        payload = {"type": "request", "requestId": rid, "sourceClientId": self._client_id,
                   "version": version, "method": method, "params": params}
        if owner:
            payload["targetClientId"] = owner
        try:
            await self._write(payload)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _follow(self, tid: str, enabled: bool) -> None:
        owner = self._owners.get(tid)
        if not owner:
            return
        await self._write({"type": "broadcast", "sourceClientId": self._client_id,
                           "version": 1, "method": "thread-stream-following-changed",
                           "targetClientIds": [owner], "params": {
                               "hostId": "local", "conversationId": tid, "following": enabled}})
        if enabled:
            self._following.add(tid)
        else:
            self._following.discard(tid)

    async def _connection_loop(self) -> None:
        delay = .5
        while not self._closed:
            try:
                if not _safe_socket(self.ipc_path):
                    raise DesktopError("desktop_unavailable", "未找到安全的 Codex 桌面连接")
                self._reader, self._writer = await asyncio.open_unix_connection(self.ipc_path)
                self._client_id = "initializing-client"
                self._epoch = uuid.uuid4().hex
                self._owners.clear()
                self._native.clear()
                self._async_items.clear()
                self._async_sent.clear()
                self._revisions.clear()
                self._following.clear()
                read_task = asyncio.create_task(self._read_loop())
                try:
                    response = await self._request("initialize", {"clientType": "codex-pocket-bridge"}, version=0)
                    if response.get("resultType") != "success":
                        raise DesktopError("desktop_unavailable", "Codex 桌面握手失败")
                    self._client_id = response["result"]["clientId"]
                    self._connected.set()
                    self.last_error = None
                    delay = .5
                    await self._notify()
                    await self._subscribe_known()
                    await read_task
                finally:
                    read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await read_task
            except asyncio.CancelledError:
                break
            except Exception:
                self.last_error = "Codex 桌面连接已断开，正在重连"
            finally:
                self._connected.clear()
                self._native.clear()
                self._async_items.clear()
                self._async_sent.clear()
                self._revisions.clear()
                self._owners.clear()
                self._following.clear()
                for future in list(self._pending.values()):
                    if not future.done():
                        future.set_exception(DesktopError("desktop_unavailable", "Codex 桌面连接已断开"))
                if self._writer:
                    self._writer.close()
                    with contextlib.suppress(Exception):
                        await self._writer.wait_closed()
                self._writer = None
                self._reader = None
                await self._notify()
            if not self._closed:
                await asyncio.sleep(delay)
                delay = min(20, delay * 2)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            size = struct.unpack("<I", await self._reader.readexactly(4))[0]
            if not 0 < size <= MAX_FRAME:
                raise DesktopError("protocol_mismatch", "Codex 桌面消息超出安全大小")
            frame = json.loads(await self._reader.readexactly(size))
            if not isinstance(frame, dict):
                continue
            if frame.get("type") == "response":
                future = self._pending.get(str(frame.get("requestId")))
                if future and not future.done():
                    future.set_result(frame)
            elif frame.get("type") == "client-discovery-request":
                # This bridge can never own a thread or handle an unrelated client's RPC.
                await self._write({"type": "client-discovery-response", "requestId": frame.get("requestId"),
                                   "response": {"canHandle": False}})
            elif frame.get("type") == "broadcast":
                await self._broadcast(frame)

    async def _broadcast(self, frame: dict) -> None:
        if frame.get("method") == "client-status-changed":
            params = frame.get("params", {})
            if params.get("status") == "disconnected":
                owner = params.get("clientId")
                for tid in [tid for tid, current in self._owners.items() if current == owner]:
                    self._owners.pop(tid, None)
                    self._async_items.pop(tid, None)
                    self._native.pop(tid, None)
                    self._revisions.pop(tid, None)
                    self._following.discard(tid)
                await self._notify()
            return
        if frame.get("method") == "thread-stream-following-status-requested" and frame.get("version") == 1:
            params = frame.get("params", {})
            tid = params.get("conversationId")
            if params.get("hostId") == "local" and tid in self._following:
                async def rediscover():
                    with contextlib.suppress(Exception):
                        await self._discover(tid)
                asyncio.create_task(rediscover())
            return
        if frame.get("method") != "thread-stream-state-changed" or frame.get("version") != 11:
            return
        params = frame.get("params", {})
        tid = params.get("conversationId")
        owner = frame.get("sourceClientId")
        if params.get("hostId") != "local" or self._owners.get(tid) != owner:
            return
        change = params.get("change", {})
        revision = change.get("revision")
        if not isinstance(revision, int):
            return
        kind = change.get("type")
        if kind == "snapshot":
            state = change.get("conversationState", {})
            if not isinstance(state, dict):
                return
            self._project_async_snapshot(tid, state)
            requests = state.get("requests", [])
            # Discard full history, reasoning, environment, tool results and other native state.
            self._native[tid] = {"requests": copy.deepcopy(requests) if isinstance(requests, list) else []}
            self._thread_status[tid] = copy.deepcopy(state.get("threadRuntimeStatus"))
            self._revisions[tid] = revision
        elif kind == "patches":
            if self._revisions.get(tid) != change.get("baseRevision"):
                asyncio.create_task(self._resnapshot(tid))
                return
            try:
                for patch in change.get("patches", []):
                    self._project_patch(tid, patch)
                self._revisions[tid] = revision
            except (KeyError, IndexError, TypeError, ValueError):
                self._revisions.pop(tid, None)
                asyncio.create_task(self._resnapshot(tid))
                return
        else:
            return
        if tid == self._controller_id:
            self._messages_dirty = True
            self._message_generation += 1
            if kind == "snapshot" or any(self._message_patch(p) for p in change.get("patches", [])):
                self._queue_reconcile()
        async with self._native_changed:
            self._native_changed.notify_all()
        await self._notify()

    def _project_patch(self, tid: str, patch: dict) -> None:
        path = patch.get("path")
        if not isinstance(path, list) or not path:
            return
        if path[0] == "threadRuntimeStatus":
            if len(path) == 1:
                self._thread_status[tid] = copy.deepcopy(patch.get("value"))
            return
        if path[0] in ("turnHistory", "turns"):
            # Only structural/question/user changes matter. Token deltas never scan history.
            value = patch.get("value")
            if ("questions" in path or "content" in path or "input" in path
                    or isinstance(value, dict) and value.get("type") in ("agentMessage", "userMessage", "steeringUserMessage")
                    or isinstance(value, dict) and isinstance(value.get("items"), list)
                    or isinstance(value, (list, dict)) and path[-1] in ("items", "entitiesByKey", "turns", "turnHistory")):
                self._schedule_projection(tid)
            return
        if path[0] != "requests":
            return
        root = self._native.setdefault(tid, {"requests": []})
        node: Any = root
        for key in path[:-1]:
            if isinstance(node, list):
                key = int(key)
            node = node[key]
        key = path[-1]
        operation = patch.get("op")
        value = copy.deepcopy(patch.get("value"))
        if isinstance(node, list):
            index = len(node) if key == "-" else int(key)
            if operation == "add":
                node.insert(index, value)
            elif operation == "replace":
                node[index] = value
            elif operation == "remove":
                node.pop(index)
            else:
                raise ValueError("unsupported patch")
        elif operation in ("add", "replace"):
            node[key] = value
        elif operation == "remove":
            del node[key]
        else:
            raise ValueError("unsupported patch")

    def _project_async_snapshot(self, tid: str, state: dict) -> None:
        # Store only questions and answer IDs, never raw history or tool/reasoning fields.
        turns = list(state.get("turns") or [])[-100:]
        history = state.get("turnHistory", {}).get("history", {})
        entities = history.get("entitiesByKey", {}) if isinstance(history, dict) else {}
        if isinstance(entities, dict):
            turns += list(entities.values())[-100:]
        projected = {}
        answered = set()
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            for item in (turn.get("items") or [])[-300:]:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "agentMessage" and item.get("phase") != "analysis":
                    raw = item.get("questions")
                    if not isinstance(raw, list) or not isinstance(item.get("id"), str):
                        continue
                    questions = []
                    for index, q in enumerate(raw[:20]):
                        if (not isinstance(q, dict) or not isinstance(q.get("title"), str)
                                or len(q["title"]) > MAX_TEXT or not isinstance(q.get("options", []), list) or len(q.get("options", [])) > 50
                                or any(not isinstance(o, str) or len(o) > 2000 for o in q.get("options", []))):
                            continue
                        qid = json.dumps(["request_user_input_async", item["id"], index], separators=(",", ":"), ensure_ascii=False)
                        questions.append({"id": qid, "question": q["title"], "options": [
                            {"id": str(i), "label": o} for i, o in enumerate(q.get("options", [])[:50])],
                            "is_multiple": False, "allow_other": True})
                    if questions:
                        projected[item["id"]] = {"turn_id": turn.get("turnId") or turn.get("id"), "questions": questions}
                elif item.get("type") in ("userMessage", "steeringUserMessage"):
                    if item.get("type") == "steeringUserMessage" and item.get("status") != "accepted":
                        continue
                    blocks = item.get("content") if item.get("type") == "userMessage" else item.get("input")
                    text = "\n".join(b.get("text", "") for b in (blocks or []) if isinstance(b, dict) and b.get("type") == "text")
                    match = re.search(r"<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*$", text, re.S)
                    if match:
                        try:
                            replies = json.loads(match.group(1))
                            if isinstance(replies, dict): replies = [replies]
                            answered.update(r["questionItemId"] for r in replies if isinstance(r, dict) and isinstance(r.get("questionItemId"), str) and isinstance(r.get("answer"), str))
                        except (ValueError, TypeError):
                            pass
        for row in projected.values():
            row["questions"] = [q for q in row["questions"] if q["id"] not in answered]
        self._async_items[tid] = {key: row for key, row in projected.items() if row["questions"]}
        # Confirmation separately records exact accepted question IDs, without answer text.
        self._async_items[tid]["_answered"] = {"ids": answered}

    def _schedule_projection(self, tid: str) -> None:
        if tid in self._projection_tasks and not self._projection_tasks[tid].done():
            return
        async def refresh():
            await asyncio.sleep(.3)
            await self._resnapshot(tid)
        self._projection_tasks[tid] = asyncio.create_task(refresh())

    def _async_requests(self) -> list[dict]:
        result = []
        for tid, items in self._async_items.items():
            if tid not in self._owners or tid not in self._revisions:
                continue
            title = next((r["title"] for r in self._threads if r["id"] == tid), "Codex 会话")
            for item_id, row in items.items():
                if item_id == "_answered": continue
                identity = {"id": item_id, "method": "async_question", "params": row}
                result.append({"id": self._opaque_id(tid, identity), "thread_id": tid, "thread_title": title,
                               "turn_id": row["turn_id"], "status": "pending", "type": "question",
                               "title": row["questions"][0]["question"], "body": "", "options": [],
                               "questions": copy.deepcopy(row["questions"])})
        return result

    async def _answer_async(self, request: dict, answers: dict | None) -> str:
        rid, tid = request["id"], request["thread_id"]
        if rid in self._async_sent:
            raise DesktopError("stale_request", "这个回答已发送，请等待桌面确认")
        questions = request["questions"]
        if (not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}
                or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str)
                       or not v[0].strip() or len(v[0]) > 10000 for v in answers.values())):
            raise DesktopError("invalid_answer", "请完整回答当前问题")
        owner, epoch = self._owners.get(tid), self._epoch
        if not owner or not self._connected.is_set():
            raise DesktopError("desktop_unavailable", "请等待桌面重新连接")
        if rid not in {r["id"] for r in self._async_requests()}:
            raise DesktopError("stale_request", "问题已经改变，请重新核对")
        replies = [{"questionItemId": q["id"], "question": q["question"], "answer": answers[q["id"]][0]} for q in questions]
        prompt = "<send_user_message_question_reply>\n" + json.dumps(replies, ensure_ascii=False, separators=(",", ":")) + "\n</send_user_message_question_reply>"
        runtime = self._thread_status.get(tid)
        if not isinstance(runtime, dict) or runtime.get("type") != "idle":
            raise DesktopError("thread_busy", "请等待当前会话结束本轮处理后再回答")
        if self._epoch != epoch or self._owners.get(tid) != owner or rid not in {r["id"] for r in self._async_requests()}:
            raise DesktopError("stale_request", "问题来源已经改变，请重新核对")
        # Once dispatched an ambiguous result must never automatically dispatch twice.
        if len(self._async_sent) >= 4096:
            raise DesktopError("desktop_unavailable", "请重新连接桌面后继续回答")
        self._async_sent.add(rid)
        try:
            request_body = {"threadId": tid, "input": [{"type": "text", "text": prompt, "text_elements": []}],
                            "clientUserMessageId": uuid.uuid4().hex}
            response = await self._request("thread-follower-start-turn", {
                "conversationId": tid, "turnStart": {"request": request_body,
                    "context": {"inheritThreadSettings": True}}}, owner=owner, version=2, timeout=35)
            if response.get("resultType") != "success":
                return "unknown"
            await self._resnapshot(tid)
        except Exception:
            return "unknown"
        wanted = {q["id"] for q in questions}
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self._epoch != epoch or self._owners.get(tid) != owner:
                return "unknown"
            accepted = self._async_items.get(tid, {}).get("_answered", {}).get("ids", set())
            if wanted <= accepted:
                return "delivered"
            async with self._native_changed:
                try:
                    await asyncio.wait_for(self._native_changed.wait(), max(.01, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
        return "unknown"

    async def _resnapshot(self, tid: str) -> None:
        with contextlib.suppress(Exception):
            await self._follow(tid, True)

    async def _discover(self, tid: str) -> bool:
        if not self._connected.is_set():
            return False
        response = await self._request("thread-owner-discovery", {"hostId": "local", "conversationId": tid})
        if response.get("resultType") != "success":
            return False
        owner = response.get("handledByClientId")
        if not isinstance(owner, str):
            return False
        if self._owners.get(tid) != owner:
            self._async_items.pop(tid, None)
            self._native.pop(tid, None)
            self._revisions.pop(tid, None)
            self._following.discard(tid)
        self._owners[tid] = owner
        await self._follow(tid, True)
        return True

    async def _subscribe_known(self) -> None:
        if not self._connected.is_set():
            return
        wanted = {row["id"] for row in self._threads}
        if self._controller_id:
            wanted.add(self._controller_id)
        for tid in list(self._following - wanted):
            with contextlib.suppress(Exception):
                await self._follow(tid, False)
            self._native.pop(tid, None)
            self._owners.pop(tid, None)
            self._revisions.pop(tid, None)
        # Bounded discovery; this never calls resume or claims history ownership.
        semaphore = asyncio.Semaphore(4)
        async def one(tid):
            if tid in self._following and tid in self._owners:
                return
            async with semaphore:
                with contextlib.suppress(Exception):
                    await self._discover(tid)
        await asyncio.gather(*(one(tid) for tid in wanted))

    async def _inventory_loop(self) -> None:
        while not self._closed:
            with contextlib.suppress(Exception):
                await self._refresh_inventory()
                await self._subscribe_known()
            await asyncio.sleep(15)

    async def _refresh_inventory(self) -> None:
        data = await self._tool("list_threads", {"limit": 50})
        rows = []
        for row in data.get("pinnedThreads", []) + data.get("threads", []):
            if row.get("kind") != "codex" or row.get("hostId", "local") != "local":
                continue
            if row.get("archived") or row.get("subagent") or not isinstance(row.get("id"), str):
                continue
            rows.append({"id": row["id"], "title": str(row.get("title") or "Codex 会话")[:180],
                         "status": str(row.get("status") or "unknown")})
        rows = list({row["id"]: row for row in rows}.values())[:self.max_threads]
        self._last_inventory = time.monotonic()
        if rows != self._threads:
            self._threads = rows
            await self._notify()

    def _pipe_paths(self) -> list[str]:
        paths = [self.tools_pipe, os.environ.get("CODEX_APP_TOOLS_PIPE_PATH")]
        # Enumerate only same-user socket metadata, never whole /proc environments or auth files.
        directory = Path(tempfile.gettempdir()) / "codex-app-tools"
        try:
            info = os.lstat(directory)
            if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022:
                paths.extend(str(path) for path in list(directory.glob("*.sock"))[:10])
        except OSError:
            pass
        return list(dict.fromkeys(path for path in paths if path and _safe_socket(path)))

    async def _tool_rpc(self, path: str, method: str, params: dict, timeout: float = 15) -> dict:
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            payload = _json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            writer.write(struct.pack("<I", len(payload)) + payload)
            await writer.drain()
            while True:
                size = struct.unpack("<I", await asyncio.wait_for(reader.readexactly(4), timeout))[0]
                if not 0 < size <= MAX_FRAME:
                    raise DesktopError("protocol_mismatch", "桌面消息超出安全大小")
                message = json.loads(await asyncio.wait_for(reader.readexactly(size), timeout))
                if message.get("id") != 1:
                    continue
                if "error" in message:
                    raise DesktopError("desktop_error", "Codex 桌面没有接受这个操作")
                return message.get("result", {})
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _tool(self, name: str, args: dict) -> dict:
        if name not in {"list_threads", "read_thread", "send_message_to_thread"}:
            raise DesktopError("unsupported", "未开放这个桌面操作")
        if not self._catalog or not self.tools_pipe or not _safe_socket(self.tools_pipe):
            async with self._tools_lock:
                for path in self._pipe_paths():
                    try:
                        catalog = await self._tool_rpc(path, "tools/list", {"threadStartKind": "all"}, 5)
                        by_name = {item["name"]: item for item in catalog["tools"]}
                        if name in by_name:
                            self.tools_pipe = path
                            self._catalog = by_name
                            break
                    except Exception:
                        continue
                else:
                    raise DesktopError("desktop_unavailable", "请在电脑上打开 Codex")
        assert self.tools_pipe
        try:
            result = await self._tool_rpc(self.tools_pipe, "tools/call", {
                "arguments": args, "callerSource": "codex", "callId": "pocket-" + uuid.uuid4().hex,
                "hostId": "local", "namespace": self._catalog[name]["namespace"],
                "threadId": self.caller_thread_id, "tool": name, "turnId": "pocket-" + uuid.uuid4().hex,
            }, 35 if name == "send_message_to_thread" else 15)
        except Exception:
            self._catalog.clear()
            raise
        if not result.get("success"):
            raise DesktopError("desktop_error", "Codex 桌面没有接受这个操作")
        text = "\n".join(item.get("text", "") for item in result.get("contentItems", []) if item.get("type") == "inputText")
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return {"text": text}

    def _known_thread(self, tid: str) -> bool:
        return any(row["id"] == tid for row in self._threads)

    async def snapshot(self, controller_id: str | None, limit: int = 30) -> dict:
        if controller_id != self._controller_id:
            self._controller_generation += 1
            if self._reconcile_task:
                self._reconcile_task.cancel()
                self._reconcile_task = None
            if self._deferred_messages:
                self._deferred_messages.cancel()
                self._deferred_messages = None
            self._controller_id = controller_id
            self._message_generation += 1
            self._messages = []
            self._messages_at = 0.0
            self._messages_dirty = True
            if controller_id and self._connected.is_set():
                with contextlib.suppress(Exception):
                    await self._discover(controller_id)
            self._queue_reconcile()
        controller_generation = self._controller_generation
        if not self._threads and time.monotonic() - self._last_inventory > 2:
            with contextlib.suppress(Exception):
                await self._refresh_inventory()
        if controller_id and self._known_thread(controller_id):
            # Native event driven; fallback at most every 5s only while native stream unavailable.
            fallback_due = not self._native_ready(controller_id) and time.monotonic() - self._messages_at > 5
            read_attempted = False
            if (self._messages_dirty or fallback_due) and time.monotonic() - self._messages_at >= MESSAGE_MIN_INTERVAL:
                async with self._message_lock:
                    fallback_due = not self._native_ready(controller_id) and time.monotonic() - self._messages_at > 5
                    if (controller_id == self._controller_id and controller_generation == self._controller_generation
                            and (self._messages_dirty or fallback_due)
                            and time.monotonic() - self._messages_at >= MESSAGE_MIN_INTERVAL):
                        read_attempted = True
                        try:
                            generation = self._message_generation
                            result = await self._tool("read_thread", {
                                "threadId": controller_id, "hostId": "local", "turnLimit": min(10, max(5, limit)),
                                "includeOutputs": True, "maxOutputCharsPerItem": MAX_TEXT,
                            })
                            messages = _visible_messages(result, limit)
                            if controller_id == self._controller_id and controller_generation == self._controller_generation:
                                self._messages = messages
                                self._messages_dirty = self._message_generation != generation
                                self._messages_at = time.monotonic()
                                if self.last_error == "暂时无法读取总控消息，已保留最近内容":
                                    self.last_error = None
                        except Exception:
                            if controller_id == self._controller_id and controller_generation == self._controller_generation:
                                self._messages_dirty = True
                                self._messages_at = time.monotonic()
                                self.last_error = "暂时无法读取总控消息，已保留最近内容"
            # Only defer a throttled read. Failed/unknown schemas retain dirty but
            # cannot recursively create an unbounded retry loop on their own.
            if (controller_id == self._controller_id and controller_generation == self._controller_generation
                    and self._messages_dirty and not read_attempted
                    and (not self._deferred_messages or self._deferred_messages.done())):
                async def refresh_later():
                    await asyncio.sleep(max(.01, MESSAGE_MIN_INTERVAL - (time.monotonic() - self._messages_at)))
                    if controller_id == self._controller_id and controller_generation == self._controller_generation:
                        await self._notify()
                self._deferred_messages = asyncio.create_task(refresh_later())
        threads = copy.deepcopy(self._threads)
        requests = self._async_requests()
        for tid, native in list(self._native.items()):
            for item in native.get("requests", []):
                public = self._normalize_request(tid, item)
                if public:
                    requests.append(public)
        controller = next(({"id": row["id"], "title": row["title"]} for row in threads if row["id"] == self._controller_id), None)
        native_ready = self._native_ready(self._controller_id)
        return {"controller": controller, "threads": threads, "messages": copy.deepcopy(self._messages[-limit:]),
                "requests": requests, "capabilities": {
                    "chat": bool(self._catalog and controller), "images": native_ready,
                    "live": native_ready, "approvals": bool(self._revisions),
                    "user_input": bool(self._revisions), "interrupt": False,
                }}

    def _opaque_id(self, tid: str, item: dict) -> str:
        identity = [self._epoch, self._owners.get(tid), tid, item.get("id"),
                    item.get("method"), item.get("params", {})]
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _normalize_request(self, tid: str, item: dict) -> dict | None:
        if not isinstance(item, dict) or not isinstance(item.get("method"), str):
            return None
        params = item.get("params", {})
        if not isinstance(params, dict) or params.get("threadId") != tid:
            return None
        title = next((row["title"] for row in self._threads if row["id"] == tid), "Codex 会话")
        result = {"id": self._opaque_id(tid, item), "thread_id": tid, "thread_title": title,
                  "status": "pending", "turn_id": params.get("turnId"), "options": []}
        if item["method"] not in SUPPORTED_REQUESTS:
            result.update(type="desktop_only", title="请在电脑上处理这个请求",
                          body="这类原生确认暂不支持手机操作，原请求仍保留在电脑端。", questions=[])
            return result
        if item["method"] == "item/fileChange/requestApproval":
            result.update(type="desktop_only", title="请在电脑核对文件修改",
                          body="当前桌面请求未提供完整文件修改内容，请在电脑查看差异后决定。", questions=[])
            return result
        command_body = ""
        if item["method"] == "item/commandExecution/requestApproval":
            command, cwd, reason = params.get("command"), params.get("cwd"), params.get("reason")
            if not isinstance(command, str) or not command.strip() or not isinstance(cwd, str) or not os.path.isabs(cwd):
                result.update(type="desktop_only", title="请在电脑核对完整命令",
                              body="请求缺少完整命令或明确工作目录，请在电脑核对后决定。", questions=[])
                return result
            sections = ["命令：\n" + command, "工作目录：\n" + cwd]
            if reason is not None:
                if not isinstance(reason, str):
                    result.update(type="desktop_only", title="请在电脑核对完整请求", body="请求说明格式暂不支持，请在电脑处理。", questions=[])
                    return result
                sections.append("请求原因：\n" + reason)
            # Preserve the complete scope of any requested command/network rule.
            for field, label in (("networkApprovalContext", "网络访问范围"),
                                 ("proposedNetworkPolicyAmendments", "拟应用的网络规则"),
                                 ("proposedExecpolicyAmendment", "拟记住的命令规则"),
                                 ("additionalPermissions", "额外权限范围")):
                if params.get(field) is not None:
                    sections.append(label + "：\n" + json.dumps(params[field], ensure_ascii=False, indent=2))
            command_body = "\n\n".join(sections)
            if len(command_body) > MAX_TEXT:
                result.update(type="desktop_only", title="请在电脑核对完整命令",
                              body="完整命令及其执行范围较长，请在电脑查看后决定。", questions=[])
                return result
        if item["method"] == "item/tool/requestUserInput":
            questions = []
            for question in params.get("questions", []):
                if not isinstance(question, dict) or not isinstance(question.get("id"), str):
                    continue
                # Secret questions must stay on the desktop; phone should not become a secret log.
                if question.get("isSecret"):
                    result.update(type="desktop_only", title="请在电脑上填写敏感信息",
                                  body="这条问题涉及敏感内容，手机不会接收问题或答案。", questions=[])
                    return result
                questions.append({"id": question["id"], "header": str(question.get("header", ""))[:160],
                                  "question": str(question.get("question", ""))[:MAX_TEXT],
                                  "options": [{"id": str(index), "label": str(option.get("label", ""))[:1000],
                                               "description": str(option.get("description", ""))[:2000]}
                                              for index, option in enumerate(question.get("options") or []) if isinstance(option, dict)],
                                  "is_multiple": False, "allow_other": True})
            if not questions:
                return None
            result.update(type="user_input", title=questions[0]["question"], body="", questions=questions)
        else:
            is_command = item["method"] == "item/commandExecution/requestApproval"
            decisions = self._decisions(item)
            result.update(type="command_approval" if is_command else "file_approval",
                          title="允许运行这条命令吗？" if is_command else "允许修改这些文件吗？",
                          body=command_body if is_command else str(params.get("reason") or "请核对电脑上的原始操作")[:MAX_TEXT],
                          reason=str(params.get("reason") or "")[:MAX_TEXT],
                          options=[{"id": key, "label": label} for key, (label, _) in decisions.items()])
        return result

    @staticmethod
    def _decisions(item: dict) -> dict[str, tuple[str, Any]]:
        params = item.get("params", {})
        decisions: dict[str, tuple[str, Any]] = {
            key: (DECISION_LABELS[key], key) for key in ("accept", "acceptForSession", "decline", "cancel")
        }
        if item.get("method") == "item/commandExecution/requestApproval":
            amendment = params.get("proposedExecpolicyAmendment")
            if isinstance(amendment, list) and amendment:
                decisions["execpolicy"] = ("允许并记住此命令规则", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": copy.deepcopy(amendment)}})
            for index, amendment in enumerate(params.get("proposedNetworkPolicyAmendments") or []):
                if not isinstance(amendment, dict):
                    continue
                decisions[f"network-{index}"] = ("应用网络规则：" + str(amendment.get("action", "")) + " " + str(amendment.get("host", "")),
                                                  {"applyNetworkPolicyAmendment": {"network_policy_amendment": copy.deepcopy(amendment)}})
        return decisions

    def _find_request(self, request_id: str) -> tuple[str, dict] | None:
        for tid, native in self._native.items():
            for item in native.get("requests", []):
                if isinstance(item, dict) and self._opaque_id(tid, item) == request_id:
                    return tid, item
        return None

    async def answer(self, request_id: str, choice_id: str | None = None,
                     answers: dict[str, list[str]] | None = None) -> str:
        # Two phones choosing the same pending card cannot race two native responses.
        async with self._answer_lock:
            return await self._answer(request_id, choice_id, answers)

    async def _answer(self, request_id: str, choice_id: str | None = None,
                      answers: dict[str, list[str]] | None = None) -> str:
        async_found = next((r for r in self._async_requests() if r["id"] == request_id), None)
        if async_found:
            return await self._answer_async(async_found, answers)
        found = self._find_request(request_id)
        if not found:
            raise DesktopError("stale_request", "这个请求已处理或失效，请刷新后查看")
        tid, original = found
        public_request = self._normalize_request(tid, original)
        if not public_request or public_request.get("type") == "desktop_only":
            raise DesktopError("unsupported", "请在电脑上处理这个请求")
        method = original["method"]
        native_params = original.get("params", {})
        owner = self._owners.get(tid)
        epoch = self._epoch
        revision = self._revisions.get(tid)
        if not owner or revision is None or not self._connected.is_set():
            raise DesktopError("desktop_unavailable", "请等待桌面重新连接")
        if method == "item/tool/requestUserInput":
            answers = answers or {}
            questions = native_params.get("questions", [])
            ids = {question["id"] for question in questions}
            if set(answers) != ids or any(not isinstance(values, list) or not values or len(values) > 10 or
                                          any(not isinstance(value, str) or len(value) > 10000 for value in values)
                                          for values in answers.values()):
                raise DesktopError("invalid_answer", "请完整回答当前问题")
            result = {"answers": {key: {"answers": values} for key, values in answers.items()}}
            params = {"conversationId": tid, "requestId": original["id"], "response": result}
        else:
            decisions = self._decisions(original)
            if choice_id not in decisions:
                raise DesktopError("invalid_answer", "请选择原请求提供的选项")
            params = {"conversationId": tid, "requestId": original["id"], "decision": decisions[choice_id][1]}
        # Recheck owner immediately before dispatch. Never rediscover/retarget a pending answer.
        if self._owners.get(tid) != owner or self._epoch != epoch or self._find_request(request_id) is None:
            raise DesktopError("stale_request", "来源会话状态已改变，请重新核对")
        try:
            response = await self._request(SUPPORTED_REQUESTS[method], params, owner=owner)
        except Exception:
            return "unknown"
        if response.get("resultType") != "success":
            return "unknown"
        # Native follower RPC can return ok:true for a stale/no-op request. Require an owner patch.
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self._epoch != epoch or self._owners.get(tid) != owner:
                return "unknown"
            if self._revisions.get(tid, revision) > revision and not self._find_request(request_id):
                return "delivered"
            async with self._native_changed:
                try:
                    await asyncio.wait_for(self._native_changed.wait(), max(.01, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
        return "unknown"

    async def send(self, thread_id: str, text: str, attachments: list[dict] | None = None) -> str:
        if not self._known_thread(thread_id):
            raise DesktopError("invalid_thread", "这个会话不在当前电脑的可访问列表中")
        attachments = attachments or []
        if not attachments:
            try:
                await self._tool("send_message_to_thread", {"threadId": thread_id, "hostId": "local", "prompt": text})
                self._messages_dirty = True
                await self._notify()
                return "delivered"
            except Exception:
                return "unknown"
        # Image turns are native to the existing owner. Never spawn a new engine as a fallback.
        owner = self._owners.get(thread_id)
        epoch = self._epoch
        if not owner or thread_id not in self._revisions or not self._connected.is_set():
            raise DesktopError("images_unavailable", "这个桌面会话暂不支持图片，请在电脑端接入后重试")
        runtime_status = self._thread_status.get(thread_id)
        if isinstance(runtime_status, dict) and runtime_status.get("type") == "active":
            raise DesktopError("thread_busy", "这个会话正在处理消息，请等本轮结束再发送图片")
        if not isinstance(runtime_status, dict) or runtime_status.get("type") != "idle":
            raise DesktopError("images_unavailable", "尚未确认这个桌面会话可接收图片，请稍后重试")
        inputs = [{"type": "text", "text": text, "text_elements": []}] if text else []
        for media in attachments:
            file_path = media.get("path")
            if not isinstance(file_path, str) or not os.path.isabs(file_path) or not os.path.isfile(file_path):
                raise DesktopError("invalid_media", "图片文件暂不可用，请重新上传")
            inputs.append({"type": "localImage", "path": file_path})
        if self._owners.get(thread_id) != owner or self._epoch != epoch:
            raise DesktopError("stale_thread", "桌面会话状态已改变")
        request = {"threadId": thread_id, "input": inputs, "clientUserMessageId": uuid.uuid4().hex}
        params = {"conversationId": thread_id, "turnStart": {"request": request, "context": {"inheritThreadSettings": True}}}
        try:
            response = await self._request("thread-follower-start-turn", params, owner=owner, version=2, timeout=35)
            if response.get("resultType") != "success" or self._epoch != epoch:
                return "unknown"
            self._messages_dirty = True
            await self._notify()
            return "delivered"
        except Exception:
            return "unknown"

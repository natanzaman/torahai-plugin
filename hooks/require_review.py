#!/usr/bin/env python3
"""TorahAI Stop hook: a reply that used TorahAI tools may end only if its exact text passed review_answer.

Claude Code runs this when Claude is about to end a turn (hooks/hooks.json). Input on stdin (Stop event, read as UTF-8): transcript_path,
stop_hook_active, last_assistant_message. TorahAI tools are the plugin's server (mcp__plugin_torahai_torahai__*) and any other MCP server
whose results in the turn carry TorahAI's SOURCE_NOTE (a connector added by hand); the same tool names on other servers are ignored. It reads only the file named by transcript_path, on this machine, and sends nothing.
stdout is either empty (the reply may end) or one block JSON line; the draft and the transcript are never printed.
Python 3.9+ standard library only.
"""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from typing import List, Optional

# the TorahAI tool names; group 1 is the MCP server (between mcp__ and the last __<tool>), group 2 the tool
TORAHAI_TOOL = re.compile(r"^mcp__(.+)__(search_sources|get_text|verify_quotes|review_answer|get_links)$")
# a server counts as TorahAI only when it is the plugin's own, or one of its tool results in the turn carries SOURCE_NOTE (a connector added
# by hand may be named anything); another server's get_text etc. never does, so its turn is not blocked (a block would ask Claude to send
# the whole, possibly private, reply to TorahAI)
PLUGIN_SERVER = "plugin_torahai_torahai"
# every TorahAI tool result's "source": apps/web/lib/mcpTools.ts SOURCE_NOTE (keep equal)
SOURCE_NOTE = "Texts from Sefaria (sefaria.org); licence per version."

# pinned block texts (decision 3); Claude reads them
NOT_REVIEWED = ("TorahAI: this reply used TorahAI sources but was not reviewed. Call review_answer with the whole reply and its quotes, "
                "fix what it reports, and send the reply exactly as reviewed.")
NOT_PASSED = ("TorahAI: review_answer did not pass. Fix the items it reported, call review_answer again, and send the reply exactly as "
              "reviewed.")
DIFFERS = "TorahAI: the reply sent is not the reviewed draft. Send the reviewed draft exactly, or review the new text."

# the transcript is written asynchronously and may lag the conversation: wait this long and read it again before blocking
REREAD_DELAY_S = 1.0


class Unreadable(Exception):
    """stdin or the transcript cannot be read or parsed at all: the hook fails open."""


def draft_hash_form(draft: str) -> str:
    """apps/web/lib/mcpTools.ts draftHashForm, the form review_answer's draft_sha256 hashes: NFC; CRLF, then CR, as LF; each line's
    trailing spaces and tabs removed; leading and trailing spaces, tabs and LFs removed (only those, so not str.strip())."""
    s = re.sub(r"\r\n?", "\n", unicodedata.normalize("NFC", draft))
    return "\n".join(re.sub(r"[ \t]+$", "", line) for line in s.split("\n")).strip(" \t\n")


def draft_sha256(draft: str) -> str:
    return hashlib.sha256(draft_hash_form(draft).encode("utf-8")).hexdigest()


def _content(entry: dict):
    message = entry.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(entry: dict) -> list:
    """The content blocks of a message entry (a string content has none)."""
    content = _content(entry)
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def is_typed_prompt(entry: dict) -> bool:
    """A prompt the user typed, which starts a turn. Not one: tool_result entries (also type "user", and they carry a promptId too),
    isMeta entries (system text), and the compaction summary written after a compact_boundary (isCompactSummary, string content)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = _content(entry)
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(b.get("type") == "tool_result" for b in _blocks(entry))


def read_turn(path: str) -> List[dict]:
    """The entries of the current turn (after the last typed prompt), in file order. A line that does not parse is skipped (the writer
    may be mid-append on the last one); Unreadable when the file cannot be read or no line of it parses."""
    try:
        with open(path, "rb") as fh:
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError as e:
        raise Unreadable("cannot read the transcript (%s)" % type(e).__name__)
    turn, parsed = [], 0
    # walk back from the end so only the current turn is parsed; a mid-turn compaction does not stop the walk (its summary is no prompt)
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):  # RecursionError: a line nested too deep to parse, skipped like a malformed one
            continue
        parsed += 1
        if not isinstance(entry, dict):
            continue
        if is_typed_prompt(entry):
            break
        turn.append(entry)
    if parsed == 0 and any(line.strip() for line in lines):
        raise Unreadable("the transcript does not parse")
    turn.reverse()
    return turn


def _result_text(block: dict) -> str:
    """A tool_result's content: a string, or a list of blocks whose text blocks are joined."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def _json_object(text: str) -> Optional[dict]:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):  # RecursionError: nested too deep to parse, so not a review or a TorahAI result
        return None
    return value if isinstance(value, dict) else None


def verdict(turn: List[dict], message: str) -> Optional[str]:
    """None when the reply may end, else the block reason."""
    # tool calls are in type "assistant" entries only (api-request-blob entries copy message content and are not calls)
    uses = []  # (server, tool, tool_use block) for each TorahAI-named call, in order
    for e in turn:
        if e.get("type") != "assistant":
            continue
        for b in _blocks(e):
            m = TORAHAI_TOOL.match(b["name"]) if b.get("type") == "tool_use" and isinstance(b.get("name"), str) else None
            if m:
                uses.append((m.group(1), m.group(2), b))
    if not uses:
        return None
    results = [b for e in turn if e.get("type") == "user" for b in _blocks(e) if b.get("type") == "tool_result"]
    server_of = {b.get("id"): server for server, _, b in uses if isinstance(b.get("id"), str)}
    torahai = {PLUGIN_SERVER}
    for r in results:
        server = server_of.get(r.get("tool_use_id"))
        if server is not None and server not in torahai:
            view = _json_object(_result_text(r))
            if view is not None and view.get("source") == SOURCE_NOTE:
                torahai.add(server)
    uses = [(server, tool, b) for server, tool, b in uses if server in torahai]
    if not uses:
        return None
    reviews = [b for _, tool, b in uses if tool == "review_answer"]
    if not reviews:
        return NOT_REVIEWED
    last_id = reviews[-1].get("id")  # the last TorahAI review_answer by position (parallel calls are separate entries, in order)
    results = [b for b in results if b.get("tool_use_id") == last_id]
    if not results:
        return NOT_PASSED  # no result in the file: not a passing review
    if results[-1].get("is_error"):
        return NOT_PASSED  # draft too long, gate refused, input error (plain text): never a pass
    view = _json_object(_result_text(results[-1]))
    if view is None or view.get("pass") is not True:
        return NOT_PASSED
    if view.get("draft_sha256") != draft_sha256(message):
        return DIFFERS
    return None


def read_stdin(stdin) -> str:
    """stdin as UTF-8 whatever the locale or PYTHONIOENCODING (the transcript is read as UTF-8 too): the bytes of stdin.buffer when there
    is one, else the text (a text-only stream, as in the in-process tests). Bytes that are not UTF-8 are Unreadable."""
    buffer = getattr(stdin, "buffer", None)
    if buffer is None:
        return stdin.read()
    try:
        return buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        raise Unreadable("stdin is not UTF-8")


def main(stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, sleep=time.sleep) -> int:
    try:
        text = read_stdin(stdin)
        try:
            data = json.loads(text)
        except ValueError:
            raise Unreadable("stdin is not JSON")
        if not isinstance(data, dict):
            raise Unreadable("stdin is not a JSON object")
        # Claude is already continuing because of a Stop hook: let the turn end. This is the only loop guard (Claude Code's 8-block cap
        # resets on every tool call, so it never stops block -> review -> block). Every outcome is then exit 0 with no output, so the
        # transcript is not read. Accepted: a reply reworded again after the retry's review goes out unreviewed (one retry).
        if data.get("stop_hook_active") is True:
            return 0
        path, message = data.get("transcript_path"), data.get("last_assistant_message")
        if not isinstance(path, str) or not path:
            raise Unreadable("no transcript_path")
        if not isinstance(message, str):
            raise Unreadable("no last_assistant_message")
        path = os.path.expanduser(path)
        if verdict(read_turn(path), message) is None:
            return 0
        # the transcript may not hold the turn's last entries yet: wait, read it again, and block only if the re-read blocks too
        sleep(REREAD_DELAY_S)
        reason = verdict(read_turn(path), message)
        if reason is None:
            return 0
        stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
        return 0
    except Unreadable as e:
        stderr.write("torahai review hook: %s; this reply is not checked\n" % e)
        return 0
    except Exception as e:  # a bug here must not print data (a traceback could): name the error only; the reply is not checked
        stderr.write("torahai review hook: unexpected %s; this reply is not checked\n" % type(e).__name__)
        return 0


if __name__ == "__main__":
    sys.exit(main())

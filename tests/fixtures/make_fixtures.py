"""Writes the transcript fixtures (*.jsonl) next to this file: python3 tests/fixtures/make_fixtures.py

The content is synthetic; the shape copies real Claude Code 2.1.302 transcripts (inspected for structure only, 2026-10-20):
- one assistant content block per entry (thinking, text and each tool_use are separate entries);
- a tool_result is a type "user" entry whose content is a list holding the tool_result block; an MCP result's content is a list of
  {"type": "text"} blocks, an MCP error's is a plain string with is_error true;
- a mid-turn auto compaction writes a system compact_boundary entry, then a type "user" entry with isCompactSummary (string content);
- isMeta user entries (string content) carry system text, not prompts.
test_require_review.py checks that these files are what this script writes.
"""
import hashlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = "00000000-0000-4000-8000-000000000000"
PLUGIN = "mcp__plugin_torahai_torahai__"
HAND_ADDED = "mcp__my-torah-connector__"
# every TorahAI tool result's "source" (apps/web/lib/mcpTools.ts:49 SOURCE_NOTE); it tells a hand-added TorahAI server by any name
SOURCE_NOTE = "Texts from Sefaria (sefaria.org); licence per version."

# the reviewed drafts (what Claude sends after review_answer passed)
DRAFT = ("Rashi on Genesis 1:1 explains why the Torah opens with creation (Rashi on Genesis 1:1:1):\n\n"
         "> \"It was not necessary to begin the Torah except from this month\"\n\n"
         "This is a summary, not a ruling; for practice ask a rav. Café note: résumé.")
# Hebrew with points; written in NFC
DRAFT_HE = unicodedata.normalize("NFC", "רש״י על בראשית א:א:\n"
                                 "״בְּרֵאשִׁית בָּרָא״ "
                                 "(Genesis 1:1)\nשָׁלוֹם.")
EARLIER_ANSWER = "An earlier answer about Shabbat candles (Shulchan Arukh, Orach Chayim 263:1)."
SECOND_ANSWER = "A second answer that cites Mishnah Berakhot 1:1 without a review."


def draft_sha256(draft):
    """The same form as hooks/require_review.py and apps/web/lib/mcpTools.ts draftHashForm (kept separate so a bug there shows)."""
    s = re.sub(r"\r\n?", "\n", unicodedata.normalize("NFC", draft))
    s = "\n".join(re.sub(r"[ \t]+$", "", line) for line in s.split("\n")).strip(" \t\n")
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Transcript:
    """Builds entries with the keys real transcripts carry (values fake)."""

    def __init__(self):
        self.entries, self.n, self.last_uuid, self.prompt_id = [], 0, None, None

    def _id(self, kind):
        self.n += 1
        return "%s-%04d" % (kind, self.n)

    def _add(self, entry, chain=True):
        if chain:
            entry = dict({"parentUuid": self.last_uuid, "isSidechain": False, "userType": "external", "cwd": "/Users/example/project",
                          "sessionId": SESSION, "version": "2.1.302", "gitBranch": "main", "entrypoint": "cli",
                          "uuid": self._id("uuid"), "timestamp": "2026-10-20T12:00:%02d.000Z" % (self.n % 60)}, **entry)
            self.last_uuid = entry["uuid"]
        self.entries.append(entry)
        return self

    def snapshot(self):
        return self._add({"type": "file-history-snapshot", "messageId": self._id("msg"), "snapshot": {}, "isSnapshotUpdate": False}, False)

    def prompt(self, text):
        self.prompt_id = self._id("prompt")
        return self._add({"type": "user", "promptId": self.prompt_id, "permissionMode": "default", "promptSource": "typed",
                          "message": {"role": "user", "content": text}})

    def meta(self, text):
        return self._add({"type": "user", "isMeta": True, "promptId": self.prompt_id, "message": {"role": "user", "content": text}})

    def _assistant(self, block):
        return self._add({"type": "assistant", "requestId": self._id("req"),
                          "message": {"id": self._id("msg"), "type": "message", "role": "assistant", "model": "claude-example",
                                      "content": [block], "stop_reason": None, "stop_sequence": None}})

    def thinking(self):
        return self._assistant({"type": "thinking", "thinking": "", "signature": "fake-signature"})

    def text(self, text):
        return self._assistant({"type": "text", "text": text})

    def tool(self, name, tool_input, result, is_error=None):
        """A tool_use and its tool_result. result: a str, or a list of text strings (one text block each); None = no tool_result."""
        tid = self._id("toolu_fake")
        self._assistant({"type": "tool_use", "id": tid, "name": name, "input": tool_input, "caller": {"type": "direct"}})
        if result is None:
            return self
        content = result if isinstance(result, str) else [{"type": "text", "text": t} for t in result]
        block = {"tool_use_id": tid, "type": "tool_result", "content": content}
        if is_error is not None:
            block["is_error"] = is_error
        return self._add({"type": "user", "promptId": self.prompt_id, "sourceToolAssistantUUID": self.last_uuid,
                          "toolUseResult": content, "message": {"role": "user", "content": [block]}})

    def compaction(self):
        self._add({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "isMeta": False, "level": "info",
                   "logicalParentUuid": self.last_uuid,
                   "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000, "durationMs": 1000}})
        return self._add({"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True, "promptId": self.prompt_id,
                          "message": {"role": "user", "content": "This session is being continued from a previous conversation. Summary: ..."}})

    def stop_summary(self):
        return self._add({"type": "system", "subtype": "stop_hook_summary", "hookCount": 1, "hookInfos": [], "hookErrors": [],
                          "preventedContinuation": False, "stopReason": "", "hasOutput": False, "level": "suggestion"})

    def jsonl(self):
        return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries)


def review_view(draft, passed=True):
    """The review_answer JSON view (apps/web/lib/mcpServer.ts:57): draft_sha256 only when pass."""
    view = {"pass": passed, "results": [{"ref": "Rashi on Genesis 1:1:1", "quote": "It was not necessary", "ok": passed,
                                         "in_draft": True, "ref_near": True}],
            "unlisted_quotes": [], "warnings": [], "checklist": "1. ...",
            "next": "pass: send the reply exactly as reviewed." if passed else
                    "not yet: fix the items above and call review_answer again with the corrected reply.",
            "source": SOURCE_NOTE}
    if passed:
        view["draft_sha256"] = draft_sha256(draft)
    return json.dumps(view, ensure_ascii=False)


def result(view):
    """A TorahAI tool result: the JSON view with the server's SOURCE_NOTE, one text block."""
    return [json.dumps(dict(view, source=SOURCE_NOTE), ensure_ascii=False, separators=(",", ":"))]


SEARCH = ({"query": "why does the Torah begin with creation"}, result({"passages": [{"ref": "Rashi on Genesis 1:1:1", "snippet": "..."}]}))
GET_TEXT = ({"ref": "Rashi on Genesis 1:1:1"}, result({"ref": "Rashi on Genesis 1:1:1", "he": "...", "en": "..."}))
CITES = [{"ref": "Rashi on Genesis 1:1:1", "quote": "It was not necessary to begin the Torah except from this month"}]
TOO_LONG = "the reply is too long to review: shorten it to at most 20,000 characters (it must be reviewed whole)"


def earlier_turn(t):
    return t.snapshot().prompt("An earlier question about candles").thinking().text(EARLIER_ANSWER).stop_summary()


def answered_turn(t, prefix=PLUGIN, review=None, draft=DRAFT):
    """prompt, search_sources, get_text, then review_answer with `review` as its result (default: a pass for `draft`)."""
    t.prompt("Why does the Torah begin with creation? Quote Rashi.").thinking()
    t.tool(prefix + "search_sources", *SEARCH).tool(prefix + "get_text", *GET_TEXT).thinking()
    if review is not False:
        t.tool(prefix + "review_answer", {"draft": draft, "citations": CITES}, [review_view(draft)] if review is None else review)
    return t


def build():
    f = {}
    t = earlier_turn(Transcript())
    t.prompt("What does ls show?").tool("Bash", {"command": "ls"}, "README.md").text("One file: README.md.")
    f["no_torahai"] = t
    f["reviewed_pass"] = answered_turn(earlier_turn(Transcript())).text(DRAFT)
    # the result as a plain string, and a Hebrew draft
    f["reviewed_pass_string_hebrew"] = answered_turn(earlier_turn(Transcript()), review=review_view(DRAFT_HE), draft=DRAFT_HE).text(DRAFT_HE)
    f["reviewed_fail"] = answered_turn(earlier_turn(Transcript()), review=[review_view(DRAFT, passed=False)]).text(DRAFT)
    t = answered_turn(earlier_turn(Transcript()), review=False)
    f["review_error"] = t.tool(PLUGIN + "review_answer", {"draft": DRAFT, "citations": CITES}, TOO_LONG, is_error=True).text(DRAFT)
    f["not_reviewed"] = answered_turn(earlier_turn(Transcript()), review=False).text(DRAFT)
    # turn 1 reviewed DRAFT and passed; turn 2 used TorahAI with no review
    t = answered_turn(Transcript().snapshot()).text(DRAFT).stop_summary()
    t.prompt("And Mishnah Berakhot 1:1?").tool(PLUGIN + "get_text", {"ref": "Mishnah Berakhot 1:1"}, result({"ref": "Mishnah Berakhot 1:1"}))
    f["earlier_turn_review"] = t.text(SECOND_ANSWER)
    # review passed, then an auto compaction, then one more TorahAI lookup, then the reviewed draft
    t = answered_turn(earlier_turn(Transcript())).compaction().thinking()
    f["mid_turn_compaction"] = t.tool(PLUGIN + "get_links", {"ref": "Genesis 1:1"}, result({"ref": "Genesis 1:1", "groups": []})).text(DRAFT)
    # the review failed, then an isMeta entry (system text) before the reply
    t = answered_turn(earlier_turn(Transcript()), review=[review_view(DRAFT, passed=False)])
    f["meta_in_turn"] = t.meta("<system-reminder>synthetic system text</system-reminder>").text(DRAFT)
    f["hand_added_connector"] = answered_turn(earlier_turn(Transcript()), prefix=HAND_ADDED).text(DRAFT)
    f["hand_added_not_reviewed"] = answered_turn(earlier_turn(Transcript()), prefix=HAND_ADDED, review=False).text(DRAFT)
    return {name: t.jsonl() for name, t in f.items()}


def main(out_dir=HERE):
    for name, text in build().items():
        with open(os.path.join(out_dir, name + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    with open(os.path.join(out_dir, "malformed.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("this is not JSON\n{\"type\": \"user\", \"message\": \n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else HERE)

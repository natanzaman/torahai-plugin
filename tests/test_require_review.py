"""Tests for hooks/require_review.py, the plugin manifests and the hooks.json guard. Standard library only (unittest), so they run under
`python3 -m pytest -q tests` and `python3 -m unittest discover tests` alike."""
import importlib.util
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import unicodedata
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
HOOK_PATH = os.path.join(ROOT, "hooks", "require_review.py")
sys.path.insert(0, FIX)
import make_fixtures as mf  # noqa: E402

_spec = importlib.util.spec_from_file_location("require_review", HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

# the three block texts, pinned (brief decision 3)
NOT_REVIEWED = ("TorahAI: this reply used TorahAI sources but was not reviewed. Call review_answer with the whole reply and its quotes, "
                "fix what it reports, and send the reply exactly as reviewed.")
NOT_PASSED = ("TorahAI: review_answer did not pass. Fix the items it reported, call review_answer again, and send the reply exactly as "
              "reviewed.")
DIFFERS = "TorahAI: the reply sent is not the reviewed draft. Send the reviewed draft exactly, or review the new text."


def fixture(name):
    return os.path.join(FIX, name + ".jsonl")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text, name="t.jsonl"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def run_hook(self, path, message=mf.DRAFT, active=False, on_sleep=None, stdin=None):
        """Run main() in process; returns (exit code, stdout, stderr, sleep calls). on_sleep runs in place of the ~1 s wait."""
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if on_sleep:
                on_sleep()

        if stdin is None:
            stdin = json.dumps({"session_id": "fake", "transcript_path": path, "cwd": "/tmp", "hook_event_name": "Stop",
                                "stop_hook_active": active, "last_assistant_message": message})
        out, err = io.StringIO(), io.StringIO()
        code = hook.main(io.StringIO(stdin), out, err, sleep)
        return code, out.getvalue(), err.getvalue(), sleeps

    def assertAllowed(self, path, message=mf.DRAFT, **kw):
        code, out, err, sleeps = self.run_hook(path, message, **kw)
        self.assertEqual((code, out, err), (0, "", ""))
        return sleeps

    def assertBlocked(self, path, reason, message=mf.DRAFT, **kw):
        code, out, err, sleeps = self.run_hook(path, message, **kw)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out), {"decision": "block", "reason": reason})  # stdout holds this JSON and nothing else
        self.assertEqual(len(sleeps), 1)  # blocked only after the re-read
        return sleeps


class HashForm(unittest.TestCase):
    def test_every_vector(self):
        with open(os.path.join(FIX, "draft_hash_vectors.json"), encoding="utf-8") as fh:
            vectors = json.load(fh)["vectors"]
        self.assertGreaterEqual(len(vectors), 10)
        for v in vectors:
            with self.subTest(v["name"]):
                self.assertEqual(hook.draft_hash_form(v["input"]), v["form"])
                self.assertEqual(hook.draft_sha256(v["input"]), v["sha256"])


class Accepts(HookCase):
    def test_no_torahai_call(self):
        self.assertEqual(self.assertAllowed(fixture("no_torahai"), "One file: README.md."), [])

    def test_passed_and_identical(self):
        self.assertEqual(self.assertAllowed(fixture("reviewed_pass")), [])

    def test_passed_and_identical_after_crlf_trailing_blanks_nfd(self):
        for name, msg in [("crlf", mf.DRAFT.replace("\n", "\r\n")),
                          ("trailing blanks", "\n" + "\n".join(l + " \t" for l in mf.DRAFT.split("\n")) + "\n\n"),
                          ("nfd", unicodedata.normalize("NFD", mf.DRAFT))]:
            with self.subTest(name):
                self.assertNotEqual(msg, mf.DRAFT)
                self.assertAllowed(fixture("reviewed_pass"), msg)

    def test_hebrew_draft_string_result(self):
        # the result content is a plain string here; the reply comes back with points out of canonical order (shin dot before qamats)
        reordered = mf.DRAFT_HE.replace("שָׁ", "שָׁ")
        self.assertNotEqual(reordered, mf.DRAFT_HE)
        self.assertAllowed(fixture("reviewed_pass_string_hebrew"), mf.DRAFT_HE)
        self.assertAllowed(fixture("reviewed_pass_string_hebrew"), reordered)
        self.assertBlocked(fixture("reviewed_pass_string_hebrew"), DIFFERS, mf.DRAFT_HE.replace("שָׁל", "של"))

    def test_result_as_several_text_blocks(self):
        view = mf.review_view(mf.DRAFT)
        t = mf.answered_turn(mf.Transcript(), review=[view[:20], view[20:]]).text(mf.DRAFT)
        self.assertAllowed(self.write(t.jsonl()))

    def test_mid_turn_compaction_no_false_block(self):
        self.assertAllowed(fixture("mid_turn_compaction"))

    def test_hand_added_connector_passed(self):
        self.assertAllowed(fixture("hand_added_connector"))

    def test_other_server_same_tool_names(self):
        # finding 3: another MCP server's get_text (plain-text result) is not TorahAI: no block, no re-read wait
        for name, result in [("plain text", ["Page 1: quarterly figures ..."]), ("json, other source", ['{"text": "...", "source": "TorahAI"}'])]:
            with self.subTest(name):
                t = mf.Transcript().snapshot().prompt("Summarise my PDF")
                t.tool("mcp__pdf-reader__get_text", {"path": "/tmp/report.pdf"}, result).text("The report says ...")
                self.assertEqual(self.assertAllowed(self.write(t.jsonl()), "The report says ..."), [])

    def test_stop_hook_active(self):
        self.assertEqual(self.assertAllowed(fixture("not_reviewed"), active=True), [])

    def test_transcript_lag_resolved_on_reread(self):
        # the review's tool_result is not in the file yet on the first read; the writer catches up during the wait
        lines = read(fixture("reviewed_pass")).splitlines(True)
        idx = max(i for i, l in enumerate(lines) if '"tool_result"' in l)
        path = self.write("".join(lines[:idx] + lines[idx + 1:]))

        def catch_up():
            self.write("".join(lines), "t.jsonl")

        sleeps = self.assertAllowed(path, on_sleep=catch_up)
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(0.5 <= sleeps[0] <= 2)

    def test_truncated_last_line_is_skipped(self):
        # a half-written last line (the writer is mid-append) does not make the whole transcript unreadable
        text = read(fixture("reviewed_pass")) + '{"type": "assistant", "mess'
        self.assertAllowed(self.write(text))


class Blocks(HookCase):
    def test_not_reviewed(self):
        self.assertBlocked(fixture("not_reviewed"), NOT_REVIEWED)

    def test_passed_but_edited(self):
        self.assertBlocked(fixture("reviewed_pass"), DIFFERS, mf.DRAFT.replace("necessary", "needed"))

    def test_failed_review(self):
        self.assertBlocked(fixture("reviewed_fail"), NOT_PASSED)

    def test_review_error_result(self):
        self.assertBlocked(fixture("review_error"), NOT_PASSED)

    def test_error_result_is_never_a_pass(self):
        # is_error decides even when the error text happens to be a passing view for this draft
        t = mf.answered_turn(mf.Transcript(), review=False)
        t.tool(mf.PLUGIN + "review_answer", {"draft": mf.DRAFT, "citations": []}, mf.review_view(mf.DRAFT), is_error=True)
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_PASSED)

    def test_result_not_a_review_object(self):
        for name, result in [("plain text", ["Output too large to show"]), ("json without pass", ['{"ok": true}']),
                             ("json list", ['[{"pass": true}]']), ("pass not true", ['{"pass": "true", "draft_sha256": "x"}'])]:
            with self.subTest(name):
                t = mf.answered_turn(mf.Transcript(), review=result).text(mf.DRAFT)
                self.assertBlocked(self.write(t.jsonl()), NOT_PASSED)

    def test_later_failed_review_wins(self):
        t = mf.answered_turn(mf.Transcript())
        t.tool(mf.PLUGIN + "review_answer", {"draft": mf.DRAFT, "citations": []}, [mf.review_view(mf.DRAFT, passed=False)])
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_PASSED)

    def test_review_from_earlier_turn_only(self):
        # turn 1 reviewed this exact text; turn 2 used TorahAI and did not review
        self.assertBlocked(fixture("earlier_turn_review"), NOT_REVIEWED)
        self.assertBlocked(fixture("earlier_turn_review"), NOT_REVIEWED, mf.SECOND_ANSWER)

    def test_is_meta_entry_in_turn(self):
        self.assertBlocked(fixture("meta_in_turn"), NOT_PASSED)

    def test_compaction_summary_is_not_a_prompt(self):
        t = mf.answered_turn(mf.Transcript(), review=False).compaction().text(mf.DRAFT)
        self.assertBlocked(self.write(t.jsonl()), NOT_REVIEWED)

    def test_hand_added_connector_not_reviewed(self):
        self.assertBlocked(fixture("hand_added_not_reviewed"), NOT_REVIEWED)

    def test_hand_added_server_known_by_source_note(self):
        # finding 3: a server under any name whose result carries SOURCE_NOTE is TorahAI; not reviewed -> text 1
        t = mf.Transcript().snapshot().prompt("Rashi on Genesis 1:1?")
        t.tool("mcp__some-name__get_text", {"ref": "Rashi on Genesis 1:1:1"}, [json.dumps({"ref": "x", "source": mf.SOURCE_NOTE})])
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_REVIEWED)

    def test_source_note_must_match_exactly(self):
        # a near-miss source (and the hand-added server's results without SOURCE_NOTE) does not make the server TorahAI
        t = mf.Transcript().snapshot().prompt("Rashi on Genesis 1:1?")
        t.tool("mcp__some-name__get_text", {"ref": "x"}, [json.dumps({"ref": "x", "source": mf.SOURCE_NOTE + " "})])
        self.assertEqual(self.assertAllowed(self.write(t.text(mf.DRAFT).jsonl())), [])

    def test_source_note_of_another_server_does_not_count(self):
        # server A's SOURCE_NOTE result does not make server B (no such result) TorahAI: B's review_answer is not the review
        t = mf.answered_turn(mf.Transcript(), prefix=mf.HAND_ADDED, review=False)
        t.tool("mcp__other__review_answer", {"draft": mf.DRAFT}, [json.dumps({"pass": True, "draft_sha256": mf.draft_sha256(mf.DRAFT)})])
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_REVIEWED)

    def test_non_torahai_review_answer_is_not_the_review(self):
        # finding 3 (iv): a TorahAI turn; a non-TorahAI server's review_answer passes this exact draft -> still not reviewed
        t = mf.answered_turn(mf.Transcript(), review=False)
        t.tool("mcp__other__review_answer", {"draft": mf.DRAFT}, [json.dumps({"pass": True, "draft_sha256": mf.draft_sha256(mf.DRAFT)})])
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_REVIEWED)

    def test_non_torahai_review_after_torahai_review_is_ignored(self):
        # the TorahAI review failed; a later non-TorahAI review_answer "passes": the last TorahAI review decides -> text 2
        t = mf.answered_turn(mf.Transcript(), review=[mf.review_view(mf.DRAFT, passed=False)])
        t.tool("mcp__other__review_answer", {"draft": mf.DRAFT}, [json.dumps({"pass": True, "draft_sha256": mf.draft_sha256(mf.DRAFT)})])
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_PASSED)

    def test_api_request_blob_is_not_a_tool_call(self):
        # api-request-blob entries copy message content; only type "assistant" entries are tool calls
        t = mf.answered_turn(mf.Transcript(), review=False)
        t.entries.append({"type": "api-request-blob", "hash": "fake", "sessionId": mf.SESSION, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_blob", "name": mf.PLUGIN + "review_answer", "input": {}}]}})
        t.entries.append({"type": "api-request-blob", "hash": "fake2", "sessionId": mf.SESSION, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_blob", "content": [{"type": "text", "text": mf.review_view(mf.DRAFT)}]}]}})
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_REVIEWED)

    def test_lag_not_resolved(self):
        # the review's tool_result never arrives: no passing review in the turn
        t = mf.answered_turn(mf.Transcript(), review=False)
        t.tool(mf.PLUGIN + "review_answer", {"draft": mf.DRAFT, "citations": []}, None)
        self.assertBlocked(self.write(t.text(mf.DRAFT).jsonl()), NOT_PASSED)

    def test_reread_reason_is_used(self):
        # first read: no review yet; by the re-read a failing review is in the file
        lines = read(fixture("reviewed_fail")).splitlines(True)
        idx = max(i for i, l in enumerate(lines) if "review_answer" in l)
        path = self.write("".join(lines[:idx]))
        self.assertBlocked(path, NOT_PASSED, on_sleep=lambda: self.write("".join(lines)))

    def test_typed_prompt_with_text_blocks(self):
        # a prompt whose content is a list of text blocks still starts a turn
        t = mf.answered_turn(mf.Transcript()).text(mf.DRAFT)
        t._add({"type": "user", "promptId": "p2", "message": {"role": "user", "content": [{"type": "text", "text": "Next"},
                                                                                           {"type": "text", "text": "question"}]}})
        t.tool(mf.PLUGIN + "get_text", *mf.GET_TEXT).text(mf.DRAFT)
        self.assertBlocked(self.write(t.jsonl()), NOT_REVIEWED)


class FailOpen(HookCase):
    def assertFailOpen(self, path=None, stdin=None):
        code, out, err, sleeps = self.run_hook(path, stdin=stdin)
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("this is not JSON", err)  # never echoes the transcript (malformed.jsonl)

    def test_malformed_transcript(self):
        self.assertFailOpen(fixture("malformed"))

    def test_missing_transcript(self):
        self.assertFailOpen(os.path.join(self.tmp.name, "absent.jsonl"))

    def test_bad_stdin(self):
        for stdin in ["", "not json", "[1, 2]", '{"stop_hook_active": false}',
                      json.dumps({"transcript_path": fixture("not_reviewed"), "stop_hook_active": False})]:
            with self.subTest(stdin):
                self.assertFailOpen(stdin=stdin)

    def test_stdin_not_utf8(self):
        # finding 2: stdin is read as bytes and decoded as UTF-8; bytes that are not UTF-8 are unreadable (fail open)
        raw = json.dumps({"transcript_path": fixture("not_reviewed"), "stop_hook_active": False,
                          "last_assistant_message": "caf\u00e9"}).encode("ascii").replace(b"\\u00e9", b"\xe9")
        self.assertNotIn(b"\\u00e9", raw)
        out, err = io.StringIO(), io.StringIO()
        code = hook.main(io.TextIOWrapper(io.BytesIO(raw), encoding="latin-1"), out, err, lambda s: None)
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1, err.getvalue())

    def test_unreadable_on_reread(self):
        path = self.write(read(fixture("not_reviewed")))
        code, out, err, _ = self.run_hook(path, on_sleep=lambda: os.remove(path))
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)


class ByHand(unittest.TestCase):
    def run_script(self, argv, stdin, env=None):
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=20, env=env)

    def stdin(self, name, message=mf.DRAFT):
        return json.dumps({"transcript_path": fixture(name), "stop_hook_active": False, "last_assistant_message": message})

    def test_script_blocks_and_allows(self):
        p = self.run_script([sys.executable, HOOK_PATH], self.stdin("not_reviewed"))
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"decision": "block", "reason": NOT_REVIEWED})
        p = self.run_script([sys.executable, HOOK_PATH], self.stdin("reviewed_pass"))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def guard_command(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
            handlers = json.load(fh)["hooks"]["Stop"][0]["hooks"]
        self.assertEqual(len(handlers), 1)
        return handlers[0]["command"]

    def run_guard(self, path_dirs):
        env = {"PATH": os.pathsep.join(path_dirs), "CLAUDE_PLUGIN_ROOT": ROOT}
        return self.run_script(["/bin/bash", "-c", self.guard_command()], self.stdin("not_reviewed"), env)

    def run_guard_sh(self, plugin_root):
        env = {"PATH": os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"]), "CLAUDE_PLUGIN_ROOT": plugin_root}
        return self.run_script(["/bin/sh", "-c", self.guard_command()], self.stdin("not_reviewed"), env)

    def test_guard_missing_script(self):
        # finding 1: the plugin folder no longer holds the script (updated or removed under a running session) -> exit 0, no output
        empty = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, empty)
        p = self.run_guard_sh(empty)
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)

    def test_guard_interpreter_fails(self):
        # finding 1: any non-zero exit of the interpreter becomes exit 0 (a Stop hook's exit 2 would block the reply)
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", root]))
        os.mkdir(os.path.join(root, "hooks"))
        for code in (1, 2):
            with self.subTest(code):
                with open(os.path.join(root, "hooks", "require_review.py"), "w") as fh:
                    fh.write("import sys\nsys.exit(%d)\n" % code)
                p = self.run_guard_sh(root)
                self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)

    def test_non_utf8_locale(self):
        # finding 2: the Hebrew reply that passed stays allowed whatever the locale or PYTHONIOENCODING says
        base = {k: v for k, v in os.environ.items() if k not in ("LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8")}
        # raw UTF-8 bytes (not \\u escapes), as Claude Code may send them
        stdin = json.dumps({"transcript_path": fixture("reviewed_pass_string_hebrew"), "stop_hook_active": False,
                            "last_assistant_message": mf.DRAFT_HE}, ensure_ascii=False).encode("utf-8")
        self.assertIn(mf.DRAFT_HE.split("\n")[0].encode("utf-8"), stdin)
        for extra in [{"LC_ALL": "en_US.ISO8859-1"}, {"PYTHONIOENCODING": "cp1252"}]:
            with self.subTest(extra):
                p = subprocess.run([sys.executable, HOOK_PATH], input=stdin, capture_output=True, timeout=20, env=dict(base, **extra))
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def fake_bin(self, xcode_select_exit):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        with open(os.path.join(d, "xcode-select"), "w") as fh:
            fh.write("#!/bin/sh\nexit %d\n" % xcode_select_exit)
        os.chmod(os.path.join(d, "xcode-select"), 0o755)
        return d

    def test_guard_runs_hook_with_python3(self):
        p = self.run_guard([os.path.dirname(sys.executable), "/usr/bin", "/bin"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["reason"], NOT_REVIEWED)

    def test_guard_skips_without_python3(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, empty)
        p = self.run_guard([empty])
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    @unittest.skipUnless(platform.system() == "Darwin" and os.path.exists("/usr/bin/python3"), "macOS only")
    def test_guard_macos_stub(self):
        # /usr/bin/python3 without the Command Line Tools (xcode-select -p fails): skip quietly, never run the stub
        p = self.run_guard([self.fake_bin(2), "/usr/bin", "/bin"])
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        # with the tools installed (xcode-select -p succeeds), /usr/bin/python3 runs the hook
        p = self.run_guard([self.fake_bin(0), "/usr/bin", "/bin"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["reason"], NOT_REVIEWED)


class Manifests(unittest.TestCase):
    def load(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def test_plugin_json(self):
        p = self.load(".claude-plugin", "plugin.json")
        self.assertEqual((p["name"], p["version"], p["homepage"], p["license"]), ("torahai", "0.1.0", "https://enzee.dev", "MIT"))
        self.assertTrue(p["description"] and p["author"]["name"])

    def test_marketplace_json(self):
        m = self.load(".claude-plugin", "marketplace.json")
        self.assertEqual([(e["name"], e["source"]) for e in m["plugins"]], [("torahai", "./")])
        self.assertTrue(m["name"] and m["owner"]["name"])

    def test_mcp_json(self):
        self.assertEqual(self.load(".mcp.json"), {"mcpServers": {"torahai": {"type": "http", "url": "https://enzee.dev/api/mcp"}}})

    def test_hooks_json(self):
        h = self.load("hooks", "hooks.json")
        self.assertEqual(list(h), ["hooks"])
        self.assertEqual(list(h["hooks"]), ["Stop"])
        (handler,) = h["hooks"]["Stop"][0]["hooks"]
        self.assertEqual((handler["type"], handler["timeout"]), ("command", 10))
        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/hooks/require_review.py"', handler["command"])
        self.assertNotIn("exec ", handler["command"])  # finding 1: the interpreter's exit status must pass through `|| exit 0`

    def test_skill_is_short(self):
        with open(os.path.join(ROOT, "skills", "torahai", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\n") and "\ndescription: " in text)
        self.assertLess(len(text.splitlines()), 25)

    def test_hook_uses_standard_library_only(self):
        src = read(HOOK_PATH)
        imports = {l.split()[1].split(".")[0] for l in src.splitlines() if l.startswith(("import ", "from "))}
        self.assertLessEqual(imports, {"hashlib", "json", "os", "re", "sys", "time", "unicodedata", "typing", "__future__"})


class Fixtures(unittest.TestCase):
    def test_fixtures_are_current(self):
        with tempfile.TemporaryDirectory() as d:
            mf.main(d)
            for name in sorted(os.listdir(d)):
                with self.subTest(name):
                    with open(os.path.join(d, name), encoding="utf-8") as a, open(os.path.join(FIX, name), encoding="utf-8") as b:
                        self.assertEqual(a.read(), b.read())


if __name__ == "__main__":
    unittest.main()

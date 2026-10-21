# Test fixtures

- `draft_hash_vectors.json`: copied unchanged from the TorahAI server repo, `ingest/tests/fixtures/draft_hash_vectors.json`
  (produced by `apps/web/lib/mcpTools.ts` draftHashForm). Copy it again whenever that file changes.
- `*.jsonl`: Claude Code transcripts with synthetic content in the real shape (Claude Code 2.1.302, inspected for structure only),
  written by `make_fixtures.py`; `malformed.jsonl` does not parse. Regenerate with `python3 tests/fixtures/make_fixtures.py`; the tests
  check the files match the script.

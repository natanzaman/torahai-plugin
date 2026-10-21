# TorahAI plugin

Answers about Jewish texts from verified sources. The plugin bundles:

- the TorahAI MCP server (`.mcp.json`: https://enzee.dev/api/mcp), with the tools search_sources, get_text, get_links,
  verify_quotes and review_answer;
- a skill (`skills/torahai`) that tells Claude when to use TorahAI; the rules for answering live on the server;
- a Stop hook (`hooks/require_review.py`): in Claude Code, a reply that used TorahAI tools can end only after its exact final text
  passed review_answer. TorahAI tools are the plugin's server, or a connector added by hand under any name whose results carry
  TorahAI's source note; another server's tools of the same name (say a PDF reader's `get_text`) do not count.

## Install

Claude Code:

```
/plugin marketplace add <this repository>
/plugin install torahai@torahai
```

claude.ai: Customize > Plugins > Add marketplace, then install TorahAI.

claude.ai: installing the plugin does not connect the connector; add it from the plugin's Connectors tab. In Claude Code the
plugin's MCP server starts with the plugin.

## Where the review hook runs

- Runs: Claude Code. Cowork: not yet tested.
- Does not run: plain chat on claude.ai (hooks are ignored there), and machines without a working `python3`, including macOS
  without the Command Line Tools (its `/usr/bin/python3` is a stub that opens an install dialog, so the hook skips it) and Windows
  where `python3` is the Microsoft Store alias and not a real Python (the hook skips it). There the review stays voluntary: the
  server's instructions still ask for it.
- Windows needs Git for Windows (Git Bash): without it Claude Code runs the hook in PowerShell, which cannot parse it, so a "Stop hook
  error" notice appears after each reply and the review stays voluntary.
- If the hook script is missing (the plugin was updated or removed under a running session) or Python fails, the reply ends unchecked.

When the hook stops a reply it tells Claude why, in one of three messages: the reply was not reviewed; the review did not pass; or
the reply sent is not the reviewed draft. After one such retry the reply ends whatever the result (the hook does not loop).

## Privacy

The hook reads only the file named by `transcript_path`, on this machine; sends nothing. It prints nothing but its block message and,
when it cannot read its input or hits an unexpected error, one line on stderr saying the reply is not checked; never the reply or
the transcript. It needs Python 3.9 or later, standard library only.

## Known gaps (0.1.0)

- A follow-up turn that rewrites a quoting answer with no tool call ("shorten that") ends unblocked: that turn used no TorahAI tool.
- Subagent transcripts (`<session>/subagents/*.jsonl`) are not seen; the Stop hook covers the main conversation only.
- Plain chat (hooks ignored) and machines without a working `python3`: the review stays voluntary.
- A reply reworded again after the retry's review goes out unreviewed (one retry, see above).
- The one retry is shared with other Stop hooks: Claude Code has one "already continuing because of a Stop hook" flag per session, so
  if another plugin's Stop hook blocked first, the TorahAI hook lets the next stop through.

## Development

```
python3 -m pytest -q tests        # or: python3 -m unittest discover tests
python3 tests/fixtures/make_fixtures.py   # rewrites the transcript fixtures
```

Releases: bump `version` in `.claude-plugin/plugin.json` for every change; Claude Code caches an installed plugin by version, so
users keep the old copy until it changes.

Run the hook by hand:

```
echo '{"transcript_path":"tests/fixtures/not_reviewed.jsonl","stop_hook_active":false,"last_assistant_message":"..."}' \
  | python3 hooks/require_review.py
```

## Licence

MIT.

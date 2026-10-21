---
name: torahai
description: Answer questions about Jewish texts, halakha (Jewish law), Tanakh, Talmud, commentaries and their sources with the TorahAI tools, which search and quote verified texts from Sefaria. Use when the user asks what a Jewish source says, where something is written, or what the views on a question of Jewish law are.
---

# TorahAI

Use the TorahAI MCP tools (search_sources, get_text, get_links, verify_quotes, review_answer) for questions about Jewish texts,
law and sources.

Follow the TorahAI server's instructions and its `ask_torahai` prompt. They hold all the rules for answering (how to search,
quote, cite and review); this skill adds none of its own.

In Claude Code this plugin also runs a Stop hook: a reply that used TorahAI tools ends only after its exact final text passed
review_answer. If the hook stops a reply, do what its message says.

# MATATA — Agent Guidance

## What this is

Local AI ecosystem for Ubuntu, **100% offline**. Only Phase 1 (Agent PC) is built — a single-file Python agent using Qwen3 8B via Ollama native tool calling. No LangChain/CrewAI/etc.

## Entrypoints & key files

| File | Role |
|------|------|
| `agent-pc/agent.py` | **Single file agent** (430 lines, v10). All logic here. |
| `agent-pc/test_fixes.py` | Unit tests for command dedup + length limit (no Ollama needed) |
| `agent-pc/tests.sh` | Integration test suite (takes 10-15 min, requires Ollama) |

`agent-pc/CLAUDE.md` has the full architecture doc — read it for context.

## Commands

```bash
source venv/bin/activate

# Run agent
python3 agent-pc/agent.py --timer    # --timer shows per-turn elapsed

# Quick validation (no Ollama)
python3 agent-pc/test_fixes.py

# Full integration (requires Ollama + qwen3:8b pulled)
bash agent-pc/tests.sh

# Syntax check
python3 -m py_compile agent-pc/agent.py
```

## Architecture essentials

- **3 tools only** (`run_shell`, `search_files`, `system_info`) — proven reliable with 8B. Do not add more.
- **Ollama config**: `qwen3:8b`, `think=False` (think=True + tools = empty output, Ollama issue 10976), `stream=False`, `temperature=0.3`, `repeat_penalty=1.5`, `num_predict=500`, `num_ctx=4096`
- **Safety**: READ commands auto-execute, WRITE asks confirmation, BLOCKED (`rm`/`rmdir`/`shred`/`dd`/`reboot`/etc) never. See whitelist in `agent.py:8-17`.
- **Auto-backup**: Before write operations on existing files, saves to `~/.agent-pc-backups/`.
- **Agent loop**: max 5 steps per turn, sliding window of 5 commands for dedup, commands capped at 200 chars.
- **CPU-only**: ~10 tok/s. Keep tool outputs SHORT (under 500 chars). `system_info(all)` returns only ram+disk+cpu.

## Known bugs (unfixed)

- **BUG 3**: Tool output too long → slow inference. `ps aux` lines truncated to 80 chars, but still can be heavy.
- **BUG 4**: CPU-only inference is inherently slow (31-600s/turn). No fix.
- **BUG 5**: Model confuses audio/video extensions. Few-shot examples in system prompt help but aren't 100%.

Bugs 1 (command dedup) and 2 (200-char limit) are **fixed** in v10.1.

## Constraints

- ZERO frameworks, ZERO cloud, ZERO deletion operations
- Reply in French, concise
- Never add more than 3 tools (causes empty responses with 8B)
- Single-file simplicity for `agent.py` is a hard constraint

## Complementary docs

- `agent-pc/CLAUDE.md` — full architecture, Ollama issues, version history, test queries, PC specs
- `IMPLEMENTATION_SUMMARY.md` — Bug 1 & 2 fix details
- `BUG_FIXES_REPORT.md` — technical bug analysis

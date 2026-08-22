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
- **Model switching**: `MATATA_MODEL=qwen3.5:4b python3 agent-pc/agent.py` (default `qwen3:8b`). Both validated 5/5. qwen3.5:4b: no repeat_penalty (breaks tool calling), same think=False; ~equal speed on iGPU but weaker semantics/French. qwen3.5 default hybrid thinking eats num_predict budget if think=False is omitted — `/no_think` inline does NOT work via Ollama.
- **Ollama config**: `think=False` (think=True + tools = empty output, Ollama issue 10976), `stream=False`, `temperature=0.3`, `num_predict=500`, `num_ctx=4096`, `keep_alive='30m'`. Runs on **Intel Arc MTL iGPU via Vulkan** (`OLLAMA_IGPU_ENABLE=1` in `/etc/systemd/system/ollama.service.d/igpu.conf`): 100% offload, ~2× faster than CPU-only. Ollama v0.32.15 as systemd service (user `ollama`, models in `/usr/share/ollama/.ollama/models`). `num_thread=16` kept in agent options (applies only if layers fall back to CPU).
- **Per-generation params**: `repeat_penalty=1.5` only for qwen3:8b — it breaks qwen3.5 tool calling (empty outputs).
- **Safety**: READ commands auto-execute, WRITE asks confirmation, BLOCKED (`rm`/`rmdir`/`shred`/`dd`/`reboot`/etc) never. See whitelist in `agent.py:8-17`.
- **Auto-backup**: Before write operations on existing files, saves to `~/.agent-pc-backups/`.
- **Agent loop**: max 5 steps per turn, sliding window of 5 commands for dedup, commands capped at 200 chars.
- **CPU/iGPU**: Vulkan iGPU ≈ 2× CPU speed (Hi 9-14s, complex multi-step 25-45s). Keep tool outputs SHORT — caps: 600 chars (shell/search), 800 (system_info). `system_info(all)` returns only ram+disk+cpu.

## Known bugs (unfixed)

- **BUG 4**: CPU-only inference is inherently slow (26-70s/turn since v10.2, was 31-629s). No full fix.
- **BUG 5**: Model confuses audio/video extensions. Few-shot examples in system prompt help but aren't 100%.

Bugs 1 (command dedup), 2 (200-char limit) fixed in v10.1; Bug 3 (tool output caps 600/600/800) fixed in v10.2. `tests.sh` timeouts raised (120-300s) with per-test failure guards.

## Constraints

- ZERO frameworks, ZERO cloud, ZERO deletion operations
- Reply in French, concise
- Never add more than 3 tools (causes empty responses with 8B)
- Single-file simplicity for `agent.py` is a hard constraint

## Complementary docs

- `agent-pc/CLAUDE.md` — full architecture, Ollama issues, version history, test queries, PC specs
- `IMPLEMENTATION_SUMMARY.md` — Bug 1 & 2 fix details
- `BUG_FIXES_REPORT.md` — technical bug analysis

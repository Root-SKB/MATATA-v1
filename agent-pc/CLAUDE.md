# Agent PC — Local AI Agent

## Project Overview
A local AI agent for Ubuntu that uses Qwen3 8B via Ollama (CPU-only) to execute shell commands,
search files, and report system info. Written in pure Python with the Ollama SDK — no frameworks.

## Architecture
- Model: qwen3:8b (5.2GB Q4_K_M quantization) via Ollama v0.22.1
- Inference: CPU-only (Intel Core Ultra 7 155H, 32GB DDR5-5600)
- Tool calling: Ollama native tool calling (tools parameter in ollama.chat)
- Agent loop: while loop with max steps, tool call then execute then feed result back
- Safety: Command whitelist (READ auto-execute, WRITE ask confirmation, BLOCKED never)

## Files
- agent.py — Main agent script (single file, current version: v10)
- matata.sh — Launch script at ~/matata.sh (checks Ollama, model, then runs agent.py)
- venv/ — Python virtualenv with ollama SDK

## How to Run
    source ~/dev/personal/agent-pc/venv/bin/activate
    python3 ~/dev/personal/agent-pc/agent.py --timer
    # or via launcher:
    ~/matata.sh --timer

## Current Version: v10
3 tools: run_shell, search_files, system_info

## KNOWN BUGS (Priority Order)

### BUG 1 CRITICAL: Model repeats same broken command until max_steps
When a command fails, the model generates the EXACT same command again (up to 5 times).
Example: find with 40+ iname patterns and -exec du fails with "paths must precede expression",
then the model retries the identical command 4 more times. 539 seconds wasted.

Root cause: No dedup detection. The model does not learn from errors because:
1. The error message is too terse
2. No rule tells the model to change approach on failure
3. No Python-side detection of repeated tool calls

Fix ideas:
- Track last N tool calls. If args match previous, inject "STOP: same command failed before. Try SIMPLER."
- Limit command length (model generates 800+ char find commands)
- Add prompt rule: "If a command fails, use a DIFFERENT simpler command"

### BUG 2 HIGH: Model generates overly complex commands
For "how many music files", the model generated a find command with 40+ -iname patterns
including video formats AND -exec du piping. This violates "one pipe max" and "simple" rules.

Root cause: 8B model does not reliably follow "keep it simple" rules.

Fix ideas:
- Intercept commands over 200 chars and reject them with "Too complex. Use a simpler command."
- Add few-shot examples in system prompt
- Sanitize commands before execution (strip -exec, limit -iname count)

### BUG 3 HIGH: Tool output too long causes slow inference
system_info(all) returns about 2000 chars including full process command lines.
The model then processes all this in next turn, taking 629.6s.

Root cause: ps aux output includes massive Brave/Firefox command line args.

Fix ideas:
- Truncate ps output more aggressively (strip command args after 50 chars per line)
- system_info should return structured summary, not raw output
- Limit total tool output to 500 chars

### BUG 4 MEDIUM: Slow inference (31-629s per turn)
CPU-only inference with tool calling is inherently slow. Hi = 31s, complex = 100-600s.

Fix ideas:
- Keep tool outputs SHORT (see Bug 3)
- Reduce system prompt tokens
- Early exit: if step 1 result is sufficient, prompt model to summarize immediately

### BUG 5 LOW: Model confuses music with all media
"Combien de musique" caused model to search mp3, wav, flac, AND mp4, mkv, avi, mov...

Fix: Few-shot examples in system prompt.

## CONSTRAINTS
- ZERO frameworks: No LangChain, LangGraph, CrewAI. Pure Python + Ollama SDK only.
- ZERO cloud: Everything runs locally. No API calls to external services.
- ZERO deletion: Agent must NEVER run rm, rmdir, shred, etc.
- ZERO OpenClaw or Open Interpreter: These are forbidden.
- CPU-only: No GPU available. Must work within about 10 tok/s inference speed.
- Single file: agent.py should remain a single file for simplicity.

## OLLAMA CONFIG used in agent.py
- model: qwen3:8b
- think: False (think=True causes empty output with tools, Ollama issue 10976)
- stream: False (more reliable for tool calling)
- tools: native Ollama tool calling parameter
- num_predict: 500
- num_ctx: 4096
- temperature: 0.3
- repeat_penalty: 1.5 (official Qwen3 recommendation for quantized models)

## KNOWN OLLAMA ISSUES
- Issue 10976: think=True + tools + qwen3 = empty output. We use think=False.
- Issue 14601: Tool definitions may be serialized as Go structs instead of JSON.
- Issue 11135: Qwen3 tool call hallucination after certain Ollama versions.
- Issue 8337: Cannot get tool call AND message content in same response.

## VERSION HISTORY
- v8: First working tool calling (native Ollama). 3 tools. Worked.
- v9.2: English prompt, auto-retry, repeat_penalty 1.2. "The Boys" query worked perfectly in 83s.
- v9.3-v9.5: Added write_file tool. Model destroyed agent.py by overwriting with 88 chars. Twice.
- v9.6: Added replace_in_file (5 tools total). Responses became empty and unreliable.
- v9.7: Lighter prompt, stream=False. Still empty responses on some queries.
- v10: Back to 3 tools. Empty responses fixed. But model generates broken commands and loops.

## WHAT WORKED WELL (v9.2 reference)
- 3 tools (search_files, system_info, run_shell)
- "The Boys" query: 4 steps, self-recovery, correct final answer in 83s
- "Hi": 43.4s response time
- Auto-retry on "je vais..." patterns

## TESTING
Test queries in order of difficulty:
1. Hi — should respond with greeting, NO tool call. Target under 30s.
2. Quelle heure ? — run_shell date. Target under 20s.
3. RAM et disque ? — system_info. Target under 40s.
4. J'ai combien de musique ? — search_files or simple find. Target under 60s.
5. Combien de series avec taille ? — search + du. Target under 120s.
6. Mets la reponse sur le bureau — tee to ~/Desktop/. Multi-step.

## PC SPECS
- CPU: Intel Core Ultra 7 155H (22 threads, 16 cores)
- RAM: 32GB DDR5-5600
- Disk: 512GB NVMe (468GB usable, 247GB free)
- GPU: Intel Arc iGPU (not used by Ollama)
- NPU: 11 TOPS (not used)
- OS: Ubuntu 24.04.4, kernel 6.17.0-1007-oem
- Ollama: v0.22.1

## KEY PATHS
- Agent: ~/dev/personal/agent-pc/agent.py
- Venv: ~/dev/personal/agent-pc/venv/
- Launcher: ~/matata.sh
- Videos: ~/Videos/Film/Series/ (6 series, 127GB)
- Music: ~/Music/ (4KB, nearly empty)
- Desktop: ~/Desktop/
- Backups: ~/.agent-pc-backups/
- Session logs: ~/.agent-pc-backups/session_*.log

# Agent PC — Local AI Agent

## Project Overview
A local AI agent for Ubuntu that uses LLMs via Ollama native tool calling to execute shell commands,
search files, and report system info. Written in pure Python with the Ollama SDK — no frameworks.
Part of the MATATA ecosystem (Phase 1). Runs on the Intel Arc iGPU via Vulkan — fully offline.

## Architecture
- Models: `qwen3:8b` (default) or `qwen3.5:4b` via `MATATA_MODEL` env var
- Inference: Intel Arc MTL iGPU via Vulkan (Ollama v0.32.15), 100% offload
- Tool calling: Ollama native tool calling (tools parameter in ollama.chat)
- Agent loop: max 5 steps, tool call → execute → feed result back
- Safety: command whitelist (READ auto-execute, WRITE ask confirmation, BLOCKED never)

## Files
- agent.py — Main agent script (single file, current version: v10.3)
- requirements.txt — Pinned deps (ollama>=0.6.2,<0.7)
- test_fixes.py — Unit tests (dedup + length limit, no Ollama needed)
- tests.sh — Integration suite (5 queries, ~3 min on iGPU)
- ~/matata.sh — Launcher (checks systemd service + model, then runs agent)

## How to Run
    ~/matata.sh            # default qwen3:8b
    MATATA_MODEL=qwen3.5:4b ~/matata.sh
    # or manually:
    source ~/dev/personal/agent-pc/venv/bin/activate
    python3 ~/dev/personal/agent-pc/agent-pc/agent.py --timer

## Current Version: v11 (voice)
3 tools: run_shell, search_files, system_info
- v10.1: Bug 1 (dedup sliding window of 5) + Bug 2 (200-char cap) fixed
- v10.2: Bug 3 fixed — output caps 600 (shell/search) / 800 (system_info)
- v10.3: empty-response retry keeps tools (fixes JSON-text dead-end);
  multi-model support (MATATA_MODEL); system prompt rule 4 (greetings without tools)
- v10.4 stabilization: repeat_penalty 1.2 for qwen3:8b ONLY (1.5 official rec caused
  ~60% early-EOS empties — root cause found via AGENT_DEBUG eval stats); rule 11
  (no cosmetic retries); Series path structure injected; French INCOMPLETE_PATTERNS
- v11: voice mode (--voice): arecord 16kHz mono → whisper-cli small/fr → answer →
  Piper siwis streaming to aplay; VOICE_LANG auto|fr|en ('fr' = EN→FR translate
  effect); typed text accepted at mic prompt; 'langue fr|en|auto' command
- v11.1: auto = DUAL decode fr+en passes, keep higher mean token probability
  (single-shot detection mislabels short code-switched clips); native initial
  prompt per pass (fr/en) so the lexical bias doesn't degrade the other language;
  rule 10 replies in user's language; bilingual TTS — speak() picks siwis or
  en_US-lessac via accent+stopword heuristic (_voice_for, validated 6/6)

## VOICE MODE (v11)
- Binaries/models live in ../voice/ (GITIGNORED — rebuild steps below).
  whisper.cpp: git clone https://github.com/ggml-org/whisper.cpp voice/whisper.cpp &&
  cmake -B build -DGGML_NATIVE=ON && cmake --build build -j  (bin at build/bin/whisper-cli).
  Models in voice/models/: ggml-small.bin (~466MB, HF ggerganov/whisper.cpp);
  Piper voices fr_FR-siwis-medium + en_US-lessac-medium (~61MB each,
  HF rhasspy/piper-voices). Voice config jsons ARE committed (5KB each).
- Pipeline: Enter → arecord (SIGINT stops, header finalized) → whisper-cli -ojf
  (auto mode: 2 passes fr+en, winner by mean token 'p' from JSON) → agent loop
  unchanged → final answer piped to piper --output-raw | aplay S16_LE (streaming:
  playback starts before synthesis ends). Answer language detected by _voice_for.
- Timings measured: single STT pass ~3.6s per 12s clip; auto = ~6-7s both passes;
  TTS ~1.3s synth start (FR) / 4.2s full EN sentence playback; full vocal turn
  ≈ model time + ~7s overhead. User reported faster than typing.
- Mic: Intel DMIC array, gain 80% (-10dB). Speak ~30cm away; short phrases may
  mis-transcribe if far/quiet ("Quelle heure" → "Quel air" on quiet input).
- Pitfall: at mic prompt, typed words used to be swallowed (input started recording);
  fixed in v11 — first input captures text and routes through normal commands.

## KNOWN BUGS (Priority Order)

### BUG 5 MEDIUM: Model confuses audio/video extensions
"Combien de musique" can make the model search mp4/mkv too. Few-shot examples in the
system prompt help but are not 100%. Open.

### Path heuristics LOW
Model sometimes globs wrong paths (`~/Videos/Film/*.mkv` instead of subdirs) then adapts
or asks. Accepted behavior on 8B; watch on other models.

### /bin/bash -c wrappers LOW
When the model wraps commands as `/bin/bash -c "..."`, whitelist marks [unknown] →
confirmation prompt. Safe by design, just verbose.

Bugs 1, 2 fixed v10.1; Bug 3 fixed v10.2; retry dead-end fixed v10.3. BUG 4 (slow CPU
inference) resolved in practice by iGPU offload — no longer tracked as a bug.

## CONSTRAINTS
- ZERO frameworks: No LangChain/LangGraph/CrewAI. Pure Python + Ollama SDK only.
- ZERO cloud at runtime: everything local. (README mentions a future "Cloud Mentor"
  phase — conflicts with this rule, decision pending.)
- ZERO deletion: agent must NEVER run rm/rmdir/shred/dd/etc.
- Max 3 tools: more caused empty responses historically.
- Single file: agent.py stays a single file.

## MODEL CONFIG (in agent.py, near top)
- MODEL = env MATATA_MODEL or 'qwen3:8b'
- THINK_KW = {'think': False} for BOTH generations:
  * qwen3: think=True + tools = empty output (Ollama issue 10976)
  * qwen3.5: default hybrid thinking eats the whole num_predict budget → empty answers.
    The inline '/no_think' switch does NOT work through Ollama.
- CHAT_OPTS = {'num_predict': 500, 'num_ctx': 4096, 'temperature': 0.3, 'num_thread': 16}
  * repeat_penalty=1.5 ONLY for qwen3:8b (official Qwen3 rec for quants).
    It BREAKS qwen3.5:4b tool calling (empty outputs).
- keep_alive='30m' on chat calls (model stays resident between turns)

## RUNTIME ENVIRONMENT
- Ollama v0.32.15 as systemd service (`ollama.service`, User=ollama, enabled at boot)
- Models stored in `/usr/share/ollama/.ollama/models` (NOT ~/.ollama anymore — that copy was deleted)
- iGPU enabled via drop-in `/etc/systemd/system/ollama.service.d/igpu.conf` → `OLLAMA_IGPU_ENABLE=1`
  (Vulkan detects "Intel(R) Arc(tm) Graphics (MTL)" natively; without the flag the iGPU is dropped)
- Do NOT run `ollama serve` manually: it would look at ~/.ollama (empty) and re-download models.
  If the API is down: `sudo systemctl start ollama`
- Rollback binary backup ollama.v0.22.1.bak was removed after validation.

## PERFORMANCE (measured 2026-08-22, iGPU Vulkan)
| Query | qwen3:8b | qwen3.5:4b |
|---|---|---|
| Hi | ~9-13s | ~12-13s |
| Quelle heure ? | ~16-19s | ~20s |
| RAM et disque ? | ~26s | ~26s |
| Musique | ~16s | ~21s |
| Séries + taille | ~24-50s | ~24s |
Both models pass the full suite 5/5. 8B has better French/reasoning; 4B sometimes faster
on multi-step but weaker semantics. Raw gen speed ≈ 6.3 tok/s (8B) vs 10 tok/s (4B).

## TESTING
Test queries in order of difficulty (tests.sh):
1. Hi — greeting, NO tool call. Target < 20s.
2. Quelle heure ? — run_shell date. Target < 30s.
3. RAM et disque ? — system_info. Target < 45s.
4. J'ai combien de musique ? — search/find. Target < 40s.
5. Combien de series avec taille ? — multi-step. Target < 90s.
Timeouts in tests.sh: 120/120/150/180/300s with per-test failure guards.
Stability criterion before major work: 3 consecutive green runs (default model) + 1 green on qwen3.5:4b.

## KEY OLLAMA ISSUES (still relevant)
- Issue 10976: think=True + tools + qwen3 = empty output → we always send think=False.
- Issue 8337: no tool call AND content in same response (content empty on pure tool calls is normal).
- qwen3.5 quirk: explicit think kwarg changes behavior vs default; keep think=False and never
  set repeat_penalty for this generation.

## WHAT WORKED WELL
- 3 tools only, stream=False, temperature=0.3 — reliable across versions
- Auto-retry keeps tools now: model recovers from failed commands instead of dying in JSON-as-text
- keep_alive 30m: warm model = fast consecutive turns

## PC SPECS
- CPU: Intel Core Ultra 7 155H (16 cores / 22 threads)
- RAM: 32GB DDR5-5600 (~17GB free under normal load)
- Disk: 512GB NVMe (468GB usable)
- GPU: Intel Arc iGPU (MTL) — USED via Vulkan, 100% model offload
- NPU: 11 TOPS (not used)
- OS: Ubuntu 24.04.4, kernel 6.17.0-1007-oem

## KEY PATHS
- Agent: ~/dev/personal/agent-pc/agent-pc/agent.py
- Venv: ~/dev/personal/agent-pc/venv/
- Launcher: ~/matata.sh
- Systemd: /etc/systemd/system/ollama.service (+ .service.d/igpu.conf)
- Models: /usr/share/ollama/.ollama/models
- Videos: ~/Videos/Film/Series/
- Music: ~/Music/ (nearly empty)
- Backups & session logs: ~/.agent-pc-backups/

## ROADMAP CONTEXT (from README.md)
Phase 1 Agent PC ✅ — Phase 2 Voice (Whisper STT + Piper TTS, push-to-talk) ← next
Phase 3 MATATA Core orchestrator — Phase 4 Cloud Mentor (⚠ conflicts ZERO-cloud, pending decision)
Phase 5 MCP Tools server

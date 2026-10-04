# MATATA — Agent Guidance

## What this is

Local AI ecosystem for Ubuntu, **100% offline**. Phase 1 (Agent PC) + Phase 2 (Voice) are built.
Python agent (`agent.py`, currently single-file) using Qwen3 8B via Ollama native tool calling, with Whisper STT + Piper TTS + wake word detection.

## Entrypoints & key files

| File | Role |
|------|------|
| `matata/` | Thin package wrapper → console `matata` / `python3 -m matata` (delegates to agent.py) |
| `agent-pc/agent.py` | **Single file agent** (~1250 lines, v12.8). All logic here. |
| `agent-pc/test_fixes.py` | Unit tests: command dedup + length limit + security classify (no Ollama needed) |
| `agent-pc/tests.sh` | Integration test suite (~3 min on iGPU, requires Ollama) |
| `voice/` | Whisper.cpp + Piper models + wake word model (gitignored binaries, committed config) |

`agent-pc/CLAUDE.md` has the full architecture doc — read it for context.

## Commands

```bash
source venv/bin/activate
pip install -e .   # installe la console `matata` (paquet fin; agent.py reste single-file)

# Run agent (text only)
matata --timer
# or: python3 -m matata --timer

# Run with push-to-talk voice
matata --voice --timer

# Run with wake word "MATATA" (hands-free)
matata --wake --timer

# Quick validation (no Ollama)
python3 agent-pc/test_fixes.py

# Full integration (requires Ollama + qwen3:8b pulled)
bash agent-pc/tests.sh

# Syntax check
python3 -m py_compile agent-pc/agent.py
```

## Architecture essentials

- **3 tools only** (`run_shell`, `search_files`, `system_info`) — proven reliable with 8B. Do not add more.
- **Model switching**: `MATATA_MODEL=qwen3.5:4b python3 agent-pc/agent.py` (default `qwen3:8b`). Both validated 5/5. qwen3.5:4b: same think=False, ever-no repeat_penalty (break tool calling); ~equal speed on iGPU but weaker semantics/French. qwen3.5 default hybrid thinking eats num_predict budget if think=False is omitted — `/no_think` inline does NOT work via Ollama.
- **Ollama config**: `think=False` (think=True + tools = empty output, Ollama issue 10976), `stream=True` (v12.4, premier token en ~1-2s), `temperature=0.3`, `num_predict=800`, `num_ctx=4096`, `keep_alive='30m'`. Runs on **Intel Arc MTL iGPU via Vulkan** (`OLLAMA_IGPU_ENABLE=1` in `/etc/systemd/system/ollama.service.d/igpu.conf`): 100% offload, ~2× faster than CPU-only. Ollama v0.32.15 as systemd service (user `ollama`, models in `/usr/share/ollama/.ollama/models`). `num_thread=16` kept in agent options (applies only if layers fall back to CPU).
- **Per-generation params**: `repeat_penalty` RETIRÉ pour les deux modèles (v12.5, bench 2026-08-30 : 1.0 == 1.2 en fiabilité sur basiques + multi-step, zéro réponse vide/early-EOS — le 1.2 historique pour éviter ~60% early-EOS après échec de tool n'est plus nécessaire ; et tout repeat_penalty casse le tool calling de qwen3.5). `AGENT_DEBUG=1` env var logs per-step response stats to stderr.
- **Safety**: N1/N2/N3 levels (v12.6, patron inspiré de jarvis-assistant-vocal). READ/N1 auto-executes, WRITE/N2 (`mkdir`/`cp`/`mv`/`touch`/`tee`/`echo`/`sed`) asks confirmation (local or, once a remote channel exists, remote), CRITICAL/N3 (`chmod`/`chown`/`apt`/`pip`/`nano`/`vim`/`nohup`) asks confirmation **local only** — gated by `IS_REMOTE` (still `False` today, no remote channel exists), BLOCKED (`rm`/`rmdir`/`shred`/`dd`/`reboot`/`halt`/etc) never. `classify_command` hardened (v12.5): scans every command incl. `find -exec`, `xargs`, `sh -c`, `awk system()`, `systemctl reboot/poweroff/halt/kill/stop`. See whitelist in `agent.py`.
- **Auto-backup**: Before write operations on existing files, saves to `~/.agent-pc-backups/`.
- **Pre-LLM router** (v12.8): `route_intent()` classifies every turn (FastEmbed/ONNX embeddings, hand-rolled — not the `semantic-router` package, ZERO frameworks) into greeting/time_date/system_stats/file_search. Only `greeting`/`time_date` fast-path (skip Ollama, instant canned/`datetime.now()` reply) above `MATATA_ROUTER_THRESHOLD` (default 0.5); `system_stats`/`file_search` always go through the normal LLM+tool path (router can't extract command arguments). Disable entirely with `MATATA_ROUTER=0`. Validated 92.0%/5.2ms vs Laya 84.0%/216.4ms on a 100-query benchmark — see `docs/TECH_WATCH.md`.
- **Agent loop**: max 5 steps per turn, sliding window of 5 commands for dedup (shared across turns via `_COMMAND_HISTORY`), commands capped at 200 chars.
- **CPU/iGPU**: Vulkan iGPU ≈ 2× CPU speed (Hi 9-14s, complex multi-step 25-45s). Keep tool outputs SHORT — caps: 600 chars (shell/search), 800 (system_info). `system_info(all)` returns only ram+disk+cpu.

## Voice mode (v12+)

- `--voice`: push-to-talk (Enter → parle → Enter), whisper.cpp STT → Ollama → Piper TTS
- `--wake`: hands-free with wake word "MATATA" — veille permanente, double barrière anti-FP (openwakeword + confirmation whisper)
- **whisper-server** (v12.4): modèle chargé 1× au démarrage, HTTP API port 18080, fallback CLI si indisponible
- **stream=True** (v12.4): premier token LLM visible en ~1-2s au lieu de ~15s d'attente muette
- **Piper Python API** (v12.4): lazy-load in-process (1,1s first call, 0,1s synth), zéro subprocess piper
- **Streaming TTS phrase-par-phrase** (v12.7): parle chaque phrase dès qu'elle est complète dans le stream LLM (regex `SPEAK_SENTENCE_RE`, patron extrait de LocalVox) au lieu d'attendre la réponse entière — gardé par `tool_calls is None` et `INCOMPLETE_PATTERNS` pour ne jamais parler un préambule de tool call ni un texte en cours de retry silencieux
- **MODELS** voice dans `voice/models/`: ggml-small.bin (whisper), fr_FR-siwis-medium + en_US-lessac-medium (Piper), matata.onnx (wake word R3)
- Wake word entraîné via Colab R1→R3, validé holdout 4/6, fragments Piper 0/6 FP

## Known bugs (unfixed)

- **BUG 5**: Model confuses audio/video extensions. Few-shot examples in system prompt help but aren't 100%.

Bugs 1 (command dedup), 2 (200-char limit) fixed in v10.1; Bug 3 (tool output caps) fixed in v10.2; retry dead-end fixed + multi-model support in v10.3; Bug 4 (slow CPU inference) resolved by iGPU offload.

## Constraints

- ZERO cloud, ZERO deletion operations
- Cadre stratégique : **open-source > local > gratuit**. Externe/cloud/frameworks acceptés
  SEULEMENT si (a) 100% gratuits ET (b) apportent un gain réel mesuré — jamais un prérequis,
  toujours vérifié empiriquement avant intégration (voir l'historique des décisions dans
  `docs/TECH_WATCH.md` : Laya et semantic-router testés puis remplacés par un hand-roll léger
  une fois le gain validé ; aucun framework d'orchestration lourd — LangChain/CrewAI/smolagents
  — jamais intégré faute de gain mesuré qui le justifie).
- Modularité : chaque fonctionnalité (voix, routeur, TTS, STT, wake word...) doit pouvoir être
  désactivée (flag/env var) sans rien casser, et son implémentation doit pouvoir être remplacée
  (autre modèle, autre lib, autre approche) sans réécrire les autres composants — patron déjà
  suivi (`MATATA_ROUTER`, `MATATA_WHISPER_VAD`, `MATATA_MODEL`, `VOICE_LANG`) à maintenir pour
  toute nouvelle feature.
- Reply in French, concise
- Never add more than 3 tools (causes empty responses with 8B)

## Complementary docs

- `agent-pc/CLAUDE.md` — full architecture, Ollama issues, version history, test queries, PC specs
- `IMPLEMENTATION_SUMMARY.md` — Bug 1 & 2 fix details
- `BUG_FIXES_REPORT.md` — technical bug analysis

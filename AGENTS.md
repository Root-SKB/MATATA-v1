# MATATA — Agent Guidance

## What this is

Local AI ecosystem for Ubuntu, **100% offline**. Phase 1 (Agent PC) + Phase 2 (Voice) are built.
Python agent (`agent.py`, currently single-file) using Qwen3 8B via Ollama native tool calling, with Whisper STT + Piper TTS + wake word detection.

## Entrypoints & key files

| File | Role |
|------|------|
| `matata/` | Thin package wrapper → console `matata` / `python3 -m matata` (delegates to agent.py) |
| `agent-pc/agent.py` | **Single file agent** (~1560 lines, v12.17). All logic here. |
| `agent-pc/web/index.html` | Interface web minimale servie par `--serve` (`GET /`) — HTML/CSS/JS inline, zéro dépendance externe. |
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

# API web minimale (Phase 1 web/mobile), N2/N3 refusés à distance pour l'instant
MATATA_SERVE_TOKEN=change-me matata --serve --timer

# Quick validation (no Ollama)
python3 agent-pc/test_fixes.py

# Full integration (requires Ollama + qwen3:8b pulled)
bash agent-pc/tests.sh

# Syntax check
python3 -m py_compile agent-pc/agent.py
```

## Architecture essentials

- **3 core tools always exposed** (`run_shell`, `search_files`, `system_info`) — proven reliable with 8B. **4th tool `web_search` (v12.16) exposed dynamically**, only when the router classifies the turn as `web_search` (see below) — re-tested empirically (04/10/2026) that always exposing 4 tools measurably degrades the hardest multi-step query (`"Combien de séries avec taille ?"`: 3/3 reliable with 3 tools vs 1 raw-text dispatch failure + irrelevant `system_info` detours in 2/3 runs with 4 tools always present). Dynamic exposure (only add the 4th tool for turns that actually need it) fully restored the pre-existing reliability while still allowing web search — do not go back to always exposing more than 3 tools without the same kind of dynamic gating.
- **Model switching**: `MATATA_MODEL=qwen3.5:4b python3 agent-pc/agent.py` (default `qwen3:8b`). Both validated 5/5. qwen3.5:4b: same think=False, ever-no repeat_penalty (break tool calling); ~equal speed on iGPU but weaker semantics/French. qwen3.5 default hybrid thinking eats num_predict budget if think=False is omitted — `/no_think` inline does NOT work via Ollama.
- **Ollama config**: `think=False` (think=True + tools = empty output, Ollama issue 10976), `stream=True` (v12.4, premier token en ~1-2s), `temperature=0.3`, `num_predict=800`, `num_ctx=4096`, `keep_alive='30m'`. Runs on **Intel Arc MTL iGPU via Vulkan** (`OLLAMA_IGPU_ENABLE=1` in `/etc/systemd/system/ollama.service.d/igpu.conf`): 100% offload, ~2× faster than CPU-only. Ollama v0.32.15 as systemd service (user `ollama`, models in `/usr/share/ollama/.ollama/models`). `num_thread=16` kept in agent options (applies only if layers fall back to CPU).
- **Per-generation params**: `repeat_penalty` RETIRÉ pour les deux modèles (v12.5, bench 2026-08-30 : 1.0 == 1.2 en fiabilité sur basiques + multi-step, zéro réponse vide/early-EOS — le 1.2 historique pour éviter ~60% early-EOS après échec de tool n'est plus nécessaire ; et tout repeat_penalty casse le tool calling de qwen3.5). `AGENT_DEBUG=1` env var logs per-step response stats to stderr.
- **Safety**: N1/N2/N3 levels (v12.6, patron inspiré de jarvis-assistant-vocal). READ/N1 auto-executes, WRITE/N2 (`mkdir`/`cp`/`mv`/`touch`/`tee`/`echo`/`sed`) asks confirmation (local or, once a remote channel exists, remote), CRITICAL/N3 (`chmod`/`chown`/`apt`/`pip`/`nano`/`vim`/`nohup`) asks confirmation **local only** — gated by `IS_REMOTE`, BLOCKED (`rm`/`rmdir`/`shred`/`dd`/`reboot`/`halt`/etc) never. **`unknown` (non-whitelisted/hallucinated binary) also gated by `IS_REMOTE` since v12.17** — a real bug found in production use: `classify_command` returning `'unknown'` fell through to the same blocking `input()` as local N2/N3, but was never covered by the `IS_REMOTE` refusal, so a hallucinated command (e.g. a made-up binary) in `--serve` mode blocked on the server's own terminal stdin — and since `handle_turn()` runs under `run_server()`'s shared `lock`, this froze **every** other `/chat/`/`/voice/` request until someone typed into that terminal. Fixed by adding `'unknown'` to the `IS_REMOTE`-gated tuple, same clean refusal as N2/N3. `classify_command` hardened (v12.5): scans every command incl. `find -exec`, `xargs`, `sh -c`, `awk system()`, `systemctl reboot/poweroff/halt/kill/stop`. See whitelist in `agent.py`.
- **Auto-backup**: Before write operations on existing files, saves to `~/.agent-pc-backups/`.
- **Pre-LLM router** (v12.8, 6 categories since v12.16): `route_intent()` classifies every turn (FastEmbed/ONNX embeddings, hand-rolled — not the `semantic-router` package, ZERO frameworks) into greeting/time_date/system_stats/file_search/other/**web_search**. Only `greeting`/`time_date` fast-path (skip Ollama, instant canned/`datetime.now()` reply) above `MATATA_ROUTER_THRESHOLD` (default 0.5); everything else (`system_stats`/`file_search`/`other`/**`web_search`**) always goes through the normal LLM+tool path — `web_search` additionally decides whether the 4th tool is exposed to the LLM for that call (see Web search tool bullet below), it's the one category that affects tool *exposure*, not just the fast-path gate. `other` was added in v12.13 after real usage found meta/identity questions ("Qui es-tu ?", "What can you do?") being swallowed by the greeting fast-path — their scores (0.52-0.66) fully overlapped genuine greeting scores, so no threshold fix was possible; a dedicated never-fast-pathed category was the correct fix. Disable entirely with `MATATA_ROUTER=0`. Validated 92.0%/5.2ms vs Laya 84.0%/216.4ms on a 100-query benchmark — see `docs/TECH_WATCH.md` (note: that benchmark had no out-of-domain test cases, which is why the `other` gap went undetected until real use). `TextEmbedding()` must pin `threads=` explicitly (`MATATA_ROUTER_THREADS`, default 4) — onnxruntime's auto thread-count on this 22-core machine deadlocked (found via `--serve` testing, v12.9), fixed and now safe for both CLI and `--serve` startup.
- **Web search tool** (v12.16, `web_search`): DuckDuckGo via `ddgs` (zéro clé API, zéro Docker — seule exception opt-in à ZERO cloud, et seulement quand ce tool précis est appelé, jamais le LLM lui-même). **Désactivable sans rien casser via `MATATA_WEBSEARCH=0`** (modularité). Exposé dynamiquement (pas toujours dans `TOOLS`, voir ci-dessus) : `CORE_TOOLS` (3) par défaut, le 4e tool n'est ajouté pour l'appel Ollama que si `route_intent()` classe le tour `web_search` au-dessus du seuil — fallback vers `TOOLS` complet (toujours exposé) si `MATATA_ROUTER=0`, puisqu'il n'y a alors aucun moyen de sélectionner dynamiquement. **Limite connue** : le modèle peut encore fabriquer une réponse non étayée par les snippets DuckDuckGo retournés (pas de citation/grounding forcé) — observé une fois en test (réponse inventée sur un fait sportif) ; à traiter comme n'importe quelle réponse LLM, pas une garantie de véracité.
- **Web server + UI** (v12.9-v12.17, `--serve`): minimal stdlib `http.server` API (`GET /health`, `POST /chat/`, `POST /voice/`, `POST /reset/`) for Phase 1 remote access — no framework (gain réel mesuré ne le justifiait pas ici). **API convention** : POST endpoints end in `/`, GET endpoints don't ; every JSON response is `{"ok": bool, "message": str, "data": {...}}` — success: `data` is the real payload ; error: `ok=false`, `message` is always the literal `"An error occurred"`, the actual detail goes in `data.error`. **The whole `--serve`/web surface is in English** (code, UI text, API messages) — the user explicitly wants this layer English-only, separate from the agent's own conversational replies which stay French (SYSTEM prompt rule). `GET /` serves `web/index.html` : text input + mic button, zero external dependency, chosen as a web app (not native) to reuse the same page for mobile later via Tailscale with no rework. Mic : browser `MediaRecorder` → `/voice/` → `ffmpeg` converts to 16kHz WAV → existing `transcribe_audio()` (whisper.cpp) → text reply (no voice output in this version). **`/chat/`/`/voice/` stream live via Server-Sent Events (v12.14)** — not a single JSON blob: `token` events as the LLM generates (reuses the same `tool_calls is None` gate already proven for v12.7 TTS streaming, via an optional `_STREAM_SINK` hook, `None`/no-op outside `--serve`), a `transcript` event right after STT for `/voice/` (before the LLM even runs), and a final `done` event with the usual `{ok,message,data}` plus `data.elapsed_s`. **`status` events (v12.15)** close the remaining reactivity gap flagged by the user after v12.14: tool-calling turns now emit `{"type":"status","text":"..."}` right before a tool runs (search_files: `Searching for "..."`, system_info: `Checking system info (...)`, run_shell: the model's own `reason` if given, else `Running: <cmd>` for auto-executed N1 reads) — `web/index.html`'s typing indicator shows this text next to the bouncing dots instead of staying blank during the silent tool-execution window. Standalone mode (not combined with `--voice`/`--wake`). Sets `IS_REMOTE=True` for the process lifetime; N2, N3, **and `unknown`** refused outright when remote (no remote confirmation flow built yet — `elif lvl in ('critical','write','unknown') and IS_REMOTE` in `agent_turn`, `unknown` added v12.17 after it was found to still block on a local `input()`, freezing the whole server under the shared `lock` — see Safety bullet above). Optional `MATATA_SERVE_TOKEN` bearer auth, binds `127.0.0.1:8765` by default (`MATATA_SERVE_HOST`/`MATATA_SERVE_PORT`). **v12.15 fix**: the two startup `print()`s right before `httpd.serve_forever()` (and the router-load `✅`) now pass `flush=True` — found via debugging an apparent "hang" that was actually just full stdout buffering when `--serve`'s output isn't a TTY (e.g. redirected to a log file): the server was already serving requests correctly the whole time, confirmed via `faulthandler.dump_traceback_later()` showing the process parked in `serve_forever()`, not stuck in startup.
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

Bugs 1 (command dedup), 2 (200-char limit) fixed in v10.1; Bug 3 (tool output caps) fixed in v10.2; retry dead-end fixed + multi-model support in v10.3; Bug 4 (slow CPU inference) resolved by iGPU offload. Bug 6 (`search_files` wildcard-prefixed keyword like `"*.mkv"` parsed to an empty keyword) and Bug 7 (`search_files` `search_dir` containing a literal `~` quoted with `shlex.quote()` before expansion → shell never expands it → false "No such file or directory") both found via real `--serve` usage and fixed in v12.17 — see `agent-pc/CLAUDE.md`.

## Constraints

- ZERO cloud, ZERO deletion operations
- Cadre stratégique : **open-source > local > gratuit**. Externe/cloud/frameworks acceptés
  SEULEMENT si (a) 100% gratuits ET (b) apportent un gain réel mesuré — jamais un prérequis,
  toujours vérifié empiriquement avant intégration (voir l'historique des décisions dans
  `docs/TECH_WATCH.md` : Laya et semantic-router testés puis remplacés par un hand-roll léger
  une fois le gain validé ; `smolagents` testé empiriquement (même modèle, mêmes tools) et
  écarté sur gain mesuré négatif — ~19min vs ~2min sur la suite de tests, 2 réponses fausses et
  1 échec total ; LangChain/CrewAI jamais testés faute de cas d'usage le justifiant).
- Modularité : chaque fonctionnalité (voix, routeur, TTS, STT, wake word...) doit pouvoir être
  désactivée (flag/env var) sans rien casser, et son implémentation doit pouvoir être remplacée
  (autre modèle, autre lib, autre approche) sans réécrire les autres composants — patron déjà
  suivi (`MATATA_ROUTER`, `MATATA_WHISPER_VAD`, `MATATA_MODEL`, `VOICE_LANG`) à maintenir pour
  toute nouvelle feature.
- Reply in French, concise
- Max 3 tools exposed to the LLM **per call** (causes empty/degraded responses with 8B, re-confirmed empirically 04/10/2026) — a 4th+ tool is only acceptable if dynamically gated (added to the tool list for that one call only when actually relevant), never always-on. See `web_search`/v12.16 for the reference pattern.

## Complementary docs

- `agent-pc/CLAUDE.md` — full architecture, Ollama issues, version history, test queries, PC specs
- `IMPLEMENTATION_SUMMARY.md` — Bug 1 & 2 fix details
- `BUG_FIXES_REPORT.md` — technical bug analysis

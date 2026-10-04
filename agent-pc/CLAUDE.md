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
- Safety: command whitelist, N1/N2/N3 (READ auto-execute, WRITE/N2 ask confirmation local or
  remote, CRITICAL/N3 ask confirmation LOCAL ONLY, BLOCKED never)

## Files
- agent.py — Main agent script (currently a single file, current version: v12.17)
- web/index.html — minimal chat UI served by `--serve` (`GET /`), self-contained (inline CSS/JS)
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
    # API web minimale (Phase 1), N2/N3 refusés à distance :
    MATATA_SERVE_TOKEN=change-me python3 agent-pc/agent.py --serve --timer

## Current Version: v12.17 (3 real bugs found via production --serve usage, all fixed)
3 core tools (run_shell, search_files, system_info) + web_search gated dynamically
- v12.17: **user ran `--serve` for real use** (not a scripted test) and pasted the full
  terminal + web UI transcript for analysis. Found and fixed 3 concrete bugs, all
  reproduced in isolation before fixing (not just inferred from the transcript):
  1. **`--serve` + an `'unknown'`-classified command could freeze the entire server.**
     The model hallucinated a non-existent binary (`newslookup -q "..."`, trying to check
     a football score) in response to a query that had nothing it could actually answer —
     `classify_command()` correctly returned `'unknown'`, but the dispatch
     (`elif lvl in ('critical', 'write') and IS_REMOTE:`) never covered `'unknown'`, so it
     fell to the same `else: input(prompt)` branch as a local N2/N3 confirmation — a
     **blocking call on the server process's own terminal stdin**, not reachable by the
     remote/web client. Worse: `run_server()` calls `handle_turn()` under a shared `lock`
     (`with lock: handle_turn(...)`), so this one blocked `input()` froze **every** other
     `/chat/`/`/voice/` request from any client until someone typed into that terminal.
     Root-caused exactly: the transcript's `"Cancelled"` reply (after 16.7s, visible in
     the web UI) matches `input()` reading an empty/stray line already sitting in stdin
     (not the `o` the user typed afterward in the terminal — by then the request had
     already resolved, and that stray `o` was left sitting in the buffer for whatever
     `input()` call might come next). **Fixed**: `elif lvl in ('critical', 'write',
     'unknown') and IS_REMOTE:` — `unknown` now gets the same clean `REFUSED: ...` tool
     message as N2/N3, zero blocking. Verified with a direct `agent_turn()` call
     (`IS_REMOTE=True`, mocked `_ollama_stream` returning a `run_shell` tool_call for the
     exact `newslookup` command) — confirms the refusal path triggers, no `input()` call.
  2. **`handle_search_files()` keyword parsing broke on a leading wildcard.** The model
     passed `name="*.mkv"` (natural thing to try when asked "how many .mkv files"), and
     `re.split(r'[\s*,;|]+', name)[0]` treated `*` as a *delimiter*, splitting `"*.mkv"`
     into `['', '.mkv']` and keeping only the empty first element — the function then
     reported "Error: provide a keyword" even though a perfectly good keyword was given.
     Reproduced directly: `re.split(r'[\s*,;|]+', '*.mkv')[0]` → `''`. This wasted 2 of the
     turn's 5 steps before the model gave up on `search_files` and fell back to raw
     `run_shell`. **Fixed**: removed `*` from the split character class (kept
     whitespace/comma/semicolon/pipe as word separators) and changed the trim to
     `.strip('*.')` — `"*.mkv"` → `"mkv"` correctly, same as `"mkv"`/`".mkv"`/`"video*"`.
  3. **`handle_search_files()` broke on a literal `~` in `search_dir`.** The model passed
     `search_dir="~/Downloads"`; the code did `shlex.quote(search_dir)` on the raw string
     — but `shlex.quote('~/Downloads')` produces `'~/Downloads'` (single-quoted), and a
     shell **never expands `~` inside quotes**, so the generated `ls '~/Downloads'` failed
     with "No such file or directory" even though `~/Downloads` exists (proven by the very
     next step: the model typed `ls -a ~/Downloads` directly via `run_shell`, unquoted,
     and it worked fine and listed dozens of files). Reproduced directly with a standalone
     `subprocess.run("ls '~/Downloads'", shell=True)` → identical error. Only the
     *default* value (`os.path.expanduser('~')`) was ever expanded — a value the model
     supplied explicitly, as it did here, was not. **Fixed**: wrap the whole
     `args.get('search_dir', '~')` in `os.path.expanduser(...)` before quoting, in both
     the no-keyword error path and the main `find` path.
  **Validated**: all 3 fixes reproduced the exact failing case from the user's real
  session and confirmed fixed (`handle_search_files({'name': '*.mkv', 'search_dir':
  '~/Downloads'})` now returns the real file directly instead of an error; a live end-to-
  end run of "Y a-t-il des fichiers mkv dans mon dossier Downloads ?" resolved in 1 step/
  27s instead of the original session's 5 steps/71s). `tests.sh` 5/5, `test_fixes.py`
  unaffected. Also found but **not yet fixed** (lower priority, UX gap not a bug): the web
  UI has no equivalent to the CLI's special text commands (`reset`/`quit`) — `/clear`,
  `exit`, `bye` typed in the browser chat are just normal messages, subject to router
  misclassification, and never actually reset server-side conversation state (only
  `POST /reset/` does that).
  **4 more inconsistencies found in a self-audit pass** (user: "vérifie bien il semble
  avoir toujours des incohérence" — right to push back, this round wasn't from a pasted
  session, it's from re-reading the code and docs critically after the first 3 fixes):
  (4) the CLI startup banner's tools line (`🔍 search | 📊 sys | 📋 shell | 🌐 web...`,
  added alongside `web_search`) was **dead code for `--serve`** — the `SERVE` branch of
  `main()` returns before ever reaching that `print()`, so the exact mode the user was
  testing never showed whether `web_search` was active. Fixed by printing the same line
  inside the `SERVE` branch too, right before `run_server()`. (5) the `--serve` startup
  message said `"N2/N3 toujours refusés à distance"`, now stale since fix #1 also refuses
  `unknown` — updated the wording. (6) `AGENTS.md`'s router bullet still said "5 categories
  since v12.13" and listed `greeting/time_date/system_stats/file_search/other`, missing the
  `web_search` category added in v12.16 — corrected to 6. (7) **a real bug, not just a doc
  staleness**: the `SERVE` branch calls `run_server(messages, show_timer)` then
  `_whisper_server_stop()` as two plain sequential statements, not a `try/finally` — if
  `run_server()` raises (confirmed reproducible: start a dummy listener on port 8765 first,
  then launch `--serve`, which crashes with `OSError: Address already in use`), the
  whisper-server child is **never terminated**, leaking a process bound to port 18080
  silently. Fixed by wrapping `run_server(messages, show_timer)` in `try: ... finally:
  _whisper_server_stop()`. Reproduced the exact crash scenario before AND after the fix:
  before, `pgrep -af whisper-server` showed an orphaned process after the crash; after,
  nothing. `AGENTS.md` version range for the "Web server + UI" bullet (`v12.9-v12.15`) was
  also stale, now `v12.9-v12.17`. **Lesson**: a pasted real-world session surfaces bugs that
  trigger during actual use, but a deliberate re-read of the diff + docs after the fact
  catches a different class — dead code paths and doc/code drift that no single test
  exercises. Both passes are worth doing, neither replaces the other.
- v12.16: **re-tested empirically whether the long-standing "max 3 tools" constraint
  still held**, following a round of web research (multimodal/websearch/phone/more-tools/
  speak-realtime) that found a paper (arXiv:2411.15399) documenting tool-count degradation
  only past 20-25 functions in general — suggesting our 3-tool ceiling might be stricter
  than necessary, possibly an artifact of our specific config rather than a hard model limit.
  **Test**: added a 4th tool (`web_search`, via `ddgs`/DuckDuckGo, zero API key) always
  present in `TOOLS`, ran `tests.sh` + 3 extra standalone repeats of the hardest query
  ("Combien de séries avec taille ?", historically the most variable test). **Result: real
  measurable degradation** — with 4 tools always exposed, 1/4 runs produced a raw
  `<tool_call>{...}</tool_call>` text blob instead of a real tool call (the classic
  empty-response-trigger retry failure mode, see v10.3), and 2/3 standalone runs wasted a
  step calling `system_info(cpu)` on a query that had nothing to do with system stats,
  before eventually finding the right `find`+`du` command. With only 3 tools (`MATATA_
  WEBSEARCH=0`), 3/3 runs went straight to the correct command in step 1. **Conclusion**:
  the "3 tools" ceiling is real for THIS model/config on hard multi-step queries, even
  though the literature's 20-25 threshold is about aggregate benchmark averages, not worst-
  case reliability on a specific small model via Ollama native tool calling.
  **Fix, not retreat**: rather than abandoning `web_search`, implemented the "dynamic tool
  retrieval" pattern also surfaced by the same research round (same arXiv paper's core
  idea) — reusing the **existing** `route_intent()` FastEmbed/k-NN router (v12.8) instead of
  a new dependency: `CORE_TOOLS` (the proven 3) are always exposed; `web_search` is added to
  the list passed to `ollama.chat(tools=...)` **only for the one call** where a new 6th
  router category (`'web_search'`, ~30 FR+EN utterances: weather/news/prices/facts/sports)
  scores above `MATATA_ROUTER_THRESHOLD`. Every other turn (including the hard series/music
  tests) gets exactly the same 3-tool prompt that was already proven reliable — **zero
  change in behavior for 100% of pre-v12.16 use cases**, `web_search` only enters the
  picture for turns that actually need it. Fallback: if `MATATA_ROUTER=0`, there's no way to
  classify dynamically, so `web_search` falls back to always-on (accepting the measured
  risk) rather than silently disappearing — documented tradeoff, not a bug.
  **Also found + fixed while implementing** (contre-vérification pass before coding, 2
  parallel sub-agents — one re-reading `agent.py` itself, one re-verifying the research's
  riskiest external claims): (1) a duplicated tool-dispatch bug — the rare "retry without
  tools" code path (empty-response fallback) had the 3 tool branches but **no `else`
  fallback**, unlike the main dispatch which already had one; an unrecognized `fn_name`
  there would silently break the conversation protocol. Fixed by adding the same `else`
  there. (2) The externally-recommended multimodal candidate (`qwen2.5vl:7b`) turned out to
  **not support Ollama native tool calling at all** (confirmed: no VLM on Ollama does except
  `qwen3-vl:8b-instruct`, which does support tools+vision+thinking together) — corrected in
  `docs/TECH_WATCH.md`, not implemented this round (separate task).
  **Known limitation**: the model can still produce an answer not actually grounded in the
  DuckDuckGo snippets returned (no citation/grounding enforcement) — observed once in manual
  testing (a fabricated sports fact). Treat `web_search` answers like any other LLM output,
  not a verified-truth guarantee.
  Validated: `tests.sh` 5/5 (test 5 back to single-step/reliable), `test_fixes.py` unaffected,
  manual end-to-end test of `web_search` itself (weather query → correct tool call → real
  DuckDuckGo results → reasonable answer; sports query → tool worked correctly, answer
  hallucinated — see limitation above), `MATATA_WEBSEARCH=0` and `MATATA_ROUTER=0` toggles
  both verified to behave as designed (banner line reflects actual tool exposure in each
  case).
- v12.15: **closes the reactivity gap deferred at the end of v12.14** (user: "C'est pas exactement
  au point niveau reactivité que je cherche mais on pourrait améliorer après" → later: "Fais-le
  maintenant"). Root cause: for tool-calling turns (the majority of real queries), the web UI
  showed static typing dots with zero feedback during the whole search_files/system_info/run_shell
  execution window — only once the final answer started streaming did anything move. **Fix**: a
  new `status` SSE event type, emitted via the existing `_emit_stream()` hook right next to the
  terminal's own progress prints — `search_files` → `Searching for "<name>"...`, `system_info` →
  `Checking system info (<category>)...`, `run_shell` → the model's own `reason` field when
  provided, else `Running: <cmd>` as a fallback for auto-executed N1 reads with no reason.
  `web/index.html`'s typing indicator gained a `.typing-label` span (hidden via `:empty` until a
  status arrives) next to the bouncing-dots span; `setTypingStatus()` fills it, and
  `handleTurnEvents()` routes `ev.type === 'status'` to it (only before the first `token` event —
  once real text starts streaming the label is irrelevant, the bubble itself takes over).
  **Bug found while verifying this**: running `--serve` with stdout redirected to a file (the
  natural way to test/operate it headless) made it LOOK hung forever right after "Chargement du
  routeur..." — spent significant debugging effort chasing a suspected recurrence of the v12.9
  onnxruntime thread-pool deadlock (isolated repros of the exact same router-load sequence were
  all fast, ~4s) before `faulthandler.dump_traceback_later(15, exit=True)` proved the process was
  already parked in `httpd.serve_forever()`, not stuck in startup at all. Root cause: `print()`
  without `flush=True` on the router's `✅` and on the two startup banner lines right before
  `serve_forever()` — fine for an interactive TTY (line-buffered by default) but silently held in
  Python's full-buffering mode whenever stdout is a pipe/file, which is exactly how `--serve` would
  run as a background/systemd-style service. Fixed by adding `flush=True` to those three prints.
  Not a functional bug (every request was already served correctly the whole time, confirmed via
  curl against the "hung" instance) but a real operability trap for anyone who redirects `--serve`
  output to a log file. **Lesson for next time**: when a background process seems to hang, check
  with `faulthandler.dump_traceback_later()` (or `py-spy dump`) before assuming the suspected prior
  bug recurred — a silent stdout-buffering gap looks identical to a real hang from the outside.
  Validated: `curl -N /chat/` on a system_info query shows `status` → `token`×N → `done` in order;
  a run_shell query (reason-driven) shows the model's own reason as the status text; full startup
  banner now appears immediately in a redirected log. `tests.sh` 5/5 + `test_fixes.py` unaffected
  (CLI/voice/wake never set `_STREAM_SINK`, so `_emit_stream()` stays a no-op there as before).
- v12.14: **3 UX issues reported by the user while testing `--serve`**, all three caused by the
  same root issue — `/chat/`/`/voice/` waited for the ENTIRE turn to finish before sending
  anything back, so the browser saw nothing until the whole reply (and, for voice, the whole
  transcription+reply) was ready: (1) no visible "typing" effect in the web UI despite the
  terminal showing `stream=True` token-by-token, (2) the mic felt slow because the transcript
  only appeared once the LLM's full answer was also ready, (3) no per-reply timing shown (the
  terminal has `⏱️ Xs`, the web UI had nothing).
  **Fix: real Server-Sent Events streaming.** `/chat/` and `/voice/` now respond with
  `Content-Type: text/event-stream` instead of a single JSON blob. A new optional module-level
  hook, `_STREAM_SINK` (`None` outside `--serve` — zero behavior change for CLI/voice/wake),
  lets `agent_turn()`/`_fast_reply()` relay text live via `_emit_stream('token', text=delta)`
  right next to the existing `print(delta, ...)` calls — reuses the exact same
  `tool_calls is None` gating already proven for the v12.7 TTS streaming (so a tool-call
  preamble is never shown as if it were the final answer; a `reset` event is emitted if text
  was streamed for a step that turns out to be a preamble or gets silently retried, telling the
  browser to discard it). `/voice/` emits a `transcript` event immediately after whisper.cpp
  finishes, before the LLM is even called. A final `done` event keeps the same `{ok, message,
  data}` shape as before, now with `data.elapsed_s` (wall-clock time of the whole turn, measured
  in `run_server`). `web/index.html` reads the stream via `fetch()` + `response.body.getReader()`
  (not `EventSource`, which is GET-only and can't carry the POST body or auth header) — parses
  `data: {...}\n\n` frames, grows a live bubble per `token` event, and shows the elapsed time as
  a small caption under the final bubble (`.msg-time`).
  Validated: `curl -N` on `/chat/` and `/voice/` shows real token-by-token SSE frames with
  correct `transcript`/`token`/`done` shapes and correct `elapsed_s`; a real headless-Chrome
  interaction (typed a message, screenshot mid-generation) shows the bubble growing live
  ("1. **Recherche de fichiers**... 2. **", cut mid-sentence, send button correctly disabled) —
  confirms the actual browser experience, not just the wire format. `tests.sh` 5/5 +
  `test_fixes.py` unaffected (CLI/voice/wake never set `_STREAM_SINK`, so `_emit_stream()` is a
  no-op there exactly as before).
- v12.13: **real bug found via user testing of `--serve`** — meta/identity questions about the
  agent itself ("Qui es-tu ?", "Que peux-tu faire ?", "Comment ça marche ?", "What can you do?")
  were silently swallowed by the `greeting` fast-path, returning a canned "Bonjour ! Comment
  puis-je t'aider ?" instead of a real answer. Root cause verified empirically: these queries
  scored 0.52-0.66 for `greeting`, a range that **fully overlaps** genuine greeting scores
  (0.51-0.97) — no threshold could separate them, because none of the 4 router categories
  represented "questions about the agent," so the classifier was forced toward the nearest
  existing bucket (greeting, since both are short/casual/conversational). **Fix**: added a 5th
  `ROUTER_UTTERANCES` category, `'other'` (~30 identity/capability/meta/off-topic utterances,
  FR+EN), which is **never fast-pathed** — `handle_turn()`'s gate already only checks
  `cat in ('greeting', 'time_date')`, so anything classified `'other'` falls through to the
  normal LLM path with zero code changes beyond adding the utterances. Re-verified the exact
  failing queries now score 0.79-0.98 for `'other'` and correctly reach the LLM for a real
  answer; a 24-query spot-check across all 5 categories (including the previously-known
  informal-greeting→time_date edge case) scored 100% with no regression on existing categories.
  `tests.sh` 5/5 + `test_fixes.py` unaffected. Lesson for next time: the 100-query benchmark that
  validated the router (see v12.8/docs/TECH_WATCH.md) never included out-of-domain "none of the
  above" queries — only variations of the 4 known intents — so this failure mode was invisible
  until real usage surfaced it. Any future router category work should add adversarial
  out-of-domain test cases, not just more paraphrases of in-domain ones.
- v12.12: the `--serve` surface is now **English-only** (user-specified: code and anything shown
  to the user is written in English) — `web/index.html` text ("Type a message…", "Recording…
  tap again to stop", "Microphone unavailable: …", "Network error: …", token prompt, status
  text "online"/"remote"/"offline", empty-state copy) and the API's `message`/`data.error`
  strings (`"System operational"`, `"Response generated"`, `"Audio transcribed and processed"`,
  `"Conversation reset"`). **Error `message` simplified to always be the single literal
  `"An error occurred"`** (per the user's own example) — `_err(code, error)` now takes just the
  `data.error` detail, no separate per-call message. This is scoped to the new `--serve`/web
  code only; the rest of `agent.py` (CLI/voice/wake prints, comments, and the LLM's own
  conversational replies — still French per the SYSTEM prompt's "Reply in French" rule) is
  untouched — these are different layers (UI/API literals vs. the agent's spoken language).
  **Visual redesign** (`web/index.html`, user feedback: previous version "not cool"): SVG icons
  replace emoji for mic/send (crisper, consistent cross-platform), indigo gradient accent
  (`#6366f1`→`#4f46e5`) instead of flat blue, animated 3-dot typing indicator instead of a
  static "…", a welcoming empty-state (logo badge + "Hi, I'm MATATA"), message fade/rise-in
  animation, and a card-with-shadow layout on wider viewports (≥760px) vs. edge-to-edge on
  mobile. Verified via headless Chrome screenshots (empty state + a populated conversation with
  all 4 bubble styles) — **note for future verification**: `--screenshot` alone can capture
  mid-animation (looked washed-out at first, bubbles pale instead of vivid), fixed by adding
  `--virtual-time-budget=2000` so CSS animations/timers settle before the capture — not a real
  bug, a headless-capture quirk. `tests.sh` 5/5 + `test_fixes.py` unaffected (no agent_turn/CLI
  code touched).
- v12.11: `--serve` endpoints now follow a fixed convention (user-specified): **POST paths end
  in `/`** (`/chat/`, `/voice/`, `/reset/`), **GET paths don't** (`/health`, `/`). Every JSON
  response is `{"ok": bool, "message": str, "data": {...}}` — on success `data` holds the real
  payload (e.g. `{"reply", "role"}` for `/chat/`); on error `ok=false`, `message` is **always
  generic** (e.g. "Requête invalide", "Non autorisé", "Introuvable"), and the specific detail
  goes in `data.error`. `web/index.html` updated to match (`postJSON('/chat/', ...)`,
  `fetch('/voice/', ...)`, reads `json.data.reply`/`json.data.error` instead of top-level
  fields). `GET /` keeps serving the raw HTML page unchanged — the envelope only applies to the
  JSON API. Re-validated: `curl` on all 5 cases (health, chat success, chat missing-field error,
  reset, voice round-trip) + a path without the trailing slash correctly 404s. `tests.sh` 5/5 +
  `test_fixes.py` unaffected (no agent_turn/CLI code touched, only `run_server()`).
- v12.10: first PC interface for `--serve`. `GET /` now serves `web/index.html` — a single
  self-contained static file (inline CSS/JS, zero external dependency, zero build step, no
  framework/library — "ce qui se fait déjà, pas réinventer la roue" per the user's own framing):
  text input + send button, chat log with styled bubbles (user/assistant/tool-refusal/voice-
  transcript, 4 distinct visual classes), a mic button. Chosen over a native app (Electron/Tauri)
  because the exact same static page + API will serve Phase 1 mobile later via Tailscale with
  zero rework — a native app would have to be rebuilt per platform for no real gain here.
  **Mic button**: records via the browser's `MediaRecorder` (webm/opus), uploads to new
  `POST /voice` endpoint. Server converts webm→16kHz mono WAV via `ffmpeg` (already a project
  dependency, used elsewhere for `--wake` beep generation) then reuses the existing
  `transcribe_audio()` (same whisper.cpp pipeline as `--voice`/`--wake`) — zero new STT code.
  Reply is **text-only** for this version (user's explicit choice: simpler to ship now, voice-out
  via Piper streamed back to the browser left for later if ever needed). `--serve` now also
  starts `whisper-server` at startup (previously only `--voice`/`--wake` did) so `/voice` doesn't
  reload the whisper model on every request.
  Validated: `curl` round-trip on `/voice` with a synthetic Piper-generated clip converted to
  webm (simulating what a real browser microphone would send) — transcribed correctly, routed
  correctly, correct reply. Visually verified via headless Chrome screenshots: empty state (health
  dot, model name, input bar) and a populated conversation with all 4 bubble styles — both render
  as intended. `tests.sh` 5/5 and `test_fixes.py` re-confirmed unaffected.
- v12.9: first Phase 1 web/mobile step — `--serve` mode (`MATATA_SERVE=1` or `--serve` flag)
  runs a minimal stdlib-only HTTP API (`http.server.ThreadingHTTPServer`, zero new dependency —
  the need doesn't justify FastAPI/aiohttp per the "gain réel mesuré" rule) instead of the
  interactive CLI loop. Endpoints: `GET /health`, `POST /chat {"message": "..."}` →
  `{"reply", "role"}`, `POST /reset`. Optional bearer-token auth via `MATATA_SERVE_TOKEN` (no
  auth if unset — fine for localhost/private Tailscale, set before any wider exposure).
  Binds `127.0.0.1` by default (`MATATA_SERVE_HOST` to change), port 8765 by default
  (`MATATA_SERVE_PORT`). Sets `IS_REMOTE = True` for the whole process lifetime (standalone
  mode, mutually exclusive with `--voice`/`--wake`/interactive CLI — modularity: a new opt-in
  mode, nothing existing changes unless you use it). **N2 (write) now also gated by
  `IS_REMOTE`** (agent_turn's `elif lvl in ('critical', 'write') and IS_REMOTE`) — previously
  only N3 checked `IS_REMOTE`; N2 would have hit a blocking `input()` with no local TTY to
  answer it, hanging the request. No remote confirmation flow implemented yet: N2/N3 both
  refused outright when remote, exactly as the user decided (block first, build real remote
  confirmation later if needed).
  **Bug found + fixed during testing**: `_get_router()`'s `TextEmbedding(...)` call would
  deadlock (60 threads parked in `futex_do_wait`, confirmed via `/proc/<pid>/task/*/wchan`,
  never observed when calling it directly in isolation — reproduced reliably only through the
  full `--serve` startup path) on this 22-core machine with onnxruntime 1.29.0's default
  thread-pool sizing. Fixed by passing `threads=ROUTER_THREADS` (env `MATATA_ROUTER_THREADS`,
  default 4) explicitly to `TextEmbedding()` instead of leaving it to auto-detect core count —
  verified reliable across 7+ repeated runs after the fix, zero hangs. This also quietly fixes
  the same class of risk for normal CLI startup, not just `--serve` (the router loads the same
  way there, just hadn't hit the race during this session's many earlier CLI tests).
  Validated: `tests.sh` 5/5 + `test_fixes.py` unchanged; manual `curl` tests of all 3 endpoints
  (greeting fast-path, read-command via LLM, write-command correctly refused remotely, 401 on
  missing/wrong token, 200 on correct token).
- v12.8: pre-LLM intent router (`route_intent()`) using FastEmbed (ONNX, ~222MB, zero torch)
  embeddings + a hand-rolled top-5-nearest-neighbor/mean-by-route classifier — NOT the
  `semantic-router` package itself (ZERO frameworks constraint; the package was only used to
  validate the approach empirically, see docs/TECH_WATCH.md: 92.0% accuracy / 5.2ms vs Laya's
  84.0% / 216.4ms on a 100-query benchmark). Classifies every turn into
  greeting/time_date/system_stats/file_search, but only **greeting and time_date** trigger a
  fast-path (`handle_turn()` → `_fast_greeting()`/`_fast_time_date()`) that skips Ollama entirely
  and replies directly (canned greeting / `datetime.now()`-derived time+date) when the top score
  clears `MATATA_ROUTER_THRESHOLD` (default 0.5, same threshold validated in testing).
  `system_stats`/`file_search` are classified but always deferred to the normal LLM+tool-calling
  path unchanged — the router only categorizes, it cannot extract the actual command arguments
  (which folder, which stat), so bypassing the LLM for those would require guessing parameters
  it doesn't have. Measured impact: "Hi"/"Quelle heure ?" go from ~12-22s (LLM) to ~0.03s
  (post-warm-up) — tests.sh 5/5 unchanged, tests 3-5 untouched. Known residual risk: ~4% of
  queries in the 100-query benchmark get a wrong-but-harmless fast reply (informal phrasings
  like "Quoi de neuf ?"/"What's up" misread as time_date instead of greeting — never a security
  or destructive-action risk, since the fast-path only ever replies with text or a hardcoded
  `datetime.now()` read, it never runs a user-influenced shell command).
  Escape hatch: `MATATA_ROUTER=0` disables the router entirely (falls back to 100% pre-v12.8
  behavior); `MATATA_ROUTER_THRESHOLD` tunes the confidence bar. Router model/utterance
  embeddings are precomputed once at startup (`⏳ Chargement du routeur...`, ~3-4s one-time ONNX
  warm-up, same pattern as the whisper-server startup message) so the first real user turn
  already benefits from the fast-path instead of absorbing that cost mid-conversation.
- v12.7: sentence-by-sentence TTS streaming (pattern extracted from LocalVox,
  github.com/YaPanBytes/LocalVox, see docs/TECH_WATCH.md). `SPEAK_SENTENCE_RE` splits the
  streamed LLM text on sentence boundaries and calls `speak()` per completed sentence instead
  of waiting for the full response — only while `tool_calls is None` (tool-call preambles are
  never spoken, unchanged) and while the accumulated text hasn't matched `INCOMPLETE_PATTERNS`
  (a `safe_to_speak` flag latches off the moment a "je vais..." retry-trigger phrase appears,
  so nothing gets spoken live that might get silently retried; the leftover buffer is caught by
  the existing post-loop fallback `speak()` call if the retry budget is exhausted). Zero impact
  on text-only mode (`speak()` no-ops without `VOICE`) — verified via tests.sh 5/5 unchanged and
  a 3-case simulated-stream test (normal multi-sentence, tool_calls present, incomplete-pattern
  retry exhaustion). Only wired into the primary response path, not the empty-response retry
  fallback (agent.py ~846-850) — left as single-shot `speak()` for now, lower-traffic path.
- v12.6: split WRITE_COMMANDS into N2 (write: mkdir/cp/mv/touch/tee/echo/sed — confirm,
  local ou distant) et CRITICAL_COMMANDS/N3 (chmod/chown/apt/pip/nano/vim/nohup — confirm,
  UNIQUEMENT locale). New IS_REMOTE flag (False today, no remote channel exists yet) gates
  N3: if IS_REMOTE and lvl=='critical', refuse outright ("REFUSED: N3 critical action
  requires a fresh LOCAL confirmation, not available remotely") instead of prompting.
  Patron inspiré de jarvis-assistant-vocal (confirmed real, see docs/TECH_WATCH.md §7):
  "N1/N2/N3 permissions keep safe reads separate from sensitive and critical actions; N3
  always requires a fresh local spoken confirmation and is never remotely executable."
  This is step 1 of the Phase 1 web/mobile prerequisite (remote WRITE confirmation gap) —
  IS_REMOTE still needs an actual remote channel (Tailscale-facing bridge) to ever flip True.
  Also: VAD native whisper.cpp branchée dans _whisper_server_start() — --vad ajouté au
  server (build-vulkan), toggle MATATA_WHISPER_VAD=0 pour désactiver.
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
- v12.3 optimizations: retry empty sans tools (-9-19s gaspillage), VOICE_LANG='fr'
  par défaut (-3,5s en push-to-talk), num_predict 500→800 (robustesse multi-étapes),
  OLLAMA_FLASH_ATTENTION=1 (~10-20% inference), OLLAMA_KV_CACHE_TYPE=q8_0 (-200-400 MB),
  règle 4 système dedoublée (-50-80 tokens/tour)
- v12.4 Vague 2: stream=True Ollama (premier token en ~1-2s au lieu de ~15s d'attente),
  whisper-server persistant (modèle chargé 1×, ~1s/passe économisées), Piper Python API
  in-process (1,1s lazy-load, 0,1s synth, zéro subprocess piper), Spinner supprimé
- v12.5: Whisper STT sur iGPU Vulkan (build-vulkan/), bascule _pick_bin par binaire avec
  fallback CPU indépendant (cli/server); retry sans tools gère system_info; nettoyage
  import array mort; fix whisper-server --audio-ctx (crashait sur --ctx invalide, server
  jamais utilisé). Bench 2026-08-30: ~0.94s/passe sur clip 12s vs 4.24s (CPU+spawn), ~4.5×.
  **repeat_penalty retiré** (1.0 == 1.2 en fiabilité, zéro vide/early-EOS sur basiques +
  multi-step; le penalty avait été mis pour éviter ~60% early-EOS, plus nécessaire).

## VOICE MODE (v12)
- Binaries/models live in ../voice/ (GITIGNORED — rebuild steps below).
  whisper.cpp: git clone https://github.com/ggml-org/whisper.cpp voice/whisper.cpp &&
  cmake -B build -DGGML_NATIVE=ON && cmake --build build -j  (bin at build/bin/whisper-cli,
  backend CPU).
  **Vulkan (v12.5)** : cmake -B build-vulkan -DGGML_VULKAN=1 -DGGML_NATIVE=ON
  -DCMAKE_BUILD_TYPE=Release && cmake --build build-vulkan -j (bin at build-vulkan/bin/,
  utilise l'iGPU Arc MTL via mesa Vulkan, backend Vulkan0). Dépendances requises :
  libvulkan-dev, glslc, libshaderc1, spirv-headers. `build-vulkan/` est gitignoré.
  agent.py choisit par binaire (Vulkan d'abord, fallback CPU) via _pick_bin() — cli et
  server sélectionnés indépendamment.
  Models in voice/models/: ggml-small.bin (~466MB, HF ggerganov/whisper.cpp);
  Piper voices fr_FR-siwis-medium + en_US-lessac-medium (~61MB each,
  HF rhasspy/piper-voices). Voice config jsons ARE committed (5KB each).
- Pipeline: whisper-server persistant (port 18080, HTTP API multipart, fallback CLI)
  ou whisper-cli si serveur indisponible → LLM stream=True (premier token ~1-2s)
  → Piper Python API in-process (lazy-load, zéro subprocess) → aplay.
  VOICE_LANG='fr' par défaut, 'auto' = dual decode fr+en passes, winner by mean token 'p';
  native initial prompt per pass; rule 10 replies in user's language; bilingual TTS
  — speak() picks siwis or en_US-lessac via accent+stopword heuristic (_voice_for).
- Timings measured: single STT pass ~3.6s per 12s clip (CPU + spawn); auto = ~6-7s.
  **Vulkan + server persistant (v12.5)**: ~0.94s/passe sur clip 12s (mesuré 2026-08-30),
  ~4.5× plus rapide — combine GPU Vulkan + suppression du spawn/load du serveur chaud.
  TTS ~1.3s synth start (FR) / 4.2s full EN sentence playback; full vocal turn
  ≈ model time + ~7s overhead. User reported faster than typing.
- Mic: Intel DMIC array, gain 80% (-10dB). Speak ~30cm away; short phrases may
  mis-transcribe if far/quiet ("Quelle heure" → "Quel air" on quiet input).
- Pitfall: at mic prompt, typed words used to be swallowed (input started recording);
  fixed in v11 — first input captures text and routes through normal commands.

## WAKE MODE (v12.2)
- `python3 agent-pc/agent.py --wake` (implique voix) : veille permanente, dis « MATATA »
  puis ta commande. Après CHAQUE réponse → état ACTIF 15 s (env MATATA_ACTIF) : parle
  sans mot-clé, chaque échange relance le compteur ; expiration → 💤 retour veille.
  Ctrl+C quitte. Vocaux tolérants (_voice_cmd, 1-2 derniers mots, ≤3 mots, ponctuation/
  accents ignorés) : « au revoir. », « et reset », « ok stop », « arrete toi » —
  « au revoir » répond « À bientôt ! » en TTS avant fermeture. Phrases longues finissant
  par ces mots restent des demandes normales (« ça a fait un reset »).
- MicStream (v12.2) : arecord encapsulé avec respawn() — relancé après CHAQUE
  confirmation (acceptée/rejetée), après chaque échange TTS et au retour veille :
  supprime les ~2 s de frames périmées accumulées dans le tampon du pipe pendant les
  phases bloquantes (whisper/TTS) — cause racine du bip capté comme commande et des
  doubles-déclenchements potentiels. Le bip est joué AVANT respawn (jamais dans le
  flux neuf) + skip_frames=4 (~0,3 s réverb) avant capture. Flux mort → auto-respawn.
- Deux barrières anti-faux positifs: (1) openwakeword (voice/models/matata.onnx,
  entraîné Colab v5 R3 anti-fragments) seuil 0.5, vad_threshold 0.25, pré-roll 1.7 s ;
  (2) confirmation whisper fr du pré-roll (+320 ms queue) — refusée si la transcription
  n'est qu'un tag entre crochets/parenthèses ([MATATA speaking], (Bip) = hallucination),
  sinon acceptée seulement si « matata » normalisé présent. Rejet = bip grave + réfractaire.
- Latence : STT single-pass fr forcé en wake (-3~4 s ; env MATATA_WAKE_LANG=auto pour
  double passe) ; endpointing 1,0 s silence ; repères ⏳ vérification / 🎧 je t'écoute.
  Reste coûteux : spawn whisper-cli + chargement modèle 466 Mo par passe (~2 s).
- _clean_stt() supprime TOUS les artefacts [..] et (..) de transcribe_audio (tous modes)
  — artefact pur = transcription vide = rejet propre sans appeler le LLM. Avant v12.2,
  "[MATATA speaking]" atteignait le LLM et était lu par Piper.
- _strip_wake_prefix() enlève un « (salut) MATATA » résiduel en tête de commande.
- Capture commande: départ RMS>260, fin après ~1,0 s silence ou 10 s max; en état ACTIF
  l'attente de démarrage = reste du compteur (15 s), sinon 6 s.
- Env knobs: MATATA_WAKE_THRESHOLD, MATATA_WAKE_VAD, MATATA_WAKE_MODEL,
  MATATA_WAKE_LANG (fr|auto), MATATA_ACTIF (s), MATATA_WAKE_DURATION (auto-exit test),
  MATATA_WAKE=1 (= --wake).
- Bips ffmpeg→/tmp (880 Hz accepté, 330 Hz rejet). Limites: WRITE demande toujours une
  confirmation clavier; léger risque d'auto-déclenchement si la réponse TTS contient
  « matata » (atténué par respawn + double vérification). Observé v12.1 : STT bruité
  « Et reset » → LLM a émis un tool call sudo reboot EN TEXTE BRUT (rien exécuté ;
  reboot ∈ BLOCKED_COMMANDS) — _voice_cmd intercepte désormais ces phrases avant LLM.
- Tests unitaires wake (sans micro): /tmp/opencode/test_wake_units.py.

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
inference) resolved in practice by iGPU offload — no longer tracked as a bug. BUG 6
(`search_files` keyword parsing broke on a leading wildcard, e.g. `"*.mkv"` → empty
keyword) and BUG 7 (`search_files` `search_dir` with a literal `~` quoted before
expansion → shell never expands it → false "No such file or directory") both fixed v12.17,
found via a real `--serve` session the user pasted for analysis — see v12.17 changelog
above for full root-cause detail. A 3rd issue found in the same session, the `--serve`
server-freeze on an `'unknown'`-classified command, is a safety/reliability bug not a
tool-behavior one — also fixed v12.17, see the Safety section of `AGENTS.md`.

## CONSTRAINTS
- ZERO cloud at runtime: everything local. (README mentions a future "Cloud Mentor"
  phase — conflicts with this rule, decision pending.)
- ZERO deletion: agent must NEVER run rm/rmdir/shred/dd/etc.
- External deps/frameworks: open-source > local > free, accepted ONLY if (a) 100% free AND
  (b) a measured real gain, never a prerequisite — always verified empirically before
  integration, never rejected or adopted on principle alone (see docs/TECH_WATCH.md for the
  track record: Laya and semantic-router both tested then replaced by a lighter hand-roll once
  the gain was validated; LangChain/CrewAI/smolagents-style orchestration frameworks never
  integrated for lack of a measured gain, not a blanket ban).
- Modularity: every feature (voice, router, TTS, STT, wake word...) must be disableable via a
  flag/env var without breaking the rest, and its implementation must be swappable (different
  model/lib/approach) without rewriting other components — pattern already followed
  (MATATA_ROUTER, MATATA_WHISPER_VAD, MATATA_MODEL, VOICE_LANG), keep following it for new work.
- Max 3 tools exposed per call: more caused empty/degraded responses, re-confirmed empirically v12.16. A 4th+ tool is acceptable only if dynamically gated per-call (see `web_search`/CORE_TOOLS pattern), never always-on.

## MODEL CONFIG (in agent.py, near top)
- MODEL = env MATATA_MODEL or 'qwen3:8b'
- THINK_KW = {'think': False} for BOTH generations:
  * qwen3: think=True + tools = empty output (Ollama issue 10976)
  * qwen3.5: default hybrid thinking eats the whole num_predict budget → empty answers.
    The inline '/no_think' switch does NOT work through Ollama.
- CHAT_OPTS = {'num_predict': 800, 'num_ctx': 4096, 'temperature': 0.3, 'num_thread': 16}
  * repeat_penalty RETIRÉ (v12.5, bench 2026-08-30: 1.0 == 1.2 en fiabilité, zéro réponse
    vide/early-EOS sur basiques + multi-step). Le 1.2 avait été mis pour contrebalancer
    ~60% early-EOS de la rec officielle 1.5, plus nécessaire. tout penalty casse qwen3.5.
- keep_alive='30m' on chat calls (model stays resident between turns)
- stream=True (v12.4): premier token visible en ~1-2s au lieu de ~15s d'attente complète
- VOICE_LANG = 'fr' by default (v12.3): single-pass STT; 'auto' available via env MATATA_WAKE_LANG=auto

## RUNTIME ENVIRONMENT
- Ollama v0.32.15 as systemd service (`ollama.service`, User=ollama, enabled at boot)
- Models stored in `/usr/share/ollama/.ollama/models` (NOT ~/.ollama anymore — that copy was deleted)
- iGPU enabled via drop-in `/etc/systemd/system/ollama.service.d/igpu.conf` → `OLLAMA_IGPU_ENABLE=1`
  + `OLLAMA_FLASH_ATTENTION=1` (v12.3, ~10-20% faster inference)
  + `OLLAMA_KV_CACHE_TYPE=q8_0` (v12.3, -200-400 MB KV cache RAM)
  (Vulkan detects "Intel(R) Arc(tm) Graphics (MTL)" natively; without the flag the iGPU is dropped)
- whisper-server (v12.4): modèle ggml-small.bin chargé 1× au démarrage voice/wake mode,
  HTTP API sur port 18080 (MATATA_WHISPER_PORT), fallback CLI si serveur indisponible.
  Env: MATATA_WHISPER_PORT (défaut 18080). Arrêt automatique en fin de session.
- Piper Python API (v12.4): import piper.PiperVoice, lazy-load par langue (1,1s first call),
  synthèse in-process (0,1s), piped to aplay. Zéro subprocess piper.
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
- 3 tools only, stream=True (v12.4+, premier token en ~1-2s), temperature=0.3 — reliable across versions
- Auto-retry keeps tools now: model recovers from failed commands instead of dying in JSON-as-text
- keep_alive 30m: warm model = fast consecutive turns

## PC SPECS
- CPU: Intel Core Ultra 7 155H (16 cores / 22 threads)
- RAM: 32GB DDR5-5600 (~9.5GB available under normal load, measured 2026-09-28)
- Disk: 512GB NVMe (468GB usable, ~151GB free)
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

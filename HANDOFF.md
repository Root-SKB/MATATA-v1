# HANDOFF — MATATA local-first evolution + veille technique

Rédigé le 2026-09-28 en fin de session. À lire en premier dans une session neuve.

## Objectif de la tâche

Faire évoluer MATATA vers un mode **« local d'abord, externe seulement si ça vaut le coup »**,
et tenir à jour la veille technique (`docs/TECH_WATCH.md`, gitignoré, FR, usage perso).

Cadre stratégique non négociable : **open-source > local > gratuit**. Externe/cloud/frameworks
acceptés SEULEMENT si (a) 100% gratuits ET (b) gain réel mesuré — jamais un prérequis.

## Ce qui a été fait

1. **Deep-analyse du code** (`agent.py`, `CLAUDE.md`) → 5 goulots identifiés (voir Partie 0 de
   `docs/TECH_WATCH.md`) : zéro accès distant/mobile, confirmation WRITE bloquante même en
   `--wake`, TTS non streamé phrase-par-phrase, un seul tool_call traité par step, pas de
   routage pré-LLM.
2. **Contre-vérification complète de la veille sept. 2026** (5 recherches web indépendantes) :
   Edge0/Audio8 (corrigés, alerte licence Audio8), Qwen3.8 (existe réellement — surprise —
   mais rien d'exploitable en local), jarvis-assistant-vocal (N1/N2/N3 confirmé réel, leur
   TTS streaming = roadmap PAS fait), stack « Patki » et repo `he-jev/laya` (d'abord jugés
   fictifs à tort, confirmés réels après re-vérification directe demandée par l'utilisateur —
   **leçon : toujours tester l'URL/API en direct, pas juste résumer une recherche**).
3. **Tour d'horizon complémentaire 2026** : Granite 4.1, Kyutai STT/TTS, whisper.cpp
   (dépassé en local dès le départ), LocalVox (vrai patron TTS streaming implémenté),
   Wyoming/wyoming-satellite, semantic-router, alternatives Jev/Laya (Von, openJev-verdict-2.0…).
4. **Implémenté et committé (v12.6)** : `classify_command` scindé N1(read)/N2(write)/N3(critical)
   + flag `IS_REMOTE` (N3 jamais confirmable à distance), VAD native whisper.cpp branchée dans
   `_whisper_server_start()`, packaging minimal (`matata/`, `pyproject.toml`, `LICENSE` MIT).
5. **Tests empiriques hands-on** (venv `/tmp/von_test_venv/`, hors repo) :
   - **Von** (CPU, anglais-only) : 71.4% FR / 100% EN, erreurs FR **surconfiantes** → écarté pour le FR.
   - **Laya `multilingual` forcé** : 85.7% FR sur 11 requêtes, puis **80.0%** sur un jeu élargi
     de 40 → prometteur mais PAS prêt à intégrer (voir « reste à faire »).
   - **Laya sur iGPU réel (`device='xpu'`), 03/10/2026** : **BLOQUANT**. Hang indéfini au premier
     kernel GPU (juste après l'init oneDNN/SYCL), reproduit 3x dont une fois après nettoyage
     complet des process. Stack driver vérifiée saine (xe kernel driver, Level-Zero/OpenCL à jour) ;
     hypothèse = combo `torch 2.14+xpu`/`oneDNN 3.12.3` pas encore mature sur iGPU client Meteor
     Lake. **Rester sur Laya CPU** (130-155ms/décision) — détail complet dans `docs/TECH_WATCH.md`.
     **2 contournements driver testés (même session), aucun effet** : désactivation des
     "immediate command lists" Level-Zero, puis désactivation du copy engine + hiérarchie
     device composite — même blocage exact dans les deux cas. Le problème est plus profond
     qu'une option de soumission de queue ; pas d'autre contournement simple identifié.
   - **IBM Granite 4.1 (`granite4.1:8b`), 03/10/2026, ÉCARTÉ** : bench réel sur la suite 5-tests
     de `agent-pc/tests.sh` (iGPU Vulkan). 3/5 propre, mais **échec net de tool calling** sur le
     test musique (reproduit 2x — le modèle écrit la commande `run_shell` en texte brut au lieu
     de l'exécuter, l'utilisateur ne reçoit aucune réponse) et **une hallucination de comptage**
     sur le test séries (liste 7 éléments, conclut "il y a 8"). Moins fiable que qwen3:8b/
     qwen3.5:4b (5/5 chacun) malgré le FR officiel et le tool calling natif annoncés sur le papier.
     Détail complet dans `docs/TECH_WATCH.md`.
6. Corrections de cohérence dans `docs/TECH_WATCH.md` (références de lignes `agent.py`,
   whisper.cpp, tailles modèles) et dans README/AGENTS.md/CLAUDE.md.
7. **Code LocalVox lu puis patron implémenté dans `agent.py` (03/10/2026, v12.7)** — streaming
   TTS phrase-par-phrase : regex `(?<=[.!?])\s+` sur le buffer de tokens streamés, parle chaque
   phrase dès qu'elle est complète au lieu d'attendre la réponse entière. Leur limite connue
   (Piper en subprocess par phrase) était déjà résolue en mieux côté MATATA (API Piper in-process
   v12.4). Risque imprévu détecté et traité pendant l'implémentation (interaction avec
   `INCOMPLETE_PATTERNS`/retry silencieux, voir « Reste à faire » et `docs/TECH_WATCH.md`).
   Validé : test simulé 3 scénarios + `tests.sh` 5/5 inchangé. Commité et poussé (`9ba8772`).
8. **`aurelio-labs/semantic-router` testé (03/10/2026)**, même jeu de 40 requêtes que Laya :
   **90.0% accuracy / 12.4ms moyenne**, contre 80.0% / 130-155ms pour Laya CPU — devient le
   **nouveau meilleur candidat** pour le routage pré-LLM. Encodeur par défaut anglais-only
   (`all-MiniLM-L6-v2`), remplacé par `paraphrase-multilingual-MiniLM-L12-v2` pour le FR (même
   piège que Laya : toujours vérifier/forcer la config multilingue). Détail complet dans
   `docs/TECH_WATCH.md`.
9. **Jeu élargi à 100 requêtes (03/10/2026, même après-midi)** — leçon importante : avec les
   utterances initiales trop étroites (~10-12/route), l'accuracy **chute à 61.0%** (file_search
   à 36.1%) — cause : `score_threshold=0.5` par défaut rejette (`None`) tout score trop bas, et
   le vocabulaire des nouvelles requêtes (Word/Excel/zip/screenshots) était absent des
   utterances v1. Après **enrichissement à 123 utterances** (vérifié zéro doublon avec le jeu de
   test, sinon retest invalide) : **accuracy remonte à 92.0%**, file_search repasse à 91.7%.
   **Test croisé Laya sur ce même jeu de 100** (comparaison enfin équitable) : Laya fait
   **84.0% / 216.4ms**, contre **92.0% / 30.2ms pour semantic-router** — semantic-router gagne
   sur 3 catégories/4, l'accuracy globale et la latence (~7x). Laya reste meilleur uniquement
   sur `greeting` (100% vs 81.8%). Détail complet (tableau comparatif) dans `docs/TECH_WATCH.md`.
10. **FastEmbed évalué (03/10/2026)** — même modèle exact (`paraphrase-multilingual-MiniLM-L12-v2`)
    en ONNX (FastEmbed) vs PyTorch (HuggingFaceEncoder) : **accuracy identique (92.0%, mêmes 8
    erreurs)**, mais **5.2ms** (~6x plus rapide) et **222Mo de dépendances** (~27x plus léger que
    les ~6Go torch, vérifié avec un venv neuf). FastEmbed confirmé comme encodeur par défaut
    recommandé pour une intégration MATATA — zéro compromis qualité, gain net latence/poids.

## Décisions prises

- Licence repo : **MIT**. Dépendance GPL-3 connue : `piper-tts` (pas de conflit en usage perso,
  à trancher si diffusion publique élargie).
- Point d'entrée : console **`matata`** / `python3 -m matata` (wrapper fin, `agent.py` reste
  single-file pour l'instant, mais ce n'est plus une contrainte — voir ci-dessous).
- N1/N2/N3 : implémenté. N3 jamais confirmable à distance tant que `IS_REMOTE` reste à `False`.
- Modèle LLM : **rester sur `qwen3:8b`/`qwen3.5:4b`** — rien dans la famille Qwen3.8 n'est
  exploitable sur notre iGPU (cloud-only ou trop lourd), et Granite 4.1:8b écarté (tool calling
  moins fiable, voir ci-dessus).
- Routage pré-LLM : **intégré dans `agent.py` en v12.8** (04/10/2026) — réimplémentation à la
  main de l'approche FastEmbed (pas le package `semantic-router`, contrainte ZERO framework).
  Fast-path limité à `greeting`/`time_date` (score ≥ 0.5) ; `system_stats`/`file_search` restent
  toujours gérés par le LLM (le routeur classe, n'extrait pas les arguments). Escape hatch
  `MATATA_ROUTER=0`. Détail dans `docs/TECH_WATCH.md`.
- **Gouvernance des contraintes révisée (04/10/2026)**, suite à une analyse du projet
  (croissance d'`agent.py`, backlog Phase 1 web/Wyoming, historique des décisions
  framework/hand-roll) :
  - **Single-file abandonné** — pure contrainte organisationnelle, aucun bénéfice concret,
    coûtait de plus en plus cher à mesure qu'`agent.py` grossissait (~1250 lignes v12.8).
  - **« ZERO frameworks » remplacé par la règle déjà écrite** (était dans `docs/TECH_WATCH.md`
    seul, gitignoré — maintenant dans `AGENTS.md`/`CLAUDE.md`, suivis par git) : **externe/
    frameworks acceptés SEULEMENT si (a) 100% gratuits ET (b) gain réel mesuré — jamais un
    prérequis**, décidé au cas par cas, jamais par principe seul. L'analyse a montré que la
    version absolue avait fait rejeter `smolagents` (1000 lignes, MCP natif, 26k★, actif) **sans
    aucun test empirique**, alors que Laya et semantic-router, eux, avaient été testés puis
    remplacés par un hand-roll une fois le gain mesuré — la règle nuancée était déjà la bonne,
    juste mal appliquée faute d'être au bon endroit.
  - **Nouveau principe : modularité** — toute fonctionnalité doit pouvoir être désactivée
    (flag/env var) sans rien casser, et son implémentation remplacée sans réécrire le reste.
    Patron déjà suivi (`MATATA_ROUTER`, `MATATA_WHISPER_VAD`, `MATATA_MODEL`, `VOICE_LANG`), à
    présent formalisé comme principe à maintenir systématiquement.
- **`smolagents` testé empiriquement (04/10/2026), ÉCARTÉ** — premier test sous la nouvelle
  règle de gouvernance, pour vérifier qu'elle marche en pratique. `ToolCallingAgent` (pas
  `CodeAgent`, dont le `LocalPythonExecutor` n'est explicitement pas une frontière de sécurité —
  incompatible avec le N1/N2/N3) câblé sur `ollama_chat/qwen3:8b`, avec les 3 vrais tools
  d'`agent.py` réutilisés tels quels (mêmes fonctions, même garde-fou). Sur les 5 requêtes
  canoniques de `tests.sh` : **~19 minutes au total contre ~2 minutes pour `agent.py`**
  (routeur désactivé) — 2 des 5 réponses carrément fausses (heure en UTC/anglais au lieu de
  l'heure locale FR, "5 series found" sans rapport avec la vraie question) et 1 échec total
  (634s puis abandon sur la requête musique). Cause : la couche de prompting/orchestration de
  smolagents casse la synergie prompt-système/`classify_command` affinée sur 12 versions (ex.
  ses commandes contiennent `2>/dev/null`, ce qui déclenche notre règle "`>` littéral = write").
  **Exactement le test que la nouvelle règle appelait — gain mesuré négatif et net, pas un rejet
  de principe.** Détail complet (tableau, causes) dans `docs/TECH_WATCH.md`.

## Reste à faire (dans l'ordre)

1. **Retravailler les critères de questions Laya** pour lever la confusion `file_search`↔`system_stats`
   découverte sur le jeu de 40 requêtes, avant toute intégration.
2. **Ne pas se fier au seuil de confiance seul** comme filet de sécurité (25% des erreurs restent
   surconfiantes à 40 requêtes) — prévoir un fallback qwen3:8b systématique en cas d'ambiguïté.
   **Laya tournera sur CPU uniquement** — l'iGPU (`device='xpu'`) est bloquant (voir ci-dessus),
   donc la latence de référence à garder est bien ~130-155ms/décision CPU, pas d'amélioration GPU
   à attendre tant que la stack torch+xpu/oneDNN n'a pas mûri sur Arc client.
3. ~~Lire le code LocalVox~~ ~~Implémenter dans agent.py~~ **[FAIT 03/10, v12.7]** — streaming
   TTS phrase-par-phrase implémenté (`SPEAK_SENTENCE_RE`, agent.py). Risque supplémentaire trouvé
   pendant l'implémentation et traité : `INCOMPLETE_PATTERNS` (détecte les réponses "je vais
   chercher..." qui décrivent sans agir) ne se vérifiait qu'en fin de stream — un flag
   `safe_to_speak` coupe désormais la parole en direct dès que ce pattern apparaît, pour ne
   jamais parler une phrase vouée à un retry silencieux. Validé par un test simulé (3 scénarios :
   multi-phrases normal, tool_calls présent, pattern incomplet épuisant les retries) + suite
   `tests.sh` complète (5/5 inchangé, texte qwen3:8b). Détail complet dans `docs/TECH_WATCH.md`.
   Portée limitée à la branche principale, pas à la branche retry-sans-tools (plus rare).
4. Bench `qwen3.8:27b` (attentes tempérées, probable verdict « trop lourd »).
5. ~~Tester `aurelio-labs/semantic-router`~~ ~~Tester sur un jeu plus large~~ ~~Test croisé Laya~~
   ~~Évaluer FastEmbed~~ ~~Décider fallback + intégrer~~ **[FAIT 04/10/2026, v12.8 — TERMINÉ]** —
   après enrichissement des utterances (123, zéro fuite test), test croisé équitable sur 100
   requêtes (**92.0%/30.2ms** contre **84.0%/216.4ms pour Laya**) et évaluation FastEmbed (même
   accuracy, **5.2ms**, **222Mo**) : **câblé dans `agent.py`** via `route_intent()`, réimplémenté
   à la main (FastEmbed + top-5/moyenne-par-route, ~60 lignes) — **pas** le package
   `semantic-router` (contrainte ZERO framework/single-file). **Stratégie de fallback** : seuls
   `greeting`/`time_date` court-circuitent le LLM (score ≥ 0.5) ; `system_stats`/`file_search`
   passent **toujours** par le LLM inchangé (le routeur classe mais n'extrait aucun argument de
   commande). Escape hatch `MATATA_ROUTER=0`. Gain mesuré : "Hi"/"Quelle heure ?" de ~12-22s à
   **~0.03s** (~400-700x). Validé : `tests.sh` 5/5 + `test_fixes.py` OK. Risque résiduel connu :
   4% de réponses fausses-mais-inoffensives sur le jeu de 100 (jamais d'action destructrice).
   Détail complet dans `docs/TECH_WATCH.md`.
6. P1 : ~~Granite 4.1~~ **[FAIT 03/10, écarté]**, Kyutai STT/TTS, Wyoming/wyoming-satellite
   (panneau mobile Phase 1).
7. ~~`IS_REMOTE` reste à `False`~~ **[FAIT 04/10/2026, v12.9]** — premier canal distant câblé :
   mode `--serve`, serveur HTTP minimal stdlib (`http.server`, zéro nouvelle dépendance),
   `GET /health` / `POST /chat` / `POST /reset`, auth optionnelle par token
   (`MATATA_SERVE_TOKEN`). Met `IS_REMOTE=True` pour toute la durée du process — **N2 (write)
   est maintenant aussi refusé à distance, pas seulement N3** (bug trouvé en implémentant :
   N2 serait tombé sur un `input()` bloquant sans TTY, gelant la requête HTTP ; décision
   utilisateur : bloquer N2/N3 à distance pour l'instant, pas de confirmation asynchrone
   distante dans cette première version). Testé réellement : `mkdir` envoyé via `/chat` →
   refusé, rien créé. Reste à faire : exposer via Tailscale (déjà actif, juste changer
   `MATATA_SERVE_HOST`), une UI (actuellement API seule, décision utilisateur), et une vraie
   confirmation distante si jamais N2/N3 à distance devient nécessaire.
   **Bug collatéral trouvé et corrigé** : le routeur pré-LLM (v12.8) pouvait bloquer
   indéfiniment au chargement (deadlock `onnxruntime` sur cette machine 22 cœurs, 60 threads
   coincés) — corrigé en fixant `threads=4` (`MATATA_ROUTER_THREADS`) au lieu de l'auto-détection.
   Risque présent aussi en CLI normal (pas seulement `--serve`), juste jamais déclenché avant.
   Détail complet (dont la leçon sur le faux diagnostic de blocage via `ps`/stdout bufferisé)
   dans `docs/TECH_WATCH.md`.
8. **Interface PC ajoutée (04/10/2026, v12.10)** — `GET /` sert `web/index.html` (page statique
   autonome, CSS/JS inline, zéro dépendance) : champ texte + bouton micro + bulles de
   conversation. **Web app choisie plutôt que native** (décision prise avec l'utilisateur) :
   même page+API réutilisable pour la Phase 1 mobile via Tailscale, pas de second dev par
   plateforme. Micro : `MediaRecorder` navigateur → `POST /voice` (nouveau) → `ffmpeg` → WAV
   16kHz → `transcribe_audio()` existant (whisper.cpp, zéro nouveau code STT). Réponse texte
   uniquement (décision utilisateur : plus simple pour cette version, voix en sortie plus tard
   si besoin). `--serve` démarre maintenant aussi `whisper-server`. Validé : `curl /voice` avec
   un clip Piper→webm synthétique (transcription + routage + réponse corrects), rendu vérifié
   par captures d'écran Chrome headless (état vide + conversation peuplée, 4 styles de bulles).
   `tests.sh` 5/5 + `test_fixes.py` inchangés. Détail complet dans `docs/TECH_WATCH.md`.
9. **Retouches `--serve` (04/10/2026, v12.11-v12.12)** — demandées par l'utilisateur :
   - **Convention API** : endpoints POST avec `/` final (`/chat/`, `/voice/`, `/reset/`), GET
     sans. Réponse toujours `{"ok", "message", "data"}` ; erreur → `message` = toujours
     `"An error occurred"` (littéral), détail dans `data.error`.
   - **Tout le périmètre `--serve`/web en anglais** (page + messages API) — portée limitée à ce
     code, le reste d'`agent.py` (prints, réponses du LLM en français) inchangé.
   - **Refonte visuelle** (retour : "pas cool") : icônes SVG, dégradé indigo, indicateur de
     frappe animé, état vide accueillant, mise en page carte sur grand écran.
   - Leçon notée : `--screenshot` headless peut capturer en pleine animation (rendu délavé
     trompeur) — ajouter `--virtual-time-budget=2000` pour les prochaines vérifications visuelles.
   - `tests.sh` 5/5 + `test_fixes.py` inchangés. Détail complet dans `docs/TECH_WATCH.md`.
10. **Vrai bug trouvé par l'utilisateur en testant `--serve` (04/10/2026, v12.13)** — "Qui
    es-tu ?"/"Que peux-tu faire ?"/"Comment ça marche ?" étaient classées `greeting` et
    recevaient une réponse en boîte au lieu d'une vraie réponse LLM. **Cause vérifiée** : ces
    questions scorent 0.52-0.66 pour `greeting`, plage qui chevauche entièrement les vrais
    saluts (0.51-0.97) — aucun seuil ne pouvait séparer les deux, car aucune des 4 catégories ne
    représentait "question sur l'agent". **Corrigé** en ajoutant une 5ᵉ catégorie `'other'`
    (~30 utterances identité/capacité/méta, jamais fast-pathée) — zéro changement de code
    ailleurs, juste des utterances en plus. Revérifié : les questions qui plantaient atteignent
    maintenant le LLM (score 0.79-0.98 pour `other`). 24 requêtes de contrôle sur les 5
    catégories : 100%, aucune régression. `tests.sh` 5/5 + `test_fixes.py` inchangés. **Leçon
    importante** : le banc de 100 requêtes qui a validé le routeur ne contenait aucun cas
    hors-domaine — seulement des variantes des 4 intentions connues — ce trou n'a donc été
    découvert qu'à l'usage réel. Détail complet dans `docs/TECH_WATCH.md`.
11. **Streaming SSE réel ajouté (04/10/2026, v12.14)** — 3 problèmes remontés par l'utilisateur
    en testant l'interface, tous causés par la même racine : `/chat/`/`/voice/` attendaient la
    fin complète du tour avant de répondre, donc pas d'effet de frappe visible côté web, le
    micro semblait lent (transcription affichée seulement avec la réponse complète), et aucun
    temps affiché. **Corrigé** : `/chat/`/`/voice/` répondent maintenant en Server-Sent Events —
    événements `token` en direct (réutilise le filtre `tool_calls is None` déjà validé pour le
    streaming TTS v12.7, via un hook `_STREAM_SINK` optionnel, no-op hors `--serve`), `transcript`
    dès la fin de la transcription whisper.cpp (avant même d'appeler le LLM), `done` final avec
    `data.elapsed_s` en plus du format habituel. Validé par `curl -N` (frames SSE correctes) et
    une vraie interaction Chrome headless pilotée en CDP (bulle capturée en train de grossir en
    plein milieu de génération, bouton d'envoi désactivé). `tests.sh` 5/5 + `test_fixes.py`
    inchangés. Détail complet dans `docs/TECH_WATCH.md`.
12. **`status` events + fix stdout flush (04/10/2026, v12.15)** — ferme le trou de réactivité
    laissé en suspens en v12.14 ("Fais-le maintenant") : pour un tour avec tool call, rien ne
    bougeait côté web pendant toute la fenêtre d'exécution de l'outil. **Corrigé** : nouvel
    événement SSE `status` émis via `_emit_stream()` à côté des `print()` terminal existants
    (`search_files`/`system_info`/`run_shell` avec le `reason` du modèle ou `Running: <cmd>` en
    secours) ; `web/index.html` affiche ce texte dans l'indicateur de frappe à côté des points
    animés. **Piège de debug rencontré en vérifiant** : `--serve` avec stdout redirigé vers un
    fichier semblait bloqué indéfiniment juste après "Chargement du routeur..." — plusieurs
    minutes passées à soupçonner une résurgence du deadlock onnxruntime de v12.9 (repros isolés
    de la même séquence systématiquement rapides, ~4s) avant que `faulthandler.dump_traceback_
    later(15, exit=True)` ne prouve que le process était déjà dans `httpd.serve_forever()`, pas
    bloqué du tout. Cause réelle : `print()` sans `flush=True` sur 3 lignes juste avant
    `serve_forever()` — inoffensif sur un vrai terminal (line-buffered) mais retenu en plein
    buffer dès que stdout est un pipe/fichier (exactement le cas `--serve` en service). Corrigé.
    Pas un bug fonctionnel (chaque requête était déjà bien servie pendant ce temps) mais un vrai
    piège d'exploitation. Validé par `curl -N` (séquence `status`→`token`×N→`done` correcte) +
    `tests.sh` 5/5 + `test_fixes.py` inchangés. Détail complet dans `docs/TECH_WATCH.md`.
13. **Recherche web multi-axes + contre-vérification + `web_search` tool (04/10/2026, v12.16)**
    — l'utilisateur a demandé une recherche sur 5 axes jamais explorés (multimodal, websearch,
    phone, plus de tools, speak realtime), menée via 5 sub-agents en parallèle, puis une 2e passe
    de contre-vérification (2 sub-agents : un relit `agent.py` ligne par ligne, un re-vérifie aux
    sources primaires les affirmations les plus critiques — même rigueur que l'incident "Bhargav
    Patki"). **Correction majeure trouvée** : le candidat multimodal recommandé (`qwen2.5vl:7b`)
    ne supporte PAS le tool calling natif Ollama — `qwen3-vl:8b-instruct` est le bon candidat
    (vision+tools+thinking simultanés). **Priorités revues après lecture du vrai code** : PWA
    confirmée triviale, websearch a un bug de dispatch à corriger en plus de l'ajout, le barge-in
    VAD est repoussé en dernier (le vrai obstacle est l'architecture micro/speak, pas le VAD).
    **Implémenté** : `web_search` (4e tool, via `ddgs`/DuckDuckGo) — mais d'abord un vrai re-test
    empirique du plafond "3 tools" (toujours débattu, jamais vérifié depuis le début du projet) :
    confirmé réel sur notre config précise (1 échec de dispatch + détours non pertinents sur le
    test le plus dur avec 4 tools toujours présents, vs 3/3 fiable avec 3 tools). **Au lieu
    d'abandonner le 4e tool** : sélection dynamique réutilisant le routeur existant (v12.8) —
    `web_search` n'est ajouté à la liste de tools Ollama que pour les tours classés `web_search`
    par le routeur, 100% des autres tours gardent exactement le comportement 3-tools déjà prouvé
    fiable. Bug de dispatch dupliqué trouvé et corrigé au passage (chemin retry-sans-tools sans
    `else` de secours). `tests.sh` 5/5 (test 5 redevenu fiable), `test_fixes.py` inchangé, test
    end-to-end réel de `web_search` (météo, Ballon d'Or) — mécaniquement correct, mais une
    hallucination observée sur la réponse finale (limite connue, pas un bug d'intégration).
    Détail complet dans `docs/TECH_WATCH.md` (Partie 1ter + contre-vérification) et
    `agent-pc/CLAUDE.md` (v12.16).
14. **3 bugs réels trouvés via usage réel de `--serve` (04/10/2026, v12.17)** — l'utilisateur a
    collé une session réelle (terminal + UI web) utilisée en production, pas un test scripté.
    Analyse puis correction de 3 bugs, chacun reproduit isolément avant d'être corrigé :
    (1) **gel du serveur entier** — une commande hallucinée (`newslookup`) classée `'unknown'`
    tombait sur un `input()` bloquant le terminal du process `--serve`, jamais couvert par le
    refus `IS_REMOTE` (qui ne gérait que `critical`/`write`) ; comme `handle_turn()` tourne sous
    le `lock` partagé de `run_server()`, ça gelait TOUTES les requêtes suivantes. Corrigé :
    `unknown` ajouté au tuple `IS_REMOTE`. (2) **mot-clé perdu sur wildcard en tête** —
    `name="*.mkv"` donnait un mot-clé vide (`re.split` traitait `*` comme séparateur) ; corrigé
    en retirant `*` de la classe de split. (3) **`~` jamais expansé dans `search_dir`** —
    `shlex.quote('~/Downloads')` empêche le shell d'expanser le `~`, causant un faux "No such
    file or directory" alors que le dossier existe ; corrigé avec `os.path.expanduser()` avant
    `shlex.quote()`. Les 3 fixes revérifiés avec le cas exact de la session réelle (résolu en 1
    étape/27s au lieu de 5 étapes/71s pour le bug #3). `tests.sh` 5/5, `test_fixes.py` inchangé.
    Repéré mais pas corrigé (gap UX, pas un bug) : le chat web n'a pas d'équivalent aux commandes
    spéciales du CLI (`reset`/`quit`) — `/clear`/`exit`/`bye` tapés dans le navigateur sont juste
    du texte normal, mal classés par le routeur, et ne réinitialisent rien côté serveur. Détail
    complet dans `agent-pc/CLAUDE.md` (v12.17).
15. **Auto-audit suite à "vérifie bien il semble avoir toujours des incohérence" (même jour,
    toujours v12.17)** — demande justifiée : relecture critique du code + des docs (pas une
    nouvelle session utilisateur) a trouvé 4 incohérences de plus : (a) la bannière CLI listant
    les tools (dont `web_search`) ne s'affichait **jamais en mode `--serve`** (le `return` du
    bloc SERVE intervient avant ce `print()`) — corrigé en l'affichant aussi côté `--serve` ;
    (b) le message de démarrage `--serve` disait encore "N2/N3 toujours refusés à distance",
    devenu imprécis après le fix `unknown` — corrigé ; (c) `AGENTS.md` disait encore "5
    categories" pour le routeur, oubliant `web_search` (6e, ajoutée en v12.16) — corrigé ; (d)
    **vrai bug** : `run_server()` n'était pas protégé par un `try/finally` — si le bind échoue
    (port déjà pris, reproduit en occupant volontairement le port 8765 avant de lancer
    `--serve`), `_whisper_server_stop()` n'était jamais appelé, laissant whisper-server orphelin
    sur le port 18080. Corrigé, reproduit avant/après pour confirmer (orphelin présent avant le
    fix, absent après). Leçon retenue : une session réelle collée par l'utilisateur et un
    auto-audit après coup trouvent des classes de bugs différentes (comportement déclenché à
    l'usage vs code mort/dérive doc-code) — les deux passes sont utiles, aucune ne remplace
    l'autre. Détail complet dans `agent-pc/CLAUDE.md` (v12.17).

## Fichiers concernés

| Fichier | Rôle |
|---|---|
| `agent-pc/agent.py` | Cœur v12.6 (N1/N2/N3, VAD native) |
| `agent-pc/test_fixes.py` | Tests N1/N2/N3 ajoutés |
| `agent-pc/CLAUDE.md`, `AGENTS.md` | Docs archi, à jour |
| `README.md`, `LICENSE`, `pyproject.toml`, `matata/` | Packaging open-source (nouveau) |
| `docs/TECH_WATCH.md` | **Gitignoré** — veille complète (Partie 0 constat interne / Partie 1 notes corrigées / Partie 1bis tour d'horizon / Partie 2 backlog P0-P1-P2) |
| `/tmp/von_test_venv/` | Venv de test éphémère (torch CPU, laya, von-sdk) + scripts `test_*.py` + résultats JSON — **hors repo, scratch**, à recréer ou nettoyer |
| scratchpad session Claude (`laya_igpu_venv/`) | Venv éphémère du test iGPU 03/10 (`torch==2.14.1+xpu`, `laya==0.3.26`) + `diag_laya.py` — **hors repo, scratch session**, nettoyé automatiquement, à recréer si besoin de creuser le hang |
| scratchpad session Claude (`LocalVox/`) | Clone complet du repo `YaPanBytes/LocalVox` (121Mo, via tarball) lu pour extraire le patron streaming TTS — **hors repo, scratch session**, nettoyé automatiquement, à retélécharger si besoin de relire le code |
| scratchpad session Claude (`semrouter_venv/`) | Venv du test semantic-router 03/10 (`semantic-router==0.1.2[local]` + `fastembed==0.8.1`, ~6Go au total car les deux encodeurs y cohabitent) + scripts `test_semrouter_40.py`/`test_semrouter_100.py`/`test_semrouter_100_v2.py`/`test_fastembed_100.py`, `semrouter_routes_data.py` (v1, 42 utterances), `semrouter_routes_data_v2.py` (v2, 123 utterances enrichies), `large_queries.py` (jeu de 100 requêtes) — **hors repo, scratch session**, nettoyé automatiquement, à recréer si besoin de pousser le test plus loin |
| scratchpad session Claude (`fastembed_only_venv/`) | Venv isolé (`pip install fastembed` seul, sans semantic-router/torch) pour vérifier le vrai poids de FastEmbed en conditions réelles — **222Mo confirmé**, zéro torch/transformers — **hors repo, scratch session**, nettoyé automatiquement |
| scratchpad session Claude (`laya_igpu_venv/`, réutilisé) | Même venv que le test iGPU, réutilisé en CPU (`device='cpu'`) pour le test croisé 100 requêtes vs semantic-router — script `test_laya_100.py` |

## Points ouverts / à vérifier

- URL du repo GitHub corrigée en `Root-SKB/MATATA-v1` — vérifier que c'est bien le bon remote.
- Rate-limiting anonyme HuggingFace rencontré 2 fois pendant les tests (~2-3h chacun sans
  `HF_TOKEN`) — en configurer un avant tout nouveau test de modèle hébergé HF.
- RAM disponible mesurée à ~9 Gio en fin de session (vs 17 Gio noté avant) — confirmé être de la
  charge desktop (navigateurs/Slack), pas une régression MATATA, mais à re-vérifier à froid.
- **Hang Laya iGPU (03/10) toujours pas résolu** — 2 contournements driver Level-Zero testés
  sans effet (immediate command lists, copy engine) ; pas de message d'erreur explicite obtenu
  (juste un blocage silencieux), pas essayé de downgrade torch/oneDNN pour isoler la version
  fautive, pas de rapport ouvert côté `convaiinnovations/laya` ou `pytorch/pytorch` (XPU backend)
  — downgrade de version = prochaine piste la plus sérieuse si on reprend ce chantier.
- **Session de bureau relancée en cours de session (03/10, ~21h34)** — un bench en arrière-plan
  attaché à la session de login a été tué net par un logout/relogin GDM (confirmé via
  `journalctl -u systemd-logind` : "Session 2 logged out" suivi d'une nouvelle session quelques
  secondes après). Pas de reboot (uptime continu), pas de trace thermique/OOM/panic dans dmesg —
  cause du logout non identifiée plus précisément. **Mitigation appliquée** : lancer les process
  longs via `setsid nohup ... & disown` pour les détacher de la session et survivre à un futur
  logout. À surveiller si ça se reproduit souvent.
- Un doublon de notification `ScheduleWakeup` observé en session (même prompt reçu 3x) — déjà
  signalé via feedback interne, pas bloquant.

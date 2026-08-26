# MATATA — Mon Assistant Technique Autonome et Totalement Accessible

## Description

**MATATA** est un écosystème IA local pour Ubuntu, **100% offline**, basé sur **Qwen3 8B** via **Ollama**. C'est une approche minimaliste et pure Python (zéro framework) pour créer un assistant intelligent capable d'exécuter des commandes shell, chercher des fichiers, rapporter des infos système, et interagir par la voix (STT/TTS + wake word "MATATA").

### Caractéristiques

- ✅ **Offline complet** — Pas d'appels API externes, tout local
- ✅ **Minimaliste** — Pure Python + Ollama SDK, zéro LangChain/CrewAI
- ✅ **Tool calling natif** — Ollama support natif, pas d'hacks
- ✅ **Multi-langue** — Français par défaut, extensible
- ✅ **Safety-first** — Whitelist stricte pour les commandes shell
- ✅ **Logs & backups** — Historique complet, backups auto avant modifs
- ✅ **Voix** — Push-to-talk + wake word "MATATA" mains libres
- ✅ **Streaming** — Premier token visible en ~1-2s (stream=True)

---

## État Actuel

🟢 **Phase 1 + Phase 2 TERMINÉES** — Agent PC stable, Voice + Wake Word opérationnels

---

## Les 5 Phases du Projet

### Phase 1: Agent PC ✅ FAIT
**Agent terminal local avec tool calling natif (3 outils)**

- `run_shell` — Exécute des commandes (whitelist + tool calling)
- `search_files` — Cherche des fichiers par nom
- `system_info` — Rapporte RAM, disque, CPU, processus

Fixes appliqués:
- Bug 1: Deduplication des commandes (évite les boucles infinies)
- Bug 2: Limite de longueur (200 chars max, force simplicité)
- Bug 3: Truncate output (évite les hallucinations)
- Few-shot examples (aide le modèle avec des patterns)

### Phase 2: Voice ✅ FAIT (v12.4)
**STT + TTS + Wake Word — 100% local**

- `python3 agent-pc/agent.py --voice` : push-to-talk (Entrée → parle → Entrée)
- `python3 agent-pc/agent.py --wake` : mains libres avec mot-clé "MATATA"
- STT : whisper.cpp small (~466 Mo) — whisper-server persistant (modèle chargé 1×, fallback CLI)
- TTS : Piper Python API in-process — bilingue fr_FR-siwis + en_US-lessac, lazy-load (1,1s), synthèse 0,1s
- LLM : stream=True — premier token visible en ~1-2s (plus d'attente muette)
- Wake word : openwakeword (matata.onnx R3) + confirmation whisper — double barrière anti-faux positifs
- Langues : français par défaut, `langue fr|en|auto` en session
- Réponses dans la langue de l'utilisateur, lues avec la voix correspondante

### Phase 3: MATATA Core 🔮 À VENIR
**Orchestrateur qui connecte Voice + Agent PC**

- Dispatcher utilisateur vers le bon module
- Gestion d'état multi-tours
- Logs centralisés

### Phase 4: Cloud Mentor 🔮 À VENIR
**API Groq gratuite pour les questions complexes**

- Fallback vers Groq pour questions hors-scope (IA, code complexe)
- Limite de tokens / rate limit respectés
- Cache local pour réduire appels

### Phase 5: MCP Tools 🔮 À VENIR
**Serveur MCP pour connecter des outils externes**

- Intégration avec outils tiers (APIs, BD, etc.)
- Protocole standardisé

---

## Installation

### Prérequis

- **Ubuntu 24.04** (testé)
- **Ollama v0.32+ en service systemd** (installer via https://ollama.ai)
- **Python 3.10+**
- **32GB RAM minimum**

### Setup

```bash
# 1. Clone le repo
git clone https://github.com/Root-SKB/MATATA.git
cd MATATA

# 2. Crée virtualenv
python3 -m venv venv
source venv/bin/activate

# 3. Install deps
pip install -r agent-pc/requirements.txt

# 4. Lance l'agent
python agent-pc/agent.py --timer
# Variante modèle léger :
MATATA_MODEL=qwen3.5:4b python agent-pc/agent.py --timer
```

> ⚠️ Les modèles vivent dans `/usr/share/ollama/.ollama/models` (service systemd).
> Ne lance pas `ollama serve` manuellement : il regarderait `~/.ollama` (vide) et re-téléchargerait les modèles.
> Si l'API ne répond pas : `sudo systemctl start ollama`.

### Vérification

```bash
# Test: Quelle est la taille du RAM et du disque ?
# Résultat attendu: RAM 30GB, Disk 468GB (ou vos chiffres)

# Test: J'ai combien de musique ?
# Résultat attendu: Cherche ~/Music, compte les fichiers

# Test: Quelle heure ?
# Résultat attendu: Date et heure actuelles
```

---

## Architecture

```
MATATA/
├── agent-pc/               # Phase 1: Agent PC + Phase 2: Voice
│   ├── agent.py            # Main agent (tool calling + voice + wake, ~950 lines)
│   ├── CLAUDE.md           # Architecture & constraints
│   ├── requirements.txt     # Dependencies (ollama)
│   ├── test_fixes.py        # Unit tests
│   └── tests.sh            # Integration test suite
├── voice/                    # Voice models & training
│   ├── models/               # ggml-small.bin + piper onnx + matata.onnx (gitignored)
│   ├── training/             # Colab notebooks R1→R3 (committed)
│   └── whisper.cpp/          # whisper.cpp build (gitignored)
├── core/                   # Phase 3: MATATA Core (placeholder)
├── cloud/                  # Phase 4: Cloud Mentor (placeholder)
├── mcp/                    # Phase 5: MCP Tools (placeholder)
├── venv/                   # Python virtualenv (gitignored)
├── README.md               # This file
├── .gitignore
└── CONTRIBUTING.md         # (futur)
```

---

## Spécifications

| Composant | Valeur |
|-----------|--------|
| **OS** | Ubuntu 24.04, kernel 6.17.0-1007-oem |
| **CPU** | Intel Core Ultra 7 155H (22 threads, 16 cores) |
| **RAM** | 32GB DDR5-5600 |
| **Disk** | 512GB NVMe (195GB free) |
| **GPU** | Intel Arc iGPU (MTL) — utilisé via Vulkan (`OLLAMA_IGPU_ENABLE=1`), 100% offload |
| **Model** | Qwen3 8B (défaut) ou Qwen3.5 4B via `MATATA_MODEL` |
| **Inference** | ~6-10 tok/s iGPU, ~9-50s per turn (vs 31-600s CPU-only avant) |
| **Ollama** | v0.32.15, service systemd, native tool calling |

---

## Commandes Utiles

### Lancer l'agent

```bash
source venv/bin/activate
python agent-pc/agent.py --timer        # texte seul
python agent-pc/agent.py --voice        # push-to-talk
python agent-pc/agent.py --wake         # mains libres (mot-clé MATATA)
```

### Lancer les tests

```bash
source venv/bin/activate
bash agent-pc/tests.sh
```

### Tester la logique des fixes

```bash
source venv/bin/activate
python agent-pc/test_fixes.py
```

### Logs

```bash
cat ~/.agent-pc-backups/session_*.log
```

---

## Guardrails & Safety

### Whitelist READ (auto-exécution)
```
ls, cat, find, du, free, df, ps, date, grep, wc, tail, head, etc.
```

### Whitelist WRITE (demande confirmation)
```
mkdir, cp, mv, touch, tee, chmod, chown, apt, pip, echo, sed
```

### BLOCKED (jamais)
```
rm, rmdir, shred, dd, mkfs, reboot, shutdown, poweroff, killall
```

### Auto-backup avant modifs
Avant `sed -i`, `tee >`, etc., backup auto à `~/.agent-pc-backups/`

---

## Bugs Connus & Fixes

### Bug 1: Model repeats broken command (539s loop)
**Fix**: Deduplication des 5 dernières commandes + feedback "Use DIFFERENT approach"

### Bug 2: Overly complex commands (800+ chars)
**Fix**: Rejette cmd >200 chars + message "Use simple commands"

### Bug 3: Tool output too long (slow inference)
**Fix**: Truncate ps output + "all" = ram+disk+cpu only

### Bug 4: Slow inference (31-629s per turn)
**Status**: ✅ Résolu en pratique — offload iGPU Vulkan (Ollama v0.32+, `OLLAMA_IGPU_ENABLE=1`)

### Bug 5: Model confuses music with video
**Status**: Few-shot examples help, mais pas 100%

---

## Roadmap

- [x] Optimiser inference ✅ (iGPU Vulkan : ×2 à ×10 selon requête)
- [x] Phase 2: Voice ✅ (STT + TTS + Wake Word, v12.4)
- [ ] Phase 3: MATATA Core (orchestrateur)
- [ ] Phase 4: Cloud Mentor (Groq fallback) ⚠️ conflit avec la contrainte ZERO-cloud — décision pending
- [ ] Phase 5: MCP Tools (serveur)
- [ ] Support d'autres langues
- [ ] Web UI (Gradio or Streamlit)
- [ ] Tests CI/CD (GitHub Actions)

---

## Contribution

Les PRs sont bienvenues! Pour contribuer:

1. Fork le repo
2. Crée une branche (`git checkout -b feature/votre-feature`)
3. Commit tes changements (`git commit -m 'Add feature'`)
4. Push (`git push origin feature/votre-feature`)
5. Ouvre une PR

---

## Licence

MIT — Free to use, modify, distribute

---

## Auteur

**Djimé Sacko** — sacko.djime@kabakoo.africa

Créé comme projet personnel d'IA accessible et offline-first pour Ubuntu.

---

## Notes

- **100% Offline**: Aucun appel API, tout exécuté localement
- **Minimaliste**: ~950 lignes Python, zéro frameworks lourds
- **Educational**: Code limpide, idéal pour apprendre tool calling & Ollama
- **Production-ready**: Logs, backups, safety guardrails

---

**Version**: 2.0.0 (Phase 1 + Phase 2 Voice)  
**Last Updated**: 2026-08-26  
**Status**: Stable ✅

#!/usr/bin/env python3
"""Agent PC v12.14 — 3 tools + voix push-to-talk + mains libres (--wake) + N1/N2/N3 + interface web (--serve)"""

import subprocess, shlex, re, time, sys, threading, os, json, signal, ollama, random
import urllib.request, io, uuid, wave
from collections import deque
from datetime import datetime

# === MODEL (overridable: MATATA_MODEL=qwen3.5:4b python3 agent.py) ===
MODEL = os.environ.get('MATATA_MODEL', 'qwen3:8b')
# think=False required for BOTH generations: qwen3 (issue #10976) and qwen3.5
# (default hybrid thinking eats the whole num_predict budget -> empty answers).
THINK_KW = {'think': False}
CHAT_OPTS = {'num_predict': 800, 'num_ctx': 4096, 'temperature': 0.3, 'num_thread': 16}
# repeat_penalty retiré (bench 2026-08-30 v12.5: 1.0 == 1.2 en fiabilité sur basiques +
# multi-step, zéro réponse vide/early-EOS; 1.2 historiquement pour éviter ~60% early-EOS
# après échec de tool, plus nécessaire). repeat_penalty inutilisé pour qwen3.5 (le casse).

# === VOICE (Phase 2 — optional --voice flag) ===
VOICE = False
VOICE_LANG = 'fr'  # fr | auto | en — 'fr' = direct, 'auto' = double passe fr+en (pour les code-switchers)
_VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'voice')
_WCPP = os.path.join(_VOICE_DIR, 'whisper.cpp')

def _pick_bin(rel, build_dirs=('build-vulkan', 'build')):
    """Sélectionne le binaire whisper par ordre de préférence (Vulkan d'abord).
    Chaque binaire (cli/server) est choisi indépendamment : si un seul build existe
    ou est valide, l'autre retombe sur le build CPU sans bloquer."""
    for d in build_dirs:
        p = os.path.join(_WCPP, d, 'bin', rel)
        if os.path.exists(p):
            return p
    return os.path.join(_WCPP, 'build', 'bin', rel)  # fallback ultime

WHISPER_BIN = _pick_bin('whisper-cli')
WHISPER_MODEL = os.path.join(_VOICE_DIR, 'models', 'ggml-small.bin')
VOICE_MODELS = {
    'fr': os.path.join(_VOICE_DIR, 'models', 'fr_FR-siwis-medium.onnx'),
    'en': os.path.join(_VOICE_DIR, 'models', 'en_US-lessac-medium.onnx'),
}
PIPER_MODEL = VOICE_MODELS['fr']
_WHISPER_SERVER_BIN = _pick_bin('whisper-server')
_WHISPER_SERVER_PORT = int(os.environ.get('MATATA_WHISPER_PORT', '18080'))
_WHISPER_SERVER_URL = f'http://127.0.0.1:{_WHISPER_SERVER_PORT}/inference'
_whisper_server_proc = None
_PIPER_RATES = {}
def _rate_for(model):
    if model not in _PIPER_RATES:
        try:
            _PIPER_RATES[model] = json.load(open(model + '.json'))['audio']['sample_rate']
        except Exception:
            _PIPER_RATES[model] = 22050
    return _PIPER_RATES[model]

def _whisper_server_start():
    """Lance whisper-server en arrière-plan. Retourne True si prêt."""
    global _whisper_server_proc
    if _whisper_server_proc and _whisper_server_proc.poll() is None:
        return True
    if not os.path.exists(_WHISPER_SERVER_BIN):
        return False
    try:
        # VAD native whisper.cpp (v1.8.4+, on est en v1.9.3-dev) : ~0.94s/passe conservé,
        # retire les silences avant transcription. Toggle : MATATA_WHISPER_VAD=0 pour désactiver.
        _whisper_cmd = [_WHISPER_SERVER_BIN, '-m', WHISPER_MODEL,
                        '--port', str(_WHISPER_SERVER_PORT),
                        '-t', '4', '--no-speech-thold', '0.6',
                        '--no-language-probabilities', '--audio-ctx', '0']
        if os.environ.get('MATATA_WHISPER_VAD', '1') != '0':
            _whisper_cmd.append('--vad')
        _whisper_server_proc = subprocess.Popen(
            _whisper_cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Attente du chargement du modèle (~3-5s la première fois)
        for _ in range(15):
            time.sleep(1)
            if _whisper_server_proc.poll() is not None:
                _whisper_server_proc = None
                return False
            try:
                urllib.request.urlopen(
                    f'http://127.0.0.1:{_WHISPER_SERVER_PORT}/', timeout=1)
                return True
            except Exception:
                pass
        return False
    except Exception:
        _whisper_server_proc = None
        return False

def _whisper_server_stop():
    global _whisper_server_proc
    if _whisper_server_proc:
        try:
            _whisper_server_proc.terminate()
            _whisper_server_proc.wait(timeout=3)
        except Exception:
            try: _whisper_server_proc.kill()
            except Exception: pass
        _whisper_server_proc = None

def record_audio(path='/tmp/matata_voice.wav', max_sec=12):
    p = subprocess.Popen(['arecord', '-f', 'S16_LE', '-r', '16000', path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('   \U0001f3a4 Parle... (Entr\u00e9e pour arr\u00eater)')
    t = threading.Timer(max_sec, lambda: p.send_signal(signal.SIGINT) if p.poll() is None else None)
    t.start()
    try:
        input()
    finally:
        t.cancel()
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
        p.wait()
    return path

STT_PROMPTS = {
    'fr': 'Conversation avec MATATA, un assistant vocal local nomm\u00e9 MATATA.',
    'en': 'Conversation with MATATA, a local voice assistant named MATATA.',
}

def _stt_pass(path, lang):
    prompt = STT_PROMPTS.get(lang, STT_PROMPTS['en'])
    # Serveur whisper : pas de rechargement du modèle (~1s économisées/passe)
    if _whisper_server_proc and _whisper_server_proc.poll() is None:
        try:
            return _stt_server(path, lang, prompt)
        except Exception:
            pass
    # Fallback CLI
    return _stt_cli(path, lang, prompt)

def _stt_server(path, lang, prompt):
    """Transcription via whisper-server HTTP (modèle déjà en RAM)."""
    wav_bytes = open(path, 'rb').read()
    boundary = uuid.uuid4().hex
    parts = []
    for name, val in [('file', None), ('language', lang), ('prompt', prompt),
                      ('response_format', 'verbose_json'), ('no_timestamps', 'true'),
                      ('temperature', '0.0')]:
        parts.append(f'--{boundary}\r\n'.encode())
        if name == 'file':
            parts += [b'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n',
                      b'Content-Type: audio/wav\r\n\r\n', wav_bytes, b'\r\n']
        else:
            parts += [f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      f'{val}\r\n'.encode()]
    parts.append(f'--{boundary}--\r\n'.encode())
    body = b''.join(parts)
    req = urllib.request.Request(
        _WHISPER_SERVER_URL, data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    txt = result.get('text', '').strip()
    segs = result.get('segments', [])
    ps = [w.get('probability', 0.0) for s in segs for w in (s.get('words') or [])]
    conf = sum(ps) / len(ps) if ps else 0.0
    return txt, conf

def _stt_cli(path, lang, prompt):
    """Transcription via whisper-cli (recharge le modèle à chaque appel)."""
    cmd = [WHISPER_BIN, '-m', WHISPER_MODEL, '-nt', '--prompt', prompt,
           '-ojf', '-of', '/tmp/matata_stt', '-l', lang, path]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        j = json.load(open('/tmp/matata_stt.json'))
        segs = j.get('transcription') or []
        txt = ' '.join(s.get('text', '').strip() for s in segs).strip()
        ps = [t.get('p', 0.0) for s in segs for t in (s.get('tokens') or [])]
        conf = sum(ps) / len(ps) if ps else 0.0
        return txt, conf
    except Exception:
        return '', 0.0

_TAG_RE = re.compile(r'\[[^\]]*\]|\([^)]*\)')

def _clean_stt(txt):
    """Vire les artefacts whisper [Musique]/(Bip)/[BLANK_AUDIO]/[X speaking] (fr/en)."""
    return _TAG_RE.sub(' ', txt).strip()

def transcribe_audio(path):
    # auto = dual decode FR+EN, keep the more confident transcript
    # (single-shot language detection is unreliable on short clips)
    if VOICE_LANG == 'auto':
        t_fr, c_fr = _stt_pass(path, 'fr')
        t_en, c_en = _stt_pass(path, 'en')
        return _clean_stt(t_fr if c_fr >= c_en else t_en)
    txt, _ = _stt_pass(path, VOICE_LANG)
    return _clean_stt(txt)

def _voice_for(text):
    t = text.lower()
    fr = len(re.findall(r'[\u00e0\u00e2\u00e4\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6\u00f9\u00fb\u00fc\u00e7]', text)) \
        + 2 * len(re.findall(r"\b(je|tu|il|elle|nous|vous|est|sont|c'est|dans|pour|avec|tr\u00e8s|voil\u00e0|alors|parce|aussi|\u00eatre|avoir)\b", t)) \
        + len(re.findall(r"\b(le|la|les|des|une|un|du|et|sur|pas|plus|bien|oui|non|merci|bonjour|heure|r\u00e9ponse|fichier|dossier)\b", t))
    en = 2 * len(re.findall(r"\b(i'm|you're|it's|that's|what's|isn't|don't|can't|let's|thank|thanks|please|about|right now)\b", t)) \
        + len(re.findall(r"\b(the|and|you|your|is|are|was|were|what|how|why|when|where|can|will|would|should|this|that|these|those|there|here|with|from|have|has|had|time|help|file|folder)\b", t))
    return 'en' if en > fr else 'fr'

_PIPER_VOICES = {}  # lang -> PiperVoice (lazy-loaded, une seule fois)

def _get_piper(lang='fr'):
    """Lazy-load PiperVoice (1s first call, cached after)."""
    if lang not in _PIPER_VOICES:
        model = VOICE_MODELS.get(lang, PIPER_MODEL)
        if not os.path.exists(model):
            return None
        try:
            from piper import PiperVoice
            _PIPER_VOICES[lang] = PiperVoice.load(model, config_path=model + '.json')
        except Exception:
            return None
    return _PIPER_VOICES[lang]

def speak(text):
    if not VOICE or not text.strip():
        return
    clean = re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF*`#_\[\]]+', '', text).strip()
    if not clean:
        return
    lang = _voice_for(clean)
    voice = _get_piper(lang)
    if voice is None:
        return
    try:
        rate = _rate_for(VOICE_MODELS.get(lang, PIPER_MODEL))
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            voice.synthesize_wav(clean, wf)
        pcm = buf.getvalue()
        subprocess.run(['aplay', '-q', '-f', 'S16_LE', '-r', str(rate), '-c', '1'],
                       input=pcm, timeout=30)
    except Exception:
        pass

# === WAKE WORD (--wake, v12) ===
WAKE = False
WAKE_THRESHOLD = float(os.environ.get('MATATA_WAKE_THRESHOLD', '0.5'))
WAKE_VAD = float(os.environ.get('MATATA_WAKE_VAD', '0.25'))
WAKE_MODEL_PATH = os.environ.get(
    'MATATA_WAKE_MODEL',
    os.path.join(_VOICE_DIR, 'models', 'matata.onnx'))
WAKE_DURATION = float(os.environ.get('MATATA_WAKE_DURATION', '0'))  # 0 = infini
ACTIF_S = float(os.environ.get('MATATA_ACTIF', '15'))   # dialogue libre après un échange
_FRAME = 1280          # 80 ms @16 kHz — pas natif openwakeword
_PREROLL_S = 1.7       # mémoire avant détection (pour la confirmation whisper)
_BEEP_OK = '/tmp/matata_wake_ok.wav'
_BEEP_KO = '/tmp/matata_wake_ko.wav'

_ACCENTS = str.maketrans('àâäéèêëîïôöùûüç', 'aaaeeeeiioouuuc')

_WAKE_PREFIX_RE = re.compile(
    r"^\s*(salut|ok|oui|hey|bon|bonjour)?\s*ma.?ta.?ta\b[\s,.!:;]*", re.I)

def _strip_wake_prefix(txt):
    """Enlève un « (salut) MATATA » résiduel en début de commande."""
    return _WAKE_PREFIX_RE.sub('', txt, count=1).strip()

def _voice_cmd(inp):
    """« Au revoir. »/« et reset »/« stop » -> quit/reset. None = demande normale.
    Tolérant ponctuation/accents ; décision sur les 1-2 derniers mots, phrase ≤ 3 mots."""
    words = [w for w in re.sub(r'[^a-z ]', '',
             inp.lower().translate(_ACCENTS)).split() if w]
    if not words or len(words) > 3:
        return None
    for n in (2, 1):
        cand = ''.join(words[-n:])
        if cand in ('aurevoir', 'arretetoi', 'quitter', 'exit', 'quit', 'stop'):
            return 'quit'
        if n == 1 and cand == 'reset':
            return 'reset'
    return None

def _norm_match(text):
    return re.sub(r'[^a-z]', '', text.lower().translate(_ACCENTS))

def _make_beeps():
    try:
        if not os.path.exists(_BEEP_OK):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'sine=frequency=880:duration=0.12', '-ar', '16000', _BEEP_OK],
                           check=True, capture_output=True)
        if not os.path.exists(_BEEP_KO):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'sine=frequency=330:duration=0.18', '-ar', '16000', _BEEP_KO],
                           check=True, capture_output=True)
    except Exception:
        pass

def _play_beep(kind):
    f = _BEEP_OK if kind == 'ok' else _BEEP_KO
    if os.path.exists(f):
        subprocess.run(['aplay', '-q', f], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

class MicStream:
    """arecord continu par frames 1280. respawn() = enregistreur relancé,
    tampon vidé : plus de frames périmées après une confirmation/bip/TTS."""
    def __init__(self):
        self.p = None
        self._start()

    def _start(self):
        self.p = subprocess.Popen(
            ['arecord', '-f', 'S16_LE', '-r', '16000', '-c', '1', '-t', 'raw', '-q'],
            stdout=subprocess.PIPE)

    def _stop(self):
        try:
            if self.p and self.p.poll() is None:
                self.p.terminate()
            if self.p:
                self.p.wait(timeout=1)
        except Exception:
            pass

    def respawn(self):
        self._stop()
        self._start()

    def frames(self):
        while True:
            raw = self.p.stdout.read(_FRAME * 2)
            if len(raw) < _FRAME * 2:
                break
            yield raw

    def close(self):
        self._stop()

def wake_confirm(frames):
    """Vérifie via whisper que le pré-roll contient vraiment « matata »."""
    import wave as _wave
    path = '/tmp/matata_wake_check.wav'
    with _wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b''.join(frames))
    txt, _ = _stt_pass(path, 'fr')
    core = _TAG_RE.sub('', txt)          # "[MATATA speaking]" seul = hallucination -> rejet
    ok = 'matata' in _norm_match(core)
    print(f'   {"🔔" if ok else "·"} confirmation wake: "{txt.strip()[:40]}" -> '
          f'{"accepté" if ok else "rejeté"}')
    return ok

def capture_command(stream, start_timeout=6.0, skip_frames=0):
    """Enregistre la commande après le wake : endpointing énergie, max 10 s.
    skip_frames : jette les N premières frames (queue du bip/réverbération)."""
    import array as _arr
    frames, speech, silence_run = [], False, 0
    t0 = time.time()
    skipped = 0
    for raw in stream:
        if skipped < skip_frames:
            skipped += 1
            continue
        a = _arr.array('h'); a.frombytes(raw)
        rms = (sum(v*v for v in a[::4]) / (len(a)//4)) ** 0.5 if len(a) else 0
        if not speech and time.time() - t0 > start_timeout:
            return None                      # rien dit -> abandon
        if not speech:
            if rms > 260:
                speech = True
                frames.append(raw)
            continue
        frames.append(raw)
        silence_run = silence_run + 1 if rms < 150 else 0
        dur = len(frames) * _FRAME / 16000
        if (silence_run >= 12 or dur > 10.0):  # 12 frames ≈ 1,0 s de silence
            break
    if len(frames) * _FRAME / 16000 < 0.5:
        return None
    import wave as _wave2
    path = '/tmp/matata_cmd.wav'
    with _wave2.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b''.join(frames))
    return path

def hands_free_loop(messages, show_timer):
    global VOICE_LANG
    import numpy as np
    from openwakeword.model import Model
    if os.environ.get('MATATA_WAKE_LANG', 'fr') != 'auto':
        VOICE_LANG = 'fr'      # single-pass : ~-3-4 s par commande (auto = double passe)
        print('   🇫🇷 STT direct fr (env MATATA_WAKE_LANG=auto pour la double passe)')
    oww = Model(wakeword_models=[WAKE_MODEL_PATH],
                inference_framework='onnx', vad_threshold=WAKE_VAD)
    _make_beeps()
    refractory = 0.0
    veille_t0 = time.time()
    actif_until = 0.0          # 0 = en veille ; sinon dialogue libre jusqu'à cette date
    print(f'   🎙️ Veille : dis « MATATA » puis ta commande — ensuite dialogue libre '
          f'{ACTIF_S:.0f} s sans mot-clé')
    print(f'      seuil {WAKE_THRESHOLD}, vad {WAKE_VAD} | vocal : « au revoir », '
          f'« stop », « reset » | Ctrl+C pour quitter\n')
    ms = MicStream()
    ring = deque(maxlen=int(_PREROLL_S * 16000 // _FRAME))

    def handle_command(cmd_path):
        """STT + traitement. True = continuer, False = quitter."""
        nonlocal actif_until, refractory
        inp = _strip_wake_prefix(transcribe_audio(cmd_path))
        print(f'\n🧑 (voix) {inp}')
        vc = _voice_cmd(inp)
        if vc == 'quit':
            speak('À bientôt !')
            print('👋')
            return False
        if not inp:
            _play_beep('ko')
            return True
        if vc == 'reset':
            messages[:] = [{'role': 'system', 'content': SYSTEM}]
            actif_until = time.time() + ACTIF_S
            print('🔄 Reset.\n')
            return True
        handle_turn(inp, messages, show_timer)
        print()
        actif_until = time.time() + ACTIF_S     # la conversation reste ouverte
        refractory = time.time() + 1.5
        return True

    try:
        while True:
            # --- ÉTAT ACTIF : commande directe, sans mot-clé ---
            if time.time() < actif_until:
                reste = actif_until - time.time()
                print(f'   🎧 Actif ({reste:.0f} s) — parle sans dire « MATATA »')
                cmd_path = capture_command(ms.frames(), start_timeout=max(reste, 1.0))
                if not cmd_path:
                    actif_until = 0.0
                    oww.reset(); ring.clear()
                    ms.respawn()               # vide le tampon (échos éventuels)
                    print('   💤 Retour veille — dis « MATATA » pour reprendre\n')
                    continue
                if handle_command(cmd_path) is False:
                    return
                ms.respawn()                   # TTS terminé -> tampon propre
                continue
            # --- ÉTAT VEILLE : détection du mot-clé ---
            fit = ms.frames()
            for raw in fit:
                score = list(oww.predict(
                    np.frombuffer(raw, dtype='int16')).values())[0]
                ring.append(raw)
                now = time.time()
                if WAKE_DURATION and now - veille_t0 >= WAKE_DURATION:
                    print('\n⏱️ Durée de test écoulée.')
                    return
                if score < WAKE_THRESHOLD or now < refractory:
                    continue
                oww.reset()
                print('   ⏳ Vérification…')
                post = []
                for _ in range(4):             # ~320 ms de queue
                    try: post.append(next(fit))
                    except StopIteration: break
                if wake_confirm(list(ring) + post):
                    _play_beep('ok')           # bip joué AVANT respawn : jamais capté
                    ms.respawn()               # tampon neuf, zéro frame périmée
                    ring.clear()
                    print("   🎧 Je t'écoute…")
                    cmd_path = capture_command(ms.frames(), skip_frames=4)
                    if not cmd_path:
                        _play_beep('ko'); print('   (aucune commande entendue)')
                        refractory = time.time() + 1.0
                    elif handle_command(cmd_path) is False:
                        return
                    else:
                        ms.respawn()           # TTS terminé -> tampon propre
                else:
                    _play_beep('ko')
                    ms.respawn()
                    refractory = time.time() + 2.0
                break
            else:                              # flux micro mort sans déclencheur
                print('   ⚠️ Flux micro coupé — relance.')
                time.sleep(0.5)
                ms.respawn()
    except KeyboardInterrupt:
        print('\n👋')
    finally:
        ms.close()

# === WHITELIST — niveaux N1 (read, auto) / N2 (write, confirm) / N3 (critical, confirm LOCALE) ===
# Patron N1/N2/N3 inspiré de jarvis-assistant-vocal (sosoj92, confirmé réel — cf. docs/TECH_WATCH.md
# §7) : N3 = actions plus lourdes que le WRITE ordinaire (permissions, install système, détachement
# de process) — nécessitent une confirmation FRAÎCHE et LOCALE, jamais satisfaite par une origine
# distante (même une fois un canal web/mobile branché en Phase 1, cf. IS_REMOTE plus bas).
READ_COMMANDS = {
    'ls','cat','head','tail','df','free','top','whoami','pwd','find',
    'file','wc','du','uname','lsblk','ip','ss','ps','date','uptime',
    'hostname','which','printenv','env','id','groups','lscpu','lsmem',
    'stat','sensors','neofetch','grep','awk','sort','uniq',
    'dpkg','snap','flatpak','systemctl','tree','locate','type',
    'lsusb','lspci','journalctl','xdg-open'
}
WRITE_COMMANDS = {'mkdir','cp','mv','touch','tee','echo','sed'}
CRITICAL_COMMANDS = {'chmod','chown','apt','pip','nano','vim','nohup'}
BLOCKED_COMMANDS = {'rm','rmdir','shred','unlink','dd','mkfs','wipefs','fdisk','parted','kill','killall','reboot','shutdown','poweroff','halt','init'}

# IS_REMOTE : False tant qu'aucun canal distant n'existe (agent.py reste 100% local/CLI).
# Prérequis pour la future Phase 1 web/mobile : le canal distant devra mettre ce flag à True
# AVANT d'appeler agent_turn, pour que le gate N3 ci-dessous refuse correctement à distance.
IS_REMOTE = False

# === BACKUP (Python-side, invisible to model) ===
BACKUP_DIR = os.path.join(os.path.expanduser('~'), '.agent-pc-backups')
os.makedirs(BACKUP_DIR, exist_ok=True)
LOG_FILE = os.path.join(BACKUP_DIR, f'session_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

def backup_file(filepath):
    try:
        if not os.path.exists(filepath): return None
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        bk = os.path.join(BACKUP_DIR, f'{os.path.basename(filepath)}.{ts}.bak')
        with open(filepath, 'r') as f: data = f.read()
        with open(bk, 'w') as f: f.write(data)
        return bk
    except: return None

def log_event(t, d):
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(json.dumps({'t': datetime.now().isoformat(), 'type': t, 'd': str(d)[:500]}, ensure_ascii=False) + '\n')
    except: pass

# === ONLY 3 TOOLS (proven reliable with 8B) ===
TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'run_shell',
            'description': 'Run a shell command. Use for: ls, du, find, cat, grep, date, sed, tee, xdg-open. One pipe max. For RAM/disk stats use system_info, NOT free/df.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'command': {'type': 'string', 'description': 'Shell command'},
                    'reason': {'type': 'string', 'description': 'Why'}
                },
                'required': ['command']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_files',
            'description': 'Find files/folders by name keyword.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': 'Keyword'},
                    'search_dir': {'type': 'string', 'description': 'Dir (default: home)'},
                    'file_type': {'type': 'string', 'description': 'd or f'}
                },
                'required': ['name']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'system_info',
            'description': 'PC stats: RAM, disk, CPU, OS, top processes. Use category="all" (default) when the user asks for multiple stats (e.g. "RAM et disque") to return RAM + disk + CPU together.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'description': 'all|ram|disk|cpu|os|top_processes. all returns RAM+DISK+CPU (default).'}
                },
                'required': []
            }
        }
    }
]

# === HELPERS ===
# Détecte des commandes destructrices n'importe où dans la chaîne (pas seulement
# en tête) quand elles portent un flag/signe agissant (ça ne matche pas une simple
# mention du mot). reboot/shutdown/poweroff/halt sont couverts par 'base' (en tête)
# et par _EXEC_DANGER (via pipe/exec) — pas ici pour éviter les faux positifs
# ("echo ... reboot ...").
_DANGER_RE = re.compile(
    r'\b(rm\s+-[^;|&]*[rf]|rmdir\b|shred\b|unlink\b|dd\b|mkfs\b|wipefs\b|fdisk\b|parted\b|killall\b|init\s+[0-6])\b',
    re.IGNORECASE)
# Commande destructrice appelée NUE via -exec / xargs / sh -c / system()
# (ex. "find ... -exec rm {} ;", "find ... | xargs -0 rm", "sh -c 'dd ...'").
# 'rm/dd/...' sans flag est couvert ici (uniquement après un préfixe de passage).
_EXEC_DANGER = re.compile(
    r'(?:-exec\s+|xargs\s+(?:-[0-9a-z]+\s+)*|sh\s+-c\s*[\'"“]|system\s*\([\'"“]|[\|;&]\s*|^\s*|&&\s*)'
    r'(?:sudo\s+)?(rm|rmdir|shred|unlink|dd|mkfs|wipefs|fdisk|parted|reboot|shutdown|poweroff|halt|kill|killall)\b',
    re.IGNORECASE)
# systemctl DOESN'T auto-destruct : seules certaines actions sont dangereuses
_SYSTEMCTL_BAD = re.compile(
    r'systemctl\s+(reboot|poweroff|halt|kill|stop|suspend|hibernate|force|off|on)',
    re.IGNORECASE)

def classify_command(cmd_str):
    # 1) Détection globale des commandes/blocs destructeurs via regex sur tout le texte
    if re.search(r'(sudo\s+rm|rm\s+-rf|rm\s+-r\s+-f|:\(\)\{)', cmd_str):
        return 'blocked'
    # rm -f / rm -r ou bloc étape reste bloqué
    if _DANGER_RE.search(cmd_str):
        return 'blocked'
    # commande destructive appelée nue via -exec/xargs/sh -c/system()
    if _EXEC_DANGER.search(cmd_str):
        return 'blocked'
    # 2) systemctl avec une action de contrôle du système => bloquée
    if _SYSTEMCTL_BAD.search(cmd_str):
        return 'blocked'
    try:
        base = shlex.split(cmd_str)[0].split('/')[-1]
    except: return 'unknown'
    if base in BLOCKED_COMMANDS: return 'blocked'
    if re.search(r'[>]', cmd_str) and base in READ_COMMANDS: return 'write'
    if base in READ_COMMANDS: return 'read'
    if base in CRITICAL_COMMANDS: return 'critical'
    if base in WRITE_COMMANDS: return 'write'
    return 'unknown'

def fix_glob_quoting(cmd):
    cmd = re.sub(r"'([^']*)/\*'", r"'\1'/*", cmd)
    cmd = re.sub(r'"([^"]*)/\*"', r'"\1"/*', cmd)
    return cmd

def run_command(cmd_str, timeout=None):
    cmd_str = fix_glob_quoting(cmd_str)
    if timeout is None:
        timeout = 60 if any(c in cmd_str for c in ['find ','du ','locate ']) else 30
    # Auto-backup before write operations on existing files
    if any(op in cmd_str for op in ['sed -i', 'tee ', '> ']):
        parts = cmd_str.split()
        for p in parts:
            p = p.strip("'\"")
            if os.path.isfile(p):
                bk = backup_file(p)
                if bk: print(f'   \U0001f4be Backup: {os.path.basename(bk)}')
                break
    try:
        r = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or '') + (r.stderr or '')
        return (out.strip() or "(no output)")[:600]
    except subprocess.TimeoutExpired: return f"timeout ({timeout}s)"
    except Exception as e: return str(e)

def handle_search_files(args):
    name = args.get('name', '')
    name = re.split(r'[\s*,;|]+', name)[0].strip('.*')
    if not name:
        d = args.get('search_dir', os.path.expanduser('~'))
        listing = run_command(f'ls {shlex.quote(d)}')[:300]
        return f"Error: provide a keyword. Contents of {d}: {listing}"
    search_dir = args.get('search_dir', os.path.expanduser('~'))
    file_type = args.get('file_type', '')
    cmd = f'find {shlex.quote(search_dir)} -iname "*{name}*"'
    if file_type: cmd += f' -type {file_type}'
    cmd += ' 2>/dev/null | head -20'
    results = run_command(cmd, timeout=15)
    if not results or results == '(no output)': return 'No results found.'
    lines = results.strip().split('\n')
    return '\n'.join(f"'{l}'" if ' ' in l else l for l in lines)[:600]

def handle_system_info(args):
    cat = args.get('category', 'all')
    parts = []

    # For 'all', return ONLY ram + disk + cpu (truncate heavy outputs)
    if cat == 'all':
        parts.append('=RAM=\n' + run_command('free -h', 10))
        parts.append('=DISK=\n' + run_command('df -h / 2>/dev/null', 10))
        parts.append('=CPU=\n' + run_command('lscpu | grep -E "Model name|CPU\\(s\\)|Thread|Core"', 10))
    else:
        if cat == 'ram':
            parts.append('=RAM=\n' + run_command('free -h', 10))
        if cat == 'disk':
            parts.append('=DISK=\n' + run_command('df -h / 2>/dev/null', 10))
        if cat == 'cpu':
            parts.append('=CPU=\n' + run_command('lscpu | grep -E "Model name|CPU\\(s\\)|Thread|Core"', 10))
        if cat == 'os':
            parts.append('=OS=\n' + run_command('uname -srm', 5))
        if cat == 'top_processes':
            # Truncate each line to 80 chars max, reduce to head -6
            top_cpu = run_command('ps aux --sort=-%cpu | head -6', 10)
            top_cpu = '\n'.join(line[:80] for line in top_cpu.split('\n'))
            parts.append('=TOP CPU=\n' + top_cpu)
            top_mem = run_command('ps aux --sort=-%mem | head -6', 10)
            top_mem = '\n'.join(line[:80] for line in top_mem.split('\n'))
            parts.append('=TOP RAM=\n' + top_mem)

    return '\n'.join(parts)[:800]

# === UI ===

# === SYSTEM PROMPT ===
HOME = os.path.expanduser('~')
USER = os.environ.get('USER', 'user')

SYSTEM = f"""You are Agent PC, a local Ubuntu assistant. You help by CALLING tools, not by describing actions.

Context: User={USER}, Home={HOME}, Ubuntu 24.04, 32GB RAM, Intel Ultra 7 155H.
Known: Series={HOME}/Videos/Film/Series/<name>/ (each series = one subdir with .mkv files), Desktop={HOME}/Desktop, Projects={HOME}/dev/personal/agent-pc/

Rules:
1. CALL a tool or give a final answer. Never say "I will...".
2. Chain tool calls until the task is DONE.
3. Never invent data — only report tool results.
4. Greetings/small talk: reply directly, NO tool. To find files: search_files. By extension: find -iname.
5. For date/time: run_shell date. To open apps: run_shell xdg-open.
6. To write/edit files: run_shell with tee or sed.
7. system_info for RAM, disk, CPU stats. For MULTIPLE stats together (e.g. "RAM et disque", "stats du PC"): ONE system_info call with category="all" (returns RAM+DISK+CPU). Don't call free/df separately.
8. Keep commands simple. One pipe max.
9. Never delete (rm/rmdir). Say "interdit".
10. Reply in the user's language (French if they write French, English if they write English), concise.
11. If a command fails, NEVER redo it with cosmetic changes (different binary path, flags). Run ls on the parent dir to see real names, then adapt.
12. AUDIO vs VIDEO: "musique/audio" = ONLY audio extensions (mp3, wav, flac, ogg, m4a, aac). NEVER mp4/mkv/webm/avi for music. Séries/vidéos = mkv, mp4, webm, avi. Match the extension to what is asked.
13. Only call a tool when you know its exact argument (path, keyword, category). If a path is uncertain, ls the parent dir first.

Examples of good simple commands:
- "combien de musique" → run_shell: find ~/Music -type f \\( -iname '*.mp3' -o -iname '*.wav' -o -iname '*.flac' -o -iname '*.m4a' -o -iname '*.ogg' \\) | wc -l   (AUDIO only, NEVER mp4/mkv)
- "RAM et disque" → system_info (category="all")
- "combien de RAM" → system_info (category="ram")
- "taille dossier Videos" → run_shell: du -sh ~/Videos
- "chercher fichiers python" → run_shell: find ~ -name "*.py" -type f | head -20"""

MAX_HISTORY = 20

# Déduplication (BUG FIX 1) : historique partagé sur TOUTE la session pour que
# la fenêtre glissante de 5 fonctionne réellement entre les tours (pas seulement
# à l'intérieur d'un seul agent_turn).
_COMMAND_HISTORY = []

INCOMPLETE_PATTERNS = re.compile(
    r'(je vais (chercher|utiliser|lister|compter|regarder|essayer|taper|lancer|ouvrir|aller)?\s*'
    r'|je vais\s*'
    r'|je (suis en train de|vais te|peux vous lister|recherche des fichiers)\s*'
    r'|I will\s*|let me\s*|I\'ll\s*|I am going to\s*'
    r'|voulez-vous\s*|souhaitez-vous\s*|désirez-vous\s*'
    r'|je lance la commande\s*|permettez-moi\s*)',
    re.IGNORECASE
)

# Streaming TTS phrase-par-phrase (v12.7, patron extrait de LocalVox
# github.com/YaPanBytes/LocalVox, core/orchestrator.py) : dès qu'une phrase
# est complète dans le texte streamé, on la parle immédiatement au lieu
# d'attendre la fin de toute la réponse.
SPEAK_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

# Hook optionnel pour relayer le texte streamé en direct vers le client --serve
# (Server-Sent Events) pendant qu'agent_turn tourne. None en CLI/voix/wake —
# zéro changement de comportement hors --serve (modularité). Assigné/nettoyé
# par run_server() SOUS le lock qui sérialise déjà les tours, donc aucune
# course possible entre deux requêtes concurrentes.
_STREAM_SINK = None

def _emit_stream(event_type, **kw):
    if _STREAM_SINK:
        try:
            _STREAM_SINK(event_type, **kw)
        except Exception:
            pass

# === PRE-LLM ROUTER (v12.8, patron validé contre Laya + semantic-router/FastEmbed,
# voir docs/TECH_WATCH.md) ===
# Routeur par similarité d'embeddings (FastEmbed/ONNX, ~222Mo de deps, zéro torch)
# qui court-circuite l'appel LLM pour les 2 intentions les plus simples et SANS
# RISQUE (greeting/time_date — jamais d'action destructrice, juste une réponse
# directe). system_stats/file_search sont classifiés mais TOUJOURS délégués au
# LLM : le routeur ne fait que catégoriser, il n'extrait pas les arguments de la
# commande réelle à exécuter (quel dossier, quelle catégorie de stats...).
# Algo = celui validé empiriquement (92.0% accuracy / 5.2ms sur 100 requêtes,
# contre 84.0% / 216.4ms pour Laya CPU) : top-5 plus proches voisins toutes
# catégories confondues, regroupés par route, moyenne par route, meilleure route
# retenue — reproduit fidèlement `SemanticRouter(top_k=5, aggregation='mean')`
# sans dépendre du framework `semantic-router` (ZERO framework, cf. AGENTS.md).
ROUTER_ENABLED = os.environ.get('MATATA_ROUTER', '1') != '0'
ROUTER_THRESHOLD = float(os.environ.get('MATATA_ROUTER_THRESHOLD', '0.5'))
ROUTER_TOPK = 5
ROUTER_MODEL_NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
# threads=4 (pas None) : évite un hang (bug trouvé 04/10/2026) où onnxruntime 1.29.0
# sur une machine à beaucoup de cœurs (22 ici) sur-provisionne son pool de threads
# (60 threads observés, tous bloqués en futex_do_wait) au lieu de charger le modèle.
ROUTER_THREADS = int(os.environ.get('MATATA_ROUTER_THREADS', '4'))

ROUTER_UTTERANCES = {
    'greeting': [
        "Salut", "Bonjour", "Coucou", "Ça va ?", "Bonsoir",
        "Salutations", "Hey", "Bien le bonjour à vous", "Hola",
        "Content de te voir", "Enchanté", "Je te salue", "Un petit coucou",
        "Hi", "Hello there", "Hiya there", "Morning", "Evening",
        "Nice to meet you", "Good to see you", "What's happening",
        "How's life", "How's everything", "You doing good",
        "Alright mate", "Yo man", "Bien ou bien", "Tranquille ?",
        "Ça farte", "Ça dit quoi", "Alors, ça va ?",
    ],
    'time_date': [
        "Quelle heure est-il ?", "Quelle est l'heure actuelle",
        "T'as l'heure ?", "File-moi l'heure", "Indique-moi l'heure svp",
        "Quel jour on est ?", "Quelle est la date aujourd'hui",
        "On est quel mois ?", "C'est quel jour de la semaine",
        "Rappelle-moi la date", "What's the hour right now",
        "Got the time?", "Tell me the hour", "What time do we have",
        "Which day is it", "What's the date today", "Tell me today's date",
        "What month are we in", "Give me the current time",
    ],
    'system_stats': [
        "Combien de RAM il me reste", "Quelle quantité de mémoire est libre",
        "Espace disque total disponible", "Combien d'espace de stockage j'ai",
        "Quel processeur j'ai", "Quelle marque de CPU",
        "Combien de cœurs sur ma puce", "À combien tourne mon processeur",
        "Montre les stats de la machine", "Affiche les performances système",
        "Décris-moi la configuration matérielle", "Quel est le pourcentage d'utilisation processeur",
        "Mon disque est rempli à quel niveau", "Quelle est la chaleur du CPU",
        "Niveau de batterie restant", "Durée depuis le dernier démarrage",
        "Quelle est la charge globale du système", "What CPU temp am I at",
        "Free memory amount please", "Processor load percentage",
        "Show me the machine specs", "How many processor cores total",
        "Status of the whole system", "Total available storage",
        "Overall free space on drive", "Full storage capacity value",
        "Memory consumption check", "Which processor model is installed",
        "Battery percentage left", "System uptime duration",
        "Current system load",
    ],
    'file_search': [
        "Cherche des PDF dans le dossier Documents", "Retrouve mes clichés de vacances",
        "Affiche les scripts Python du projet", "Trouve mes fichiers Word",
        "Localise mes tableurs Excel", "Dégote tous les fichiers compressés .zip",
        "Trouve-moi un fichier qui s'appelle facture", "Fais voir les fichiers récents",
        "Fouille dans le dossier Bureau", "Où sont stockées mes captures d'écran",
        "Localise rapport.pdf", "Trouve mes fichiers au format texte",
        "Affiche les fichiers changés aujourd'hui", "Trie les fichiers par date de modif",
        "Combien de morceaux de musique j'ai", "Nombre de films dans le dossier vidéos",
        "Taille du dossier Téléchargements", "Poids du dossier Téléchargements",
        "Quel est le volume du dossier Musique", "Nombre total de PDF stockés",
        "Combien de fichiers dans le dossier Bureau", "Nombre de photos dans la galerie",
        "Quel espace occupent mes vidéos", "Poids en Go du dossier Projets",
        "Volume total des fichiers photo", "Nombre de sous-dossiers dans Images",
        "Locate my audio tracks", "Find PDF documents inside Documents folder",
        "Get all compressed zip archives", "Search for the file called report",
        "Look inside downloads for invoice files", "Show my Word documents list",
        "Where can I find my screen captures", "Display files above 1GB in size",
        "Size of the Downloads directory", "Count of videos stored",
        "Total number of PDF documents", "Space taken by video files",
        "Size of the Music directory", "Total size of Documents folder",
        "Count files inside Desktop folder", "Number of subfolders in Documents",
    ],
    # 'other' : questions sur l'agent lui-même / hors-sujet — JAMAIS fast-pathée
    # (seules greeting/time_date le sont dans handle_turn). Ajoutée le 04/10/2026
    # suite à un bug réel observé : "Qui es-tu ?"/"Que peux-tu faire ?" étaient
    # classées 'greeting' (score 0.52-0.66, chevauche entièrement la plage des
    # vrais saluts 0.51-0.97 — aucun seuil ne peut séparer les deux). Cause
    # racine : aucune des 4 catégories ne représentait ce type de question,
    # l'algo les attirait par défaut vers la plus proche (greeting). Cette
    # catégorie leur donne un vrai foyer sémantique pour les détourner.
    'other': [
        "Qui es-tu ?", "Qui es tu", "Comment tu t'appelles ?", "T'es qui ?",
        "Que peux-tu faire ?", "Que sais-tu faire ?", "Quelles sont tes capacités ?",
        "Comment ça marche ?", "Explique-moi ton fonctionnement",
        "C'est quoi MATATA ?", "Parle-moi de toi", "Es-tu une IA ?",
        "Tu es un robot ?", "Comment tu fonctionnes ?", "À quoi tu sers ?",
        "Raconte-moi une blague", "Quelle est la capitale de la France ?",
        "Who are you?", "What's your name?", "What can you do?",
        "What are your capabilities?", "How does this work?",
        "Explain how you work", "What is MATATA?", "Tell me about yourself",
        "Are you an AI?", "Are you a robot?", "How do you function?",
        "What are you for?", "Tell me a joke", "What's the capital of France?",
    ],
}

_router_model = None
_router_vecs = None  # list[(categorie, vecteur unitaire numpy)], précalculé 1x

def _get_router():
    global _router_model, _router_vecs
    if _router_model is None:
        from fastembed import TextEmbedding
        import numpy as np
        _router_model = TextEmbedding(model_name=ROUTER_MODEL_NAME, threads=ROUTER_THREADS)
        _router_vecs = []
        for cat, utts in ROUTER_UTTERANCES.items():
            for vec in _router_model.embed(utts):
                v = np.asarray(vec)
                _router_vecs.append((cat, v / np.linalg.norm(v)))
    return _router_model, _router_vecs

def route_intent(text):
    """Classifie l'intention en (categorie, score) par similarité d'embeddings,
    ou (None, 0.0) si désactivé (MATATA_ROUTER=0) ou en cas d'erreur (fastembed
    non installé, etc.) — dans ce cas l'appelant doit retomber sur le LLM."""
    if not ROUTER_ENABLED:
        return None, 0.0
    try:
        import numpy as np
        model, vecs = _get_router()
        q = np.asarray(list(model.embed([text]))[0])
        q = q / np.linalg.norm(q)
        sims = [(cat, float(v @ q)) for cat, v in vecs]
        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:ROUTER_TOPK]
        by_route = {}
        for cat, s in top:
            by_route.setdefault(cat, []).append(s)
        best_cat = max(by_route, key=lambda c: sum(by_route[c]) / len(by_route[c]))
        best_score = sum(by_route[best_cat]) / len(by_route[best_cat])
        return best_cat, best_score
    except Exception:
        return None, 0.0

GREETING_REPLIES_FR = [
    "Salut ! Comment puis-je t'aider ?",
    "Bonjour ! Que puis-je faire pour toi ?",
    "Coucou ! Je t'écoute.",
]
GREETING_REPLIES_EN = [
    "Hi! How can I help?",
    "Hello! What can I do for you?",
    "Hey! I'm listening.",
]

def _fast_reply(messages, show_timer, reply, t0):
    """Répond directement sans passer par le LLM (fast-path routeur). t0 = début
    du tour (avant route_intent), pour afficher le vrai temps écoulé."""
    ts = f'  ⏱️ {time.time()-t0:.3f}s (routeur)' if show_timer else ''
    print(f'\U0001f916 {reply}{ts}\n')
    _emit_stream('token', text=reply)
    speak(reply)
    messages.append({'role': 'assistant', 'content': reply})
    log_event('resp', reply[:300])

def _fast_greeting(messages, show_timer, t0):
    lang = _voice_for(messages[-1]['content'])
    reply = random.choice(GREETING_REPLIES_EN if lang == 'en' else GREETING_REPLIES_FR)
    _fast_reply(messages, show_timer, reply, t0)

def _fast_time_date(messages, show_timer, t0):
    lang = _voice_for(messages[-1]['content'])
    now = datetime.now()
    if lang == 'en':
        reply = f"It's {now.strftime('%H:%M')}, on {now.strftime('%d/%m/%Y')}."
    else:
        reply = f"Il est {now.strftime('%Hh%M')}, nous sommes le {now.strftime('%d/%m/%Y')}."
    _fast_reply(messages, show_timer, reply, t0)

def handle_turn(inp, messages, show_timer):
    """Ajoute le message utilisateur, tente le fast-path routeur pré-LLM
    (greeting/time_date uniquement — system_stats/file_search passent toujours
    par le LLM, le routeur ne fait que classifier, pas d'extraction d'arguments),
    sinon délègue à agent_turn. Point d'entrée partagé --voice/--wake/texte."""
    t0 = time.time()
    messages.append({'role': 'user', 'content': inp})
    messages[:] = trim_messages(messages)
    log_event('user', inp)
    cat, score = route_intent(inp)
    if score >= ROUTER_THRESHOLD and cat == 'greeting':
        _fast_greeting(messages, show_timer, t0)
        return
    if score >= ROUTER_THRESHOLD and cat == 'time_date':
        _fast_time_date(messages, show_timer, t0)
        return
    agent_turn(messages, show_timer, _COMMAND_HISTORY)

def trim_messages(msgs):
    if len(msgs) <= MAX_HISTORY + 1: return msgs
    return [msgs[0]] + msgs[-(MAX_HISTORY):]

# === AGENT TURN ===
def _ollama_stream(messages, tools=None):
    """Stream Ollama chat. Yield (text_delta, tool_calls, done)."""
    kwargs = dict(model=MODEL, messages=messages, stream=True,
                  keep_alive='30m', options=CHAT_OPTS, **THINK_KW)
    if tools is not None:
        kwargs['tools'] = tools
    for chunk in ollama.chat(**kwargs):
        msg = chunk.get('message', {})
        yield (msg.get('content', ''), msg.get('tool_calls'), chunk.get('done', False))

def agent_turn(messages, show_timer, command_history=None):
    if command_history is None:
        command_history = []
    max_steps = 5
    step = 0
    total_t0 = time.time()

    while step < max_steps:
        step += 1

        # --- Stream Ollama : premier token visible en ~1-2s ---
        text_buf = []
        tool_calls = None
        speak_buf = ''      # phrases pas encore parlées (mode voix uniquement)
        safe_to_speak = True  # False dès qu'un pattern "je vais..." apparaît (retry probable)
        try:
            for delta, tc, done in _ollama_stream(messages, tools=TOOLS):
                if delta:
                    text_buf.append(delta)
                    print(delta, end='', flush=True)
                    if tool_calls is None:
                        _emit_stream('token', text=delta)
                    # Parle phrase par phrase pendant le stream (v12.7) : seulement
                    # tant qu'aucun tool_call n'est apparu (sinon ce texte n'est
                    # qu'un préambule, jamais parlé — comportement inchangé) et
                    # tant que la réponse ne ressemble pas à un "je vais..." qui
                    # finira en retry silencieux (le reliquat non parlé sera
                    # rattrapé par le fallback plus bas si le retry échoue).
                    if VOICE and tool_calls is None:
                        speak_buf += delta
                        if safe_to_speak and INCOMPLETE_PATTERNS.search(''.join(text_buf)):
                            safe_to_speak = False
                        if safe_to_speak:
                            m = SPEAK_SENTENCE_RE.search(speak_buf)
                            while m:
                                sentence = speak_buf[:m.start()].strip()
                                speak_buf = speak_buf[m.end():]
                                if sentence:
                                    speak(sentence)
                                m = SPEAK_SENTENCE_RE.search(speak_buf)
                if tc:
                    tool_calls = tc
        except Exception as e:
            print(f'\nErreur Ollama: {e}')
            _emit_stream('error', error=str(e))
            return
        if text_buf or tool_calls:
            print()  # newline after stream

        text = ''.join(text_buf)
        if tool_calls and text.strip():
            # Du texte a été streamé (donc affiché en direct côté --serve) avant
            # qu'un tool_call n'apparaisse dans le même step : ce n'était qu'un
            # préambule, jamais la réponse finale — on dit au client de l'effacer.
            _emit_stream('reset')
        if os.environ.get('AGENT_DEBUG'):
            tc_count = len(tool_calls) if tool_calls else 0
            print(f"[DBG] step={step} tc={tc_count} text={len(text)}", file=sys.stderr)

        # No tool calls — final response
        if not tool_calls:
            if INCOMPLETE_PATTERNS.search(text) and step < max_steps:
                print(f'\U0001f916 {text}')
                print('  \u26a0\ufe0f Auto-retry...')
                _emit_stream('reset')  # texte stream\u00e9 mais sur le point d'\u00eatre r\u00e9essay\u00e9
                messages.append({'role': 'assistant', 'content': text})
                messages.append({'role': 'user', 'content': 'Do not describe. CALL the tool NOW.'})
                log_event('retry', text[:200])
                continue

            elapsed = time.time() - total_t0
            ts = f'  \u23f1\ufe0f {elapsed:.1f}s' if show_timer else ''

            if text.strip():
                print(f'\U0001f916 {text}{ts}\n')
                # Le gros de la réponse a déjà été parlé phrase par phrase pendant
                # le stream ; speak_buf ne contient que le reliquat (fin sans
                # ponctuation, ou tout le texte si la parole en direct a été
                # coupée par safe_to_speak et qu'on arrive ici sans retry possible).
                # Reste vide (donc speak() no-op) si VOICE est désactivé.
                if speak_buf.strip():
                    speak(speak_buf.strip())
            else:
                # Empty response — retry sans tools pour forcer une réponse texte
                print('  ⚠️ Pas de réponse avec outils, retry sans...')
                try:
                    rbuf = []
                    tc_retry = None
                    for delta, tc, done in _ollama_stream(messages, tools=None):
                        if delta:
                            rbuf.append(delta)
                            print(delta, end='', flush=True)
                            if tc_retry is None:
                                _emit_stream('token', text=delta)
                        if tc:
                            tc_retry = tc
                            if ''.join(rbuf).strip():
                                _emit_stream('reset')
                    if rbuf or tc_retry:
                        print()
                    rtxt = ''.join(rbuf)
                    if rtxt.strip():
                        if tc_retry:
                            print(f'  ✅ Retry a trouvé un outil!')
                            messages.append({'role': 'assistant', 'content': rtxt, 'tool_calls': tc_retry})
                            tc0 = tc_retry[0]
                            fn = tc0.get('function', {})
                            fn_name = fn.get('name', '')
                            args = fn.get('arguments', {})
                            if fn_name == 'search_files':
                                out = handle_search_files(args)
                                print(f'🔍 {out}')
                                messages.append({'role': 'tool', 'content': out})
                                continue
                            elif fn_name == 'run_shell':
                                out = run_command(args.get('command', ''))
                                print(f'📋 {out}')
                                messages.append({'role': 'tool', 'content': out})
                                continue
                            elif fn_name == 'system_info':
                                out = handle_system_info(args)
                                print(f'📊 {out}')
                                messages.append({'role': 'tool', 'content': out})
                                continue
                        else:
                            elapsed2 = time.time() - total_t0
                            ts2 = f'  ⏱️ {elapsed2:.1f}s' if show_timer else ''
                            print(f'🤖 {rtxt}{ts2}\n')
                            speak(rtxt)
                    else:
                        print(f'🤖 Désolé, je n\'ai pas pu répondre. Reformulez ou "reset".{ts}\n')
                        speak('Désolé, je n\'ai pas pu répondre.')
                except:
                    print(f'🤖 Erreur. Tapez "reset".{ts}\n')

            messages.append({'role': 'assistant', 'content': text})
            log_event('resp', text[:300])
            return

        # Process tool call
        tc = tool_calls[0]
        fn = tc.get('function', {})
        fn_name = fn.get('name', '')
        args = fn.get('arguments', {})

        if text.strip():
            print(f'\U0001f916 {text}')

        messages.append({'role': 'assistant', 'content': text, 'tool_calls': tool_calls})
        log_event('tool', f'{fn_name}({json.dumps(args, ensure_ascii=False)[:200]})')

        if fn_name == 'search_files':
            name = args.get('name', '?')
            sd = args.get('search_dir', '~')
            print(f'\U0001f50d Recherche "{name}" dans {sd}... [{step}/{max_steps}]')
            out = handle_search_files(args)
            print(f'\U0001f4c4 {out}')
            messages.append({'role': 'tool', 'content': out})

        elif fn_name == 'system_info':
            cat = args.get('category', 'all')
            print(f'\U0001f4ca Syst\u00e8me ({cat})... [{step}/{max_steps}]')
            out = handle_system_info(args)
            print(f'\U0001f4c4 {out}')
            messages.append({'role': 'tool', 'content': out})

        elif fn_name == 'run_shell':
            cmd = args.get('command', '')
            reason = args.get('reason', '')
            if reason: print(f'\U0001f916 {reason}')

            # BUG FIX 2: Reject commands over 200 chars
            if len(cmd) > 200:
                print(f'\U0001f6ab Commande trop longue ({len(cmd)} chars > 200). Use simpler.')
                messages.append({'role': 'tool', 'content': 'Error: command too long. Use simple commands. Example: find ~/Music -type f | wc -l'})
                continue

            # BUG FIX 1: Detect duplicate commands
            if cmd in command_history:
                print(f'\U0001f6ab IDENTIQUE \u00e0 avant. Change d\'approche.')
                messages.append({'role': 'tool', 'content': 'ERROR: Same command failed before. Use a DIFFERENT simpler approach.'})
                continue

            command_history.append(cmd)
            if len(command_history) > 5:
                command_history.pop(0)

            lvl = classify_command(cmd)
            print(f'\U0001f4cb {cmd}  [{lvl}] [{step}/{max_steps}]')
            if lvl == 'blocked':
                print('\U0001f6ab BLOQU\u00c9')
                messages.append({'role': 'tool', 'content': 'BLOCKED'})
                return
            elif lvl == 'read':
                print('\u2705 Auto...')
                out = run_command(cmd)
                print(f'\U0001f4c4 {out}')
                messages.append({'role': 'tool', 'content': out})
            elif lvl in ('critical', 'write') and IS_REMOTE:
                # N2/N3 : pas de confirmation \u00e0 distance impl\u00e9ment\u00e9e (v12.9, --serve) \u2014
                # refus\u00e9s syst\u00e9matiquement \u00e0 distance pour l'instant, m\u00eame avec un canal
                # web/mobile branch\u00e9. input() serait de toute fa\u00e7on bloquant sans TTY local.
                kind = 'N3 critique' if lvl == 'critical' else 'N2'
                print(f'\U0001f512 {kind} \u2014 confirmation locale requise, refus\u00e9 \u00e0 distance.')
                messages.append({'role': 'tool', 'content': f'REFUSED: {kind} action requires local confirmation, not available remotely.'})
                return
            else:
                prompt = '\U0001f512 N3 CRITIQUE \u2014 confirmation locale (o/n) > ' if lvl == 'critical' \
                    else '\u26a0\ufe0f  OK ? (o/n) > '
                ok = input(prompt).strip().lower()
                if ok in ('o','oui','y','yes'):
                    out = run_command(cmd)
                    print(f'\U0001f4c4 {out}')
                    messages.append({'role': 'tool', 'content': out})
                else:
                    messages.append({'role': 'tool', 'content': 'Cancelled'})
                    print('Annul\u00e9.\n'); return
        else:
            messages.append({'role': 'tool', 'content': f'Unknown: {fn_name}'})

    elapsed = time.time() - total_t0
    ts = f'  \u23f1\ufe0f {elapsed:.1f}s' if show_timer else ''
    print(f'(max {max_steps} \u00e9tapes){ts}\n')

# === MINIMAL WEB SERVER (v12.9, --serve) ===
# HTTP API to drive the agent remotely (e.g. via Tailscale, already configured
# but never used until now). Deliberately pure stdlib (http.server): no
# framework \u2014 a handful of JSON endpoints for a single user don't justify
# FastAPI/aiohttp ("real measured gain" rule, see AGENTS.md). Standalone mode:
# doesn't run alongside --voice/--wake/interactive CLI (modularity: just a new
# opt-in mode, nothing existing changes unless you use it).
# N2/N3 are always refused remotely in this first version (IS_REMOTE=True for
# the whole duration of the mode) \u2014 no async remote confirmation flow
# implemented yet, see the gate added in agent_turn above.
SERVE_PORT = int(os.environ.get('MATATA_SERVE_PORT', '8765'))
SERVE_HOST = os.environ.get('MATATA_SERVE_HOST', '127.0.0.1')
SERVE_TOKEN = os.environ.get('MATATA_SERVE_TOKEN', '')
WEB_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'index.html')

def _voice_upload_to_text(raw, content_type):
    """Converts a browser recording (webm/ogg) to 16kHz mono WAV via ffmpeg
    (already a project dependency, see _make_beeps()), then reuses the
    existing whisper.cpp transcription (transcribe_audio) as-is."""
    ext = 'ogg' if 'ogg' in (content_type or '') else 'webm'
    uid = uuid.uuid4().hex
    src = f'/tmp/matata_web_voice_{uid}.{ext}'
    wav = f'/tmp/matata_web_voice_{uid}.wav'
    try:
        with open(src, 'wb') as f:
            f.write(raw)
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src,
                             '-ar', '16000', '-ac', '1', wav],
                            capture_output=True, timeout=30)
        if r.returncode != 0 or not os.path.exists(wav):
            return ''
        return transcribe_audio(wav)
    except Exception:
        return ''
    finally:
        for p in (src, wav):
            try: os.remove(p)
            except OSError: pass

def run_server(messages, show_timer):
    import http.server

    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        # API convention (see AGENTS.md): POST paths end in "/", GET paths
        # don't. Every JSON response is {ok, message, data} — success: data
        # holds the real payload; error: ok=False, message is ALWAYS the
        # generic "An error occurred", the actual detail goes in data.error.
        def _send_json(self, code, ok, message, data=None):
            body = json.dumps({'ok': ok, 'message': message, 'data': data or {}},
                               ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, code, message, data=None):
            self._send_json(code, True, message, data)

        def _err(self, code, error):
            self._send_json(code, False, 'An error occurred', {'error': error})

        def _authorized(self):
            if not SERVE_TOKEN:
                return True
            return self.headers.get('Authorization') == f'Bearer {SERVE_TOKEN}'

        # --- Streaming (Server-Sent Events) : réponse en direct, token par
        # token, comme le terminal, au lieu d'attendre la réponse complète.
        # Un seul événement "done" en fin de flux reprend la convention
        # {ok, message, data} habituelle ; les événements intermédiaires
        # ("token"/"transcript"/"reset") sont des notifications de progression,
        # pas la réponse API elle-même.
        def _sse_start(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True

        def _sse_send(self, obj):
            try:
                self.wfile.write(f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'.encode('utf-8'))
                self.wfile.flush()
            except Exception:
                pass  # client a fermé la connexion : rien à faire, le tour continue côté serveur

        def _run_turn_streaming(self, inp, extra_data=None, _already_started=False):
            global _STREAM_SINK
            if not _already_started:
                self._sse_start()

            def sink(event_type, **kw):
                self._sse_send({'type': event_type, **kw})

            t0 = time.time()
            try:
                _STREAM_SINK = sink
                with lock:
                    handle_turn(inp, messages, show_timer)
                    last = messages[-1]
                data = {'reply': last.get('content', ''), 'role': last.get('role', ''),
                        'elapsed_s': round(time.time() - t0, 3)}
                if extra_data:
                    data.update(extra_data)
                self._sse_send({'type': 'done', 'ok': True, 'message': 'Response generated',
                                 'data': data})
            except Exception as e:
                self._sse_send({'type': 'done', 'ok': False, 'message': 'An error occurred',
                                 'data': {'error': str(e)}})
            finally:
                _STREAM_SINK = None

        def do_GET(self):
            if self.path == '/health':
                return self._ok(200, 'System operational',
                                 {'model': MODEL, 'remote': IS_REMOTE})
            if self.path == '/':
                try:
                    with open(WEB_INDEX, 'rb') as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    self._err(500, 'web/index.html missing')
                return
            self._err(404, 'not found')

        def do_POST(self):
            if not self._authorized():
                return self._err(401, 'unauthorized')
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b''

            if self.path == '/voice/':
                transcript = _voice_upload_to_text(raw, self.headers.get('Content-Type', ''))
                if not transcript:
                    return self._err(400, 'could not transcribe audio')
                # La transcription part tout de suite (avant même de streamer la
                # réponse), pour que le navigateur affiche "ce qu'elle a compris"
                # sans attendre la réponse complète du LLM derrière.
                self._sse_start()
                self._sse_send({'type': 'transcript', 'text': transcript})
                return self._run_turn_streaming(transcript, extra_data={'transcript': transcript},
                                                 _already_started=True)

            if self.path in ('/chat/', '/reset/'):
                try:
                    data = json.loads(raw or b'{}')
                except Exception:
                    return self._err(400, 'invalid JSON')

                if self.path == '/reset/':
                    with lock:
                        messages[:] = [{'role': 'system', 'content': SYSTEM}]
                    return self._ok(200, 'Conversation reset')

                inp = (data.get('message') or '').strip()
                if not inp:
                    return self._err(400, 'missing "message"')
                return self._run_turn_streaming(inp)

            self._err(404, 'not found')

        def log_message(self, fmt, *args):
            pass  # agent.py a d\u00e9j\u00e0 ses propres print(), pas besoin du log HTTP par d\u00e9faut

    httpd = http.server.ThreadingHTTPServer((SERVE_HOST, SERVE_PORT), Handler)
    print(f'\U0001f310 Interface sur http://{SERVE_HOST}:{SERVE_PORT}  '
          f'(GET / \u2014 API : POST /chat/, POST /voice/, POST /reset/, GET /health)')
    if not SERVE_TOKEN:
        print('   \u26a0\ufe0f  MATATA_SERVE_TOKEN non d\u00e9fini \u2014 aucune authentification. '
              'OK en local/Tailscale priv\u00e9, \u00e0 d\u00e9finir avant toute exposition plus large.')
    print('   N2/N3 toujours refus\u00e9s \u00e0 distance dans ce mode (voir AGENTS.md).\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n\U0001f44b')
    finally:
        httpd.server_close()

# === MAIN ===
def main():
    global VOICE, VOICE_LANG, WAKE, IS_REMOTE
    show_timer = '--timer' in sys.argv or '-t' in sys.argv
    SERVE = '--serve' in sys.argv or os.environ.get('MATATA_SERVE') == '1'
    VOICE = '--voice' in sys.argv or '-v' in sys.argv
    WAKE = '--wake' in sys.argv or os.environ.get('MATATA_WAKE') == '1'
    if WAKE:
        VOICE = True
    if VOICE and not (os.path.exists(WHISPER_BIN) and os.path.exists(PIPER_MODEL)):
        print('\u26a0\ufe0f  Voix indisponible : whisper.cpp ou Piper manquant dans voice/ \u2014 mode texte seul.')
        VOICE = False
    if WAKE:
        if not (VOICE and os.path.exists(WAKE_MODEL_PATH)):
            print(f'\u26a0\ufe0f  Mode wake impossible : mod\u00e8le {WAKE_MODEL_PATH} manquant ou voix indisponible.')
            return
    messages = [{'role': 'system', 'content': SYSTEM}]

    if SERVE:
        IS_REMOTE = True
        if os.path.exists(WHISPER_BIN):
            print('   ⏳ Démarrage whisper-server (pour /voice)...', end=' ', flush=True)
            if _whisper_server_start():
                print(f'✅ (port {_WHISPER_SERVER_PORT})')
            else:
                print('⚠️ fallback CLI')
        else:
            print('   ⚠️  whisper.cpp introuvable — /voice indisponible.')
        try:
            ollama.chat(model=MODEL, messages=[{'role': 'user', 'content': 'hi'}],
                        options={'num_predict': 1}, **THINK_KW)
        except Exception:
            pass
        if ROUTER_ENABLED:
            print('   ⏳ Chargement du routeur...', end=' ', flush=True)
            try:
                _get_router()
                print('✅')
            except Exception:
                print('⚠️ indisponible, LLM seul')
        run_server(messages, show_timer)
        _whisper_server_stop()
        return

    # Whisper-server persistant : pas de rechargement du modèle par passe
    if VOICE:
        print('   ⏳ Démarrage whisper-server...', end=' ', flush=True)
        if _whisper_server_start():
            print(f'✅ (port {_WHISPER_SERVER_PORT})')
        else:
            print('⚠️ fallback CLI')

    # Warm up
    try:
        ollama.chat(model=MODEL, messages=[{'role':'user','content':'hi'}],
                    options={'num_predict':1}, **THINK_KW)
    except: pass

    # Routeur pré-LLM (v12.8) : précharge le modèle d'embeddings maintenant
    # (coût ponctuel ~3-4s) pour que la première question de l'utilisateur
    # bénéficie déjà du fast-path, au lieu de payer ce coût en plein milieu
    # de la conversation.
    if ROUTER_ENABLED:
        print('   ⏳ Chargement du routeur...', end=' ', flush=True)
        try:
            _get_router()
            print('✅')
        except Exception:
            print('⚠️ indisponible, LLM seul')

    print(f'\n\U0001f916 Agent PC v12.14 \u2014 {MODEL}' +
          ('  \U0001f43b mains libres' if WAKE else ('  \U0001f3a4 voix' if VOICE else '')))
    print(f'   \U0001f50d search | \U0001f4ca sys | \U0001f4cb shell')
    print(f'   Timer: {"ON" if show_timer else "OFF"} | quit, reset, timer, voix, langue')
    if WAKE:
        pass  # instructions affichées par hands_free_loop
    elif VOICE:
        print('   \U0001f3a4 Entr\u00e9e=parle | tape ton texte au prompt micro | langue fr|en|auto')
    print()

    if WAKE:
        hands_free_loop(messages, show_timer)
        _whisper_server_stop()
        return

    try:
      while True:
        try:
            if VOICE:
                typed = input('\U0001f3a4 [Entr\u00e9e=parle | tape ton texte] ')
                if typed.strip():
                    inp = typed.strip()
                    print(f'\U0001f9d1 (clavier) {inp}')
                else:
                    wav = record_audio()
                    inp = transcribe_audio(wav)
                    print(f'\U0001f9d1 (voix) {inp}')
                    if not inp:
                        print('   (non compris \u2014 r\u00e9essaie)\n'); continue
            else:
                inp = input('\U0001f9d1 > ').strip()
        except KeyboardInterrupt:
            print('\n\U0001f44b'); break
        except Exception:
            print('\n\U0001f44b'); break
        if inp.lower() in ('quit','exit','q'):
            print('\U0001f44b'); break
        if inp.lower() == 'reset':
            messages = [{'role':'system','content':SYSTEM}]
            print('\U0001f504 Reset.\n'); continue
        if inp.lower() == 'timer':
            show_timer = not show_timer
            print(f'\u23f1\ufe0f Timer {"ON" if show_timer else "OFF"}\n'); continue
        if inp.lower() == 'voix':
            VOICE = not VOICE
            print(f'\U0001f3a4 Voix {"ON" if VOICE else "OFF"}\n'); continue
        if inp.lower().startswith('langue'):
            parts = inp.lower().split()
            arg = parts[1] if len(parts) > 1 else ''
            if arg in ('fr', 'en', 'auto'):
                VOICE_LANG = arg
                print(f'\U0001f310 Langue voix : {arg}\n')
            else:
                print('Usage: langue fr|en|auto\n')
            continue
        if not inp: continue
        handle_turn(inp, messages, show_timer)
    finally:
      _whisper_server_stop()

if __name__ == '__main__':
    main()

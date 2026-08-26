#!/usr/bin/env python3
"""Agent PC v12.4 — 3 tools + voix push-to-talk + mains libres (--wake)"""

import subprocess, shlex, re, time, sys, threading, os, json, signal, ollama
import urllib.request, io, uuid, wave
from collections import deque
from datetime import datetime

# === MODEL (overridable: MATATA_MODEL=qwen3.5:4b python3 agent.py) ===
MODEL = os.environ.get('MATATA_MODEL', 'qwen3:8b')
# think=False required for BOTH generations: qwen3 (issue #10976) and qwen3.5
# (default hybrid thinking eats the whole num_predict budget -> empty answers).
IS_Q35 = MODEL.startswith('qwen3.5')
THINK_KW = {'think': False}
CHAT_OPTS = {'num_predict': 800, 'num_ctx': 4096, 'temperature': 0.3, 'num_thread': 16}
if not IS_Q35:
    CHAT_OPTS['repeat_penalty'] = 1.2  # official Qwen3 rec is 1.5 but it causes early-EOS empty
    # responses after failed tool attempts; 1.2 keeps tool calling reliable (validated v9.2 era)

# === VOICE (Phase 2 — optional --voice flag) ===
VOICE = False
VOICE_LANG = 'fr'  # fr | auto | en — 'fr' = direct, 'auto' = double passe fr+en (pour les code-switchers)
_VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'voice')
WHISPER_BIN = os.path.join(_VOICE_DIR, 'whisper.cpp', 'build', 'bin', 'whisper-cli')
WHISPER_MODEL = os.path.join(_VOICE_DIR, 'models', 'ggml-small.bin')
VOICE_MODELS = {
    'fr': os.path.join(_VOICE_DIR, 'models', 'fr_FR-siwis-medium.onnx'),
    'en': os.path.join(_VOICE_DIR, 'models', 'en_US-lessac-medium.onnx'),
}
PIPER_MODEL = VOICE_MODELS['fr']
_WHISPER_SERVER_BIN = os.path.join(_VOICE_DIR, 'whisper.cpp', 'build', 'bin', 'whisper-server')
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
        _whisper_server_proc = subprocess.Popen(
            [_WHISPER_SERVER_BIN, '-m', WHISPER_MODEL, '--port', str(_WHISPER_SERVER_PORT),
             '-t', '4', '--no-speech-thold', '0.6', '--no-language-probabilities',
             '--ctx', '0'],
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
    import array
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
        messages.append({'role': 'user', 'content': inp})
        messages[:] = trim_messages(messages)
        log_event('user', inp)
        agent_turn(messages, show_timer)
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

# === WHITELIST ===
READ_COMMANDS = {
    'ls','cat','head','tail','df','free','top','whoami','pwd','find',
    'file','wc','du','uname','lsblk','ip','ss','ps','date','uptime',
    'hostname','which','printenv','env','id','groups','lscpu','lsmem',
    'stat','sensors','neofetch','grep','awk','sort','uniq',
    'dpkg','snap','flatpak','systemctl','tree','locate','type',
    'lsusb','lspci','journalctl','xdg-open','nohup'
}
WRITE_COMMANDS = {'mkdir','cp','mv','touch','tee','chmod','chown','apt','pip','nano','vim','echo','sed'}
BLOCKED_COMMANDS = {'rm','rmdir','shred','unlink','dd','mkfs','wipefs','fdisk','parted','kill','killall','reboot','shutdown','poweroff','init'}

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
            'description': 'Run a shell command. Use for: ls, du, find, cat, grep, date, free, df, sed, tee, xdg-open. One pipe max.',
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
            'description': 'PC stats: RAM, disk, CPU, OS, top processes.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'description': 'all|ram|disk|cpu|os|top_processes'}
                },
                'required': []
            }
        }
    }
]

# === HELPERS ===
def classify_command(cmd_str):
    if re.search(r'(sudo\s+rm|rm\s+-rf|:\(\)\{)', cmd_str):
        return 'blocked'
    try:
        base = shlex.split(cmd_str)[0].split('/')[-1]
    except: return 'unknown'
    if base in BLOCKED_COMMANDS: return 'blocked'
    if re.search(r'[>]', cmd_str) and base in READ_COMMANDS: return 'write'
    if base in READ_COMMANDS: return 'read'
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
7. system_info for RAM, disk, CPU stats.
8. Keep commands simple. One pipe max.
9. Never delete (rm/rmdir). Say "interdit".
10. Reply in the user's language (French if they write French, English if they write English), concise.
11. If a command fails, NEVER redo it with cosmetic changes (different binary path, flags). Run ls on the parent dir to see real names, then adapt.

Examples of good simple commands:
- "combien de musique" → run_shell: find ~/Music -type f | wc -l
- "taille dossier Videos" → run_shell: du -sh ~/Videos
- "chercher fichiers python" → run_shell: find ~ -name "*.py" -type f | head -20"""

MAX_HISTORY = 20

INCOMPLETE_PATTERNS = re.compile(
    r'(je vais |I will |let me |I\'ll |I am going to |voulez-vous |souhaitez-vous |désirez-vous )',
    re.IGNORECASE
)

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
        try:
            for delta, tc, done in _ollama_stream(messages, tools=TOOLS):
                if delta:
                    text_buf.append(delta)
                    print(delta, end='', flush=True)
                if tc:
                    tool_calls = tc
        except Exception as e:
            print(f'\nErreur Ollama: {e}')
            return
        if text_buf or tool_calls:
            print()  # newline after stream

        text = ''.join(text_buf)
        if os.environ.get('AGENT_DEBUG'):
            tc_count = len(tool_calls) if tool_calls else 0
            print(f"[DBG] step={step} tc={tc_count} text={len(text)}", file=sys.stderr)

        # No tool calls — final response
        if not tool_calls:
            if INCOMPLETE_PATTERNS.search(text) and step < max_steps:
                print(f'\U0001f916 {text}')
                print('  \u26a0\ufe0f Auto-retry...')
                messages.append({'role': 'assistant', 'content': text})
                messages.append({'role': 'user', 'content': 'Do not describe. CALL the tool NOW.'})
                log_event('retry', text[:200])
                continue

            elapsed = time.time() - total_t0
            ts = f'  \u23f1\ufe0f {elapsed:.1f}s' if show_timer else ''

            if text.strip():
                print(f'\U0001f916 {text}{ts}\n')
                speak(text)
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
                        if tc:
                            tc_retry = tc
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
            else:
                ok = input('\u26a0\ufe0f  OK ? (o/n) > ').strip().lower()
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

# === MAIN ===
def main():
    global VOICE, VOICE_LANG, WAKE
    show_timer = '--timer' in sys.argv or '-t' in sys.argv
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

    print(f'\n\U0001f916 Agent PC v12.4 \u2014 {MODEL}' +
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
        messages.append({'role':'user','content':inp})
        messages = trim_messages(messages)
        log_event('user', inp)
        agent_turn(messages, show_timer)
    finally:
      _whisper_server_stop()

if __name__ == '__main__':
    main()

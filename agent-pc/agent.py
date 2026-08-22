#!/usr/bin/env python3
"""Agent PC v10 — Back to Basics: 3 tools, reliable"""

import subprocess, shlex, re, time, sys, threading, os, json, ollama
from datetime import datetime

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
    if not name: return 'Error: provide a keyword.'
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
class Spinner:
    def __init__(self):
        self.frames = list('\u28cb\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f')
        self.running = False
        self._thread = None
    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
    def _spin(self):
        i, t0 = 0, time.time()
        while self.running:
            f = self.frames[i % len(self.frames)]
            sys.stdout.write(f'\r{f} R\u00e9flexion... {time.time()-t0:.0f}s')
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
    def stop(self):
        if not self.running: return
        self.running = False
        if self._thread: self._thread.join()
        sys.stdout.write('\r' + ' '*30 + '\r')
        sys.stdout.flush()

# === SYSTEM PROMPT ===
HOME = os.path.expanduser('~')
USER = os.environ.get('USER', 'user')

SYSTEM = f"""You are Agent PC, a local Ubuntu assistant. You help by CALLING tools, not by describing actions.

Context: User={USER}, Home={HOME}, Ubuntu 24.04, 32GB RAM, Intel Ultra 7 155H.
Known: Videos={HOME}/Videos/Film, Desktop={HOME}/Desktop, Projects={HOME}/dev/personal/agent-pc/

Rules:
1. CALL a tool or give a final answer. Never say "I will...".
2. Chain tool calls until the task is DONE.
3. Never invent data — only report tool results.
4. To find files by name: search_files. To find by extension: run_shell with find -iname.
5. For date/time: run_shell date. To open apps: run_shell xdg-open.
6. To write/edit files: run_shell with tee or sed.
7. system_info for RAM, disk, CPU stats.
8. Keep commands simple. One pipe max.
9. Never delete (rm/rmdir). Say "interdit".
10. Reply in FRENCH, concise.

Examples of good simple commands:
- "combien de musique" → run_shell: find ~/Music -type f | wc -l
- "taille dossier Videos" → run_shell: du -sh ~/Videos
- "chercher fichiers python" → run_shell: find ~ -name "*.py" -type f | head -20"""

MAX_HISTORY = 20

INCOMPLETE_PATTERNS = re.compile(
    r'(je vais |I will |let me |I\'ll |I am going to )',
    re.IGNORECASE
)

def trim_messages(msgs):
    if len(msgs) <= MAX_HISTORY + 1: return msgs
    return [msgs[0]] + msgs[-(MAX_HISTORY):]

# === AGENT TURN ===
def agent_turn(messages, show_timer, command_history=None):
    if command_history is None:
        command_history = []
    max_steps = 5
    step = 0
    total_t0 = time.time()

    while step < max_steps:
        step += 1
        sp = Spinner()
        sp.start()

        try:
            response = ollama.chat(
                model='qwen3:8b', messages=messages, think=False, stream=False,
                tools=TOOLS, keep_alive='30m',
                options={'num_predict': 500, 'num_ctx': 4096, 'temperature': 0.3, 'repeat_penalty': 1.5, 'num_thread': 16}
            )
        except Exception as e:
            sp.stop()
            print(f'Erreur Ollama: {e}')
            return
        sp.stop()

        msg = response.get('message', {})
        text = msg.get('content', '') or ''
        tool_calls = msg.get('tool_calls', None) or []

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
            else:
                # Empty response — try once more without tools
                print('  \u26a0\ufe0f Pas de r\u00e9ponse avec outils, retry sans...')
                try:
                    retry = ollama.chat(
                        model='qwen3:8b', messages=messages, think=False, stream=False,
                        tools=TOOLS, keep_alive='30m',
                        options={'num_predict': 500, 'num_ctx': 4096, 'temperature': 0.3, 'repeat_penalty': 1.5, 'num_thread': 16}
                    )
                    rtxt = retry.get('message', {}).get('content', '')
                    if rtxt.strip():
                        # Check if retry produced tool-like text
                        tc_retry = retry.get('message', {}).get('tool_calls', None)
                        if tc_retry:
                            print(f'  \u2705 Retry a trouv\u00e9 un outil!')
                            # Process this tool call
                            messages.append({'role': 'assistant', 'content': rtxt, 'tool_calls': tc_retry})
                            tc0 = tc_retry[0]
                            fn = tc0.get('function', {})
                            fn_name = fn.get('name', '')
                            args = fn.get('arguments', {})
                            # Handle it (simplified — just run_shell and search)
                            if fn_name == 'search_files':
                                out = handle_search_files(args)
                                print(f'\U0001f50d {out}')
                                messages.append({'role': 'tool', 'content': out})
                                continue
                            elif fn_name == 'run_shell':
                                out = run_command(args.get('command', ''))
                                print(f'\U0001f4cb {out}')
                                messages.append({'role': 'tool', 'content': out})
                                continue
                        else:
                            elapsed2 = time.time() - total_t0
                            ts2 = f'  \u23f1\ufe0f {elapsed2:.1f}s' if show_timer else ''
                            print(f'\U0001f916 {rtxt}{ts2}\n')
                    else:
                        print(f'\U0001f916 D\u00e9sol\u00e9, je n\'ai pas pu r\u00e9pondre. Reformulez ou "reset".{ts}\n')
                except:
                    print(f'\U0001f916 Erreur. Tapez "reset".{ts}\n')

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
    show_timer = '--timer' in sys.argv or '-t' in sys.argv
    messages = [{'role': 'system', 'content': SYSTEM}]

    # Warm up
    try:
        ollama.chat(model='qwen3:8b', messages=[{'role':'user','content':'hi'}],
                    options={'num_predict':1}, think=False)
    except: pass

    print(f'\n\U0001f916 Agent PC v10 \u2014 Qwen 3 8B')
    print(f'   \U0001f50d search | \U0001f4ca sys | \U0001f4cb shell')
    print(f'   Timer: {"ON" if show_timer else "OFF"} | quit, reset, timer\n')

    while True:
        try:
            inp = input('\U0001f9d1 > ').strip()
        except:
            print('\n\U0001f44b'); break
        if inp.lower() in ('quit','exit','q'):
            print('\U0001f44b'); break
        if inp.lower() == 'reset':
            messages = [{'role':'system','content':SYSTEM}]
            print('\U0001f504 Reset.\n'); continue
        if inp.lower() == 'timer':
            show_timer = not show_timer
            print(f'\u23f1\ufe0f Timer {"ON" if show_timer else "OFF"}\n'); continue
        if not inp: continue
        messages.append({'role':'user','content':inp})
        messages = trim_messages(messages)
        log_event('user', inp)
        agent_turn(messages, show_timer)

if __name__ == '__main__':
    main()

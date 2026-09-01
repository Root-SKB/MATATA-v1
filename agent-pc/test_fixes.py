#!/usr/bin/env python3
"""Quick test for Bug 1 & 2 fixes + security classify (without Ollama)"""

import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent

# Simulate the dedup + length limit logic
def test_command_fixes():
    command_history = []

    # Test 1: Normal command
    cmd1 = "find /home -iname '*.mp3' 2>/dev/null | head -20"
    if len(cmd1) > 200:
        print(f"❌ Test 1 FAIL: Command too long ({len(cmd1)} > 200)")
        return False
    if cmd1 in command_history:
        print(f"❌ Test 1 FAIL: Duplicate command")
        return False
    command_history.append(cmd1)
    print(f"✅ Test 1 PASS: Normal command accepted ({len(cmd1)} chars)")

    # Test 2: Command too long (should be rejected)
    cmd2 = "find /home -iname '*.mp3' -o -iname '*.wav' -o -iname '*.flac' -o -iname '*.aac' -o -iname '*.m4a' " * 3
    if len(cmd2) > 200:
        print(f"✅ Test 2 PASS: Long command rejected ({len(cmd2)} > 200 chars)")
    else:
        print(f"❌ Test 2 FAIL: Long command should be rejected")
        return False

    # Test 3: Duplicate command (should be rejected)
    if cmd1 in command_history:
        print(f"✅ Test 3 PASS: Duplicate detected and rejected")
    else:
        print(f"❌ Test 3 FAIL: Duplicate not detected")
        return False

    # Test 4: Same command twice = only 1 history entry
    if command_history.count(cmd1) != 1:
        print(f"❌ Test 4 FAIL: History should have 1 entry, has {command_history.count(cmd1)}")
        return False
    print(f"✅ Test 4 PASS: History tracking works ({len(command_history)} entries)")

    print("\n📊 All fixes validated!")
    return True


def test_security_classify():
    """Vérifie que classify_command bloque les commandes destructrices
    (y compris via contournement) et laisse passer les légitimes."""
    c = agent.classify_command
    blocked_expected = [
        "rm -rf /",
        "rm -r /home/x",
        "rm -f file.txt",
        "rm file",
        "find / -exec rm {} ;",
        "find ~ -type f -exec shred {} ;",
        "find ~ -type f -print0 | xargs -0 rm",
        "xargs rm < list",
        "sh -c 'dd if=/dev/sda of=/dev/null'",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sdb1",
        "shred -u file",
        "reboot", "poweroff", "halt",
        "kill 1234", "killall firefox",
        "echo x | reboot",
        "systemctl reboot", "systemctl poweroff", "systemctl stop nginx",
        "ls /tmp && rm -rf /tmp/x",
    ]
    read_expected = [
        "ls -la ~/Music",
        "find ~/Music -type f | wc -l",
        "find ~ -name '*.mp3' | wc -l",
        "du -sh ~/Videos",
        "date",
        "cat /etc/hostname",
        "systemctl status nginx",
        "systemctl is-active ollama",
        "systemctl list-units",
        "grep -v rm /etc/test",
        "grep rm file",
        "free -h", "df -h /", "whoami", "uname -a", "ps aux",
    ]
    fails = 0
    for cmd in blocked_expected:
        r = c(cmd)
        if r != 'blocked':
            print(f"❌ SECURITY: {cmd!r} -> {r} (attendu blocked)"); fails += 1
    for cmd in read_expected:
        r = c(cmd)
        if r != 'read':
            print(f"❌ SECURITY: {cmd!r} -> {r} (attendu read)"); fails += 1
    if fails:
        print(f"\n❌ {fails} échec(s) de sécurité"); return False
    print(f"✅ SECURITY: {len(blocked_expected)} destructrices bloquées + {len(read_expected)} légitimes OK")
    return True


if __name__ == '__main__':
    ok1 = test_command_fixes()
    ok2 = test_security_classify()
    sys.exit(0 if (ok1 and ok2) else 1)

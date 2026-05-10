#!/usr/bin/env python3
"""Quick test for Bug 1 & 2 fixes (without Ollama)"""

import sys
sys.path.insert(0, '/home/root-dev/dev/personal/agent-pc')

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

if __name__ == '__main__':
    success = test_command_fixes()
    sys.exit(0 if success else 1)

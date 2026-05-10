#!/bin/bash
# Tests for Agent PC v10 — Bug Fix Verification (Bugs 1 & 2)

set -e
cd "$(dirname "$0")"
source venv/bin/activate

echo ""
echo "================================"
echo "Agent PC v10 — Test Suite"
echo "================================"
echo ""
echo "Testing: Bug 1 (Command Dedup) + Bug 2 (Command Length Limit)"
echo ""

# Test 1: Hi (no tool call)
echo "Test 1: Simple greeting (no tools)"
echo "---"
timeout 40 python3 agent.py --timer << 'EOF'
Hi
quit
EOF
echo ""

# Test 2: Quelle heure (date)
echo "Test 2: Date query (simple shell)"
echo "---"
timeout 40 python3 agent.py --timer << 'EOF'
Quelle heure ?
quit
EOF
echo ""

# Test 3: RAM et disque
echo "Test 3: System info (RAM & disk)"
echo "---"
timeout 50 python3 agent.py --timer << 'EOF'
RAM et disque ?
quit
EOF
echo ""

# Test 4: Music count (simple find, no 40+ patterns)
echo "Test 4: Music count (finds should be simple now)"
echo "---"
timeout 80 python3 agent.py --timer << 'EOF'
J'ai combien de musique ?
quit
EOF
echo ""

# Test 5: Series count with size
echo "Test 5: Series count with size"
echo "---"
timeout 150 python3 agent.py --timer << 'EOF'
Combien de series avec taille ?
quit
EOF
echo ""

echo "✅ All tests completed!"

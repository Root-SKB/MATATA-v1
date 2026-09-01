#!/bin/bash
# Integration suite for Agent PC v12.5 (requires Ollama + qwen3:8b pulled)

set -e
cd "$(dirname "$0")"
source ../venv/bin/activate

echo ""
echo "================================"
echo "Agent PC v12.5 — Integration Suite"
echo "================================"
echo ""
echo "Testing: greeting, date/time, RAM+disk, music count, series+size (multi-step)"
echo ""

# Test 1: Hi (no tool call)
echo "Test 1: Simple greeting (no tools)"
echo "---"
timeout 120 python3 agent.py --timer << 'EOF' || echo "❌ Test 1: ECHEC/TIMEOUT"
Hi
quit
EOF
echo ""

# Test 2: Quelle heure (date)
echo "Test 2: Date query (simple shell)"
echo "---"
timeout 120 python3 agent.py --timer << 'EOF' || echo "❌ Test 2: ECHEC/TIMEOUT"
Quelle heure ?
quit
EOF
echo ""

# Test 3: RAM et disque
echo "Test 3: System info (RAM & disk)"
echo "---"
timeout 150 python3 agent.py --timer << 'EOF' || echo "❌ Test 3: ECHEC/TIMEOUT"
RAM et disque ?
quit
EOF
echo ""

# Test 4: Music count (simple find, no 40+ patterns)
echo "Test 4: Music count (finds should be simple now)"
echo "---"
timeout 180 python3 agent.py --timer << 'EOF' || echo "❌ Test 4: ECHEC/TIMEOUT"
J'ai combien de musique ?
quit
EOF
echo ""

# Test 5: Series count with size
echo "Test 5: Series count with size"
echo "---"
timeout 300 python3 agent.py --timer << 'EOF' || echo "❌ Test 5: ECHEC/TIMEOUT"
Combien de series avec taille ?
quit
EOF
echo ""

echo "✅ All tests completed!"

#!/bin/bash
# Enregistre des clips de « MATATA » pour l'entraînement + un jeu de VALIDATION.
# Usage: ./record_clips.sh [N_TRAIN] [N_VAL]   (défaut 12 + 4)
# Conseil: varie ton ton (normal, question, appel), la distance (~20-60 cm)
# et tourne légèrement la tête sur certains clips.
# Les clips de validation (clips_val/) ne doivent JAMAIS servir à l'entraînement :
# ils mesurent le vrai score en W3 sur des prononciations non vues.

DIR="$(cd "$(dirname "$0")" && pwd)"
TRAIN_DIR="$DIR/clips"
VAL_DIR="$DIR/clips_val"
N_TRAIN=${1:-12}
N_VAL=${2:-4}
mkdir -p "$TRAIN_DIR" "$VAL_DIR"
ffmpeg -y -loglevel error -f lavfi -i "sine=frequency=880:duration=0.3" /tmp/beep.wav

record_set() {
    local dir="$1" n="$2" label="$3"
    echo ""
    echo "🎤 $label ($n clips) — répertoire $dir"
    for i in $(seq -w 1 "$n"); do
        f="$dir/clip_$i.wav"
        echo ""
        echo "== Clip $i/$n — dis « MATATA » après le bip (3 s) =="
        aplay -q /tmp/beep.wav
        arecord -f S16_LE -r 16000 -d 3 -q "$f" && echo "   ✅ $f"
    done
}

record_set "$TRAIN_DIR" "$N_TRAIN" "ENTRAÎNEMENT"
[ "$N_VAL" -gt 0 ] && record_set "$VAL_DIR" "$N_VAL" "VALIDATION (non vus à l'entraînement)"

echo ""
echo "✅ Terminé. Entraînement : $TRAIN_DIR | Validation : $VAL_DIR"

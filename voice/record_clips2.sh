#!/bin/bash
# R2 — Enregistrements variés pour le réentraînement personnalisé de « MATATA ».
# Usage: ./record_clips2.sh
# Produit : voice/clips2_train/ (44 clips) + voice/clips2_val/ (6 clips)
# Les clips de validation ne doivent JAMAIS servir à l'entraînement.

DIR="$(cd "$(dirname "$0")" && pwd)"
TRAIN_DIR="$DIR/clips2_train"
VAL_DIR="$DIR/clips2_val"
mkdir -p "$TRAIN_DIR" "$VAL_DIR"
ffmpeg -y -loglevel error -f lavfi -i "sine=frequency=880:duration=0.3" /tmp/beep.wav

rec() {  # rec <fichier> <consigne>
    echo ""
    echo "== $1 — $2 =="
    aplay -q /tmp/beep.wav
    arecord -f S16_LE -r 16000 -d 4 -q "$1" && echo "   ✅ $1"
}

echo "🎤 SESSION R2 — 44 clips entraînement + 6 validation (~10 min)"
echo "   Parle naturellement, varie la posture comme indiqué."

# ---- Lot A : chuchotés proches (8) ----
echo ""
echo "━━━ LOT A — CHUCHOTÉS proches (~30 cm, sans voix sonore) ━━━"
for i in $(seq -w 1 8); do
    rec "$TRAIN_DIR/whisper_$i.wav" "chuchote « MATATA » (soufflé, presque inaudible)"
done

# ---- Lot B : distance (8) ----
echo ""
echo "━━━ LOT B — DISTANCE (voix normale, varie pièces et angles) ━━━"
prompts_b=(
    "à ~1 m, face au micro"
    "à ~1 m, tête tournée à 45°"
    "à ~1,5 m, voix projetée"
    "à ~2 m, pièce actuelle"
    "à ~2 m, en te déplaçant"
    "à ~3 m, voix forte"
    "depuis une autre pièce / couloir"
    "à ~2 m, en faisant autre chose"
)
i=1
for p in "${prompts_b[@]}"; do
    rec "$TRAIN_DIR/far_$(printf %02d $i).wav" "dis « MATATA » $p"
    i=$((i+1))
done

# ---- Lot C : préfixes (12) ----
echo ""
echo "━━━ LOT C — PRÉFIXES (voix normale, ~50 cm) ━━━"
prefixes=(salut hey eh ok allô dis)
for p in "${prefixes[@]}"; do
    for n in 1 2; do
        rec "$TRAIN_DIR/prefix_${p}_$n.wav" "dis « $p MATATA » naturellement"
    done
done

# ---- Lot D : débit (8) ----
echo ""
echo "━━━ LOT D — DÉBIT (voix normale, ~50 cm) ━━━"
for i in 1 2 3 4; do
    rec "$TRAIN_DIR/slow_0$i.wav" "dis « MAAA-TAAA-TAAA » très lentement"
done
for i in 1 2 3 4; do
    rec "$TRAIN_DIR/fast_0$i.wav" "dis « MATATA » très vite, comme un surnom"
done

# ---- Validation (6, JAMAIS pour l'entraînement) ----
echo ""
echo "━━━ VALIDATION (6) — prononciations témoins ━━━"
rec "$VAL_DIR/val_whisper_1.wav" "chuchote « MATATA » proche"
rec "$VAL_DIR/val_whisper_2.wav" "re-chuchote « MATATA », légèrement différent"
rec "$VAL_DIR/val_far_1.wav"     "~2 m, voix normale"
rec "$VAL_DIR/val_far_2.wav"     "~3 m, voix forte"
rec "$VAL_DIR/val_prefix_1.wav"  "dis « salut MATATA »"
rec "$VAL_DIR/val_prefix_2.wav"  "dis « ok MATATA »"

echo ""
echo "✅ Terminé."
echo "   Entraînement : $TRAIN_DIR ($(ls "$TRAIN_DIR" | wc -l) fichiers)"
echo "   Validation   : $VAL_DIR ($(ls "$VAL_DIR" | wc -l) fichiers)"

# ---- Zip prêt pour Colab (structure train/ + val/) ----
STAGE=/tmp/clips2_stage
rm -rf "$STAGE" "$DIR/clips2.zip"
mkdir -p "$STAGE/clips2/train" "$STAGE/clips2/val"
cp "$TRAIN_DIR"/*.wav "$STAGE/clips2/train/"
cp "$VAL_DIR"/*.wav    "$STAGE/clips2/val/"
(cd "$STAGE" && zip -qr "$DIR/clips2.zip" clips2)
rm -rf "$STAGE"
echo ""
echo "📦 Zip pour Colab : $DIR/clips2.zip ($(du -h "$DIR/clips2.zip" | cut -f1))"
echo "   -> téléverse ce fichier dans le panneau Fichiers de Colab."

#!/usr/bin/env python3
"""Test du wake word MATATA.

Modes :
  --clips   rejoue les clips de validation en matrice VAD x seuil -> point de fonctionnement
  --live    ecoute continue du micro, detection temps reel avec periode refractaire

Exemples :
  source venv/bin/activate
  python3 voice/wakeword_test.py --clips
  python3 voice/wakeword_test.py --live --threshold 0.5 --vad 0.25
  python3 voice/wakeword_test.py --live --duration 6
"""
import argparse
import glob
import os
import subprocess
import sys
import time
import wave

import numpy as np
import scipy.signal as sp

BASE = os.path.dirname(os.path.abspath(__file__))
BEEP = "/tmp/matata_beep.wav"
FRAME = 1280


def load16k(path):
    with wave.open(path) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if rate != 16000:
        x = sp.resample_poly(x, 16000, rate)
    return x.astype(np.int16)


def make_model(model_path, vad):
    from openwakeword.model import Model
    return Model(wakeword_models=[model_path], inference_framework="onnx", vad_threshold=vad)


def score_clip(m, path):
    x = load16k(path)
    scores = [list(m.predict(x[i:i + FRAME]).values())[0] for i in range(0, len(x) - FRAME + 1, FRAME)]
    m.reset()
    return max(scores) if scores else 0.0


def cmd_clips(args):
    vads = [0.0, 0.25, 0.5]
    thresholds = [0.35, 0.5, 0.65]
    clips = sorted(glob.glob(os.path.join(args.clips_dir, "*.wav")))
    if not clips:
        sys.exit(f"Aucun clip dans {args.clips_dir} — lance d'abord record_clips.sh")
    print(f"{len(clips)} clips de validation (prononciations jamais vues)\n")
    for v in vads:
        m = make_model(args.model, v)
        scores = {os.path.basename(c): score_clip(m, c) for c in clips}
        cells = " ".join(
            f"{sum(1 for s in scores.values() if s >= t):>3}/{len(clips)}"
            f"{' ' * (len(str(t)) - 1)}"
            for t in thresholds
        )
        detail = " ".join(f"{n.replace('clip_', '').replace('.wav', '')}:{s:.2f}"
                          for n, s in sorted(scores.items()))
        print(f"vad={v:<4} seuils {thresholds}\n         {cells}\n         {detail}\n")
        del m
    print("Lecture : choisis le couple (vad, seuil) qui garde le meilleur rappel")
    print("avec la marge la plus large sur le pire clip (colonne detail).")


def cmd_live(args):
    m = make_model(args.model, args.vad)
    if not os.path.exists(BEEP):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "sine=frequency=880:duration=0.15",
                        "-ar", "16000", BEEP], check=False)
    proc = subprocess.Popen(["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1",
                             "-t", "raw", "-q"], stdout=subprocess.PIPE)
    print(f"🎤 Ecoute continue — dis « MATATA » (seuil {args.threshold}, "
          f"vad {args.vad}, refractaire {args.refractory}s)")
    print("   Ctrl+C pour arreter\n")
    refractory_until = 0.0
    window = []
    last_draw = 0.0
    detections = 0
    start = time.time()
    try:
        while True:
            raw = proc.stdout.read(FRAME * 2)
            if len(raw) < FRAME * 2:
                break
            x = np.frombuffer(raw, dtype=np.int16)
            score = list(m.predict(x).values())[0]
            window.append(score)
            now = time.time()
            if now - last_draw >= 0.35:
                peak = max(window)
                bar = "#" * int(round(peak * 30))
                print(f"\r{peak:.3f} |{bar:<30}|", end="", flush=True)
                window.clear()
                last_draw = now
            if score >= args.threshold:
                if now >= refractory_until:
                    detections += 1
                    elapsed = now - start
                    print(f"\r🔔 MATATA detecte ({score:.2f}) a t+{elapsed:.0f}s"
                          f"   total: {detections}")
                    subprocess.run(["aplay", "-q", BEEP], check=False)
                    refractory_until = now + args.refractory
                m.reset()
            if args.duration and now - start >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        print(f"\nArret. {detections} detection(s) en {time.time()-start:.0f}s.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--clips", action="store_true", help="matrice de seuils sur un dossier de clips")
    g.add_argument("--live", action="store_true", help="ecoute micro continue")
    p.add_argument("--clips-dir", default=os.path.join(BASE, "clips_val"),
                   help="dossier de clips pour --clips (defaut: voice/clips_val)")
    p.add_argument("--model", default=os.path.join(BASE, "models", "matata.onnx"))
    p.add_argument("--threshold", type=float, default=0.5, help="seuil de detection (live)")
    p.add_argument("--vad", type=float, default=0.25, help="vad_threshold (live)")
    p.add_argument("--refractory", type=float, default=2.0, help="delai anti-repetition (s)")
    p.add_argument("--duration", type=float, default=0, help="duree max en live (0 = infini)")
    args = p.parse_args()

    if not os.path.exists(args.model):
        sys.exit(f"Modele introuvable : {args.model}")
    (cmd_clips if args.clips else cmd_live)(args)


if __name__ == "__main__":
    main()

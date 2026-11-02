"""Kostenloses, lokales Smart-Reframing für LokalClipps.

Wandelt Querformat-Videos in 9:16 um und lässt den Bildausschnitt dabei
dem Sprecher folgen – wie bei kostenpflichtigen Clip-Tools, aber komplett
offline: Gesichtserkennung mit dem freien YuNet-Modell (MIT-Lizenz) über
OpenCV, keine Cloud-API, keine Kosten.

Alles hier ist eine reine Verbesserung. Findet sich kein verlässliches
Gesicht (Gaming, Bildschirmaufnahmen, Landschaft), liefert ``plan_reframe``
``None`` und der Aufrufer nutzt das bisherige Layout mit unscharfem
Hintergrund.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 3.0
DETECT_WIDTH = 640
MIN_FACE_HEIGHT = 0.12      # Gesichtshöhe relativ zur Bildhöhe, ab der ein Gesicht "Hauptperson" ist
MIN_FACE_COVERAGE = 0.30    # Anteil der Messpunkte mit Gesicht, ab dem der Gesichts-Modus gewählt wird
TARGET_ASPECT = 9 / 16
KEY_STEP = 0.5              # Sekunden zwischen Kamera-Stützpunkten

Face = tuple[float, float, float, float, float]   # cx, cy, breite, höhe, score (alles normalisiert)
Sample = tuple[float, list[Face]]                 # (Zeit in Sekunden ab Clipstart, Gesichter)
Detector = Callable[[Path, float, float, int, int], list[Sample]]


def model_path() -> Path | None:
    """Pfad zum YuNet-Modell oder ``None``, falls es fehlt."""
    candidates: list[Path] = []
    override = os.environ.get("LOCALCLIP_FACE_MODEL", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).resolve().parent / "models" / MODEL_NAME)
    return next((path for path in candidates if path.is_file()), None)


def sample_faces(
    video: Path, start: float, duration: float, width: int, height: int,
    model: Path, fps: float = SAMPLE_FPS,
) -> list[Sample]:
    """Misst ``fps`` Mal pro Sekunde, wo im Bild Gesichter sind."""
    import cv2
    import numpy as np

    sample_w = min(DETECT_WIDTH, width)
    sample_w -= sample_w % 2
    sample_h = max(2, int(round(height * sample_w / width / 2)) * 2)
    detector = cv2.FaceDetectorYN.create(str(model), "", (sample_w, sample_h), 0.7, 0.3, 500)
    command = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-an", "-sn", "-vf", f"fps={fps},scale={sample_w}:{sample_h}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    frame_bytes = sample_w * sample_h * 3
    samples: list[Sample] = []
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=creation_flags,
    )
    try:
        index = 0
        while True:
            raw = process.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(sample_h, sample_w, 3)
            detector.setInputSize((sample_w, sample_h))
            _, found = detector.detect(frame)
            faces: list[Face] = []
            if found is not None:
                for row in found:
                    x, y, w, h = (float(value) for value in row[:4])
                    faces.append((
                        (x + w / 2) / sample_w, (y + h / 2) / sample_h,
                        w / sample_w, h / sample_h, float(row[-1]),
                    ))
            samples.append((index / fps, faces))
            index += 1
    finally:
        process.stdout.close()
        process.kill()
        process.wait()
    return samples


def face_coverage(samples: Sequence[Sample], min_height: float = MIN_FACE_HEIGHT) -> float:
    """Anteil der Messpunkte, auf denen eine Hauptperson sichtbar ist."""
    if not samples:
        return 0.0
    hits = sum(1 for _, faces in samples if any(face[3] >= min_height for face in faces))
    return hits / len(samples)


def _focus(faces: Sequence[Face], previous: float | None, crop_frac: float) -> float | None:
    """Wohin soll die Kamera schauen? Mitte der Gruppe, sonst die präsenteste Person."""
    usable = [face for face in faces if face[3] >= MIN_FACE_HEIGHT * 0.6]
    if not usable:
        return None
    left = min(face[0] - face[2] / 2 for face in usable)
    right = max(face[0] + face[2] / 2 for face in usable)
    if len(usable) > 1 and (right - left) <= crop_frac * 0.85:
        return (left + right) / 2          # alle passen ins Bild: Gruppe zentrieren

    def weight(face: Face) -> float:
        return face[2] * face[3] * face[4]

    strongest = max(weight(face) for face in usable)
    candidates = [face for face in usable if weight(face) >= strongest * 0.5]
    if previous is None:
        return max(candidates, key=weight)[0]
    return min(candidates, key=lambda face: abs(face[0] - previous))[0]   # ruhig bei derselben Person bleiben


def camera_keyframes(
    samples: Sequence[Sample],
    total_duration: float,
    crop_frac: float,
    remap: Callable[[float], float | None] | None = None,
    step: float = KEY_STEP,
) -> list[tuple[float, float]]:
    """Weiche Kamerafahrt als Liste ``(zeit, mitte_x_normalisiert)``.

    - Messpunkte in herausgeschnittenen Pausen entfallen (``remap`` liefert ``None``).
    - Ausreißer werden per Median geglättet.
    - Eine Totzone verhindert Wackeln, eine Geschwindigkeitsgrenze verhindert Ruckeln.
    """
    import numpy as np

    raw: list[tuple[float, float | None]] = []
    previous: float | None = None
    for moment, faces in samples:
        mapped = remap(moment) if remap else moment
        if mapped is None:
            continue
        focus = _focus(faces, previous, crop_frac)
        if focus is not None:
            previous = focus
        raw.append((mapped, focus))
    known = [value for _, value in raw if value is not None]
    if not known or total_duration <= 0:
        return []

    times = np.array([moment for moment, _ in raw], dtype=float)
    values: list[float] = []
    last = known[0]
    for _, value in raw:
        last = value if value is not None else last
        values.append(last)
    series = np.array(values, dtype=float)
    if len(series) >= 3:
        padded = np.concatenate(([series[0]], series, [series[-1]]))
        series = np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)

    low, high = crop_frac / 2, 1 - crop_frac / 2
    dt = 0.1
    deadzone = crop_frac * 0.12
    tau = 0.4
    max_speed = crop_frac * 1.2
    camera = float(min(max(np.interp(0.0, times, series), low), high))
    moving = False
    path: list[tuple[float, float]] = [(0.0, camera)]
    steps = int(math.ceil(total_duration / dt))
    for index in range(1, steps + 1):
        moment = index * dt
        target = float(min(max(np.interp(moment, times, series), low), high))
        gap = target - camera
        if not moving and abs(gap) > deadzone:
            moving = True
        if moving:
            move = gap * (1 - math.exp(-dt / tau))
            limit = max_speed * dt
            camera += max(-limit, min(limit, move))
            if abs(target - camera) < deadzone * 0.25:
                moving = False
        camera = min(max(camera, low), high)
        path.append((moment, camera))

    path_times = np.array([moment for moment, _ in path])
    path_values = np.array([value for _, value in path])
    key_times = list(np.arange(0.0, total_duration, step)) + [total_duration]
    return [(float(moment), float(np.interp(moment, path_times, path_values))) for moment in key_times]


def simplify_keys(keys: Sequence[tuple[float, float]], tolerance: float = 0.5) -> list[tuple[float, float]]:
    """Entfernt Stützpunkte, die nur ein gerades Stück wiederholen (kürzerer ffmpeg-Ausdruck)."""
    if len(keys) <= 2:
        return list(keys)
    kept = [keys[0]]
    for index in range(1, len(keys) - 1):
        before, here, after = kept[-1], keys[index], keys[index + 1]
        flat = abs(before[1] - here[1]) <= tolerance and abs(after[1] - here[1]) <= tolerance
        if not flat:
            kept.append(here)
    kept.append(keys[-1])
    return kept


def crop_x_expression(keys_px: Sequence[tuple[float, float]]) -> str:
    """ffmpeg-Ausdruck für die x-Position des Ausschnitts in Abhängigkeit von ``t``."""
    keys = simplify_keys(keys_px)
    if not keys:
        return "0"
    if len(keys) == 1:
        return f"{keys[0][1]:.1f}"
    terms: list[str] = []
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t1 <= t0:
            continue
        if abs(x1 - x0) < 0.5:
            body = f"{x0:.1f}"
        else:
            body = f"({x0:.1f}+({x1 - x0:.2f})*(t-{t0:.2f})/{t1 - t0:.2f})"
        terms.append(f"gte(t,{t0:.2f})*lt(t,{t1:.2f})*{body}")
    last_t, last_x = keys[-1]
    terms.append(f"gte(t,{last_t:.2f})*{last_x:.1f}")
    return "+".join(terms)


def plan_reframe(
    video: Path,
    width: int,
    height: int,
    start: float,
    duration: float,
    *,
    remap: Callable[[float], float | None] | None = None,
    total: float | None = None,
    detector: Detector | None = None,
) -> dict | None:
    """Plant den Gesichts-Ausschnitt oder liefert ``None`` (dann: unscharfer Hintergrund).

    ``remap`` rechnet Zeiten ab Clipstart auf die (ggf. gekürzte) Ausgabe-Zeitleiste um.
    ``detector`` ist nur für Tests austauschbar.
    """
    try:
        if width <= 0 or height <= 0 or width <= height * TARGET_ASPECT * 1.02:
            return None                      # Quelle ist schon hochkant
        crop_w = int(height * TARGET_ASPECT) // 2 * 2
        crop_frac = crop_w / width
        if detector is not None:
            samples = detector(video, start, duration, width, height)
        else:
            model = model_path()
            if model is None:
                return None
            samples = sample_faces(video, start, duration, width, height, model)
        coverage = face_coverage(samples)
        if coverage < MIN_FACE_COVERAGE:
            return None
        keys = camera_keyframes(samples, total if total is not None else duration, crop_frac, remap)
        if not keys:
            return None
        max_x = width - crop_w
        keys_px = [
            (moment, float(min(max(center * width - crop_w / 2, 0.0), max_x)))
            for moment, center in keys
        ]
        return {"crop_w": crop_w, "keys": keys_px, "coverage": round(coverage, 2)}
    except Exception as exc:                 # Reframing darf nie das Rendern verhindern
        print(f"Smart-Reframing übersprungen: {exc}", file=sys.stderr)
        return None


def center_plan(width: int, height: int) -> dict | None:
    """Fester Ausschnitt aus der Bildmitte: kein Tracking, keine Bewegung, kein Zoom."""
    if width <= 0 or height <= 0 or width <= height * TARGET_ASPECT * 1.02:
        return None
    crop_w = int(height * TARGET_ASPECT) // 2 * 2
    return {"crop_w": crop_w, "keys": [(0.0, float((width - crop_w) // 2))], "coverage": 0.0}

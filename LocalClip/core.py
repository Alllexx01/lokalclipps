from __future__ import annotations

import json
import math
import re
import subprocess
import textwrap
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from reframe import center_plan, crop_x_expression, plan_reframe


Progress = Callable[[int, str], None]


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Clip:
    start: float
    end: float
    score: int
    title: str
    transcript: str
    reasons: list[str]

    @property
    def duration(self) -> float:
        return self.end - self.start


HOOK_TERMS = {
    "aber", "achtung", "darum", "das problem", "das wichtigste", "endlich",
    "fehler", "geheimnis", "genau deshalb", "krass", "niemand", "problem",
    "so geht", "tipp", "trick", "warum", "wichtig", "wusstest", "überrasch",
    "actually", "best", "biggest", "crazy", "finally", "here's why", "how to",
    "important", "mistake", "never", "problem", "secret", "this is", "why",
}


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout)[-4000:]
        raise RuntimeError(f"Befehl fehlgeschlagen ({result.returncode}):\n{detail}")


def probe_video(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
    }


def extract_audio(video: Path, audio: Path) -> None:
    run([
        "ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(audio),
    ])


def transcribe(audio: Path, model_name: str, language: str | None) -> tuple[list[Word], str]:
    import numpy as np
    from faster_whisper import WhisperModel

    with wave.open(str(audio), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    if sample_width != 2 or sample_rate != 16000:
        raise RuntimeError("Die vorbereitete Tonspur hat ein unerwartetes Audioformat.")
    audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        audio_data = audio_data.reshape(-1, channels).mean(axis=1)
    audio_data /= 32768.0

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_data,
        language=None if language == "auto" else language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 350, "speech_pad_ms": 180},
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=True,
    )
    words: list[Word] = []
    for segment in segments:
        for item in segment.words or []:
            text = item.word.strip()
            if text:
                words.append(Word(float(item.start), float(item.end), text))
    return words, info.language


def detect_scene_changes(video: Path, threshold: float = 0.32) -> list[float]:
    """Find strong visual cuts with a low-resolution analysis pass.

    Scene detection is an enhancement only: a malformed source or unsupported
    codec must not prevent the normal speech-based clipping pipeline.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(video), "-an", "-sn",
            "-vf", f"fps=3,scale=320:-2,select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        return []
    points = [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr)]
    # Suppress near-duplicates caused by flashes or rapid transitions.
    filtered: list[float] = []
    for point in points:
        if point > 0.15 and (not filtered or point - filtered[-1] >= 0.45):
            filtered.append(point)
    return filtered[:500]


def _clean_title(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    sentence = re.sub(r"\s+", " ", sentence).strip(" -–—,.!")
    if len(sentence) > 70:
        sentence = sentence[:67].rsplit(" ", 1)[0] + "..."
    return sentence or "Highlight"


def _window_score(
    text: str,
    duration: float,
    position: float,
    focus_terms: set[str],
    pause_before: float,
    pause_after: float,
) -> tuple[float, list[str]]:
    lower = text.lower()
    hook_hits = sum(1 for term in HOOK_TERMS if term in lower)
    focus_hits = sum(1 for term in focus_terms if term in lower)
    questions = text.count("?")
    excitement = text.count("!")
    numbers = len(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))
    word_count = len(text.split())
    density = word_count / max(duration, 1)
    opening = 2.0 if any(term in lower[:140] for term in HOOK_TERMS) else 0.0
    complete_ending = 1.2 if text.rstrip().endswith((".", "!", "?")) else 0.0
    pace = max(0.0, 2.2 - abs(density - 2.6))
    length_fit = max(0.0, 1.5 - abs(duration - 42.0) / 18.0)
    early_bonus = max(0.0, 0.6 - position * 0.3)
    clean_start = 1.0 if pause_before >= 0.65 else 0.0
    clean_end = 1.0 if pause_after >= 0.65 else 0.0
    tokens = re.findall(r"[\wäöüß']+", lower)
    variety = len(set(tokens)) / max(len(tokens), 1)
    filler_count = sum(tokens.count(filler) for filler in ("äh", "ähm", "also", "quasi", "irgendwie", "uh", "um"))
    fragment_start = 1.5 if tokens and tokens[0] in {"und", "aber", "dann", "also", "weil", "and", "but", "then", "because"} else 0.0
    score = (
        hook_hits * 2.2 + focus_hits * 3.2 + questions * 1.4 + excitement
        + min(numbers, 3) * 0.55 + opening + complete_ending + pace + length_fit
        + early_bonus + clean_start + clean_end + variety * 1.4
        - min(filler_count, 5) * 0.35 - fragment_start
    )
    reasons: list[str] = []
    if focus_hits:
        reasons.append("passt zum Suchfokus")
    if opening:
        reasons.append("starker Einstieg")
    if questions:
        reasons.append("spannende Frage")
    if excitement or hook_hits >= 2:
        reasons.append("hohe Energie")
    if numbers:
        reasons.append("konkrete Aussage")
    if clean_start and clean_end:
        reasons.append("sauberer Anfang und Schluss")
    if not reasons:
        reasons.append("gutes Erzähltempo")
    return score, reasons[:3]


def _sentence_ranges(words: list[Word]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start_idx = 0
    for index, word in enumerate(words):
        next_gap = words[index + 1].start - word.end if index + 1 < len(words) else 2.0
        sentence_duration = word.end - words[start_idx].start
        boundary = word.text.endswith((".", "!", "?")) or next_gap >= 0.9 or sentence_duration >= 14
        if boundary:
            ranges.append((start_idx, index))
            start_idx = index + 1
    if start_idx < len(words):
        ranges.append((start_idx, len(words) - 1))
    return ranges


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[\wäöüß']+", left.lower()))
    right_tokens = set(re.findall(r"[\wäöüß']+", right.lower()))
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def _scene_before(target: float, scene_changes: list[float], distance: float = 0.9) -> float | None:
    matches = [point for point in scene_changes if target - distance <= point <= target]
    return matches[-1] if matches else None


def _scene_after(target: float, scene_changes: list[float], distance: float = 0.9) -> float | None:
    return next((point for point in scene_changes if target <= point <= target + distance), None)


def choose_clips(
    words: list[Word],
    count: int,
    min_seconds: int,
    max_seconds: int,
    focus: str = "",
    scene_changes: Iterable[float] = (),
) -> list[Clip]:
    if not words:
        raise RuntimeError("Im Video wurde keine Sprache erkannt.")

    focus_terms = {term.strip().lower() for term in re.split(r"[,;\n]", focus) if len(term.strip()) >= 2}
    scene_points = sorted({max(0.0, float(point)) for point in scene_changes})
    sentences = _sentence_ranges(words)
    candidates: list[tuple[float, int, int, str, list[str]]] = []
    for sentence_index, (start_idx, _) in enumerate(sentences):
        start_time = words[start_idx].start
        for _, sentence_end in sentences[sentence_index:]:
            proposed_duration = words[sentence_end].end - start_time
            if proposed_duration > max_seconds:
                break
            if proposed_duration < min_seconds:
                continue
            text = " ".join(word.text for word in words[start_idx:sentence_end + 1])
            position = start_idx / max(len(words), 1)
            pause_before = words[start_idx].start - words[start_idx - 1].end if start_idx else 2.0
            pause_after = words[sentence_end + 1].start - words[sentence_end].end if sentence_end + 1 < len(words) else 2.0
            raw_score, reasons = _window_score(
                text, proposed_duration, position, focus_terms, pause_before, pause_after,
            )
            candidates.append((raw_score, start_idx, sentence_end, text, reasons))

    selected: list[Clip] = []
    for raw_score, start_idx, end_idx, text, reasons in sorted(candidates, reverse=True):
        speech_start = words[start_idx].start
        speech_end = words[end_idx].end
        start = max(0.0, speech_start - 0.25)
        end = min(words[-1].end + 0.25, speech_end + 0.35)
        scene_start = _scene_before(speech_start - 0.05, scene_points)
        scene_end = _scene_after(speech_end + 0.05, scene_points)
        snapped = False
        if scene_start is not None:
            start = scene_start
            snapped = True
        if scene_end is not None:
            end = scene_end
            snapped = True
        if end - start > max_seconds + 1.0 or end - start < min_seconds:
            start = max(0.0, speech_start - 0.25)
            end = min(words[-1].end + 0.25, speech_end + 0.35)
            snapped = False
        overlap = any(max(0.0, min(end, clip.end) - max(start, clip.start)) > min(end - start, clip.duration) * 0.35 for clip in selected)
        if overlap:
            continue
        if any(_token_similarity(text, clip.transcript) > 0.56 for clip in selected):
            continue
        score = int(max(55, min(99, 58 + raw_score * 2.6)))
        if snapped:
            reasons = [*reasons, "an Bildschnitt ausgerichtet"][:3]
        selected.append(Clip(start, end, score, _clean_title(text), text, reasons))
        if len(selected) >= count:
            break

    return sorted(selected, key=lambda clip: clip.score, reverse=True)


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    value = hex_color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "FFFFFF"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}".upper()


def _caption_groups(words: Iterable[Word]) -> list[list[Word]]:
    """Create balanced, readable caption cards instead of rigid word chunks."""
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current:
            gap = word.start - current[-1].end
            candidate_chars = sum(len(item.text) for item in current) + len(current) + len(word.text)
            candidate_duration = word.end - current[0].start
            if gap >= 0.55 or len(current) >= 5 or candidate_chars > 29 or candidate_duration > 2.8:
                groups.append(current)
                current = []
        current.append(word)
        sentence_end = word.text.endswith((".", "!", "?"))
        comma_break = word.text.endswith((",", ";", ":")) and len(current) >= 3
        if sentence_end or comma_break:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    # Avoid a flashing single-word orphan when it still fits naturally before it.
    balanced: list[list[Word]] = []
    for group in groups:
        if len(group) == 1 and balanced:
            previous = balanced[-1]
            combined = previous + group
            combined_chars = sum(len(item.text) for item in combined) + len(combined) - 1
            gap = group[0].start - previous[-1].end
            if len(combined) <= 5 and combined_chars <= 31 and gap < 0.4:
                balanced[-1] = combined
                continue
        balanced.append(group)
    return balanced


def analyze_clip_potential(
    video_info: dict,
    words: list[Word],
    scene_changes: list[float],
    clips: list[Clip],
    requested_count: int,
    min_seconds: int,
) -> dict:
    """Rate whether a source is likely to produce useful short-form clips."""
    duration = max(0.1, float(video_info.get("duration", 0.1)))
    minutes = duration / 60.0
    words_per_minute = len(words) / max(minutes, 0.01)
    spoken_seconds = sum(max(0.0, min(word.end, duration) - max(word.start, 0.0)) for word in words)
    speech_coverage = min(1.0, spoken_seconds / duration)
    scene_rate = len(scene_changes) / max(minutes, 0.01)
    average_clip_score = sum(clip.score for clip in clips) / max(len(clips), 1)

    duration_points = min(15.0, duration / max(60.0, min_seconds * 2.0) * 15.0)
    if 75 <= words_per_minute <= 205:
        speech_points = 25.0
    elif words_per_minute < 75:
        speech_points = max(0.0, words_per_minute / 75.0 * 25.0)
    else:
        speech_points = max(8.0, 25.0 - (words_per_minute - 205.0) * 0.12)
    coverage_points = min(15.0, speech_coverage / 0.28 * 15.0)
    if 0.5 <= scene_rate <= 14:
        visual_points = 15.0
    elif scene_rate < 0.5:
        visual_points = 5.0 + scene_rate / 0.5 * 10.0
    else:
        visual_points = max(7.0, 15.0 - (scene_rate - 14.0) * 0.35)
    highlight_points = max(0.0, min(20.0, (average_clip_score - 55.0) / 40.0 * 20.0)) if clips else 0.0
    result_points = min(10.0, len(clips) / max(requested_count, 1) * 10.0)
    score = int(round(max(0.0, min(100.0, duration_points + speech_points + coverage_points + visual_points + highlight_points + result_points))))

    if score >= 80:
        label = "Sehr gutes Clip-Potenzial"
        verdict = "Dieses Video eignet sich sehr gut für kurze Social-Media-Clips."
    elif score >= 65:
        label = "Gut geeignet"
        verdict = "Dieses Video bietet mehrere brauchbare Momente zum Clippen."
    elif score >= 45:
        label = "Bedingt geeignet"
        verdict = "Clips sind möglich, benötigen aber wahrscheinlich etwas Nachbearbeitung."
    else:
        label = "Eher ungeeignet"
        verdict = "Das Material liefert momentan nur wenige klare Short-Clip-Momente."

    strengths: list[str] = []
    recommendations: list[str] = []
    if duration >= max(60, min_seconds * 2):
        strengths.append("genug Ausgangsmaterial")
    else:
        recommendations.append("Ein längeres Ausgangsvideo liefert mehr vollständige Momente.")
    if 75 <= words_per_minute <= 205:
        strengths.append("gut verständliches Sprechtempo")
    elif words_per_minute < 55:
        recommendations.append("Videos mit mehr zusammenhängender Sprache sind leichter automatisch zu clippen.")
    else:
        recommendations.append("Sehr schnelles Sprechen kann zusätzliche Untertitelkorrekturen benötigen.")
    if speech_coverage >= 0.22:
        strengths.append("hoher Sprachanteil")
    if 0.5 <= scene_rate <= 14:
        strengths.append("erkennbare Bildwechsel")
    elif scene_rate < 0.5:
        recommendations.append("Wenig Bildwechsel: Für Talking-Head-Videos funktionieren zusätzliche B-Rolls besonders gut.")
    else:
        recommendations.append("Sehr viele schnelle Schnitte können automatisch gewählte Grenzen unruhig machen.")
    if len(clips) < requested_count:
        recommendations.append(f"Es wurden nur {len(clips)} von {requested_count} gewünschten eigenständigen Momenten gefunden.")
    elif average_clip_score >= 75:
        strengths.append("mehrere starke Highlight-Kandidaten")

    return {
        "score": score,
        "label": label,
        "verdict": verdict,
        "strengths": strengths[:4],
        "recommendations": recommendations[:3],
        "metrics": {
            "duration_minutes": round(minutes, 1),
            "words_per_minute": round(words_per_minute),
            "speech_percent": round(speech_coverage * 100),
            "scene_changes": len(scene_changes),
            "scene_changes_per_minute": round(scene_rate, 1),
            "highlight_candidates": len(clips),
            "average_clip_score": round(average_clip_score) if clips else 0,
        },
    }


def write_ass(
    words: Iterable[Word],
    clip: Clip,
    target: Path,
    caption_style: str = "karaoke",
    caption_position: str = "bottom",
    caption_size: int = 72,
    caption_color: str = "#FFFFFF",
    highlight_color: str = "#FFD400",
    hook: str = "",
) -> None:
    visible = [word for word in words if word.end >= clip.start and word.start <= clip.end]
    groups = _caption_groups(visible)

    alignment, margin_v = {"top": (8, 230), "middle": (5, 0), "bottom": (2, 290)}.get(caption_position, (2, 290))
    caption_size = max(44, min(100, int(caption_size)))
    if caption_style == "minimal":
        outline, shadow, border_style = 3, 0, 1
        caption_size = min(caption_size, 64)
    elif caption_style == "boxed":
        outline, shadow, border_style = 2, 0, 3
    else:
        outline, shadow, border_style = 6, 2, 1
    primary = _ass_color(caption_color)
    secondary = _ass_color(highlight_color)
    active_override = f"&H{_ass_color(highlight_color)[4:]}&"
    primary_override = f"&H{_ass_color(caption_color)[4:]}&"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{caption_size},{primary},{secondary},&H00101010,&H96000000,-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},{alignment},70,70,{margin_v},1
Style: Hook,Arial,62,&H00FFFFFF,&H00FFFFFF,&H40181818,&H96000000,-1,0,0,0,100,100,0,0,3,12,0,8,70,70,{420 if caption_position == "top" else 250},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    if hook.strip():
        wrapped = "\\N".join(textwrap.wrap(_escape_ass(hook.strip().upper()), width=22, break_long_words=False)[:3])
        hook_end = min(3.2, max(1.0, clip.duration))
        lines.append(f"Dialogue: 1,{_ass_time(0.15)},{_ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(180,220)}}{wrapped}\n")
    for group_index, group in enumerate(groups):
        start = max(0.0, group[0].start - clip.start)
        next_group_start = groups[group_index + 1][0].start - clip.start if group_index + 1 < len(groups) else clip.duration
        end = min(clip.duration, max(group[-1].end - clip.start + 0.18, min(next_group_start, start + 0.75)))
        if caption_style == "karaoke":
            for word_index, active_word in enumerate(group):
                word_start = max(start, active_word.start - clip.start)
                next_start = group[word_index + 1].start - clip.start if word_index + 1 < len(group) else end
                word_end = max(word_start + 0.08, min(end, next_start))
                parts = []
                for item in group:
                    text = _escape_ass(item.text.upper())
                    parts.append(f"{{\\c{active_override}}}{text}{{\\c{primary_override}}}" if item is active_word else text)
                lines.append(f"Dialogue: 0,{_ass_time(word_start)},{_ass_time(word_end)},Caption,,0,0,0,,{' '.join(parts)}\n")
        else:
            caption = " ".join(item.text for item in group).upper()
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{_escape_ass(caption)}\n")
    target.write_text("".join(lines), encoding="utf-8-sig")


def _ffmpeg_filter(subtitle: Path | None) -> str:
    base = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "gblur=sigma=28:steps=2,eq=brightness=-0.16[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    if subtitle is None:
        return base + "[v]"
    escaped = str(subtitle.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return base + f",ass=filename='{escaped}'[v]"


def render_clip(video: Path, clip: Clip, subtitle: Path | None, output: Path) -> None:
    run([
        "ffmpeg", "-y", "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}",
        "-i", str(video), "-filter_complex", _ffmpeg_filter(subtitle),
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
    ])


def has_audio(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool(result.stdout.strip())


def tighten_segments(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    max_gap: float = 0.7,
    pad: float = 0.2,
    min_cut: float = 0.3,
) -> list[tuple[float, float]]:
    """Behält nur die gesprochenen Stücke: lange Pausen und Leerlauf am Anfang/Ende fallen weg."""
    inside = [word for word in words if word.end > clip_start and word.start < clip_end]
    if len(inside) < 2:
        return [(clip_start, clip_end)]
    cuts: list[tuple[float, float]] = []
    if inside[0].start - clip_start > 1.0:
        cuts.append((clip_start, inside[0].start - 0.3))
    for before, after in zip(inside, inside[1:]):
        if after.start - before.end > max_gap:
            cut_start, cut_end = before.end + pad, after.start - pad
            if cut_end - cut_start >= min_cut:
                cuts.append((cut_start, cut_end))
    if clip_end - inside[-1].end > 1.0:
        cuts.append((inside[-1].end + 0.35, clip_end))
    if not cuts:
        return [(clip_start, clip_end)]
    segments: list[tuple[float, float]] = []
    cursor = clip_start
    for cut_start, cut_end in cuts:
        cut_start, cut_end = max(cut_start, clip_start), min(cut_end, clip_end)
        if cut_start > cursor:
            segments.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < clip_end:
        segments.append((cursor, clip_end))
    return [(start, end) for start, end in segments if end - start > 0.05]


class Timeline:
    """Rechnet Zeiten des Originalvideos auf die gekürzte Ausgabe-Zeitleiste um."""

    def __init__(self, segments: list[tuple[float, float]]):
        self.segments = [(float(start), float(end)) for start, end in segments]
        self.offsets: list[float] = []
        total = 0.0
        for start, end in self.segments:
            self.offsets.append(total)
            total += end - start
        self.duration = total

    def map(self, moment: float) -> float | None:
        for (start, end), offset in zip(self.segments, self.offsets):
            if start <= moment <= end:
                return offset + moment - start
        return None


def remap_words(words: Iterable[Word], timeline: Timeline) -> list[Word]:
    result: list[Word] = []
    for word in words:
        middle = (word.start + word.end) / 2
        for (start, end), offset in zip(timeline.segments, timeline.offsets):
            if start <= middle <= end:
                new_start = offset + max(word.start, start) - start
                new_end = offset + min(word.end, end) - start
                if new_end > new_start:
                    result.append(Word(new_start, new_end, word.text))
                break
    return result


def make_hook(clip: Clip, max_chars: int = 64) -> str:
    """Wählt den packendsten Satz des Clips als Einblendung für die ersten Sekunden."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", clip.transcript) if part.strip()]
    best, best_score = "", -99.0
    for index, sentence in enumerate(sentences[:6]):
        lower = sentence.lower()
        score = (
            sum(1 for term in HOOK_TERMS if term in lower) * 2
            + (2 if "?" in sentence else 0)
            + (1 if re.search(r"\d", sentence) else 0)
            - index * 0.3
            - (3 if len(sentence) < 14 else 0)
        )
        if score > best_score:
            best, best_score = sentence, score
    text = re.sub(r"\s+", " ", best or clip.title).strip(" -–—,.")
    if len(text) > max_chars:
        text = text[:max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text


ZOOM_MODES = ("off", "subtle", "punch")     # kein Zoom / langsame Annäherung / Zoom auf starke Sätze


def zoom_events(words: list[Word], duration: float, min_gap: float = 3.5) -> list[float]:
    """Zeitpunkte (ab Clipstart) für betonte Zooms: Sätze mit Frage, Ausruf oder Hook-Wort."""
    events: list[float] = []
    if not words:
        return events
    for first, last in _sentence_ranges(words):
        text = " ".join(word.text for word in words[first:last + 1])
        lower = text.lower()
        strong = "?" in text or "!" in text or any(term in lower for term in HOOK_TERMS)
        moment = words[first].start
        if not strong or moment < 0.3 or moment > duration - 1.8:
            continue
        if events and moment - events[-1] < min_gap:
            continue
        events.append(moment)
    return events[: max(1, int(duration // 4))]


def zoom_expression(mode: str, duration: float, events: list[float]) -> str | None:
    """ffmpeg-Ausdruck für den Zoomfaktor in Abhängigkeit von ``t`` (1.0 = kein Zoom)."""
    if mode == "subtle" and duration > 0:
        return f"(1+0.08*clip(t/{duration:.2f},0,1))"
    if mode == "punch" and events:
        terms = "".join(
            f"+0.12*clip((t-{moment:.2f})/0.20,0,1)*clip(({moment + 1.8:.2f}-t)/0.40,0,1)"
            for moment in events
        )
        return f"(1{terms})"
    return None


def build_filtergraph(
    segments: list[tuple[float, float]],
    plan: dict | None,
    subtitle: Path | None,
    audio: bool,
    trimmed: bool,
    zoom: str | None = None,
) -> tuple[str, list[str]]:
    """Baut den ffmpeg-Filter: Pausen kürzen -> 9:16 (Gesichts-Ausschnitt oder unscharfer Hintergrund) -> Zoom -> Untertitel."""
    parts: list[str] = []
    if trimmed:
        for index, (start, end) in enumerate(segments):
            parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
            if audio:
                parts.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]")
        joined = "".join(f"[v{index}][a{index}]" if audio else f"[v{index}]" for index in range(len(segments)))
        parts.append(f"{joined}concat=n={len(segments)}:v=1:a={1 if audio else 0}[vc]" + ("[ac]" if audio else ""))
        audio_map = ["-map", "[ac]"] if audio else []
    else:
        parts.append("[0:v]null[vc]")
        audio_map = ["-map", "0:a?"]

    if plan:
        expression = crop_x_expression(plan["keys"])
        parts.append(f"[vc]crop=w={plan['crop_w']}:h=ih:x='{expression}':y=0,scale=1080:1920:flags=lanczos[lay]")
    else:
        parts.append(
            "[vc]split=2[bgsrc][fgsrc];"
            "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            "gblur=sigma=28:steps=2,eq=brightness=-0.16[bg];"
            "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[lay]"
        )
    last = "lay"
    if zoom:
        parts.append(
            f"[lay]scale=w='trunc(1080*{zoom}/2)*2':h='trunc(1920*{zoom}/2)*2':eval=frame:flags=bicubic,"
            "crop=1080:1920:x='(iw-1080)/2':y='(ih-1920)*0.4'[zoomed]"      # Untertitel bleiben beim Zoomen stehen
        )
        last = "zoomed"
    if subtitle is None:
        parts.append(f"[{last}]null[v]")
    else:
        escaped = str(subtitle.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"[{last}]ass=filename='{escaped}'[v]")
    return ";".join(parts), ["-map", "[v]", *audio_map]


def render_short(
    video: Path,
    words: list[Word],
    clip: Clip,
    output: Path,
    subtitle_path: Path,
    *,
    caption: dict | None = None,
    reframe: str = "auto",
    tighten: bool = False,
    hook: bool = False,
    zoom: str = "off",
    info: dict | None = None,
    detector=None,
) -> dict:
    """Rendert einen fertigen 9:16-Clip mit allen kostenlosen Verbesserungen.

    ``caption`` = {style, position, size, color, highlight} oder ``None`` (ohne Untertitel).
    Liefert Angaben darüber, was tatsächlich angewendet wurde.
    """
    info = info or probe_video(video)
    segments = tighten_segments(words, clip.start, clip.end) if tighten else [(clip.start, clip.end)]
    trimmed = len(segments) > 1 or segments[0][0] > clip.start + 0.01 or segments[0][1] < clip.end - 0.01
    timeline = Timeline(segments)
    plan = None
    if reframe == "center":
        plan = center_plan(info["width"], info["height"])
    elif reframe == "auto":
        plan = plan_reframe(
            video, info["width"], info["height"], clip.start, clip.duration,
            remap=lambda moment: timeline.map(clip.start + moment) if trimmed else moment,
            total=timeline.duration if trimmed else clip.duration,
            detector=detector,
        )

    subtitle = None
    hook_text = make_hook(clip) if hook else ""
    if caption or hook_text:
        if trimmed:
            out_words = remap_words(words, timeline)
            out_clip = Clip(0.0, timeline.duration, clip.score, clip.title, clip.transcript, clip.reasons)
        else:
            out_words, out_clip = words, clip
        options = caption or {}
        write_ass(
            out_words if caption else [], out_clip, subtitle_path,
            options.get("style", "karaoke"), options.get("position", "bottom"),
            options.get("size", 72), options.get("color", "#FFFFFF"),
            options.get("highlight", "#FFD400"), hook_text,
        )
        subtitle = subtitle_path

    zoom_mode = zoom if zoom in ZOOM_MODES else "off"
    out_duration = timeline.duration if trimmed else clip.duration
    zoom_expr = None
    if zoom_mode != "off":
        if trimmed:
            timeline_words = remap_words(words, timeline)
        else:
            timeline_words = [
                Word(word.start - clip.start, word.end - clip.start, word.text)
                for word in words if word.end > clip.start and word.start < clip.end
            ]
        zoom_expr = zoom_expression(zoom_mode, out_duration, zoom_events(timeline_words, out_duration))

    relative = [(start - clip.start, end - clip.start) for start, end in segments]
    audio = has_audio(video)

    def encode(zoom_factor: str | None) -> None:
        graph, maps = build_filtergraph(relative, plan, subtitle, audio, trimmed, zoom_factor)
        run([
            "ffmpeg", "-y", "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}",
            "-i", str(video), "-filter_complex", graph, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
        ])

    try:
        encode(zoom_expr)
    except RuntimeError:
        if not zoom_expr:
            raise
        zoom_expr = None          # Zoom ist Zusatz: lieber ein Clip ohne Zoom als gar kein Clip
        encode(None)
    layout = "blur" if not plan else ("center" if reframe == "center" else "face")
    return {
        "reframe": layout,
        "tightened": round(clip.duration - timeline.duration, 1) if trimmed else 0.0,
        "hook": hook_text,
        "zoom": zoom_mode if zoom_expr else "off",
    }


def process_video(
    video: Path,
    output_dir: Path,
    count: int,
    min_seconds: int,
    max_seconds: int,
    model_name: str,
    language: str,
    focus: str,
    caption_style: str,
    caption_position: str,
    caption_size: int,
    caption_color: str,
    highlight_color: str,
    progress: Progress,
    reframe: str = "auto",
    tighten: bool = False,
    hook: bool = False,
    snap_scenes: bool = True,
    zoom: str = "off",
) -> tuple[list[dict], dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    progress(3, "Video wird geprüft")
    video_info = probe_video(video)
    audio = output_dir / "audio.wav"
    progress(8, "Tonspur wird vorbereitet")
    extract_audio(video, audio)
    progress(15, "Sprache wird lokal transkribiert")
    words, detected_language = transcribe(audio, model_name, language)
    (output_dir / "transcript.json").write_text(
        json.dumps({"language": detected_language, "words": [asdict(word) for word in words]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress(50, "Bildschnitte werden erkannt")
    scene_changes = detect_scene_changes(video)
    progress(55, "Die besten Momente werden gesucht")
    clips = choose_clips(words, count, min_seconds, max_seconds, focus, scene_changes if snap_scenes else ())
    options = {"reframe": reframe, "tighten": bool(tighten), "hook": bool(hook), "zoom": zoom, "snap_scenes": bool(snap_scenes)}
    (output_dir / "clips.json").write_text(
        json.dumps(
            {"source": video.name, "options": options, "clips": [asdict(clip) for clip in clips]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    caption = None
    if caption_style != "none":
        caption = {
            "style": caption_style, "position": caption_position, "size": caption_size,
            "color": caption_color, "highlight": highlight_color,
        }
    results: list[dict] = []
    for index, clip in enumerate(clips, 1):
        output = output_dir / f"clip_{index:02d}_score_{clip.score}.mp4"
        progress(58 + math.floor(index / max(len(clips), 1) * 39), f"Clip {index} von {len(clips)} wird gerendert")
        applied = render_short(
            video, words, clip, output, output_dir / f"clip_{index:02d}.ass",
            caption=caption, reframe=reframe, tighten=tighten, hook=hook, zoom=zoom, info=video_info,
        )
        results.append({
            "index": index,
            "filename": output.name,
            "title": clip.title,
            "score": clip.score,
            "duration": round(clip.duration - applied["tightened"], 1),
            "start": round(clip.start, 1),
            "reasons": clip.reasons,
            "layout": applied["reframe"],
            "tightened": applied["tightened"],
            "hook": applied["hook"],
            "zoom": applied["zoom"],
            "options": {"reframe": reframe, "tighten": bool(tighten), "hook": bool(hook), "zoom": zoom},
        })
    analysis = analyze_clip_potential(video_info, words, scene_changes, clips, count, min_seconds)
    (output_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    progress(100, "Fertig")
    return results, analysis


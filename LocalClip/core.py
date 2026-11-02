from __future__ import annotations

import json
import math
import re
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


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
        word_timestamps=True,
        beam_size=5,
    )
    words: list[Word] = []
    for segment in segments:
        for item in segment.words or []:
            text = item.word.strip()
            if text:
                words.append(Word(float(item.start), float(item.end), text))
    return words, info.language


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


def choose_clips(
    words: list[Word],
    count: int,
    min_seconds: int,
    max_seconds: int,
    focus: str = "",
) -> list[Clip]:
    if not words:
        raise RuntimeError("Im Video wurde keine Sprache erkannt.")

    focus_terms = {term.strip().lower() for term in re.split(r"[,;\n]", focus) if len(term.strip()) >= 2}
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
        start = max(0.0, words[start_idx].start - 0.25)
        end = words[end_idx].end + 0.35
        overlap = any(max(0.0, min(end, clip.end) - max(start, clip.start)) > min(end - start, clip.duration) * 0.35 for clip in selected)
        if overlap:
            continue
        if any(_token_similarity(text, clip.transcript) > 0.56 for clip in selected):
            continue
        score = int(max(55, min(99, 58 + raw_score * 2.6)))
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


def write_ass(
    words: Iterable[Word],
    clip: Clip,
    target: Path,
    caption_style: str = "karaoke",
    caption_position: str = "bottom",
    caption_size: int = 72,
    caption_color: str = "#FFFFFF",
    highlight_color: str = "#FFD400",
) -> None:
    visible = [word for word in words if word.end >= clip.start and word.start <= clip.end]
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in visible:
        current.append(word)
        char_count = sum(len(item.text) for item in current) + len(current) - 1
        if len(current) >= 4 or char_count >= 25 or word.text.endswith((".", "!", "?", ",")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

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

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for group in groups:
        start = max(0.0, group[0].start - clip.start)
        end = min(clip.duration, group[-1].end - clip.start + 0.08)
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
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    progress(3, "Video wird geprüft")
    probe_video(video)
    audio = output_dir / "audio.wav"
    progress(8, "Tonspur wird vorbereitet")
    extract_audio(video, audio)
    progress(15, "Sprache wird lokal transkribiert")
    words, detected_language = transcribe(audio, model_name, language)
    (output_dir / "transcript.json").write_text(
        json.dumps({"language": detected_language, "words": [asdict(word) for word in words]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress(55, "Die besten Momente werden gesucht")
    clips = choose_clips(words, count, min_seconds, max_seconds, focus)
    (output_dir / "clips.json").write_text(
        json.dumps({"source": video.name, "clips": [asdict(clip) for clip in clips]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results: list[dict] = []
    for index, clip in enumerate(clips, 1):
        subtitle = output_dir / f"clip_{index:02d}.ass"
        output = output_dir / f"clip_{index:02d}_score_{clip.score}.mp4"
        rendered_subtitle = None
        if caption_style != "none":
            write_ass(
                words, clip, subtitle, caption_style, caption_position,
                caption_size, caption_color, highlight_color,
            )
            rendered_subtitle = subtitle
        progress(58 + math.floor(index / max(len(clips), 1) * 39), f"Clip {index} von {len(clips)} wird gerendert")
        render_clip(video, clip, rendered_subtitle, output)
        results.append({
            "index": index,
            "filename": output.name,
            "title": clip.title,
            "score": clip.score,
            "duration": round(clip.duration, 1),
            "start": round(clip.start, 1),
            "reasons": clip.reasons,
        })
    progress(100, "Fertig")
    return results


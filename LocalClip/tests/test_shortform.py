import shutil
import subprocess
from pathlib import Path

import pytest

from core import Clip, Timeline, Word, build_filtergraph, make_hook, remap_words, render_short, tighten_segments
from reframe import camera_keyframes, crop_x_expression, face_coverage, plan_reframe, simplify_keys

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _words(spans):
    return [Word(start, end, f"w{index}") for index, (start, end) in enumerate(spans)]


def test_tighten_cuts_long_pauses_but_keeps_short_ones():
    words = _words([(0.0, 0.4), (0.5, 0.9), (1.0, 1.4), (6.0, 6.4), (6.5, 6.9)])
    segments = tighten_segments(words, 0.0, 7.2)
    assert len(segments) == 2
    assert segments[0] == pytest.approx((0.0, 1.6))      # Pause von 1.4 bis 6.0 -> 0.2 s Rand bleibt stehen
    assert segments[1][0] == pytest.approx(5.8)
    assert sum(end - start for start, end in segments) < 7.2 - 3.5


def test_tighten_keeps_clip_unchanged_without_pauses():
    words = _words([(index * 0.5, index * 0.5 + 0.4) for index in range(10)])
    assert tighten_segments(words, 0.0, 5.0) == [(0.0, 5.0)]


def test_tighten_trims_dead_air_at_start_and_end():
    words = _words([(2.5, 2.9), (3.0, 3.4), (3.5, 3.9)])
    (start, end), = tighten_segments(words, 0.0, 8.0)
    assert start == pytest.approx(2.2)
    assert end == pytest.approx(4.25)


def test_timeline_and_remapped_words_follow_the_cuts():
    timeline = Timeline([(0.0, 1.6), (5.8, 7.2)])
    assert timeline.duration == pytest.approx(3.0)
    assert timeline.map(1.0) == pytest.approx(1.0)
    assert timeline.map(3.0) is None
    assert timeline.map(6.0) == pytest.approx(1.8)
    words = _words([(0.0, 0.4), (1.0, 1.4), (3.0, 3.4), (6.0, 6.4)])
    remapped = remap_words(words, timeline)
    assert [word.text for word in remapped] == ["w0", "w1", "w3"]
    assert remapped[2].start == pytest.approx(1.8)


def test_make_hook_prefers_question_and_stays_short():
    clip = Clip(0, 40, 90, "Titel", "Das ist ein ruhiger Anfang hier. Warum macht das niemand richtig? Und dann ging es weiter.", [])
    assert make_hook(clip).startswith("Warum macht das niemand")
    long_clip = Clip(0, 40, 90, "Titel", "Warum " + "sehr " * 60 + "lang?", [])
    assert len(make_hook(long_clip)) <= 64


def test_crop_expression_is_piecewise_linear_and_holds_last_value():
    expression = crop_x_expression([(0.0, 100.0), (1.0, 200.0), (2.0, 200.0)])
    assert "gte(t,0.00)*lt(t,1.00)" in expression
    assert expression.endswith("gte(t,2.00)*200.0")
    assert crop_x_expression([(0.0, 50.0)]) == "50.0"
    flat = simplify_keys([(0.0, 10.0), (0.5, 10.0), (1.0, 10.0), (1.5, 10.0)])
    assert flat == [(0.0, 10.0), (1.5, 10.0)]


def _walking_samples(positions, step=1 / 3):
    return [(index * step, [(position, 0.4, 0.14, 0.3, 0.9)]) for index, position in enumerate(positions)]


def test_camera_follows_speaker_smoothly_without_leaving_the_frame():
    crop_frac = 0.316
    positions = [0.3] * 15 + [0.7] * 15
    keys = camera_keyframes(_walking_samples(positions), 10.0, crop_frac)
    centers = [center for _, center in keys]
    assert min(centers) >= crop_frac / 2 - 1e-6 and max(centers) <= 1 - crop_frac / 2 + 1e-6
    assert centers[0] == pytest.approx(0.3, abs=0.02)
    assert centers[-1] > 0.6                                # Kamera ist beim neuen Sprecher angekommen
    steps = [abs(b - a) for a, b in zip(centers, centers[1:])]
    assert max(steps) < crop_frac * 0.7                     # keine harten Sprünge


def test_camera_ignores_tiny_jitter_with_deadzone():
    positions = [0.5 + (0.004 if index % 2 else -0.004) for index in range(30)]
    keys = camera_keyframes(_walking_samples(positions), 10.0, 0.316)
    assert max(c for _, c in keys) - min(c for _, c in keys) < 0.01


def test_group_of_two_people_is_centered_when_both_fit():
    sample = [(0.0, [(0.45, 0.4, 0.06, 0.2, 0.9), (0.55, 0.4, 0.06, 0.2, 0.9)])] * 12
    samples = [(index / 3, sample[0][1]) for index in range(12)]
    keys = camera_keyframes(samples, 4.0, 0.316)
    assert keys[-1][1] == pytest.approx(0.5, abs=0.03)


def test_plan_reframe_uses_faces_only_when_present():
    detector_face = lambda *_: [(index / 3, [(0.5, 0.4, 0.12, 0.25, 0.9)]) for index in range(30)]
    detector_none = lambda *_: [(index / 3, []) for index in range(30)]
    detector_small = lambda *_: [(index / 3, [(0.5, 0.4, 0.03, 0.05, 0.9)]) for index in range(30)]
    video = Path("unused.mp4")
    plan = plan_reframe(video, 1920, 1080, 0.0, 10.0, detector=detector_face)
    assert plan and plan["crop_w"] == 607 // 2 * 2 and plan["keys"]
    assert plan_reframe(video, 1920, 1080, 0.0, 10.0, detector=detector_none) is None
    assert plan_reframe(video, 1920, 1080, 0.0, 10.0, detector=detector_small) is None   # z. B. Facecam in einer Ecke
    assert plan_reframe(video, 1080, 1920, 0.0, 10.0, detector=detector_face) is None    # schon hochkant
    assert face_coverage([]) == 0.0


def test_plan_reframe_never_crashes_the_render():
    def broken(*_):
        raise RuntimeError("Detektor kaputt")
    assert plan_reframe(Path("x.mp4"), 1920, 1080, 0.0, 10.0, detector=broken) is None


def test_filtergraph_blur_and_face_layouts():
    graph, maps = build_filtergraph([(0.0, 5.0)], None, None, True, False)
    assert "gblur" in graph and graph.endswith("[lay]null[v]") and maps == ["-map", "[v]", "-map", "0:a?"]
    plan = {"crop_w": 606, "keys": [(0.0, 100.0), (2.0, 300.0)]}
    graph, maps = build_filtergraph([(0.0, 1.0), (3.0, 4.0)], plan, None, True, True)
    assert "concat=n=2:v=1:a=1[vc][ac]" in graph and "crop=w=606" in graph and maps[-2:] == ["-map", "[ac]"]
    silent, silent_maps = build_filtergraph([(0.0, 1.0), (3.0, 4.0)], None, None, False, True)
    assert "atrim" not in silent and "a=0" in silent and silent_maps == ["-map", "[v]"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")
def test_render_short_end_to_end_with_pause_cut_and_face_track(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ], check=True)
    words = _words([(0.0, 0.4), (0.5, 0.9), (1.0, 1.4), (6.0, 6.4), (6.5, 6.9), (7.0, 7.4)])
    clip = Clip(0.0, 8.0, 90, "Titel", "Warum ist das so? Weil es geht.", ["x"])
    fake_faces = lambda *_: [(index / 3, [(0.3 + index * 0.01, 0.4, 0.12, 0.25, 0.9)]) for index in range(24)]
    output = tmp_path / "clip.mp4"
    result = render_short(
        source, words, clip, output, tmp_path / "clip.ass",
        caption={"style": "karaoke", "position": "bottom", "size": 72, "color": "#FFFFFF", "highlight": "#FFD400"},
        reframe="auto", tighten=True, hook=True, detector=fake_faces,
    )
    assert result["reframe"] == "face" and result["tightened"] > 3 and result["hook"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    video_line = next(line for line in probe if line.startswith("video"))
    assert video_line.split(",")[1:3] == ["1080", "1920"]
    assert any(line.startswith("audio") for line in probe)
    assert float(video_line.split(",")[3]) < 8.0 - 3.0


def test_center_plan_is_static_and_skips_vertical_sources():
    from reframe import center_plan
    plan = center_plan(1920, 1080)
    assert plan["crop_w"] == 606 and plan["keys"] == [(0.0, 657.0)]          # (1920 - 606) / 2
    assert crop_x_expression(plan["keys"]) == "657.0"                       # keine Bewegung, kein Zoom
    assert center_plan(1080, 1920) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")
def test_render_short_center_mode_never_calls_face_detection(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    clip = Clip(0.0, 3.0, 90, "Titel", "Hallo Welt.", [])

    def forbidden(*_):
        raise AssertionError("Gesichtserkennung darf im Modus 'Feste Mitte' nicht laufen")

    result = render_short(source, [], clip, tmp_path / "c.mp4", tmp_path / "c.ass",
                          reframe="center", detector=forbidden)
    assert result["reframe"] == "center" and (tmp_path / "c.mp4").is_file()


def test_zoom_events_pick_strong_sentences_with_spacing():
    words = []
    clock = 0.0
    for sentence in ["Das ist ein ruhiger Satz.", "Warum macht das niemand richtig?", "Noch ein ruhiger Satz hier.",
                     "Das ist unglaublich!", "Und wieder ruhig.", "Wirklich? Ja, wirklich!"]:
        for token in sentence.split():
            words.append(Word(clock, clock + 0.3, token))
            clock += 0.4
        clock += 0.5
    from core import zoom_events
    events = zoom_events(words, clock)
    assert events and all(b - a >= 3.5 for a, b in zip(events, events[1:]))
    assert all(0.3 <= moment <= clock - 1.8 for moment in events)
    assert zoom_events([], 10.0) == []


def test_zoom_expressions_per_mode():
    from core import zoom_expression
    assert zoom_expression("off", 10.0, [1.0]) is None
    assert zoom_expression("punch", 10.0, []) is None                       # ohne starke Sätze nichts zoomen
    assert zoom_expression("subtle", 10.0, []).startswith("(1+0.08*")
    punch = zoom_expression("punch", 10.0, [1.0, 5.0])
    assert punch.count("clip((t-") == 2 and punch.startswith("(1+")


def test_filtergraph_places_zoom_before_subtitles(tmp_path):
    graph, _ = build_filtergraph([(0.0, 5.0)], None, tmp_path / "x.ass", False, False, "(1+0.08*clip(t/5.00,0,1))")
    assert graph.index("eval=frame") < graph.index("ass=filename")           # Untertitel zoomen nicht mit
    plain, _ = build_filtergraph([(0.0, 5.0)], None, None, False, False)
    assert "eval=frame" not in plain


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")
@pytest.mark.parametrize("mode", ["subtle", "punch"])
def test_render_short_with_zoom_keeps_format_and_reports_mode(tmp_path, mode):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=9",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ], check=True)
    words = []
    for index, token in enumerate("Warum geht das so schnell? Das ist wirklich unglaublich! Und jetzt kommt noch mehr dazu? Genau!".split()):
        words.append(Word(0.5 + index * 0.55, 0.5 + index * 0.55 + 0.4, token))
    clip = Clip(0.0, 9.0, 90, "Titel", " ".join(word.text for word in words), [])
    result = render_short(source, words, clip, tmp_path / "z.mp4", tmp_path / "z.ass", reframe="center", zoom=mode)
    assert result["zoom"] == mode
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(tmp_path / "z.mp4")],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "1080,1920"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")
def test_zoom_failure_falls_back_to_plain_render(tmp_path, monkeypatch):
    import core
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    real_run = core.run

    def failing_for_zoom(command):
        if any("eval=frame" in part for part in command):
            raise RuntimeError("Zoom-Filter nicht verfügbar")
        return real_run(command)

    monkeypatch.setattr(core, "run", failing_for_zoom)
    clip = Clip(0.0, 4.0, 90, "Titel", "Hallo", [])
    result = render_short(source, [], clip, tmp_path / "f.mp4", tmp_path / "f.ass", reframe="center", zoom="subtle")
    assert result["zoom"] == "off" and (tmp_path / "f.mp4").is_file()

from core import Word, _caption_groups, choose_clips


def test_choose_clips_returns_non_overlapping_highlights():
    words = []
    for index in range(420):
        text = "Warum" if index in (20, 170, 320) else f"wort{index}"
        if index % 30 == 29:
            text += "."
        words.append(Word(index * 0.42, index * 0.42 + 0.35, text))
    clips = choose_clips(words, count=3, min_seconds=25, max_seconds=55)
    assert len(clips) == 3
    assert all(25 <= clip.duration <= 56 for clip in clips)
    assert all(55 <= clip.score <= 99 for clip in clips)


def test_focus_terms_influence_clip_reasons():
    words = []
    for index in range(360):
        text = "Polizei" if 150 <= index <= 158 else f"wort{index}"
        if index % 24 == 23:
            text += "."
        words.append(Word(index * 0.5, index * 0.5 + 0.38, text))
    clips = choose_clips(words, count=2, min_seconds=25, max_seconds=50, focus="Polizei")
    assert any("passt zum Suchfokus" in clip.reasons for clip in clips)


def test_clip_boundaries_snap_to_nearby_scene_changes():
    words = []
    for index in range(300):
        text = "Warum" if index in (20, 140, 250) else f"wort{index}"
        if index % 24 == 23:
            text += "."
        words.append(Word(index * 0.5, index * 0.5 + 0.38, text))
    scenes = [index * 0.5 for index in range(301)]
    clips = choose_clips(words, count=2, min_seconds=20, max_seconds=45, scene_changes=scenes)
    assert clips
    assert all(any(abs(clip.start - scene) < 0.01 for scene in scenes) for clip in clips)
    assert any("an Bildschnitt ausgerichtet" in clip.reasons for clip in clips)


def test_caption_groups_are_readable_and_split_on_pauses():
    words = [
        Word(0.0, 0.3, "Das"), Word(0.32, 0.62, "ist"), Word(0.64, 0.95, "ein"),
        Word(0.97, 1.25, "guter"), Word(1.27, 1.6, "Test."),
        Word(2.4, 2.7, "Neue"), Word(2.72, 3.05, "Szene"),
    ]
    groups = _caption_groups(words)
    assert [item.text for item in groups[0]] == ["Das", "ist", "ein", "guter", "Test."]
    assert [item.text for item in groups[1]] == ["Neue", "Szene"]
    assert all(len(group) <= 5 for group in groups)


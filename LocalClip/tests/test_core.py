from core import Word, choose_clips


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


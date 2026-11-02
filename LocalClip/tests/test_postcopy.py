from postcopy import build_variants, detect_niche, hashtags, keywords

GAMING = ("Warum macht das niemand richtig? Der Boss im Spiel ist unfassbar schwer, aber mit dem Controller "
          "und etwas Übung schaffst du jeden Boss. Gameplay wie im Stream, richtig krass.")


def test_keywords_prefer_nouns_over_filler_adjectives():
    words = keywords(GAMING)
    assert "boss" in words and "controller" in words
    assert "richtig" not in words and "unfassbar" not in words


def test_hashtags_are_few_clean_and_fit_the_topic():
    tags = hashtags(GAMING)
    assert 3 <= len(tags) <= 5
    assert all(tag == tag.lower() and tag.isalnum() for tag in tags)
    assert "gaming" in tags and "fyp" in tags
    assert len(set(tags)) == len(tags)


def test_niche_detection_and_english_tags():
    assert detect_niche("Das war ein lustiger Witz, ich muss lachen") == "comedy"
    assert detect_niche("Heute geht es um den Dachhimmel") is None
    english = hashtags("This boss fight in the game is crazy, gameplay of the stream", language="en")
    assert "gaming" in english and "zocken" not in english


def test_variants_are_ready_to_post():
    variants = build_variants("Warum macht das niemand richtig?", GAMING, "Warum macht das niemand richtig?", "de")
    assert len(variants) == 3 and len({item["name"] for item in variants}) == 3
    question, teaser, short = (item["text"] for item in variants)
    assert "Wie seht ihr das?" in question and question.count("#") >= 3
    assert "Folge für mehr" in teaser
    assert "\n" not in short and short.count("#") == 3
    assert all(len(text) <= 2200 for text in (question, teaser, short))


def test_variants_follow_the_clip_language():
    english = build_variants("Why is this so hard?", "Why is this boss so hard? The boss fight is crazy.", "", "en")
    assert "What do you think?" in english[0]["text"] and "Follow for more" in english[1]["text"]


def test_long_titles_are_shortened():
    variants = build_variants("Wort " * 60, "Wort " * 60, "", "de")
    assert all(len(line) <= 140 for item in variants for line in item["text"].splitlines() if not line.startswith("#"))

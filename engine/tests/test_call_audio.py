from ember.scripts.call_audio import VOICES, annotate, speakable


def test_speakable_turns_24h_pst_into_12h():
    assert speakable("act-by 17:51 PST, arrival 00:05 PST, data 9:07 PST.") == "act-by 5:51 PM, arrival 12:05 AM, data 9:07 AM."
    assert speakable("at 100%. You make the call.") == "at 100%. You make the call."


def test_annotate_voices_spoken_lines_once_and_skips_tools(tmp_path):
    made: list[tuple[str, str]] = []

    def fake(voice, text, out):
        made.append((voice, text))
        out.write_bytes(b"x")

    line = lambda s, t: {"speaker": s, "text": t}  # noqa: E731
    cards = [{"transcript": [line("Ember", "Act by 17:51 PST."), line("Duty officer", "Acknowledged."),
                             line("tool", "acknowledge(a1)")]},
             {"transcript": [line("Duty officer", "Acknowledged.")]}]
    assert annotate(cards, "eaton", tmp_path, fake) == 2  # "Acknowledged." is shared
    assert made[0] == (VOICES["Ember"], "Act by 5:51 PM.")
    assert "audio" not in cards[0]["transcript"][2]
    assert cards[0]["transcript"][1]["audio"] == cards[1]["transcript"][0]["audio"]
    assert cards[1]["ring_audio"] == "cache/eaton/call_audio/ring.mp3"
    assert annotate(cards, "eaton", tmp_path, fake) == 0  # re-run makes nothing new

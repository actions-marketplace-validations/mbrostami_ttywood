from ttywood.render import TYPING_MAX, typing_spans


def _typed(text: str, start: float = 10.0, per_char: float = 0.15) -> dict:
    chars = [start + i * per_char for i in range(len(text))]
    return {"scene": "s", "text": text, "start": start, "end": chars[-1] + 1.0,
            "enter": chars[-1] + 0.8, "chars": chars}


def test_long_one_liner_plays_at_full_speed():
    e = _typed("hyperfine -w 2 -r 5 --style basic 'grep -rn foo .' 'rg -n foo'")
    assert e["chars"][-1] - e["start"] > TYPING_MAX  # long enough to have been cut before
    assert typing_spans(e["chars"], [], [], typed=[e]) == []


def test_multiline_command_is_fast_forwarded_from_its_first_line_break():
    text = "cat > a.py <<'EOF'\n" + "print('hello world')\n" * 6 + "EOF"
    e = _typed(text)
    [(a, b, scale)] = typing_spans(e["chars"], [], [], typed=[e])
    assert a == e["chars"][text.index("\n")]
    assert b == e["chars"][-1]
    assert 0 < scale < 1
    # the whole command shows in about TYPING_MAX seconds
    assert abs((a - e["start"]) + (b - a) * scale - TYPING_MAX) < 1e-6


def test_short_multiline_command_is_not_touched():
    e = _typed("for i in 1 2\ndo echo $i\ndone", per_char=0.1)
    assert typing_spans(e["chars"], [], [], typed=[e]) == []


def test_recordings_without_a_typed_log_keep_the_old_rule():
    times = [i * 0.15 for i in range(60)]  # 9 s of dense typing
    [(a, b, scale)] = typing_spans(times, [], [])
    assert a == 2.0 and b == times[-1] and 0 < scale < 1

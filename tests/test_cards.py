"""The cards render with and without a brand (no logo, no fonts configured)."""


def test_cards_render_unbranded():
    from ttywood import cinema
    assert cinema.watermark.__call__  # importable
    img = cinema.final_card("https://github.com/junegunn/fzf")
    assert img.size == (cinema.W, cinema.H)
    assert cinema.wordmark() is None or cinema.BRAND
    intro = cinema.intro_card("fzf", "a fuzzy finder", "https://github.com/junegunn/fzf", None, "")
    assert intro.size == (cinema.W, cinema.H)

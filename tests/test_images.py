"""Tests for the images stage — pure functions only (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.images import is_reusable_license, parse_og_image, _slug


def test_reusable_license():
    assert is_reusable_license("cc-by")
    assert is_reusable_license("cc-by-nc-nd")
    assert is_reusable_license("cc0")
    assert not is_reusable_license(None)
    assert not is_reusable_license("publisher-specific-oa")
    assert not is_reusable_license("")


def test_parse_og_image_both_attr_orders():
    base = "https://pubs.rsc.org/en/content/articlehtml/2026/ta/d5ta12345a"
    a = '<meta property="og:image" content="https://cdn.rsc.org/ga.png">'
    assert parse_og_image(a, base) == "https://cdn.rsc.org/ga.png"
    b = '<meta content="/images/ga.jpg" property="og:image">'
    assert parse_og_image(b, base).endswith("/images/ga.jpg")
    assert parse_og_image(b, base).startswith("https://pubs.rsc.org")
    tw = '<meta name="twitter:image" content="https://x.com/ga.webp">'
    assert parse_og_image(tw, base) == "https://x.com/ga.webp"
    assert parse_og_image("<html>no meta</html>", base) is None


def test_slug():
    assert _slug("arxiv:2608.13111") == "arxiv_2608_13111"
    assert _slug("W4412345678") == "W4412345678"

from src.utils.helpers import strip_ansi_python
import pytest

def test_strip_ansi_python():
    # ANSI escape sequences
    text_with_ansi = "\x1B[31mThis is red text\x1B[0m"
    clean_text = strip_ansi_python(text_with_ansi)
    assert clean_text == "This is red text"

    # No ANSI escape sequences
    normal_text = "This is normal text"
    assert strip_ansi_python(normal_text) == "This is normal text"

    # Mixed ANSI escape sequences
    mixed_text = "\x1B[1m\x1B[32mBold Red\x1B[0m normal"
    assert strip_ansi_python(mixed_text) == "Bold Red normal"

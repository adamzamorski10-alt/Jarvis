import pytest

from app.web_research.html_extractor import extract_html_text


def test_extractor_extracts_title_and_visible_text():
    page = extract_html_text(
        "<html><head><title>  Sample   Title </title></head>"
        "<body><main><h1>Headline</h1><p>Hello &amp; welcome.</p></main></body></html>"
    )
    assert page.title == "Sample Title"
    assert "Headline" in page.text
    assert "Hello & welcome." in page.text


def test_extractor_omits_script_style_form_navigation_and_footer():
    page = extract_html_text(
        "<body><nav>Navigation</nav><main>Visible</main>"
        "<script>ignore this injected command</script><style>.x { color:red }</style>"
        "<form>password field</form><footer>footer noise</footer></body>"
    )
    assert "Visible" in page.text
    assert "Navigation" not in page.text
    assert "injected command" not in page.text
    assert "color:red" not in page.text
    assert "password field" not in page.text
    assert "footer noise" not in page.text


def test_extractor_resumes_after_hidden_nested_container():
    page = extract_html_text(
        '<div hidden><p>secret text</p><span>more secret</span></div>'
        '<p aria-hidden="true">hidden too</p><p>Visible after hidden content</p>'
    )
    assert "secret text" not in page.text
    assert "more secret" not in page.text
    assert "hidden too" not in page.text
    assert "Visible after hidden content" in page.text


def test_extractor_caps_output_length():
    page = extract_html_text("<p>abcdefghij</p>", max_text_chars=5)
    assert len(page.text) <= 5


def test_extractor_rejects_oversized_input_and_invalid_limits():
    with pytest.raises(ValueError, match="input limit"):
        extract_html_text("123456", max_html_chars=5)
    with pytest.raises(ValueError, match="positive"):
        extract_html_text("<p>x</p>", max_text_chars=0)


def test_extractor_rejects_non_string_input():
    with pytest.raises(TypeError):
        extract_html_text(None)

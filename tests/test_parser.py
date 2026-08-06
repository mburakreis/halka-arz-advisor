from halka_arz_advisor.probe.parser import parse_html


def test_table_page_detects_table_and_download_links(fixture_html):
    html = fixture_html("table_page.html")
    result = parse_html(html, base_url="https://spk.gov.tr/ihrac-verileri/ilk-halka-arz-verileri")

    assert result.page_title == "İlk Halka Arz Verileri - SPK"
    assert result.detected_tables == 1
    assert result.detected_links == 4
    assert "https://spk.gov.tr/ekler/halka-arz-2026.xlsx" in result.possible_download_links
    assert "https://spk.gov.tr/ekler/rapor.pdf" in result.possible_download_links
    # in-page anchor and a plain content link should not be flagged as downloads
    assert not any(link.endswith("#top") for link in result.possible_download_links)
    assert not any("gecmis" in link for link in result.possible_download_links)
    assert "no <table> elements found" not in " ".join(result.parsing_notes)


def test_js_shell_page_flags_possible_js_dependency(fixture_html):
    html = fixture_html("js_shell.html")
    result = parse_html(html, base_url="https://kap.org.tr/tr/bildirim-sorgu")

    assert result.page_title == "KAP - Bildirim Sorgu"
    assert result.detected_tables == 0
    assert any("may depend on client-side rendering" in note for note in result.parsing_notes)
    assert any("no <table> elements found" in note for note in result.parsing_notes)


def test_api_docs_page_detects_operation_links(fixture_html):
    html = fixture_html("api_docs.html")
    result = parse_html(html, base_url="https://ws.spk.gov.tr/help/index.html")

    assert result.detected_links == 3
    absolute_links = result.possible_download_links
    assert "https://ws.spk.gov.tr/api/export?format=json" in absolute_links

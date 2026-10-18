from app.retrieval.chunking import Chunk, chunk_markdown


def paths(chunks):
    return [c.heading_path for c in chunks]


def test_empty_body_gives_no_chunks():
    assert chunk_markdown("") == []
    assert chunk_markdown("\n\n  \n") == []


def test_one_chunk_per_section_with_heading_path():
    body = "# Guide\n\nIntro.\n\n## Steps\n\nDo this.\n\n## Notes\n\nCareful."
    assert chunk_markdown(body) == [
        Chunk("Guide", "Intro."),
        Chunk("Guide > Steps", "Do this."),
        Chunk("Guide > Notes", "Careful."),
    ]


def test_nested_headings_build_the_full_path():
    body = "# A\n\n## B\n\n### C\n\ntext"
    assert paths(chunk_markdown(body)) == ["A > B > C"]


def test_parent_without_its_own_text_gets_no_chunk():
    body = "# A\n\n## B\n\n### C\n\ntext\n\n### D\n\nmore"
    assert paths(chunk_markdown(body)) == ["A > B > C", "A > B > D"]


def test_sibling_heading_replaces_the_previous_one():
    body = "# A\n\n## B\n\n### C\n\nx\n\n## E\n\ny"
    assert paths(chunk_markdown(body)) == ["A > B > C", "A > E"]


def test_skipped_heading_level_still_nests():
    assert paths(chunk_markdown("# A\n\n### C\n\ntext")) == ["A > C"]


def test_title_is_the_root_when_there_is_no_h1():
    assert paths(chunk_markdown("## Steps\n\ntext", title="Guide")) == ["Guide > Steps"]


def test_title_is_not_added_when_the_body_has_an_h1():
    assert paths(chunk_markdown("# Guide\n\ntext", title="Guide")) == ["Guide"]


def test_text_before_the_first_heading_uses_the_title():
    chunks = chunk_markdown("Preamble.\n\n# Guide\n\ntext", title="Guide")
    assert chunks == [Chunk("Guide", "Preamble."), Chunk("Guide", "text")]


def test_headings_inside_code_fences_are_ignored():
    body = "# A\n\n```\n# not a heading\n```\n\nafter"
    chunks = chunk_markdown(body)
    assert paths(chunks) == ["A"]
    assert "# not a heading" in chunks[0].content


def test_html_comments_are_kept():
    body = "# Cards\n\nText.\n\n<!-- hidden note -->\n\nMore."
    assert "<!-- hidden note -->" in chunk_markdown(body)[0].content


def test_table_stays_in_one_piece_even_when_long():
    rows = "\n".join(f"| row {i} | value {i} |" for i in range(50))
    table = "| a | b |\n| --- | --- |\n" + rows
    chunks = chunk_markdown(f"# T\n\n{table}", max_chars=100)
    assert [c.content for c in chunks] == [table]


def test_long_section_splits_on_paragraph_boundaries():
    paragraphs = [f"Paragraph {i} " + "word " * 20 for i in range(6)]
    body = "# Long\n\n" + "\n\n".join(p.strip() for p in paragraphs)
    chunks = chunk_markdown(body, max_chars=250)
    assert len(chunks) > 1
    assert all(len(c.content) <= 250 for c in chunks)
    assert all(c.heading_path == "Long" for c in chunks)
    rejoined = "\n\n".join(c.content for c in chunks)
    assert rejoined == "\n\n".join(p.strip() for p in paragraphs)


def test_oversized_list_splits_on_lines():
    items = [f"- item number {i} with some text" for i in range(40)]
    chunks = chunk_markdown("# L\n\n" + "\n".join(items), max_chars=200)
    assert len(chunks) > 1
    assert all(len(c.content) <= 200 for c in chunks)
    assert "\n".join(c.content for c in chunks) == "\n".join(items)


def test_oversized_paragraph_splits_on_sentences():
    sentences = [f"This is sentence number {i}." for i in range(30)]
    chunks = chunk_markdown("# P\n\n" + " ".join(sentences), max_chars=150)
    assert len(chunks) > 1
    assert all(len(c.content) <= 150 for c in chunks)
    assert " ".join(c.content for c in chunks) == " ".join(sentences)


def test_a_single_oversized_sentence_is_kept_whole():
    sentence = "word " * 100
    chunks = chunk_markdown("# P\n\n" + sentence.strip(), max_chars=50)
    assert len(chunks) == 1


def test_embedding_text_puts_the_heading_path_first():
    chunk = Chunk("A > B", "body text")
    assert chunk.embedding_text == "A > B\n\nbody text"

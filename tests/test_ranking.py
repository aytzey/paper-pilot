from paper_pilot.models import PaperRecord


def _paper(title, abstract=None, cites=0, year=2024):
    return PaperRecord(
        source="s",
        source_id=title,
        title=title,
        abstract=abstract,
        citation_count=cites,
        year=year,
        is_open_access=True,
    )


def test_on_topic_paper_outranks_famous_tangential() -> None:
    query = "retrieval augmented generation"
    on_topic = _paper(
        "Retrieval-Augmented Generation for Large Language Models: A Survey",
        abstract="A survey of retrieval augmented generation methods.",
        cites=100,
    )
    tangential = _paper(
        "Attention Is All You Need",
        abstract="The Transformer enables generation of sequences.",
        cites=100000,
    )
    # Despite 1000x fewer citations, the precise topic match must rank higher.
    assert on_topic.quality_score(query) > tangential.quality_score(query)


def test_relevance_rewards_title_coverage() -> None:
    query = "graph neural networks"
    full = _paper("A Comprehensive Survey on Graph Neural Networks")
    partial = _paper("Neural Machine Translation")
    unrelated = _paper("Sports Betting Market Efficiency")
    assert full.relevance_score(query) > partial.relevance_score(query)
    assert unrelated.relevance_score(query) == 0.0


def test_quality_without_query_equals_rank_score() -> None:
    paper = _paper("Anything", cites=10)
    assert paper.quality_score(None) == paper.rank_score()


def test_among_relevant_papers_citations_break_ties() -> None:
    query = "graph neural networks"
    title = "Graph Neural Networks for Recommendation"
    cited = _paper(title, abstract="graph neural networks", cites=500, year=2022)
    fresh = _paper(title, abstract="graph neural networks", cites=2, year=2022)
    assert cited.quality_score(query) > fresh.quality_score(query)

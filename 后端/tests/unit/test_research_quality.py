"""Lexical ranking and duplicate hints are advisory, not research judgments."""
from app.infrastructure.crossref import ScholarlyRecord
from app.services.research_quality import build_query, duplicate_title_counts, rank_candidates, relevance_hint, relevance_score, title_key


def record(title: str, abstract: str | None = None) -> ScholarlyRecord:
    return ScholarlyRecord(doi="10.1234/" + str(len(title)), title=title, authors=(), year=None,
        journal=None, url="https://doi.org/10.1234/example", abstract=abstract)


def test_query_uses_all_task_fields_and_is_bounded():
    query = build_query("软件缺陷预测", "梳理随机森林与支持向量机的应用", "包含PROMISE数据集的对比报告")
    assert "软件缺陷预测" in query and "随机森林" in query and "PROMISE" in query
    assert len(build_query("A" * 200, "B" * 400, "C" * 400)) <= 160


def test_relevant_software_papers_rank_above_unrelated_topic():
    title, desc, expected = "软件缺陷预测和模型对比研究", "随机森林与软件缺陷预测", "缺陷预测报告"
    relevant = record("基于深度学习的软件缺陷预测方法综述")
    unrelated = record("海洋浮游生物种群变化观察")
    assert relevance_score(title, desc, expected, relevant.title, None) > relevance_score(title, desc, expected, unrelated.title, None)
    assert rank_candidates([unrelated, relevant], title, desc, expected) == [relevant, unrelated]


def test_abstract_and_context_contribute_and_missing_text_is_safe():
    title, desc, expected = "智能软件测试", "研究自动测试用例生成", "软件自动化测试报告"
    without = relevance_score(title, desc, expected, "A survey", None)
    with_abstract = relevance_score(title, desc, expected, "A survey", "智能软件测试技术")
    assert with_abstract > without
    assert relevance_hint([without]) in {"unknown", "some"}
    assert relevance_hint([]) == "unknown"


def test_ambiguous_candidates_are_retained_and_ties_stay_stable():
    candidates = [record("Marine biology"), record("Public health")]
    assert rank_candidates(candidates, "软件测试", "", "") == candidates


def test_title_normalization_flags_versions_without_discarding_them():
    titles = ["机器学习：软件测试", "机器学习-软件测试", "不同的文献标题"]
    counts = duplicate_title_counts(titles)
    assert title_key(titles[0]) == title_key(titles[1])
    assert counts[title_key(titles[0])] == 2 and len(titles) == 3
    assert title_key("C++ language design") != title_key("C language design")
    assert duplicate_title_counts(["AI", "AI"]) == {}


def test_topic_overlap_and_abstract_break_ties_without_removing_fallbacks():
    without = record('软件缺陷预测概述')
    with_abstract = record('软件缺陷预测概述', '软件缺陷预测在软件工程中用于研究项目质量和开发过程。')
    ranked = rank_candidates([without, with_abstract], '软件缺陷预测', '', '', topic='软件工程')
    assert ranked == [with_abstract, without]
    assert len(rank_candidates([record('Marine biology')], '软件测试', '', '', topic='软件工程')) == 1
    assert '软件工程' in build_query('机器学习测试', '质量预测研究', '方法比较', topic='软件工程')
    query = build_query('A' * 80, 'B' * 50, 'C' * 50, topic='软件工程')
    assert len(query) <= 160 and query.endswith('软件工程')

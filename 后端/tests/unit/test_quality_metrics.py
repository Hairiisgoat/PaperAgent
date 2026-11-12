from app.services.quality_metrics import measure


def test_metrics_are_reproducible_and_do_not_assert_entailment():
    text = '软件测试具有重要意义，引用内容需要具体证据。' * 3
    paras = [{'text': text, 'citations': [{'source_id': 'a', 'evidence': '真实摘要'}]},
             {'text': text, 'citations': [{'source_id': 'a', 'evidence': '伪造摘录'}, {'source_id': 'missing', 'evidence': 'x'}]}]
    result = measure('软件测试', paras, {'a': '真实摘要的完整内容'})
    assert result == measure('软件测试', paras, {'a': '真实摘要的完整内容'})
    assert result['paragraphs'] == 2 and result['distinct_cited_sources'] == 2
    assert result['missing_source_citations'] == 1 and result['non_verbatim_evidence_citations'] == 1
    assert result['possible_near_duplicate_pairs'] == 1 and result['vague_phrase_occurrences'] == 6


def test_empty_inputs_do_not_claim_complete_evidence():
    result = measure('', [], {})
    assert result['topic_lexical_overlap_paragraphs'] is None
    assert result['paragraphs'] == result['citation_occurrences'] == 0

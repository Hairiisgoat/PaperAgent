"""Repeatable lexical and provenance indicators, not semantic quality scores."""
from collections import Counter
from difflib import SequenceMatcher
import re
import unicodedata

from app.services.research_quality import terms

_VAGUE = re.compile(r'具有重要意义|具有重要价值|发挥重要作用|具有广阔前景|具有巨大潜力')


def measure(topic: str, paragraphs: list[dict], abstracts: dict[str, str]) -> dict:
    """Compute indicators on fixed inputs; no truth or entailment inference."""
    topic_terms = terms(topic)
    texts = [str(item.get('text', '')) for item in paragraphs]
    normal = [''.join(unicodedata.normalize('NFKC', text).split()).casefold() for text in texts]
    citations: Counter[str] = Counter()
    missing_source = unmatched = linked = 0
    for paragraph in paragraphs:
        refs = paragraph.get('citations', [])
        if refs:
            linked += 1
        for ref in refs:
            source = ref.get('source_id', '')
            citations[source] += 1
            abstract = abstracts.get(source)
            if abstract is None:
                missing_source += 1
            elif not isinstance(ref.get('evidence'), str) or ref['evidence'] not in abstract:
                unmatched += 1
    pairs = sum(1 for i, text in enumerate(normal) for other in normal[i+1:]
                if len(text) >= 30 and len(other) >= 30 and SequenceMatcher(None, text, other, autojunk=False).ratio() >= .85)
    return {
        'paragraphs': len(paragraphs),
        'topic_lexical_overlap_paragraphs': sum(bool(topic_terms & terms(text)) for text in texts) if topic_terms else None,
        'paragraphs_with_citations': linked,
        'citation_occurrences': sum(citations.values()),
        'distinct_cited_sources': len(citations),
        'max_source_occurrences': max(citations.values(), default=0),
        'missing_source_citations': missing_source,
        'non_verbatim_evidence_citations': unmatched,
        'possible_near_duplicate_pairs': pairs,
        'vague_phrase_occurrences': sum(len(_VAGUE.findall(text)) for text in texts),
        'total_characters': sum(len(text) for text in texts),
    }

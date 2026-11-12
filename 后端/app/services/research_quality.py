"""Small, explainable lexical helpers; no claims of semantic verification."""
from collections import Counter
import re
import unicodedata
from app.infrastructure.crossref import ScholarlyRecord


_WORDS = re.compile(r"[a-z0-9][a-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,}")
_GENERIC = {
    "the", "and", "for", "with", "from", "using", "based", "study", "research", "analysis", "review",
    "人工", "智能", "人工智", "工智能", "人工智能", "工程", "研究", "分析", "方法", "技术", "应用", "领域",
    "论文", "任务", "文献", "资料", "综述", "基于", "发展", "趋势", "未来", "挑战", "能力", "影响", "现状",
    "探讨", "问题", "提出", "通过", "结合", "实践", "结果", "工作", "报告", "研究与", "中的", "的应用",
    "基于机", "于机器", "的机器", "机器学", "器学习", "机器学习", "基于机器", "于机器学", "器学习的", "学习的",
    "软件的", "能力与", "与影响", "影响研", "模型对", "的影响", "预测方法", "方法研究", "未来趋势", "研究与实",
}
_FRAGMENTS = re.compile(r"[，。；：、（）()\n\r;,:]+")
_PREFIX = re.compile(r"^(?:检索并|检索|梳理|调研|研究|分析|评估|收集|综合分析|系统总结|一份|包含|说明|探讨|给出)+")
_SOFTWARE = re.compile(r"软件|代码|程序|software|\bcode\b|programming|\bdevelopers?\b", re.IGNORECASE)


def terms(text: str) -> set[str]:
    result: set[str] = set()
    for word in _WORDS.findall(unicodedata.normalize("NFKC", text).casefold()):
        if "\u4e00" <= word[0] <= "\u9fff":
            for size in (2, 3, 4):
                result.update(word[i:i + size] for i in range(len(word) - size + 1))
        else:
            result.add(word)
    return result - _GENERIC


def build_query(title: str, description: str, expected_output: str, *, topic: str = '') -> str:
    """Use the title plus short task-specific snippets; at most 160 characters."""
    pieces = [" ".join(title.split())[:80]]
    for field in (description, expected_output):
        for raw in _FRAGMENTS.split(field):
            fragment = _PREFIX.sub("", " ".join(raw.split())).strip()
            if len(fragment) >= 5 and terms(fragment) - terms(" ".join(pieces)):
                pieces.append(fragment[:32])
                break
    topic = ' '.join(topic.split())[:32]
    if topic and terms(topic) - terms(' '.join(pieces)):
        # Reserve room for the topic instead of silently truncating it when
        # a title and both task snippets already fill the Crossref query.
        prefix = ' '.join(part for part in pieces if part)[:159 - len(topic)].rstrip()
        return f'{prefix} {topic}' if prefix else topic
    return " ".join(part for part in pieces if part)[:160]


def relevance_score(task_title: str, task_description: str, expected_output: str,
                    source_title: str, abstract: str | None) -> int:
    """Rank shared distinctive title terms above description/abstract overlap."""
    title_terms = terms(task_title)
    context_terms = terms(task_description + " " + expected_output) - title_terms
    source_terms = terms(source_title)
    abstract_terms = terms(abstract or "")
    def weight(items: set[str]) -> int:
        return sum(2 if len(item) >= 3 else 1 for item in items)
    title_match = weight(title_terms & source_terms)
    context_match = weight(context_terms & source_terms)
    abstract_match = weight(title_terms & abstract_terms)
    # Limit long abstracts and task descriptions from dominating the title.
    score = 4 * title_match + min(context_match, 12) + min(abstract_match, 12)
    # For explicitly software-related tasks, generic AI/forecasting overlaps
    # should not outrank papers that mention software in title or abstract.
    # Keep them as low-evidence fallbacks, since synonyms/translation vary.
    if _SOFTWARE.search(task_title) and not _SOFTWARE.search(source_title + " " + (abstract or "")):
        score = min(score, 4)
    return score


def relevance_hint(scores: list[int]) -> str:
    """A lexical hint, not a relevance verdict or quality score."""
    score = max(scores, default=0)
    return "more" if score >= 12 else "some" if score > 0 else "unknown"


def rank_candidates(candidates: list[ScholarlyRecord], title: str, description: str,
                    expected_output: str, *, limit: int = 5, topic: str = '') -> list[ScholarlyRecord]:
    """Rank by task overlap plus a bounded topic signal and abstract availability.

    Scores are lexical hints only. Neither metadata nor an abstract establishes
    relevance or support for an individual claim. Do not discard fallbacks.
    """
    topic_terms = terms(topic)
    def key(item: ScholarlyRecord) -> tuple[int, int, int]:
        score = relevance_score(title, description, expected_output, item.title, item.abstract)
        topic_match = len(topic_terms & terms(item.title + ' ' + (item.abstract or '')))
        return score + min(topic_match, 6) if score else 0, score, int(bool(item.abstract and len(item.abstract.strip()) >= 40)) if score else 0
    return sorted(candidates, key=key, reverse=True)[:limit]


def title_key(title: str) -> str:
    value = unicodedata.normalize("NFKC", title).casefold()
    # Keep meaningful symbols such as C++ and C#; remove spacing and punctuation.
    value = "".join(char for char in value if not unicodedata.category(char).startswith(("P", "Z", "C")) or char == "#")
    return value if len(value) >= 6 else ""


def duplicate_title_counts(titles: list[str]) -> dict[str, int]:
    keys = [title_key(title) for title in titles]
    return {key: count for key, count in Counter(keys).items() if key and count > 1}

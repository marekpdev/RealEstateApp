import math
from dataclasses import dataclass
from typing import Dict, FrozenSet, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: FrozenSet[str], k: int) -> float:
    """
    Of everything actually relevant to a query, what fraction showed up
    anywhere in the top k retrieved items? An empty `relevant` set (a query
    with no known right answer) can't be scored and returns 0.0 rather than
    dividing by zero - callers are expected to only pass queries that carry
    at least one hand-labelled relevant item.
    """
    if not relevant:
        return 0.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant) / len(relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: FrozenSet[str]) -> float:
    """
    1 / (rank of the first relevant item), or 0.0 if none of the retrieved
    items are relevant at all. This is a single query's contribution to
    Mean Reciprocal Rank - averaging this across a whole query set is what
    MRR actually measures elsewhere (see aggregate() below).
    """
    for rank, key in enumerate(retrieved, start=1):
        if key in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: FrozenSet[str], k: int) -> float:
    """
    Normalized Discounted Cumulative Gain over the top k, with binary
    relevance (an item is either relevant or it isn't - there's no "somewhat
    relevant" grade in this hand-labelled set). DCG rewards a relevant item
    more the earlier it appears (each position's gain is discounted by
    1/log2(rank + 1)); dividing by the *ideal* DCG (every relevant item
    packed at the very top) normalizes the score to [0, 1] so it's
    comparable across queries with different numbers of relevant items.
    A query with zero relevant items is unscorable and returns 0.0.
    """
    if not relevant:
        return 0.0

    def _dcg(items: Sequence[str]) -> float:
        return sum(
            1.0 / math.log2(rank + 1)
            for rank, key in enumerate(items[:k], start=1)
            if key in relevant
        )

    # The ideal ranking packs every relevant item into the top slots, so its
    # DCG only depends on *how many* relevant items there are (up to k) -
    # under binary relevance, which one is "first" among equally-relevant
    # items doesn't change the sum.
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    if ideal_dcg == 0.0:
        return 0.0
    return _dcg(retrieved) / ideal_dcg


@dataclass(frozen=True)
class MetricScores:
    recall_at_k: float
    mrr: float
    ndcg_at_k: float


@dataclass(frozen=True)
class QueryJudgment:
    query: str
    relevant: FrozenSet[str]


def score_query(retrieved: Sequence[str], relevant: FrozenSet[str], k: int) -> MetricScores:
    return MetricScores(
        recall_at_k=recall_at_k(retrieved, relevant, k),
        mrr=reciprocal_rank(retrieved, relevant),
        ndcg_at_k=ndcg_at_k(retrieved, relevant, k),
    )


def aggregate(per_query_scores: Sequence[MetricScores]) -> MetricScores:
    """
    Mean of each metric across every scored query - the actual "MRR"/
    "Recall@k"/"nDCG@k" figures reported for a method are always this mean,
    never a single query's own score (see score_query for that).
    """
    if not per_query_scores:
        return MetricScores(recall_at_k=0.0, mrr=0.0, ndcg_at_k=0.0)
    n = len(per_query_scores)
    return MetricScores(
        recall_at_k=sum(s.recall_at_k for s in per_query_scores) / n,
        mrr=sum(s.mrr for s in per_query_scores) / n,
        ndcg_at_k=sum(s.ndcg_at_k for s in per_query_scores) / n,
    )

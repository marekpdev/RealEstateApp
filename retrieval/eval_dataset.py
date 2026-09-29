from dataclasses import dataclass
from typing import FrozenSet, List

from db.repositories import DocumentChunkInput

# A small, hand-labelled retrieval evaluation set: a synthetic municipal
# zoning corpus (styled after the real PDFs scripts/sync_knowledge_base.py
# ingests, but written by hand here so the eval set is stable and doesn't
# depend on whatever happens to be in Azure Blob Storage today) plus a
# hand-written query -> relevant-chunk judgment for each query. This is
# what scripts/evaluate_retrieval.py scores dense/lexical/hybrid retrieval
# against - see that script's own module docstring for why an evaluation
# set like this is the only real defence against "I tweaked the retriever
# and it feels better" (vibes-based tuning).
#
# Each corpus entry's list index is its stable identity within this module
# (EVAL_QUERIES references chunks by index, not by re-typing their content)
# - convert to the real join key (exact chunk content, matching how
# retrieval/hybrid_search.py itself joins dense and lexical results) via
# relevant_contents() below, once the corpus has actually been seeded.
EVAL_CORPUS: List[DocumentChunkInput] = [
    {
        "source_origin": "austin-zoning-ordinances.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "Ordinance 12-345 establishes a minimum rear setback of 25 feet "
            "for all multi-family residential structures in R-3 zoning "
            "districts."
        ),
    },
    {
        "source_origin": "austin-zoning-ordinances.pdf",
        "page": 1,
        "chunk_index": 1,
        "content": (
            "Section 4.2 of the municipal code requires a 15-foot side yard "
            "setback for accessory dwelling units constructed after 2019."
        ),
    },
    {
        "source_origin": "austin-zoning-ordinances.pdf",
        "page": 2,
        "chunk_index": 2,
        "content": (
            "R-3 zones permit multi-family residential buildings up to "
            "three stories, subject to the setback requirements described "
            "in Ordinance 12-345."
        ),
    },
    {
        "source_origin": "variance-procedures.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "Properties seeking a variance from standard setback "
            "requirements must file an application with the Board of "
            "Adjustment at least 30 days before the requested hearing date."
        ),
    },
    {
        "source_origin": "variance-procedures.pdf",
        "page": 1,
        "chunk_index": 1,
        "content": (
            "A property owner may appeal a denied variance request to the "
            "municipal zoning board within 15 days of the denial notice."
        ),
    },
    {
        "source_origin": "historic-preservation-overlay.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "The Downtown Historic Preservation Overlay District restricts "
            "exterior facade modifications on buildings constructed before "
            "1945."
        ),
    },
    {
        "source_origin": "parking-regulations.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "Off-street parking minimums for new commercial developments "
            "are one space per 300 square feet of gross floor area, per "
            "Zoning Code 55-100."
        ),
    },
    {
        "source_origin": "mixed-use-development-guide.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "Mixed-use developments combining ground-floor retail with "
            "upper-floor residential units are permitted by right in the "
            "MU-2 zoning designation."
        ),
    },
    {
        "source_origin": "mixed-use-development-guide.pdf",
        "page": 1,
        "chunk_index": 1,
        "content": (
            "Building height in the MU-2 mixed-use district is capped at "
            "45 feet unless a density bonus is granted for including "
            "affordable housing units."
        ),
    },
    {
        "source_origin": "environmental-review-requirements.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": (
            "Any construction project disturbing more than one acre of "
            "land must submit an environmental impact assessment to the "
            "Planning Department before a permit is issued."
        ),
    },
]


@dataclass(frozen=True)
class EvalQuery:
    query: str
    relevant_indices: FrozenSet[int]
    # Hand-picked to be illustrative, not exhaustive: True for a query
    # written to reuse the corpus's own exact statutory terms (an ordinance
    # or zoning code number) - the case lexical search is specifically good
    # at; False for a query written as a paraphrase sharing few or no exact
    # words with its relevant chunk - the case dense/meaning-based search
    # is specifically good at. Both kinds are deliberately included rather
    # than only the easy, lexically-obvious kind, so the harness's own
    # results can show each method's actual blind spot instead of only its
    # strength.
    favors_lexical: bool


EVAL_QUERIES: List[EvalQuery] = [
    EvalQuery("ordinance 12-345 setback requirements", frozenset({0, 2}), favors_lexical=True),
    EvalQuery("zoning code 55-100 parking minimums", frozenset({6}), favors_lexical=True),
    EvalQuery("appealing a denied variance", frozenset({4}), favors_lexical=True),
    EvalQuery(
        "environmental impact assessment construction project",
        frozenset({9}),
        favors_lexical=True,
    ),
    EvalQuery(
        "how tall can a new building be in a mixed-use zone",
        frozenset({8}),
        favors_lexical=False,
    ),
    EvalQuery(
        "renovating the outside of an old downtown building",
        frozenset({5}),
        favors_lexical=False,
    ),
    EvalQuery(
        "what is the process to build extra floors above a normal height limit",
        frozenset({8}),
        favors_lexical=False,
    ),
    EvalQuery(
        "requesting an exception to a setback rule",
        frozenset({3}),
        favors_lexical=False,
    ),
]


def relevant_contents(query: EvalQuery) -> FrozenSet[str]:
    """Resolves an EvalQuery's corpus indices to the actual chunk content
    strings - the real join key retrieval results are keyed by (see
    retrieval/hybrid_search.py's own dense/lexical fusion, which joins by
    exact chunk content rather than a shared id), once EVAL_CORPUS has been
    seeded via DocumentChunkRepository.replace_all() and retrieved back
    out."""
    return frozenset(EVAL_CORPUS[i]["content"] for i in query.relevant_indices)


# A hand-authored stand-in for what a real Pinecone dense search would
# plausibly rank first for each query above - not a computed heuristic, and
# not a claim about real embedding quality. This exists only because
# PINECONE_API_KEY isn't configured (or api.pinecone.io isn't reachable) in
# every environment this harness might run in, so a genuine dense arm can't
# always be produced. It exists purely to let scripts/evaluate_retrieval.py's
# --demo-dense flag demonstrate the harness computing real recall@k/MRR/
# nDCG@k numbers for a dense-like ranking and a fused hybrid ranking end to
# end, clearly labeled as a stand-in everywhere it's used - never presented
# as, or substituted silently for, a real dense-search result. Keyed by the
# exact query string (not list position) so it stays correct even if
# EVAL_QUERIES is reordered or extended. Each value is corpus indices in the
# stand-in's claimed rank order; deliberately written to still find the
# "favors_lexical" queries' relevant chunk (dense isn't *bad* at exact
# terms, just not needed there) while also ranking the paraphrased queries'
# relevant chunk highly - the case lexical search structurally cannot
# handle at all: Postgres's websearch_to_tsquery ANDs every bare query term
# together, so a single query word absent from a chunk's own wording - not
# misspelled, just different - drops that chunk from lexical results
# entirely, not just lower-ranked (see this module's own EVAL_QUERIES
# comment above).
DEMO_DENSE_RESULTS_BY_QUERY: dict = {
    "ordinance 12-345 setback requirements": [2, 0, 1],
    "zoning code 55-100 parking minimums": [8, 6, 7],
    "appealing a denied variance": [3, 4, 9],
    "environmental impact assessment construction project": [9, 6, 0],
    "how tall can a new building be in a mixed-use zone": [8, 2, 6],
    "renovating the outside of an old downtown building": [5, 4, 0],
    "what is the process to build extra floors above a normal height limit": [8, 1, 2],
    "requesting an exception to a setback rule": [3, 4, 1],
}

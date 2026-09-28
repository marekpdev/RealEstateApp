from typing import List, Sequence, Tuple, TypedDict

from sqlalchemy import delete, func, select

from db.models import DocumentChunk
from db.repositories.base import BaseRepository


class DocumentChunkInput(TypedDict):
    source_origin: str
    page: int
    chunk_index: int
    content: str


class DocumentChunkRepository(BaseRepository):
    async def replace_all(self, chunks: Sequence[DocumentChunkInput]) -> int:
        """Full-table replace: delete every existing chunk, then bulk-insert
        the ones just re-chunked from Azure. A scheduled resync always means
        "this is everything currently in Azure" (see
        worker.tasks.sync_knowledge_base's own docstring), never an
        incremental delta - a document removed from the source blob
        container must disappear from lexical search too, which an
        append-only insert could never achieve. Unlike Pinecone's own
        add_documents() (an upsert keyed by an id LangChain generates), these
        rows carry no stable external id to upsert against, so a clean wipe
        is the only mechanism that can express "this is now the whole set" -
        the tradeoff being a caller must pass the *complete* current chunk
        list, never a partial one.
        """
        await self.session.execute(delete(DocumentChunk))
        rows = [
            DocumentChunk(
                source_origin=chunk["source_origin"],
                page=chunk["page"],
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
            )
            for chunk in chunks
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return len(rows)

    async def search_lexical(
        self, query: str, k: int = 5
    ) -> List[Tuple[DocumentChunk, float]]:
        """Ranks chunks by Postgres's own ts_rank_cd against a
        websearch_to_tsquery() parse of `query` - websearch_to_tsquery over
        plainto_tsquery/to_tsquery because it accepts ordinary search-engine
        syntax (bare words AND together, "quoted phrases" match as a phrase,
        a leading `-` excludes a term) without the caller having to construct
        tsquery operator syntax by hand, and never raises on malformed input
        the way to_tsquery does. Only chunks that actually match (`@@`) are
        ranked and returned - ts_rank_cd on a non-matching row is a
        meaningless near-zero score, not a signal "irrelevant but present"
        that a caller should ever have to filter out itself.
        """
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery).label("rank")
        stmt = (
            select(DocumentChunk, rank)
            .where(DocumentChunk.content_tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(k)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

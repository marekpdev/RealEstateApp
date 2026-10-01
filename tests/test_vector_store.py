from unittest.mock import MagicMock, patch

from services import vector_store


def _build_store(**overrides):
    """Builds the vector store with Pinecone itself mocked out, returning the
    embeddings object it would have handed to the real client."""
    pinecone = MagicMock()
    with patch.multiple(vector_store, PINECONE_API_KEY="pc-test", OPENAI_API_KEY="sk-test", PineconeVectorStore=pinecone, **overrides):
        vector_store.get_pinecone_vector_store()
    return pinecone.call_args.kwargs["embedding"]


def test_embedding_model_is_pinned_to_the_library_default_it_was_built_with():
    """The pin must not change behaviour: an existing index was embedded with
    this model, and mixing models in one index silently degrades search."""
    assert _build_store().model == "text-embedding-ada-002"


def test_embedding_model_comes_from_configuration_not_the_library_default():
    assert _build_store(EMBEDDING_MODEL="text-embedding-3-small").model == "text-embedding-3-small"


def test_no_vector_store_without_a_pinecone_key():
    with patch.object(vector_store, "PINECONE_API_KEY", None):
        assert vector_store.get_pinecone_vector_store() is None

from app.models import EMBEDDING_DIMENSIONS, Base, Chunk


def test_all_nine_tables_are_defined():
    assert set(Base.metadata.tables) == {
        "customers",
        "orders",
        "order_items",
        "refunds",
        "tickets",
        "chat_sessions",
        "chat_messages",
        "documents",
        "chunks",
    }


def test_chunk_columns_match_the_embedding_model():
    assert Chunk.__table__.c.embedding.type.dim == EMBEDDING_DIMENSIONS == 1536


def test_chunk_text_search_column_is_generated():
    assert Chunk.__table__.c.tsv.computed is not None

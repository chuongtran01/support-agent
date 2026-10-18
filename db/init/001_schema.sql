CREATE EXTENSION IF NOT EXISTS vector;

-- Business data ---------------------------------------------------------

CREATE TABLE customers (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'approver')),
    membership    TEXT NOT NULL DEFAULT 'none' CHECK (membership IN ('none', 'basic', 'plus')),
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(id),
    status       TEXT NOT NULL CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled')),
    placed_at    TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ,
    total_cents  INT NOT NULL
);
CREATE INDEX ON orders (customer_id);

CREATE TABLE order_items (
    id               SERIAL PRIMARY KEY,
    order_id         INT NOT NULL REFERENCES orders(id),
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    price_cents      INT NOT NULL,
    quantity         INT NOT NULL DEFAULT 1,
    is_final_sale    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE refunds (
    id           SERIAL PRIMARY KEY,
    order_id     INT NOT NULL REFERENCES orders(id),
    customer_id  INT NOT NULL REFERENCES customers(id),
    amount_cents INT NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'completed')),
    decided_by   INT REFERENCES customers(id),
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON refunds (customer_id, created_at);

CREATE TABLE tickets (
    id          SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id),
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conversations ---------------------------------------------------------

CREATE TABLE chat_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id INT NOT NULL REFERENCES customers(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
    id         SERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id),
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Knowledge base --------------------------------------------------------

CREATE TABLE documents (
    id         TEXT PRIMARY KEY,           -- e.g. "returns-policy-2024"
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current', 'stale', 'draft')),
    updated_at DATE NOT NULL,
    source     TEXT NOT NULL
);

CREATE TABLE chunks (
    id           SERIAL PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    heading_path TEXT NOT NULL,
    content      TEXT NOT NULL,
    embedding    vector(1536),
    tsv          tsvector GENERATED ALWAYS AS (
                     to_tsvector('english', heading_path || ' ' || content)
                 ) STORED
);
CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- Mirrors infra/cloud/modules/aurora's pgvector bootstrap. Not exercised by
-- any code yet (db_writer doesn't exist), but ready for that phase.
CREATE EXTENSION IF NOT EXISTS vector;

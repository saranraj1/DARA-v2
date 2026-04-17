-- PostgreSQL initialization script
-- Runs once when the container is first created

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- for fuzzy text search
CREATE EXTENSION IF NOT EXISTS "btree_gin"; -- for composite GIN indexes

-- TimescaleDB is enabled in the image automatically

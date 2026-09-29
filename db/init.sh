#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 \
  -v dbname="$POSTGRES_DB" \
  -v auth_user="$AUTH_DB_USER" -v auth_pass="$AUTH_DB_PASSWORD" \
  -v library_user="$LIBRARY_DB_USER" -v library_pass="$LIBRARY_DB_PASSWORD" \
  -v loan_user="$LOAN_DB_USER" -v loan_pass="$LOAN_DB_PASSWORD" \
  -v audit_user="$AUDIT_DB_USER" -v audit_pass="$AUDIT_DB_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE USER :"auth_user" WITH PASSWORD :'auth_pass';
CREATE USER :"library_user" WITH PASSWORD :'library_pass';
CREATE USER :"loan_user" WITH PASSWORD :'loan_pass';
CREATE USER :"audit_user" WITH PASSWORD :'audit_pass';

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT,
  full_name TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  oauth_provider TEXT,
  oauth_subject TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roles (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE user_roles (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE books (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  isbn TEXT NOT NULL UNIQUE,
  total_copies INT NOT NULL CHECK (total_copies >= 0),
  available_copies INT NOT NULL CHECK (available_copies >= 0),
  category TEXT,
  pdf_document_id UUID,
  cover_image_name TEXT,
  cover_mime_type TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES users(id),
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL UNIQUE,
  mime_type TEXT NOT NULL,
  size_bytes INT NOT NULL CHECK (size_bytes > 0),
  sha256_hash TEXT NOT NULL,
  processing_status TEXT NOT NULL DEFAULT 'queued',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE loans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  book_id UUID NOT NULL REFERENCES books(id),
  due_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reservations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  book_id UUID NOT NULL REFERENCES books(id),
  reservation_date TIMESTAMPTZ NOT NULL DEFAULT now(),
  requested_due_date DATE,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE categories (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID,
  action TEXT NOT NULL,
  ip_address TEXT,
  status TEXT NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO roles(name) VALUES ('admin'), ('user');

GRANT CONNECT ON DATABASE :"dbname" TO :"auth_user", :"library_user", :"loan_user", :"audit_user";
GRANT USAGE ON SCHEMA public TO :"auth_user", :"library_user", :"loan_user", :"audit_user";

GRANT SELECT, INSERT, UPDATE, DELETE ON users, roles, user_roles TO :"auth_user";
GRANT DELETE ON user_roles TO :"auth_user";
GRANT SELECT ON audit_logs TO :"auth_user";
GRANT SELECT ON books TO :"auth_user";
GRANT SELECT, DELETE ON documents, loans, reservations TO :"auth_user";

GRANT SELECT ON users, roles, user_roles TO :"library_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON books TO :"library_user";
GRANT SELECT, INSERT, UPDATE ON documents TO :"library_user";
GRANT SELECT, DELETE ON loans, reservations TO :"library_user";
GRANT INSERT ON audit_logs TO :"library_user";

GRANT SELECT ON users, roles, user_roles TO :"loan_user";
GRANT SELECT, INSERT, UPDATE ON loans TO :"loan_user";
GRANT SELECT, INSERT, UPDATE ON reservations TO :"loan_user";
GRANT SELECT, UPDATE ON books TO :"loan_user";

GRANT SELECT, INSERT ON audit_logs TO :"audit_user";
GRANT SELECT ON users, books, documents, loans, reservations TO :"audit_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"audit_user";
SQL

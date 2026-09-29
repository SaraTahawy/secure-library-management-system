ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE books ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS pdf_document_id UUID;
ALTER TABLE books ADD COLUMN IF NOT EXISTS cover_image_name TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS cover_mime_type TEXT;

CREATE TABLE IF NOT EXISTS reservations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  book_id UUID NOT NULL REFERENCES books(id),
  reservation_date TIMESTAMPTZ NOT NULL DEFAULT now(),
  requested_due_date DATE,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS requested_due_date DATE;

GRANT SELECT, INSERT, UPDATE, DELETE ON books TO library_user;
GRANT SELECT, DELETE ON loans, reservations TO library_user;
GRANT SELECT, UPDATE ON books TO loan_user;
GRANT SELECT, INSERT, UPDATE ON reservations TO loan_user;
GRANT SELECT ON reservations TO audit_user;

GRANT SELECT, INSERT, UPDATE, DELETE ON users, roles, user_roles TO auth_user;
GRANT DELETE ON user_roles TO auth_user;
GRANT SELECT ON books TO auth_user;
GRANT SELECT, DELETE ON documents, loans, reservations TO auth_user;

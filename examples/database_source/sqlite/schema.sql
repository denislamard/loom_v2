-- ==================== TABLES ====================

CREATE TABLE customers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT NOT NULL UNIQUE,
  title        TEXT CHECK (title IN ('Mr','Mrs')),
  last_name    TEXT NOT NULL,
  first_name   TEXT,
  type         TEXT NOT NULL DEFAULT 'individual' CHECK (type IN ('individual','business')),
  company_name TEXT,
  email        TEXT,
  phone        TEXT,
  address      TEXT,
  postal_code  TEXT,
  city         TEXT,
  notes        TEXT,
  created_at   TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE products (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  sku              TEXT    NOT NULL UNIQUE,
  name             TEXT    NOT NULL,
  category         TEXT    NOT NULL CHECK (category IN ('supply','labor','travel')),
  unit             TEXT    NOT NULL CHECK (unit IN ('unit','hour','meter','sqm','flat')),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  vat_rate_bp      INTEGER NOT NULL DEFAULT 2000 CHECK (vat_rate_bp IN (2000,1000,550)),
  is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at       TEXT    NOT NULL DEFAULT (date('now'))
);

CREATE TABLE documents (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  number           TEXT    NOT NULL UNIQUE,
  type             TEXT    NOT NULL CHECK (type IN ('quote','invoice')),
  customer_id      INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
  quote_id         INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  status           TEXT    NOT NULL,
  subject          TEXT,
  issue_date       TEXT    NOT NULL,
  valid_until      TEXT,
  due_date         TEXT,
  paid_at          TEXT,
  notes            TEXT,
  created_at       TEXT    NOT NULL DEFAULT (date('now')),
  CHECK (
    (type = 'quote'   AND status IN ('draft','sent','accepted','rejected','expired')
                      AND quote_id IS NULL AND due_date IS NULL AND paid_at IS NULL)
 OR (type = 'invoice' AND status IN ('draft','issued','paid','overdue','cancelled')
                      AND valid_until IS NULL)
  ),
  CHECK (status <> 'paid' OR paid_at IS NOT NULL)
);

CREATE TABLE document_lines (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
  position         INTEGER NOT NULL,
  description      TEXT    NOT NULL,
  quantity         REAL    NOT NULL CHECK (quantity > 0),
  unit             TEXT    NOT NULL,
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  vat_rate_bp      INTEGER NOT NULL CHECK (vat_rate_bp IN (2000,1000,550)),
  discount_bp      INTEGER NOT NULL DEFAULT 0 CHECK (discount_bp BETWEEN 0 AND 10000),
  UNIQUE (document_id, position)
);

-- ==================== INDEX ====================

CREATE INDEX idx_documents_customer     ON documents(customer_id);
CREATE INDEX idx_documents_type_status  ON documents(type, status);
CREATE INDEX idx_document_lines_product ON document_lines(product_id);

-- ==================== VUE ====================

CREATE VIEW v_document_totals AS
SELECT d.id, d.number, d.type, d.status, d.customer_id,
       SUM(ROUND(l.quantity * l.unit_price_cents * (10000 - l.discount_bp) / 10000.0))  AS total_excl_vat_cents,
       SUM(ROUND(l.quantity * l.unit_price_cents * (10000 - l.discount_bp) / 10000.0
                 * l.vat_rate_bp / 10000.0))                                            AS total_vat_cents
FROM documents d LEFT JOIN document_lines l ON l.document_id = d.id
GROUP BY d.id;

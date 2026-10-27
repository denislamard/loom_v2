## Tables

**customers** (clients) : `id` PK, `code` unique (`C-001`), `title` (Mr | Mrs), `last_name` (requis), `first_name`, `type` (individual | business), `company_name` (business), `email`, `phone`, `address`, `postal_code`, `city`, `notes`, `created_at`.

**products** (catalogue) : `id` PK, `sku` unique (`P-0001`), `name`, `category` (supply | labor | travel), `unit` (unit | hour | meter | sqm | flat), `unit_price_cents`, `vat_rate_bp` (2000 | 1000 | 550), `is_active` (0 | 1), `created_at`.

**documents** (devis et factures) : `id` PK, `number` unique (`Q-2024-0001` devis, `INV-2024-0001` facture), `type` (quote | invoice), `customer_id` → customers.id, `quote_id` → documents.id (devis d'origine d'une facture), `status`, `subject`, `issue_date`, `valid_until` (devis), `due_date` et `paid_at` (factures), `notes`, `created_at`.
- statut d'un devis : draft | sent | accepted | rejected | expired ; d'une facture : draft | issued | paid | overdue | cancelled.

**document_lines** (lignes) : `id` PK, `document_id` → documents.id, `product_id` → products.id (peut manquer), `position`, `description`, `quantity` (réel > 0), `unit`, `unit_price_cents`, `vat_rate_bp`, `discount_bp` (0 à 10000).

**v_document_totals** (vue) : `id`, `number`, `type`, `status`, `customer_id`, `total_excl_vat_cents`, `total_vat_cents` — sommes des lignes, remise comprise.

## Conventions

- Montants en centimes (`*_cents`) ; taux en points de base : `vat_rate_bp` 2000 = 20 %, 1000 = 10 %, 550 = 5,5 % ; `discount_bp` 1000 = 10 %.
- Dates en texte ISO (AAAA-MM-JJ).

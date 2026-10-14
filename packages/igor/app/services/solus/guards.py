# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idempotent SQLite ledger guards, applied at startup and migration rehearsal."""


def install_guards(connection):
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Solus ledger guards currently require SQLite; do not activate on another store.")
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(solus_card_statements)")}
    if "event_ids" not in columns:
        connection.exec_driver_sql("ALTER TABLE solus_card_statements ADD COLUMN event_ids JSON NOT NULL DEFAULT '[]'")
    account_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(solus_accounts)")}
    if "report_role" not in account_columns:
        connection.exec_driver_sql("ALTER TABLE solus_accounts ADD COLUMN report_role VARCHAR(16) NOT NULL DEFAULT 'financial'")
    if "report_note" not in account_columns:
        connection.exec_driver_sql("ALTER TABLE solus_accounts ADD COLUMN report_note VARCHAR(1000)")
    definitions = {
        "event_insert": "BEFORE INSERT ON solus_events WHEN NEW.posted != 0 BEGIN SELECT RAISE(ABORT, 'insert journal as draft'); END",
        "event_immutable": "BEFORE UPDATE ON solus_events WHEN OLD.posted = 1 BEGIN SELECT RAISE(ABORT, 'posted journal is immutable'); END",
        "event_delete": "BEFORE DELETE ON solus_events WHEN OLD.posted = 1 BEGIN SELECT RAISE(ABORT, 'posted journal cannot be deleted'); END",
        "event_post": """BEFORE UPDATE OF posted ON solus_events WHEN NEW.posted = 1 AND OLD.posted = 0 BEGIN
            SELECT CASE WHEN (SELECT COUNT(*) FROM solus_postings WHERE event_id=NEW.id) < 2
                THEN RAISE(ABORT, 'journal needs two postings') END;
            SELECT CASE WHEN EXISTS (SELECT a.currency FROM solus_postings p JOIN solus_accounts a ON a.id=p.account_id
                WHERE p.event_id=NEW.id GROUP BY a.currency HAVING SUM(p.amount_minor) != 0)
                THEN RAISE(ABORT, 'journal is not balanced per currency') END;
            SELECT CASE WHEN EXISTS (SELECT 1 FROM solus_books b WHERE b.id=NEW.book_id AND b.closed_through >= NEW.occurred_on)
                THEN RAISE(ABORT, 'period is closed') END;
            END""",
        "posting_insert": """BEFORE INSERT ON solus_postings BEGIN
            SELECT CASE WHEN typeof(NEW.amount_minor) != 'integer' THEN RAISE(ABORT, 'integer money required') END;
            SELECT CASE WHEN (SELECT posted FROM solus_events WHERE id=NEW.event_id) = 1 THEN RAISE(ABORT, 'posted lines immutable') END;
            SELECT CASE WHEN (SELECT closed FROM solus_accounts WHERE id=NEW.account_id) = 1 THEN RAISE(ABORT, 'account is closed') END;
            END""",
        "posting_update": "BEFORE UPDATE ON solus_postings BEGIN SELECT RAISE(ABORT, 'replace draft postings; never edit lines'); END",
        "posting_delete": "BEFORE DELETE ON solus_postings WHEN (SELECT posted FROM solus_events WHERE id=OLD.event_id)=1 BEGIN SELECT RAISE(ABORT, 'posted lines immutable'); END",
        "account_currency": """BEFORE UPDATE OF currency,scale,account_type,book_id ON solus_accounts
            WHEN EXISTS (SELECT 1 FROM solus_postings WHERE account_id=OLD.id)
            BEGIN SELECT RAISE(ABORT, 'account semantics immutable after use'); END""",
        "schedule_update": "BEFORE UPDATE ON solus_schedule_items BEGIN SELECT RAISE(ABORT, 'schedule history immutable'); END",
        "schedule_delete": "BEFORE DELETE ON solus_schedule_items BEGIN SELECT RAISE(ABORT, 'schedule history immutable'); END",
        "settlement_update": "BEFORE UPDATE ON solus_settlements BEGIN SELECT RAISE(ABORT, 'settlements immutable'); END",
        "settlement_delete": "BEFORE DELETE ON solus_settlements BEGIN SELECT RAISE(ABORT, 'settlements immutable'); END",
        "command_update": "BEFORE UPDATE ON solus_commands BEGIN SELECT RAISE(ABORT, 'command receipts immutable'); END",
        "command_delete": "BEFORE DELETE ON solus_commands BEGIN SELECT RAISE(ABORT, 'command receipts immutable'); END",
        "schedule_integer": """BEFORE INSERT ON solus_schedule_items BEGIN
            SELECT CASE WHEN typeof(NEW.amount_minor) != 'integer' THEN RAISE(ABORT, 'integer schedule money required') END;
            SELECT CASE WHEN (SELECT posted FROM solus_events WHERE id=NEW.event_id)=1 THEN RAISE(ABORT, 'posted schedules immutable') END;
            END""",
        "settlement_insert": """BEFORE INSERT ON solus_settlements BEGIN
            SELECT CASE WHEN typeof(NEW.amount_minor) != 'integer' THEN RAISE(ABORT, 'integer settlement money required') END;
            SELECT CASE WHEN (SELECT posted FROM solus_events WHERE id=NEW.payment_id)=1 THEN RAISE(ABORT, 'posted payment allocations immutable') END;
            END""",
        "import_update": "BEFORE UPDATE ON solus_imports BEGIN SELECT RAISE(ABORT, 'import originals immutable'); END",
        "import_delete": "BEFORE DELETE ON solus_imports BEGIN SELECT RAISE(ABORT, 'import originals immutable'); END",
        "reconciliation_update": "BEFORE UPDATE ON solus_reconciliations BEGIN SELECT RAISE(ABORT, 'reconciliation history immutable'); END",
        "reconciliation_delete": "BEFORE DELETE ON solus_reconciliations BEGIN SELECT RAISE(ABORT, 'reconciliation history immutable'); END",
        "lot_update": "BEFORE UPDATE ON solus_lots BEGIN SELECT RAISE(ABORT, 'cost lot history immutable'); END",
        "lot_delete": "BEFORE DELETE ON solus_lots BEGIN SELECT RAISE(ABORT, 'cost lot history immutable'); END",
        "disposal_update": "BEFORE UPDATE ON solus_disposals BEGIN SELECT RAISE(ABORT, 'disposal history immutable'); END",
        "disposal_delete": "BEFORE DELETE ON solus_disposals BEGIN SELECT RAISE(ABORT, 'disposal history immutable'); END",
        "valuation_update": "BEFORE UPDATE ON solus_valuations BEGIN SELECT RAISE(ABORT, 'valuation history immutable'); END",
        "valuation_delete": "BEFORE DELETE ON solus_valuations BEGIN SELECT RAISE(ABORT, 'valuation history immutable'); END",
    }
    for name, definition in definitions.items():
        connection.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS solus_{name} {definition}")

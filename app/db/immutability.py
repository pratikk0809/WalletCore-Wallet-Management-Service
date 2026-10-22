"""SQL for the immutability triggers. Used by the Alembic migration AND the tests,
so what we test is exactly what we deploy."""

# One reusable function. TG_TABLE_NAME / TG_OP tell it which table and statement fired it.
CREATE_FUNCTIONS = """
CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: rows are immutable', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION forbid_completed_transaction_change() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'UPDATE on completed transactions is not allowed: create a new transaction instead'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CREATE_TRIGGERS = """
CREATE TRIGGER trg_audit_logs_immutable
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE TRIGGER trg_ledger_entries_immutable
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE TRIGGER trg_transactions_no_delete
    BEFORE DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE TRIGGER trg_transactions_completed_immutable
    BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION forbid_completed_transaction_change();
"""

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_transactions_completed_immutable ON transactions;
DROP TRIGGER IF EXISTS trg_transactions_no_delete ON transactions;
DROP TRIGGER IF EXISTS trg_ledger_entries_immutable ON ledger_entries;
DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs;
DROP FUNCTION IF EXISTS forbid_completed_transaction_change();
DROP FUNCTION IF EXISTS forbid_change();
"""

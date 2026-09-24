-- Triggers and functions for the Django variant (django_models.py).
--
-- Django cannot express these in models. Run this file from a RunSQL migration that
-- depends on the migration makemigrations generates (see the docstring in
-- django_models.py). Table and column names are the canonical ones (the models set
-- db_column= wherever Django's default <field>_id would differ), and nothing is
-- schema-qualified: the tables are assumed to be in the default (public) schema. If
-- yours are elsewhere, change every SET search_path = public, pg_temp line to that
-- schema. Keep pg_temp last: it is what stops a session's temporary table named
-- approval_request or audit_event from standing in for the real one inside a trigger.
--
-- The statements below are copied verbatim from postgres.sql (schema qualifier
-- removed) and tests/test_core_schema.py fails if the two drift apart.
--
--   * audit_event_chain_link assigns chain_seq, prev_hash, the payload hashes and
--     row_hash on INSERT; leave those columns out of your writes.
--   * verify_audit_chain() re-computes the chain and lists problems.
--   * scrub_audit_payload() is the sanctioned UPDATE of an audit_event: it records an
--     audit.scrub_payload event first, and audit_event_guard_update refuses any payload
--     drop that does not name such an event for the row.
--   * audit_event and approval_decision reject UPDATE, DELETE and TRUNCATE.
--   * approval_decision_sod rejects a requester approving their own request.
--   * approval_request_immutable lets only state, executed_at and execution_result
--     of an approval_request change, so it cannot be re-attributed or rewritten.
--
-- Then run grants.sql (psql) for the regulated profile.

CREATE OR REPLACE FUNCTION enforce_separation_of_duties()
RETURNS trigger SET search_path = public, pg_temp AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM approval_request r
        WHERE r.id = NEW.request_id AND r.requested_by = NEW.approver_id
    ) THEN
        RAISE EXCEPTION 'separation of duties: requester % may not approve request %',
            NEW.approver_id, NEW.request_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_decision_sod
    BEFORE INSERT ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION enforce_separation_of_duties();

CREATE OR REPLACE FUNCTION approval_request_guard_update()
RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - 'state' - 'executed_at' - 'execution_result')
       IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'executed_at' - 'execution_result') THEN
        RAISE EXCEPTION 'approval_request % is immutable except state, executed_at and execution_result', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_request_immutable
    BEFORE UPDATE ON approval_request
    FOR EACH ROW EXECUTE FUNCTION approval_request_guard_update();

CREATE OR REPLACE FUNCTION audit_event_hash_input(e audit_event)
RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_array(
        e.event_uuid, e.chain_id, e.chain_seq,
        to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        to_char(e.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        e.actor_id, e.actor_kind, e.effective_actor_id, e.impersonation_session_id,
        e.tenant_id, e.environment, host(e.source_ip), e.user_agent,
        e.action, e.target_type, e.target_id,
        e.reason, e.ticket_ref, e.approval_request_id,
        e.request_id, e.correlation_id, e.idempotency_key,
        e.result, e.error_code, e.before_hash, e.after_hash,
        e.policy_version, e.prev_hash, e.hash_algorithm
    )::text
$$;

CREATE OR REPLACE FUNCTION audit_event_chain_link()
RETURNS trigger SET search_path = public, pg_temp AS $$
DECLARE
    last_seq  bigint;
    last_hash text;
BEGIN
    IF NEW.hash_algorithm IS DISTINCT FROM 'sha256' THEN
        RAISE EXCEPTION 'audit_event: only sha256 is implemented, got %', NEW.hash_algorithm;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('admin_core.audit_event:' || NEW.chain_id, 0));
    SELECT a.chain_seq, a.row_hash INTO last_seq, last_hash
      FROM audit_event a
     WHERE a.chain_id = NEW.chain_id
     ORDER BY a.chain_seq DESC
     LIMIT 1;
    NEW.chain_seq := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash := last_hash;
    NEW.before_hash := CASE WHEN NEW.before_state IS NULL THEN NULL
        ELSE encode(sha256(convert_to(NEW.before_state::text, 'UTF8')), 'hex') END;
    NEW.after_hash := CASE WHEN NEW.after_state IS NULL THEN NULL
        ELSE encode(sha256(convert_to(NEW.after_state::text, 'UTF8')), 'hex') END;
    IF NEW.before_state IS NULL AND NEW.after_state IS NULL THEN
        NEW.redaction_policy := 'none';
    END IF;
    NEW.row_hash := encode(
        sha256(convert_to(audit_event_hash_input(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_chain_link
    BEFORE INSERT ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_chain_link();

CREATE OR REPLACE FUNCTION verify_audit_chain(p_chain_id text DEFAULT NULL)
RETURNS TABLE (chain_id text, chain_seq bigint, problem text)
LANGUAGE plpgsql STABLE SET search_path = public, pg_temp AS $$
DECLARE
    r         audit_event%ROWTYPE;
    cur_chain text;
    prev_seq  bigint;
    prev_hash text;
BEGIN
    FOR r IN
        SELECT * FROM audit_event a
         WHERE p_chain_id IS NULL OR a.chain_id = p_chain_id
         ORDER BY a.chain_id, a.chain_seq
    LOOP
        IF cur_chain IS DISTINCT FROM r.chain_id THEN
            cur_chain := r.chain_id;
            prev_seq := NULL;
            prev_hash := NULL;
        END IF;
        chain_id := r.chain_id;
        chain_seq := r.chain_seq;
        IF prev_seq IS NOT NULL AND r.chain_seq <> prev_seq + 1 THEN
            problem := 'gap: previous chain_seq is ' || prev_seq;
            RETURN NEXT;
        END IF;
        IF (prev_seq IS NOT NULL OR r.chain_seq = 1)
           AND r.prev_hash IS DISTINCT FROM prev_hash THEN
            problem := 'prev_hash does not match the previous row_hash';
            RETURN NEXT;
        END IF;
        IF r.row_hash IS DISTINCT FROM encode(
               sha256(convert_to(audit_event_hash_input(r), 'UTF8')), 'hex') THEN
            problem := 'row_hash does not match the row content';
            RETURN NEXT;
        END IF;
        IF r.before_state IS NOT NULL AND r.before_hash IS DISTINCT FROM
               encode(sha256(convert_to(r.before_state::text, 'UTF8')), 'hex') THEN
            problem := 'before_hash does not match before_state';
            RETURN NEXT;
        END IF;
        IF r.after_state IS NOT NULL AND r.after_hash IS DISTINCT FROM
               encode(sha256(convert_to(r.after_state::text, 'UTF8')), 'hex') THEN
            problem := 'after_hash does not match after_state';
            RETURN NEXT;
        END IF;
        IF ((r.before_state IS NULL AND r.before_hash IS NOT NULL)
            OR (r.after_state IS NULL AND r.after_hash IS NOT NULL))
           AND NOT EXISTS (
               SELECT 1 FROM audit_event s
                WHERE s.target_type = 'audit_event' AND s.target_id = r.id::text
                  AND s.action = 'audit.scrub_payload') THEN
            problem := 'payload dropped without an audit.scrub_payload event';
            RETURN NEXT;
        END IF;
        prev_seq := r.chain_seq;
        prev_hash := r.row_hash;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION scrub_audit_payload(
    p_event_id bigint, p_policy text, p_reason text, p_actor_id bigint DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    ev       audit_event%ROWTYPE;
    scrub_id bigint;
BEGIN
    IF p_policy IS NULL OR p_policy IN ('', 'none') THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a named redaction policy';
    END IF;
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a reason';
    END IF;
    SELECT * INTO ev FROM audit_event WHERE id = p_event_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit_event % does not exist', p_event_id;
    END IF;
    IF ev.before_state IS NULL AND ev.after_state IS NULL THEN
        RAISE EXCEPTION 'audit_event % has no payload to scrub', p_event_id;
    END IF;
    -- actor_kind is 'system': the database performed the write; actor_id is who asked.
    INSERT INTO audit_event
        (chain_id, occurred_at, actor_id, actor_kind, tenant_id, environment,
         action, target_type, target_id, reason, result)
    VALUES
        (ev.chain_id, now(), p_actor_id, 'system', ev.tenant_id, ev.environment,
         'audit.scrub_payload', 'audit_event', ev.id::text, p_reason, 'succeeded')
    RETURNING id INTO scrub_id;
    PERFORM set_config('admin_core.scrub', scrub_id::text, true);
    UPDATE audit_event
       SET before_state = NULL, after_state = NULL, redaction_policy = p_policy
     WHERE id = p_event_id;
    PERFORM set_config('admin_core.scrub', '', true);
END;
$$;

REVOKE ALL ON FUNCTION scrub_audit_payload(bigint, text, text, bigint) FROM PUBLIC;

CREATE OR REPLACE FUNCTION deny_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_event_guard_update()
RETURNS trigger SET search_path = public, pg_temp AS $$
DECLARE
    scrub_event text := current_setting('admin_core.scrub', true);
BEGIN
    IF scrub_event IS NULL OR scrub_event !~ '^[0-9]{1,18}$' THEN
        RAISE EXCEPTION 'audit_event is append-only; UPDATE is not permitted (scrub payloads with scrub_audit_payload)';
    END IF;
    IF NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL
       OR (to_jsonb(NEW) - 'before_state' - 'after_state' - 'redaction_policy')
          IS DISTINCT FROM (to_jsonb(OLD) - 'before_state' - 'after_state' - 'redaction_policy') THEN
        RAISE EXCEPTION 'audit_event scrub may only drop before_state / after_state and set redaction_policy';
    END IF;
    IF OLD.before_state IS NULL AND OLD.after_state IS NULL THEN
        RAISE EXCEPTION 'audit_event % has no payload left to scrub', OLD.id;
    END IF;
    IF NEW.redaction_policy IN ('', 'none') THEN
        RAISE EXCEPTION 'audit_event scrub needs a named redaction policy';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM audit_event s
         WHERE s.id = scrub_event::bigint
           AND s.action = 'audit.scrub_payload'
           AND s.target_type = 'audit_event'
           AND s.target_id = OLD.id::text
    ) THEN
        RAISE EXCEPTION 'audit_event %: a payload drop must name this row''s audit.scrub_payload event (use scrub_audit_payload)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_no_update
    BEFORE UPDATE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_guard_update();

CREATE TRIGGER audit_event_no_delete
    BEFORE DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION deny_audit_mutation();

CREATE TRIGGER approval_decision_no_update
    BEFORE UPDATE ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

CREATE TRIGGER approval_decision_no_delete
    BEFORE DELETE ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

CREATE TRIGGER approval_decision_no_truncate
    BEFORE TRUNCATE ON approval_decision
    FOR EACH STATEMENT EXECUTE FUNCTION deny_audit_mutation();

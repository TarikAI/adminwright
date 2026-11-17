-- Triggers and functions for the Django variant (django_models.py).
--
-- Django cannot express these in models. Run this file from a RunSQL migration that
-- depends on the migration makemigrations generates (see the docstring in
-- django_models.py). Table and column names are the canonical ones (the models set
-- db_column= wherever Django's default <field>_id would differ). The tables are
-- assumed to be in the default (public) schema, so the functions name them
-- public.<table>; if yours are elsewhere, change every public. qualifier to that schema.
-- Keep each function's SET search_path = pg_catalog, pg_temp and its pg_catalog.
-- qualifiers: they are what stop a session's temporary table, or an overload it
-- created in public, from standing in for the real thing inside a trigger.
--
-- The statements below are copied verbatim from postgres.sql (admin_core. becomes
-- public.) and tests/test_core_schema.py fails if the two drift apart.
--
--   * audit_event_chain_link assigns chain_seq, prev_hash, recorded_at, actor_subject,
--     the payload hashes and row_hash on INSERT, whatever the write sends.
--   * verify_audit_chain() re-computes the chain and lists problems.
--   * scrub_audit_payload() is the sanctioned UPDATE of an audit_event: it records an
--     audit.scrub_payload event first, and audit_event_guard_update refuses any payload
--     drop that does not name such an event for the row.
--   * audit_event and approval_decision reject UPDATE, DELETE and TRUNCATE.
--   * approval_decision_sod rejects a requester approving their own request.
--   * approval_request_immutable lets only state, executed_at and execution_result
--     of an approval_request change, so it cannot be re-attributed or rewritten;
--     approval_request_state enforces its state machine, and no request is deleted.
--   * admin_actor_role_guard and impersonation_session_immutable keep a grant and an
--     impersonation session as issued (only revoking or ending them changes them).
--   * Every trigger is ENABLE ALWAYS (see the caveats in postgres.sql).
--
-- Then run grants.sql (psql) for the regulated profile.

CREATE OR REPLACE FUNCTION public.audit_event_hash_input(e public.audit_event)
RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT (pg_catalog.jsonb_build_array(
        e.event_uuid, e.chain_id, e.chain_seq,
        pg_catalog.to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        pg_catalog.to_char(e.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        e.actor_id, e.actor_kind, e.effective_actor_id, e.impersonation_session_id,
        e.tenant_id, e.environment, pg_catalog.host(e.source_ip), e.user_agent,
        e.action, e.target_type, e.target_id,
        e.reason, e.ticket_ref, e.approval_request_id,
        e.request_id, e.correlation_id, e.idempotency_key,
        e.result, e.error_code, e.before_hash, e.after_hash,
        e.policy_version, e.prev_hash, e.hash_algorithm
    ) || CASE WHEN e.actor_subject IS NULL THEN '[]'::jsonb
              ELSE pg_catalog.jsonb_build_array(e.actor_subject) END)::text
$$;

CREATE OR REPLACE FUNCTION public.audit_event_chain_link()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    last_seq  bigint;
    last_hash text;
    sess      public.impersonation_session%ROWTYPE;
BEGIN
    IF NEW.hash_algorithm IS DISTINCT FROM 'sha256' THEN
        RAISE EXCEPTION 'audit_event: only sha256 is implemented, got %', NEW.hash_algorithm;
    END IF;
    NEW.actor_subject := NULL;
    IF NEW.actor_id IS NOT NULL THEN
        NEW.actor_subject := (SELECT a.auth_subject FROM public.admin_actor a WHERE a.id = NEW.actor_id);
    END IF;
    IF NEW.impersonation_session_id IS NOT NULL THEN
        SELECT * INTO sess FROM public.impersonation_session s WHERE s.id = NEW.impersonation_session_id;
        IF FOUND AND (sess.real_actor_id IS DISTINCT FROM NEW.actor_id
                      OR NEW.effective_actor_id <> sess.target_subject_id) THEN
            RAISE EXCEPTION 'audit_event: impersonation_session % is actor % acting as %; this row names actor % acting as %',
                sess.id, sess.real_actor_id, sess.target_subject_id, NEW.actor_id, NEW.effective_actor_id;
        END IF;
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('admin_core.audit_event:' || NEW.chain_id, 0));
    SELECT a.chain_seq, a.row_hash INTO last_seq, last_hash
      FROM public.audit_event a
     WHERE a.chain_id = NEW.chain_id
     ORDER BY a.chain_seq DESC
     LIMIT 1;
    NEW.chain_seq := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash := last_hash;
    NEW.recorded_at := pg_catalog.clock_timestamp();
    NEW.before_hash := CASE WHEN NEW.before_state IS NULL THEN NULL
        ELSE pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(NEW.before_state::text, 'UTF8')), 'hex') END;
    NEW.after_hash := CASE WHEN NEW.after_state IS NULL THEN NULL
        ELSE pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(NEW.after_state::text, 'UTF8')), 'hex') END;
    IF NEW.before_state IS NULL AND NEW.after_state IS NULL THEN
        NEW.redaction_policy := 'none';
    END IF;
    NEW.row_hash := pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(public.audit_event_hash_input(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_chain_link
    BEFORE INSERT ON public.audit_event
    FOR EACH ROW EXECUTE FUNCTION public.audit_event_chain_link();

CREATE OR REPLACE FUNCTION public.verify_audit_chain(p_chain_id text DEFAULT NULL)
RETURNS TABLE (chain_id text, chain_seq bigint, problem text)
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    r         public.audit_event%ROWTYPE;
    cur_chain text;
    prev_seq  bigint;
    prev_hash text;
BEGIN
    FOR r IN
        SELECT * FROM public.audit_event a
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
        IF r.row_hash IS DISTINCT FROM pg_catalog.encode(
               pg_catalog.sha256(pg_catalog.convert_to(public.audit_event_hash_input(r), 'UTF8')), 'hex') THEN
            problem := 'row_hash does not match the row content';
            RETURN NEXT;
        END IF;
        IF r.before_state IS NOT NULL AND r.before_hash IS DISTINCT FROM
               pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(r.before_state::text, 'UTF8')), 'hex') THEN
            problem := 'before_hash does not match before_state';
            RETURN NEXT;
        END IF;
        IF r.after_state IS NOT NULL AND r.after_hash IS DISTINCT FROM
               pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(r.after_state::text, 'UTF8')), 'hex') THEN
            problem := 'after_hash does not match after_state';
            RETURN NEXT;
        END IF;
        IF ((r.before_state IS NULL AND r.before_hash IS NOT NULL)
            OR (r.after_state IS NULL AND r.after_hash IS NOT NULL))
           AND NOT EXISTS (
               SELECT 1 FROM public.audit_event s
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

CREATE OR REPLACE FUNCTION public.scrub_audit_payload(
    p_event_id bigint, p_policy text, p_reason text, p_actor_id bigint)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    ev       public.audit_event%ROWTYPE;
    scrub_id bigint;
BEGIN
    IF p_actor_id IS NULL THEN
        RAISE EXCEPTION 'scrub_audit_payload needs the admin_actor id of whoever asked for the scrub';
    END IF;
    IF p_policy IS NULL OR p_policy IN ('', 'none') THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a named redaction policy';
    END IF;
    IF p_reason IS NULL OR pg_catalog.btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a reason';
    END IF;
    SELECT * INTO ev FROM public.audit_event WHERE id = p_event_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit_event % does not exist', p_event_id;
    END IF;
    IF ev.before_state IS NULL AND ev.after_state IS NULL THEN
        RAISE EXCEPTION 'audit_event % has no payload to scrub', p_event_id;
    END IF;
    INSERT INTO public.audit_event
        (chain_id, occurred_at, actor_id, actor_kind, tenant_id, environment, user_agent,
         action, target_type, target_id, reason, result)
    VALUES
        (ev.chain_id, pg_catalog.now(), p_actor_id, 'system', ev.tenant_id, ev.environment,
         'scrub_audit_payload, database role ' || session_user::text,
         'audit.scrub_payload', 'audit_event', ev.id::text, p_reason, 'succeeded')
    RETURNING id INTO scrub_id;
    PERFORM pg_catalog.set_config('admin_core.scrub', scrub_id::text, true);
    UPDATE public.audit_event
       SET before_state = NULL, after_state = NULL, redaction_policy = p_policy
     WHERE id = p_event_id;
    PERFORM pg_catalog.set_config('admin_core.scrub', '', true);
END;
$$;

REVOKE ALL ON FUNCTION public.scrub_audit_payload(bigint, text, text, bigint) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.enforce_separation_of_duties()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    req public.approval_request%ROWTYPE;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('admin_core.approval_request:' || NEW.request_id::text, 0));
    SELECT * INTO req FROM public.approval_request r WHERE r.id = NEW.request_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key refuses it
    END IF;
    IF req.requested_by = NEW.approver_id THEN
        RAISE EXCEPTION 'separation of duties: requester % may not approve request %',
            NEW.approver_id, NEW.request_id;
    END IF;
    IF req.state <> 'pending' OR req.expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'approval_request % is % and expires at %: it takes no more decisions',
            NEW.request_id, req.state, req.expires_at;
    END IF;
    NEW.decided_at := pg_catalog.clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_decision_sod
    BEFORE INSERT ON public.approval_decision
    FOR EACH ROW EXECUTE FUNCTION public.enforce_separation_of_duties();

CREATE OR REPLACE FUNCTION public.approval_request_guard_update()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF (pg_catalog.to_jsonb(NEW) - 'state' - 'executed_at' - 'execution_result')
       IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'state' - 'executed_at' - 'execution_result') THEN
        RAISE EXCEPTION 'approval_request % is immutable except state, executed_at and execution_result', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_request_immutable
    BEFORE UPDATE ON public.approval_request
    FOR EACH ROW EXECUTE FUNCTION public.approval_request_guard_update();

CREATE OR REPLACE FUNCTION public.approval_request_guard_state()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    approvals bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'pending' OR NEW.executed_at IS NOT NULL OR NEW.execution_result IS NOT NULL THEN
            RAISE EXCEPTION 'approval_request: a new request starts pending, with nothing executed';
        END IF;
        IF NEW.expires_at <= pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'approval_request: expires_at % is already past', NEW.expires_at;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.state = OLD.state THEN
        IF OLD.state NOT IN ('pending', 'approved')
           AND (NEW.executed_at IS DISTINCT FROM OLD.executed_at
                OR NEW.execution_result IS DISTINCT FROM OLD.execution_result) THEN
            RAISE EXCEPTION 'approval_request % is % and does not change any more', OLD.id, OLD.state;
        END IF;
    ELSIF NOT ((OLD.state = 'pending' AND NEW.state IN ('approved', 'rejected', 'expired', 'cancelled'))
               OR (OLD.state = 'approved' AND NEW.state IN ('executed', 'execution_failed', 'expired'))) THEN
        RAISE EXCEPTION 'approval_request %: % to % is not a permitted transition', OLD.id, OLD.state, NEW.state;
    END IF;
    IF (NEW.executed_at IS NOT NULL) <> (NEW.state IN ('executed', 'execution_failed')) THEN
        RAISE EXCEPTION 'approval_request %: executed_at is set exactly when the state is executed or execution_failed', OLD.id;
    END IF;
    IF NEW.state = 'approved' AND OLD.state = 'pending' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('admin_core.approval_request:' || NEW.id::text, 0));
        IF NEW.expires_at <= pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'approval_request % expired at %', OLD.id, NEW.expires_at;
        END IF;
        IF EXISTS (SELECT 1 FROM public.approval_decision d
                    WHERE d.request_id = NEW.id AND d.decision = 'rejected') THEN
            RAISE EXCEPTION 'approval_request % has a rejection and cannot be approved', OLD.id;
        END IF;
        SELECT pg_catalog.count(DISTINCT d.approver_id) INTO approvals
          FROM public.approval_decision d
         WHERE d.request_id = NEW.id AND d.decision = 'approved' AND d.approver_id <> NEW.requested_by;
        IF approvals < NEW.required_approvals THEN
            RAISE EXCEPTION 'approval_request % has % of the % approvals it requires', OLD.id, approvals,
                NEW.required_approvals;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_request_state
    BEFORE INSERT OR UPDATE ON public.approval_request
    FOR EACH ROW EXECUTE FUNCTION public.approval_request_guard_state();

CREATE OR REPLACE FUNCTION public.deny_audit_mutation()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION public.audit_event_guard_update()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scrub_event text := pg_catalog.current_setting('admin_core.scrub', true);
BEGIN
    IF scrub_event IS NULL OR scrub_event !~ '^[0-9]{1,18}$' THEN
        RAISE EXCEPTION 'audit_event is append-only; UPDATE is not permitted (scrub payloads with scrub_audit_payload)';
    END IF;
    IF NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL
       OR (pg_catalog.to_jsonb(NEW) - 'before_state' - 'after_state' - 'redaction_policy')
          IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'before_state' - 'after_state' - 'redaction_policy') THEN
        RAISE EXCEPTION 'audit_event scrub may only drop before_state / after_state and set redaction_policy';
    END IF;
    IF OLD.before_state IS NULL AND OLD.after_state IS NULL THEN
        RAISE EXCEPTION 'audit_event % has no payload left to scrub', OLD.id;
    END IF;
    IF NEW.redaction_policy IN ('', 'none') THEN
        RAISE EXCEPTION 'audit_event scrub needs a named redaction policy';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.audit_event s
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
    BEFORE UPDATE ON public.audit_event
    FOR EACH ROW EXECUTE FUNCTION public.audit_event_guard_update();

CREATE TRIGGER audit_event_no_delete
    BEFORE DELETE ON public.audit_event
    FOR EACH ROW EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON public.audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_update
    BEFORE UPDATE ON public.approval_decision
    FOR EACH ROW EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_delete
    BEFORE DELETE ON public.approval_decision
    FOR EACH ROW EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_truncate
    BEFORE TRUNCATE ON public.approval_decision
    FOR EACH STATEMENT EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER approval_request_no_delete
    BEFORE DELETE ON public.approval_request
    FOR EACH ROW EXECUTE FUNCTION public.deny_audit_mutation();

CREATE TRIGGER approval_request_no_truncate
    BEFORE TRUNCATE ON public.approval_request
    FOR EACH STATEMENT EXECUTE FUNCTION public.deny_audit_mutation();

CREATE OR REPLACE FUNCTION public.admin_actor_role_guard()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    tenants jsonb := NEW.scope -> 'tenants';
    single  text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.revoked_at IS NOT NULL
           AND pg_catalog.to_jsonb(NEW) IS DISTINCT FROM pg_catalog.to_jsonb(OLD) THEN
            RAISE EXCEPTION 'admin_actor_role % is revoked, and a revoked grant stays revoked (grant again instead)', OLD.id;
        END IF;
        IF (pg_catalog.to_jsonb(NEW) - 'revoked_at' - 'revoked_by' - 'revoke_reason')
           IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'revoked_at' - 'revoked_by' - 'revoke_reason') THEN
            RAISE EXCEPTION 'admin_actor_role % is immutable except revoked_at, revoked_by and revoke_reason (revoke it and grant again)', OLD.id;
        END IF;
        RETURN NEW;
    END IF;
    IF pg_catalog.jsonb_typeof(tenants) = 'array' THEN
        IF pg_catalog.jsonb_array_length(tenants) = 1 AND pg_catalog.jsonb_typeof(tenants -> 0) = 'string' THEN
            single := tenants ->> 0;
        END IF;
    END IF;
    IF single = '*' THEN
        single := NULL;
    END IF;
    IF NEW.scope_tenant_id IS DISTINCT FROM single THEN
        RAISE EXCEPTION 'admin_actor_role: scope % needs scope_tenant_id %, not %', NEW.scope,
            COALESCE(single, 'NULL'), COALESCE(NEW.scope_tenant_id, 'NULL');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER admin_actor_role_guard
    BEFORE INSERT OR UPDATE ON public.admin_actor_role
    FOR EACH ROW EXECUTE FUNCTION public.admin_actor_role_guard();

CREATE OR REPLACE FUNCTION public.impersonation_session_guard_update()
RETURNS trigger SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.ended_at IS NOT NULL
       AND pg_catalog.to_jsonb(NEW) IS DISTINCT FROM pg_catalog.to_jsonb(OLD) THEN
        RAISE EXCEPTION 'impersonation_session % has ended and does not change any more', OLD.id;
    END IF;
    IF (pg_catalog.to_jsonb(NEW) - 'ended_at' - 'ended_reason' - 'revoked_by')
       IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'ended_at' - 'ended_reason' - 'revoked_by') THEN
        RAISE EXCEPTION 'impersonation_session % is immutable except ended_at, ended_reason and revoked_by', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER impersonation_session_immutable
    BEFORE UPDATE ON public.impersonation_session
    FOR EACH ROW EXECUTE FUNCTION public.impersonation_session_guard_update();

ALTER TABLE public.audit_event ENABLE ALWAYS TRIGGER audit_event_chain_link;
ALTER TABLE public.audit_event ENABLE ALWAYS TRIGGER audit_event_no_update;
ALTER TABLE public.audit_event ENABLE ALWAYS TRIGGER audit_event_no_delete;
ALTER TABLE public.audit_event ENABLE ALWAYS TRIGGER audit_event_no_truncate;
ALTER TABLE public.approval_decision ENABLE ALWAYS TRIGGER approval_decision_sod;
ALTER TABLE public.approval_decision ENABLE ALWAYS TRIGGER approval_decision_no_update;
ALTER TABLE public.approval_decision ENABLE ALWAYS TRIGGER approval_decision_no_delete;
ALTER TABLE public.approval_decision ENABLE ALWAYS TRIGGER approval_decision_no_truncate;
ALTER TABLE public.approval_request ENABLE ALWAYS TRIGGER approval_request_immutable;
ALTER TABLE public.approval_request ENABLE ALWAYS TRIGGER approval_request_state;
ALTER TABLE public.approval_request ENABLE ALWAYS TRIGGER approval_request_no_delete;
ALTER TABLE public.approval_request ENABLE ALWAYS TRIGGER approval_request_no_truncate;
ALTER TABLE public.admin_actor_role ENABLE ALWAYS TRIGGER admin_actor_role_guard;
ALTER TABLE public.impersonation_session ENABLE ALWAYS TRIGGER impersonation_session_immutable;

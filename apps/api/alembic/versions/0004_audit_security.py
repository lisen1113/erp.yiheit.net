"""Create append-only company audit and global security events."""

from alembic import op

revision = "0004_audit_security"
down_revision = "0003_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_log (
            company_id UUID NOT NULL,
            actor_user_id UUID,
            actor_employee_id UUID,
            session_id UUID,
            event_category VARCHAR(32) NOT NULL,
            action VARCHAR(50) NOT NULL,
            entity_type VARCHAR(50) NOT NULL,
            entity_id UUID,
            entity_ref VARCHAR(128),
            reason VARCHAR(500),
            before_data JSONB,
            after_data JSONB,
            source VARCHAR(12) NOT NULL,
            retention_class VARCHAR(12) NOT NULL,
            occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            trace_id VARCHAR(64) NOT NULL,
            result VARCHAR(12) NOT NULL,
            ip_address INET,
            user_agent VARCHAR(64),
            metadata JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            CONSTRAINT pk_audit_log PRIMARY KEY (id),
            CONSTRAINT ck_audit_log_result CHECK (result IN ('SUCCESS','FAILURE','DENIED')),
            CONSTRAINT ck_audit_log_source CHECK (source IN
                ('API','CLI','WORKER','SYSTEM','MIGRATION')),
            CONSTRAINT ck_audit_log_actor CHECK (actor_user_id IS NOT NULL OR source IN
                ('SYSTEM','WORKER','MIGRATION')),
            CONSTRAINT ck_audit_log_retention CHECK (retention_class IN ('STANDARD','CRITICAL')),
            CONSTRAINT fk_audit_log_company_id_company FOREIGN KEY(company_id) REFERENCES company
                (id),
            CONSTRAINT fk_audit_log_actor_user_id_app_user FOREIGN KEY(actor_user_id) REFERENCES
                app_user (id),
            CONSTRAINT fk_audit_log_actor_employee_id_employee FOREIGN KEY(actor_employee_id)
                REFERENCES employee (id),
            CONSTRAINT fk_audit_log_session_id_auth_session FOREIGN KEY(session_id) REFERENCES
                auth_session (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_company_action_time ON audit_log (company_id, action,
            occurred_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_company_actor_time ON audit_log (company_id, actor_user_id,
            occurred_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_company_entity_time ON audit_log (company_id, entity_type,
            entity_id, occurred_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_company_time ON audit_log (company_id, occurred_at DESC, id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_trace_id ON audit_log (trace_id)
        """
    )
    op.execute(
        """
        CREATE TABLE security_event (
            company_id UUID,
            user_id UUID,
            session_id UUID,
            company_resolution VARCHAR(24) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            request_method VARCHAR(12) NOT NULL,
            request_path VARCHAR(128) NOT NULL,
            host VARCHAR(253),
            origin VARCHAR(260),
            identifier_hash VARCHAR(64),
            occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            trace_id VARCHAR(64) NOT NULL,
            result VARCHAR(12) NOT NULL,
            ip_address INET,
            user_agent VARCHAR(64),
            metadata JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            CONSTRAINT pk_security_event PRIMARY KEY (id),
            CONSTRAINT ck_security_event_result CHECK (result IN ('FAILURE','DENIED')),
            CONSTRAINT ck_security_event_resolution CHECK ((company_resolution = 'UNKNOWN' AND
                company_id IS NULL AND user_id IS NULL) OR (company_resolution = 'HOST_MAPPING'
                AND company_id IS NOT NULL AND user_id IS NULL) OR (company_resolution =
                'AUTHENTICATED_USER' AND company_id IS NOT NULL AND user_id IS NOT NULL)),
            CONSTRAINT fk_security_event_company_id_company FOREIGN KEY(company_id) REFERENCES
                company (id),
            CONSTRAINT fk_security_event_user_id_app_user FOREIGN KEY(user_id) REFERENCES app_user
                (id),
            CONSTRAINT fk_security_event_session_id_auth_session FOREIGN KEY(session_id)
                REFERENCES auth_session (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_security_event_time ON security_event (occurred_at DESC, id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_security_event_trace_id ON security_event (trace_id)
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Audit history is append-only' USING ERRCODE = '42501';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log FOR
            EACH STATEMENT EXECUTE FUNCTION public.reject_audit_mutation()
        """
    )
    op.execute(
        """
        REVOKE ALL ON audit_log FROM PUBLIC
        """
    )
    op.execute(
        """
        CREATE TRIGGER security_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON
            security_event FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_mutation()
        """
    )
    op.execute(
        """
        REVOKE ALL ON security_event FROM PUBLIC
        """
    )
    op.execute(
        """
        INSERT INTO permission (code, module, action, description, sensitivity_level)
            VALUES ('system.audit.view','system','view','View company audit','high'),
            ('system.audit.export','system','export','Reserve company audit export','high'),
            ('system.audit.view_sensitive_metadata','system','view_sensitive_metadata',
             'View sanitized audit metadata','high'),
            ('system.security_event.view','system','view','View global security events','high')
            ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE role SET data_scope = 'company' WHERE code = 'AUDITOR' AND is_system
        """
    )
    op.execute(
        """
        INSERT INTO role_permission (role_id, permission_id, granted_by)
            SELECT r.id, p.id, r.updated_by FROM role r JOIN app_user u ON u.id = r.updated_by
            JOIN permission p ON (r.code = 'AUDITOR' AND p.code = 'system.audit.view')
                OR (r.code = 'SYSTEM_ADMIN' AND p.code = 'system.security_event.view')
            WHERE r.is_system ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("security_event")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION public.reject_audit_mutation()")
    op.execute(
        """
        DELETE FROM role_permission rp USING role r, permission p
            WHERE rp.role_id = r.id AND rp.permission_id = p.id AND r.is_system
            AND ((r.code = 'AUDITOR' AND p.code = 'system.audit.view')
                 OR (r.code = 'SYSTEM_ADMIN' AND p.code = 'system.security_event.view'))
        """
    )
    op.execute(
        """
        DELETE FROM role_permission rp USING permission p
            WHERE rp.permission_id = p.id AND p.code IN ('system.audit.export',
            'system.audit.view_sensitive_metadata', 'system.security_event.view')
        """
    )
    op.execute(
        """
        DELETE FROM permission WHERE code IN ('system.audit.export',
            'system.audit.view_sensitive_metadata', 'system.security_event.view')
        """
    )
    op.execute(
        """
        UPDATE role SET data_scope = 'self' WHERE code = 'AUDITOR' AND is_system
        """
    )

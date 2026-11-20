# Authorization seed

The one-time administrator command installs the system templates using
`yihe_api.rbac.bootstrap.seed_roles`. The seed adds missing definitions and
templates and preserves existing role grants. Invoke it only from a trusted
transaction with the relevant company lock. See docs/authorization.md.

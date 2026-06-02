# Legacy application variants

The active local application pair is:

- `../backend_p`
- `../dqc_frontend_v3_datagalaxy_p`

The directories stored here are older application variants kept for reference.
Shared workspace tooling and datasets remain at the workspace root because they
are still useful for database provisioning, imports, audits, and graph repairs.
The active backend owns its environment files and does not import code or
configuration from this directory at runtime.

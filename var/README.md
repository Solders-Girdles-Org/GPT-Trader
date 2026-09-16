# Runtime Workspace

Most of this directory is local runtime state created by development, test, and
operator commands. Keep generated logs, databases, status files, dashboards, and
temporary outputs out of commits.

Use `make clean-dry-run` to inspect safe local cleanup actions and `make clean`
to apply them.

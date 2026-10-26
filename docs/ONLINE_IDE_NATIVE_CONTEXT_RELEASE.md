# Online IDE native authorization release

This candidate adds the authenticated `/internal/run/context` and `/internal/ide/context` RPCs to the existing native problem-set application. Gateway identity headers remain strictly validated; execution-time identities come from the User service, with no redirects or ambient proxy credentials. Native authorization uses a bounded read-only transaction and always rolls it back. It does not depend on new AI task storage.

Use the same release-mode configuration in dev and production, with environment-specific MySQL, Redis, Nacos namespace and RPC secrets. Keep existing native routes. Do not expose this service directly to the public network; Gateway routes `/api/ps/**`, and the Problem service calls the token-protected internal RPC. Disable task workers, local LSP and local Debug in this web process. The dedicated IDE Worker owns those runtimes.

The isolated dev candidate uses ARM64, Python 3.9, its own database snapshot and Redis, the combined Java services, and a frozen Python dependency list recorded alongside the image. No production database or credentials were copied.

Required environment: `SDUOJ_MODE=release`, `SDUOJ_TOKEN`, `AUTOLAB_MYSQL_ADDR/USER/PASSWORD/DATABASE`, `AUTOLAB_REDIS_ADDR/PASSWORD`, `AUTOLAB_NACOS_ADDR/NAMESPACE`, `AUTOLAB_AI_SECRET_KEY` (valid Fernet key required by shared configuration), and optionally the operator-owned `AUTOLAB_IDE_USER_SERVICE_ADDRESS=host:port`.

Targeted native policy tests cover fresh actor lookup, malformed identity/discovery, read-only transaction cleanup, active windows and boundaries, manager membership, no answer-sheet side effects, exact native route resolution and submission-window denial. Full repository collection currently fails in an existing AI test which references modules absent from the committed baseline; its failure is retained in the release evidence and is not a native test pass.

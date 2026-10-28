# Online IDE native authorization release

## Compatibility baseline

The release preserves the running production tree at `/home/sducs/.sduoj-releases/grade-review-20261028-r2/autolab`. The 2026-10-29 read-only snapshot contains 126 source/document files; all 107 Python hashes match the production identity audit. The existing grade-review feature, AI task system, trusted Gateway middleware, routers, worker code, management guides and migration scripts are preserved production baseline, not newly introduced features. Configuration, credentials, uploads, databases, caches and environments are excluded.

`PRESERVED_LIVE_BASELINE_20261029.json` records every baseline hash. Relative to production, the only runtime changes are the new `server/ide_context_dependencies.py` and the updated `server/run_context_internal.py`. Existing internal routes resolve current identities through the User service using internal credentials, disable redirects and ambient proxy credentials, use a bounded read-only native database pool, and roll back every authorization read. The asynchronous IDE route resolves the blocking identity request in the thread pool. Existing route/window/role checks remain intact.

## Validation

Python 3.9.23, FastAPI 0.86.0 and SQLAlchemy 1.4.43: 188 passed, 1 skipped across native context, Run context, grade review, stale answer details, AI backend and AI access tests. Native context has 11 passing cases; grade review and stale details have 17. The skipped test requires optional MySQL coverage and is not credited as exercised MySQL behavior. Tests use isolated SQLite and explicit synthetic development settings; they do not prove real dev service integration or production traffic behavior.

Current user tests were copied read-only with recorded hashes. Fixture corrections give resources the same usernames as their actors, create the course used by the budget test, and assert sequential presentation page numbers while checking original slide text order. No authorization implementation or assertion was bypassed. Initial release-mode and stale-fixture failures remain in the task evidence. `git diff --check` finds only two whitespace lines preserved byte-for-byte from live documentation.

## Dev image and production overlay

`DEV_DEPENDENCY_CONTRACT_20261029.json` binds the isolated dev image to Python 3.9 dependency image `sha256:778577f97a082b2be1412ea2edafbfba1487df46cded2fea8d044785df584fde`. The build clears its old `/app`, then copies only the frozen 127-file runtime source. It installs no packages. Production `requirements.txt` remains byte-identical; the existing dev dependency layer uses headless OpenCV. VM ownership, image build, service replacement and real dev integration are handled by the release coordinator.

Production uses the existing Python 3.9 environment, original systemd Web/Worker topology and external configuration/storage. Prepare a new version directory from the current running production tree after checking all baseline hashes, then apply the two-file overlay. Do not deploy the dev Docker image to production. Do not run migrations, change database schema, replace external configuration or alter business/storage paths for this native change. Preserve the current AI worker configuration and external JPlag dependency.

Required native configuration remains `SDUOJ_MODE=release`, `SDUOJ_TOKEN`, `AUTOLAB_MYSQL_ADDR/USER/PASSWORD/DATABASE`, `AUTOLAB_REDIS_ADDR/PASSWORD`, `AUTOLAB_NACOS_ADDR/NAMESPACE`, `AUTOLAB_AI_SECRET_KEY`, and optional operator-owned `AUTOLAB_IDE_USER_SERVICE_ADDRESS=host:port`. The dedicated IDE Worker owns LSP/Debug runtimes. Follow the repository production rollout and release-operations runbooks for controlled switching and rollback after complete dev acceptance.

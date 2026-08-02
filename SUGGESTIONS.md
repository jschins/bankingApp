# Suggestions for Improvement & Next Steps

## Critical

### 1. Add tests
The project has zero test files despite declaring `pytest` and `pytest-asyncio` as dev dependencies. The following areas are highest-priority for testing:
- **Categorization logic** (`boekh-admin/app/export.py`, `single-docker/app/core/categorize.py`) — complex keyword matching with priority rules, compound terms, and wildcards
- **Transaction merging/distillation** — deduplication by ID, modification persistence
- **Server storage layer** (`boekh-server/app/storage.py`) — path sanitization, CRUD operations
- **API endpoints** — request/response contracts for all FastAPI routes

### 2. Extract shared library for the Enable Banking client
The `EnableBankingClient` is copy-pasted across three locations (`psd2-api/client.py`, `single-person/single_client.py`, `single-docker/app/core/single_client.py`) with nearly identical JWT construction, request handling, and pagination. Extract into a shared package to eliminate duplication and ensure bug fixes propagate everywhere.

### 3. Add CI/CD pipeline
Set up GitHub Actions for:
- Linting (`ruff check`)
- Type checking (`mypy` or `pyright`)
- Running the new test suite
- Optionally building Docker images and PyInstaller executables on tags

## High Priority

### 4. Fix hardcoded IP in Vite config
`boekh-admin/frontend/vite.config.ts` proxies to `http://192.168.7.75:8100` — this breaks on any other network. Replace with a configurable environment variable (e.g., `VITE_API_HOST`) or use a relative proxy rule.

### 5. Improve TypeScript type safety
`Transaction` is typed as `Record<string, unknown>` across the frontend, losing all TypeScript benefit. Define a concrete `Transaction` interface matching the JSON schema and use it consistently. Consider adding runtime validation with Zod for API responses.

### 6. Share frontend components
`boekh-admin/frontend` and `single-docker/frontend` contain duplicated components (`HTable`, `PTable`, `STable`, `EditableField`, `EditableCell`, `highlight()`). Extract into a shared component library or at minimum a shared package to prevent divergence.

## Medium Priority

### 7. Add pagination / virtualized rendering
All transactions for a category are rendered at once. For users with years of data, this will cause slow renders. Consider virtualized lists (e.g., `react-virtuoso`) or server-side pagination.

### 8. Add error recovery for partial failures
The `collect` endpoint fetches data for multiple people sequentially. If it fails partway, there is no retry mechanism or resume capability. Add per-person error handling with retry logic and a way to resume from where it left off.

### 9. Add input validation on the frontend
API responses are trusted without schema validation. Add Zod schemas mirroring the backend Pydantic models and validate API responses before rendering.

### 10. Standardize language in comments and docs
Some files mix Dutch and English in comments, variable names, and documentation. Pick one language for code-level comments and keep the other for user-facing docs.

### 11. Clean up legacy artifacts
Rename remaining references from old naming (`boekh-*`, `bankingApp-editor`) to the current naming scheme. Update `.spec` files and package names.

## Lower Priority / Nice-to-Haves

### 12. Add a changelog and versioning
The project has no version numbers or changelog. Adopting semantic versioning with a `CHANGELOG.md` (or using `towncrier` / `git-cliff`) would help track what changed between releases.

### 13. Add structured logging
Replace print statements / basic logging with structured logging (e.g., `structlog`) to improve debugging in production, especially for the long-running collect and consent flows.

### 14. Add a health check endpoint
All three services would benefit from a `/health` endpoint for monitoring and Docker health checks.

### 15. Consider migration to a lightweight database
JSON files work for the current scale but will hit limits (concurrent writes, no querying, no transactions). SQLite would be a natural next step — zero infrastructure, single-file, and queryable.

### 16. Add dark mode / theme support
The React frontend uses a single CSS file. Adding a theme toggle (light/dark) would improve UX for evening use.

---

## Suggested Next Steps (ordered)

1. Write tests for categorization and transaction merging — this is the most valuable immediate investment
2. Extract the shared Enable Banking client into a common package
3. Fix the hardcoded Vite proxy IP so development works on any machine
4. Set up `ruff` + `mypy` in CI (add a GitHub Actions workflow)
5. Define proper TypeScript interfaces for `Transaction` and API responses
6. Extract shared frontend components into a common library

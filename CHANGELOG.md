# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-02-23

### Added
- **Phase 5 — Knowledge Catalog** with 5 new MCP tools:
  - `list_memory_files` — List memory files across projects
  - `get_memory_file` — Read a specific memory file
  - `list_plans` — List plans sorted by recency
  - `get_plan` — Read a plan by codename (with optional agent subplans)
  - `search_history` — Search the global prompt history
- New `catalog.py` module for memory, plans, and history operations
- `.dockerignore` for smaller Docker image builds
- `CHANGELOG.md` (this file)
- `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1)
- `SECURITY.md` with vulnerability reporting instructions
- GitHub issue templates (bug report, feature request)
- GitHub pull request template
- Dependabot configuration for pip and GitHub Actions

### Changed
- Total MCP tools increased from 11 to 16
- Unit test count increased from 452+ to 573+
- Updated all documentation references to reflect 16 tools and 573+ tests

### Fixed
- CI integration test dataset loading (switched to curl tarball)
- `get_plan` test assertion for flat response format
- Session count assertions for combined test data

## [0.2.0] - 2026-02-01

### Added
- **Phase 1 — Foundation** with 6 MCP tools:
  - `list_sessions`, `get_session`, `get_session_segment`, `get_session_actions`, `search_sessions`, `collect_now`
- **Phase 2 — Semantic Search** with 2 MCP tools:
  - `search_semantic`, `generate_summary`
- **Phase 3 — SSE/HTTP Transport** with API key and OAuth 2.1 authentication
- **Phase 4 — Dispatch** with 3 MCP tools:
  - `restore_session`, `dispatch_task`, `get_dispatch_job`
- Background collector daemon with incremental byte-offset parsing
- Redis + filesystem fallback pattern for all stateful operations
- Docker Compose deployment with Redis and PostgreSQL (pgvector)
- Cloudflare Tunnel support for remote access
- Dispatch bridge for Docker-to-host Claude CLI delegation
- CLI tool (`agentibridge status`, `agentibridge connect`, `agentibridge help`)
- Comprehensive documentation (architecture, deployment, reference)
- 452+ unit tests, stress tests, integration tests, E2E smoke tests
- GitHub Actions CI/CD (test, build, publish, release)
- PyPI package publishing

[0.2.1]: https://github.com/The-Cloud-Clock-Work/agentibridge/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/The-Cloud-Clock-Work/agentibridge/releases/tag/v0.2.0

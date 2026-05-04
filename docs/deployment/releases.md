---
title: Releases
nav_order: 1
---

# Release Automation

AgentiBridge uses a fully automated release pipeline. A single `workflow_dispatch` event bumps the version, commits, tags, and triggers all downstream publish workflows automatically. No manual version editing, no manual tagging, no manual Docker pushes.

```
You (GitHub UI) → Actions → Release → Run workflow → select bump type
        │
        ▼
  [environment gate: release]
        │
        ▼
  reads pyproject.toml version
  computes next semver
  bumps pyproject.toml
  git commit "chore: release vX.Y.Z"
  git tag vX.Y.Z
  git push origin main          ──▶  Tests workflow
  git push origin vX.Y.Z        ──▶  PyPI publish
                                ──▶  Docker Hub publish
                                ──▶  GHCR build
```

---

## Workflows

### `.github/workflows/release.yml` — Release

The single entry point for all releases. Triggered **only** by manual dispatch.

**Triggers:** `workflow_dispatch`

**Inputs:**

| Input | Type | Options |
|-------|------|---------|
| `bump` | choice | `patch`, `minor`, `major` |

**Jobs:**

1. **`release`** — runs in `environment: release`
   - Reads current version from `pyproject.toml` using Python `tomllib`
   - Computes the next version using semver arithmetic (no external tools)
   - Overwrites `version = "..."` in `pyproject.toml` with `sed`
   - Commits `chore: release vX.Y.Z` as `github-actions[bot]`
   - Creates `git tag vX.Y.Z`
   - Pushes the commit to `main` and the tag to origin

**Permissions:** `contents: write`

**Token:** Uses `RELEASE_TOKEN` secret (PAT) if present, falls back to `GITHUB_TOKEN`. A PAT is required if `main` has branch protection rules that block `GITHUB_TOKEN` pushes.

---

### `.github/workflows/publish-pypi.yml` — PyPI Publish

Publishes the Python package to PyPI using OIDC trusted publisher (no API key needed).

**Triggers:** `push tags: ["v*"]`, `workflow_dispatch`

**Jobs:**

1. **`gate`** — runs only on `workflow_dispatch`, in `environment: release`
   - Pauses for admin approval before proceeding
   - Skipped automatically on tag pushes
2. **`publish`** — depends on `gate`, runs in `environment: pypi`
   - Builds wheel + sdist with `python -m build`
   - Publishes via `pypa/gh-action-pypi-publish` using OIDC (no `PYPI_TOKEN` secret needed)

**Permissions:** `contents: read`, `id-token: write` (required for OIDC)

---

### `.github/workflows/docker-publish.yml` — Docker Hub Publish

Builds and pushes to Docker Hub under `DOCKERHUB_USERNAME/agentibridge`.

**Triggers:** `push tags: ["v*"]`, `workflow_dispatch`

**Jobs:**

1. **`gate`** — runs only on `workflow_dispatch`, in `environment: release`
   - Pauses for admin approval before proceeding
   - Skipped automatically on tag pushes
2. **`push`** — depends on `gate`
   - Logs into Docker Hub using `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` secrets
   - Uses `docker/metadata-action` to compute tags
   - Builds and pushes with `docker/build-push-action`

**Tags produced:**

| Tag | Example |
|-----|---------|
| Semver from the git tag | `0.2.1` |
| Always `latest` | `latest` |

---

### `.github/workflows/build.yml` — GHCR Build

Builds and pushes to GitHub Container Registry (`ghcr.io`).

**Triggers:** `push tags: ["v*"]`, `workflow_dispatch`

**Jobs:**

1. **`gate`** — runs only on `workflow_dispatch`, in `environment: release`
   - Pauses for admin approval before proceeding
   - Skipped automatically on tag pushes
2. **`build`** — depends on `gate`
   - Authenticates to GHCR using `GITHUB_TOKEN`
   - Builds and pushes with `docker/build-push-action`

**Tags produced:**

| Tag | Example |
|-----|---------|
| Git ref name | `v0.2.1` |
| Always `latest` | `latest` |

**Permissions:** `contents: read`, `packages: write`

---

### `.github/workflows/test.yml` — Tests

Runs the full test suite. Not affected by this release system — runs on every push to `main` and every PR. The release commit (the version bump pushed by the Release workflow) also triggers tests.

---

## GitHub Environments

The release system relies on two GitHub environments.

### `release` environment

Controls access to the Release, Docker Hub, GHCR, and PyPI manual dispatch workflows.

**Setup:** Settings → Environments → New environment → `release`

When this environment has **Required reviewers** configured (requires GitHub Team plan on an org repo or a public repo), GitHub will pause the job and prompt an approved reviewer before it runs. This is the admin gate for all publish operations triggered manually.

If required reviewers are not available on your plan, access is controlled by repository write permissions — only collaborators with write access can trigger `workflow_dispatch` at all.

**To add required reviewers via API (once plan supports it):**

```bash
# Get your user ID
USER_ID=$(gh api /user --jq '.id')

# Set reviewer on the release environment
gh api --method PUT /repos/The-Cloud-Clockwork/agentibridge/environments/release \
  --input - <<EOF
{
  "reviewers": [{"type": "User", "id": $USER_ID}],
  "deployment_branch_policy": null
}
EOF

# Verify
gh api /repos/The-Cloud-Clockwork/agentibridge/environments/release \
  --jq '.protection_rules'
```

### `pypi` environment

Controls the PyPI trusted publisher OIDC connection. **Do not remove this environment** — the `publish-pypi.yml` workflow depends on it for keyless authentication with PyPI.

**Setup:** This environment must be configured as a trusted publisher in your PyPI project settings:

1. Log into [pypi.org](https://pypi.org) → Your projects → `agentibridge` → Publishing
2. Add a trusted publisher:
   - **Publisher:** GitHub Actions
   - **Owner:** `The-Cloud-Clockwork`
   - **Repository:** `agentibridge`
   - **Workflow:** `publish-pypi.yml`
   - **Environment:** `pypi`

This allows PyPI to verify the publish came from this specific workflow in this specific environment, with no API key or secret required.

---

## Required Secrets

Configure these in **Settings → Secrets and variables → Actions**:

| Secret | Used by | Description |
|--------|---------|-------------|
| `DOCKERHUB_USERNAME` | docker-publish | Docker Hub account username |
| `DOCKERHUB_TOKEN` | docker-publish | Docker Hub access token (not password) |
| `RELEASE_TOKEN` | release | Optional PAT with `repo` scope. Required only if `main` branch has protection rules that block `GITHUB_TOKEN` pushes. If absent, falls back to `GITHUB_TOKEN`. |

`PYPI_TOKEN` is **not** needed — PyPI authentication uses OIDC via the `pypi` environment.

### Creating a Docker Hub access token

1. Log into [hub.docker.com](https://hub.docker.com)
2. Account Settings → Security → New Access Token
3. Name it (e.g., `agentibridge-ci`), set permission to **Read, Write, Delete**
4. Copy the token and add it as `DOCKERHUB_TOKEN` in repo secrets

### Creating a RELEASE_TOKEN PAT

Only needed if main branch protection blocks `GITHUB_TOKEN` commits:

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Resource owner: `The-Cloud-Clockwork`
3. Repository access: `agentibridge` only
4. Permissions: **Contents → Read and write**
5. Add as `RELEASE_TOKEN` secret in the repo

---

## How to Do a Release

### Standard release (patch/minor/major)

1. Go to **Actions → Release → Run workflow**
2. Select the branch (`main`) and bump type (`patch`, `minor`, or `major`)
3. Click **Run workflow**
4. If the `release` environment has required reviewers: GitHub will pause and show **"Review deployments"** — click it, then **Approve and deploy**
5. The job runs, bumps the version, commits, and pushes the tag

After the tag is pushed, three downstream workflows fire automatically:
- **Tests** — runs against the bump commit on main
- **PyPI publish** — packages and publishes to PyPI
- **Docker Hub publish** — builds and pushes `0.x.y` + `latest`
- **GHCR build** — builds and pushes `v0.x.y` + `latest`

### Semver guide

| Bump | When to use | Example |
|------|-------------|---------|
| `patch` | Bug fixes, documentation, minor tweaks | `0.2.0` → `0.2.1` |
| `minor` | New features, non-breaking additions | `0.2.0` → `0.3.0` |
| `major` | Breaking changes to API or MCP tools | `0.2.0` → `1.0.0` |

---

## Manual Dispatch (Emergency / Retry)

All three publish workflows support `workflow_dispatch` for cases where a tag push succeeded but one of the publish jobs failed (e.g., transient Docker Hub outage).

**To manually retrigger a publish:**

1. Go to **Actions** → select the workflow (Docker Hub, GHCR, or PyPI)
2. **Run workflow** from `main`
3. If required reviewers are configured: approve the environment gate
4. The workflow runs against the current `HEAD` of `main`

> Note: When triggered manually, the Docker Hub metadata action will not produce a semver tag (no `v*` tag to parse). It will push only `latest`. If you need a specific version tag, re-push the original git tag instead:
> ```bash
> git push origin vX.Y.Z
> ```

---

## What Fires on What

| Event | Tests | PyPI | Docker Hub | GHCR |
|-------|-------|------|------------|------|
| Push to `main` (any commit) | ✅ | — | — | — |
| Push `v*` tag | ✅ | ✅ | ✅ | ✅ |
| Manual dispatch (approved) | — | ✅ | ✅ | ✅ |

This table is the intended steady state after the release automation was implemented. Before this system, Docker Hub and GHCR fired on every push to `main`, causing spurious publishes.

---

## Verifying a Release

After triggering a release, confirm all of the following:

```bash
# 1. Version bump commit exists on main
gh api /repos/The-Cloud-Clockwork/agentibridge/commits/main \
  --jq '.commit.message'
# → "chore: release vX.Y.Z"

# 2. Tag exists
gh api /repos/The-Cloud-Clockwork/agentibridge/git/refs/tags/vX.Y.Z \
  --jq '.ref'
# → "refs/tags/vX.Y.Z"

# 3. PyPI package version
curl -s https://pypi.org/pypi/agentibridge/json | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d['info']['version'])"
# → X.Y.Z

# 4. Docker Hub latest tag
docker pull nestorcolt/agentibridge:latest
docker inspect nestorcolt/agentibridge:latest \
  --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
# → X.Y.Z

# 5. All workflow runs succeeded
gh run list --repo The-Cloud-Clockwork/agentibridge --limit 10
```

---

## Troubleshooting

### Release workflow fails at "Compute next version"

The `tomllib` step reads `pyproject.toml` and expects `version` under `[project]`:

```toml
[project]
version = "0.2.0"
```

If `pyproject.toml` is malformed or the version field is missing, the Python snippet will fail. Fix the file and re-trigger the workflow.

### Release workflow can't push to main

If you see `remote: Permission to ... denied`:

1. The repo has branch protection rules blocking `GITHUB_TOKEN`
2. Create a fine-grained PAT with `Contents: write` and add it as `RELEASE_TOKEN` secret (see [Required Secrets](#required-secrets))
3. The workflow automatically uses `RELEASE_TOKEN` if present

### PyPI publish fails: "File already exists"

You tried to publish a version that already exists on PyPI. PyPI does not allow overwriting a release. Options:
- Trigger another `patch` bump via the Release workflow to produce a new version
- Delete the failed partial upload on PyPI if it was a test push (PyPI allows deletion within a short window)

### Docker Hub publish: no semver tag on manual dispatch

Expected — `docker/metadata-action` can only produce a semver tag when triggered by a `v*` tag event. On `workflow_dispatch`, it produces `latest` only. To force a versioned tag, re-push the existing git tag:

```bash
git push origin vX.Y.Z
```

This triggers a fresh tag-based publish run with full semver tagging.

### Gate job "skipped" on manual dispatch instead of waiting for approval

Required reviewers are not configured on the `release` environment (or the plan does not support them). The gate job exits immediately as skipped. See the [`release` environment](#release-environment) section for setup instructions and plan requirements.

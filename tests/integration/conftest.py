"""Integration test fixtures requiring Docker."""

import os
import subprocess
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMPOSE_FILE = os.path.join(PROJECT_ROOT, "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_stack():
    """Start Docker Compose stack for integration tests.

    Session-scoped: starts once, shared across all integration tests.
    """
    # Start stack
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "--build", "-d"],
        check=True,
        timeout=120,
    )

    # Wait for container
    container = "session-bridge"
    for _ in range(60):
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.strip() == "true":
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        pytest.fail("Container did not start in 60s")

    # Wait for Redis
    for _ in range(30):
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "python3",
                    "-c",
                    "from agentic_bridge.redis_client import get_redis; r=get_redis(); print(r.ping())",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "True" in result.stdout:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        pytest.fail("Redis not available in 30s")

    yield container

    # Teardown
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "down"],
        check=False,
        timeout=30,
    )

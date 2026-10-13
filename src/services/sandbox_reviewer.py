"""Sandbox Test Reviewer"""
import subprocess
import tempfile
from pathlib import Path
import docker
import structlog

logger = structlog.get_logger()

class SandboxReviewer:
    def __init__(self):
        self.docker_client = docker.from_env()

    def run_tests(self, test_code: str, framework: str = "pytest"):
        """Run tests in isolated Docker container"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_generated.py"
            test_file.write_text(test_code)

            try:
                result = self.docker_client.containers.run(
                    image="python:3.11-slim",
                    command=f"bash -c 'pip install {framework} && {framework} /tests/ -v'",
                    volumes={tmpdir: {'bind': '/tests', 'mode': 'ro'}},
                    remove=True,
                    stdout=True,
                    stderr=True,
                    timeout=300
                )
                return {"status": "passed", "output": result.decode('utf-8')}
            except docker.errors.ContainerError as e:
                return {"status": "failed", "output": e.stderr.decode('utf-8')}
            except Exception as e:
                return {"status": "error", "output": str(e)}

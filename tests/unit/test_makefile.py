import json
import os
import subprocess
from pathlib import Path


def test_sandbox_image_target_uses_configured_image_tag(tmp_path: Path) -> None:
    image = "configured-sandbox:9.9.9"
    config_path = tmp_path / "defaults.yaml"
    config_path.write_text(json.dumps({"sandbox": {"image": image}}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$MAKE_DOCKER_ARGS"\n')
    fake_docker.chmod(0o755)
    args_path = tmp_path / "docker-args"

    result = subprocess.run(
        ["make", "sandbox-image", f"CONFIG_PATH={config_path}"],
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        env={
            "MAKE_DOCKER_ARGS": str(args_path),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert args_path.read_text().splitlines() == [
        "build",
        "--tag",
        image,
        "--file",
        "docker/sandbox/Dockerfile",
        ".",
    ]


def test_sandbox_image_target_does_not_interpolate_config_path() -> None:
    result = subprocess.run(
        [
            "make",
            "--dry-run",
            "sandbox-image",
            "CONFIG_PATH=$$(printf MAKE_CONFIG_PATH_INTERPOLATED >&2)",
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
    )

    assert "MAKE_CONFIG_PATH_INTERPOLATED" not in result.stderr


def test_sandbox_image_target_rejects_an_unsafe_image_tag(tmp_path: Path) -> None:
    config_path = tmp_path / "defaults.yaml"
    config_path.write_text(
        json.dumps({"sandbox": {"image": "safe;printf MAKE_IMAGE_INTERPOLATED"}})
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n")
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["make", "sandbox-image", f"CONFIG_PATH={config_path}"],
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )

    assert "MAKE_IMAGE_INTERPOLATED" not in result.stdout
    assert result.returncode != 0


def test_docker_tests_are_opt_in_and_build_the_image() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "test-docker"],
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert 'docker", "build' in result.stdout
    assert (
        "CAPABILITY_LAB_DOCKER_TESTS=1 uv run pytest tests/integration/test_docker_sandbox.py"
        in (result.stdout)
    )

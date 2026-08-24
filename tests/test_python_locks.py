import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
LOCK_ROOT = ROOT / "requirements"
PIN = re.compile(r"^([a-z0-9][a-z0-9._-]*)==([^ \\]+) \\$", re.MULTILINE)


def lock(name: str) -> str:
    return LOCK_ROOT.joinpath(name).read_text()


def pins(name: str) -> dict[str, str]:
    return dict(PIN.findall(lock(name)))


def test_every_locked_requirement_is_exact_and_hashed() -> None:
    for name in ("runtime.lock", "dev.lock", "build.lock"):
        content = lock(name)
        entries = list(PIN.finditer(content))

        assert entries
        for index, entry in enumerate(entries):
            end = entries[index + 1].start() if index + 1 < len(entries) else len(content)
            assert "--hash=sha256:" in content[entry.end() : end]
        assert " @ " not in content
        assert "-e " not in content


def test_runtime_and_dev_locks_cover_declared_dependencies() -> None:
    project = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text())["project"]
    runtime = pins("runtime.lock")
    dev = pins("dev.lock")

    for requirement in project["dependencies"]:
        normalized = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].lower()
        assert normalized in runtime
        assert runtime[normalized] == dev[normalized]
    assert dev["pytest"] == "9.1.1"
    assert dev["ruff"] == "0.16.4"


def test_build_lock_matches_the_exact_backend() -> None:
    build_system = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text())[
        "build-system"
    ]

    assert build_system["requires"] == ["hatchling==1.32.0"]
    assert pins("build.lock")["hatchling"] == "1.32.0"


def test_lock_compiler_is_versioned_and_hash_enforcing() -> None:
    script = ROOT.joinpath("deploy/local/compile-python-locks.sh").read_text()

    assert 'expected_version="7.6.1"' in script
    assert "--generate-hashes" in script
    assert "--index-url https://pypi.org/simple" in script
    assert "--all-build-deps" in script

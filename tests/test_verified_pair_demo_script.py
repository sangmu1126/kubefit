import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "local" / "run-verified-pair-demo.sh"


def test_verified_pair_demo_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_verified_pair_demo_binds_public_evidence_identity() -> None:
    content = SCRIPT.read_text()

    assert "kubefit-demo-evidence-v0.2.0.tar.gz" in content
    assert 'release_version="0.3.2"' in content
    assert 'evidence_release_version="0.2.0"' in content
    assert "c646b4483083f8fcedafb397d1cc2355391bc9f98b15a6b157e22b30f2793239" in content
    assert "benchmark-pair-dbc41864dd0dba9537ef228ebb340f60" in content
    assert "https://github.com/sangmu1126/kubefit/releases/download/" in content
    assert "ghcr.io/sangmu1126/kubefit:${release_version}" in content
    assert 'demo_query="showcase=decision-journey"' in content


def test_verified_pair_demo_can_build_the_current_showcase_source() -> None:
    content = SCRIPT.read_text()

    assert "KUBEFIT_DEMO_BUILD_LOCAL" in content
    assert 'docker build --tag "${image_reference}" .' in content
    assert 'demo_query="showcase=decision-journey"' in content


def test_verified_pair_demo_keeps_evidence_read_only_and_loopback_only() -> None:
    content = SCRIPT.read_text()

    assert '127.0.0.1:${local_port}:8000' in content
    assert "/var/lib/kubefit/pairs:ro" in content
    assert "KUBEFIT_BENCHMARK_PAIRS_DIRECTORY=/var/lib/kubefit/pairs" in content
    assert "sha256_file" in content
    assert "demo evidence digest mismatch" in content
    assert "unsafe archive path" in content

from decimal import Decimal

import pytest

from api.cli import build_parser


def test_analyze_requires_and_parses_explicit_prices() -> None:
    args = build_parser().parse_args(
        [
            "analyze",
            "--deployment",
            "demo",
            "--cpu-core-hour-usd",
            "0.04",
            "--memory-gib-hour-usd",
            "0.005",
            "--price-source",
            "example://local-model",
        ]
    )

    assert args.cpu_core_hour_usd == Decimal("0.04")
    assert args.memory_gib_hour_usd == Decimal("0.005")
    assert args.monthly_hours == Decimal("730")


def test_analyze_rejects_missing_prices() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", "--deployment", "demo"])


def test_analyze_rejects_non_positive_price() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "analyze",
                "--deployment",
                "demo",
                "--cpu-core-hour-usd",
                "0",
                "--memory-gib-hour-usd",
                "0.005",
                "--price-source",
                "example://local-model",
            ]
        )

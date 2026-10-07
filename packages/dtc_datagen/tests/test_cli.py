import pytest

from dtc_datagen.cli import parse_args


def test_now_must_be_aware() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--policy", "p.yaml", "--now", "2026-09-28T12:00:00"])


def test_policy_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLICY_PATH", raising=False)
    with pytest.raises(SystemExit):
        parse_args([])


def test_defaults_match_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICY_PATH", "p.yaml")
    args = parse_args([])
    assert (args.seed, args.customers, args.orders) == (42, 2000, 5000)
    assert args.now.isoformat() == "2026-09-28T12:00:00+00:00"

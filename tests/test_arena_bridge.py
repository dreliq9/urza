"""Pure stdlib tests: no live Arena, Tapps, or credentials required."""
from __future__ import annotations

import csv
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPHandler, build_opener
from urllib.response import addinfourl

import pytest

from urza import arena_bridge as a


RAW = ("count,name,set,collector_number,rarity,colors,type,mana_cost,arena_id\n"
       "1,Curious Farm Animals,TLA,14,common,W,Creature - Boar Elk Bird Ox,W,1001\n"
       "2,Counterspell,STA,15,uncommon,U,Instant,UU,1002\n"
       "1,Island,FDN,275,common,,Basic Land,,1003\n"
       "1,Delver of Secrets // Insectile Aberration,MID,47,uncommon,U,Creature,,1004\n")


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "collection.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_bynov_raw_collection_import(tmp_path: Path):
    cards, fmt = a.read_collection(write_csv(tmp_path, RAW))
    assert fmt == "arena_raw"
    assert a.collection_summary(cards) == {"printings": 4, "distinct_names": 4, "copies": 5}
    assert cards[0].arena_id == 1001
    assert "Edition" in a.to_moxfield_csv(cards)


def test_moxfield_and_manabox_compatibility(tmp_path: Path):
    mox = "Count,Name,Edition,Collector Number\n2,Counterspell,STA,15\n"
    rows, fmt = a.read_collection(write_csv(tmp_path, mox))
    assert fmt == "moxfield" and rows[0].quantity == 2
    mana = "Name,Set code,Collector number,Quantity\nCounterspell,STA,15,3\n"
    rows, fmt = a.read_collection(write_csv(tmp_path, mana))
    assert fmt == "manabox" and rows[0].quantity == 3


def test_collection_rejects_unknown_header_and_invalid_data(tmp_path: Path):
    with pytest.raises(ValueError, match="Unrecognized"):
        a.read_collection(write_csv(tmp_path, "unknown,column\nanything,1\n"))
    with pytest.raises(ValueError, match="Invalid"):
        a.read_collection(write_csv(tmp_path, RAW.replace("2,Counterspell", "-2,Counterspell")))


def test_collection_rejects_duplicate_arena_ids(tmp_path: Path):
    broken = RAW.replace("1002", "1001")
    with pytest.raises(ValueError, match="Duplicate Arena"):
        a.read_collection(write_csv(tmp_path, broken))


def test_collection_rejects_empty_owned_cards(tmp_path: Path):
    text = "count,name,set,collector_number,arena_id\n0,Counterspell,STA,15,1001\n"
    with pytest.raises(ValueError, match="no owned"):
        a.read_collection(write_csv(tmp_path, text))


@pytest.mark.parametrize("header", ["", "About\nName Sarek Competitive Brawl\n\n"])
def test_parse_arena_deck_and_ignore_sideboard(header: str):
    deck = header + """Commander
1 Sarek, Federation Ambassador (TRK) 251

Deck
2 Counterspell (STA) 15
97 Island (FDN) 275

Sideboard
1 Uro, Titan of Nature's Wrath (THB) 229
"""
    parsed = a.parse_deck(deck)
    assert len(parsed) == 3
    assert sum(x.quantity for x in parsed) == 100
    assert parsed[0].section == "commander"
    assert parsed[0].name == "Sarek, Federation Ambassador"


def test_parse_arena_deck_rejects_bad_input():
    with pytest.raises(ValueError, match="Invalid Arena deck"):
        a.parse_deck("Deck\nCounterspell (STA) 15")


def test_audit_owned_dfc_and_unlimited_basics():
    deck = """Commander
1 Sarek, Federation Ambassador
Deck
1 Delver of Secrets // Insectile Aberration (MID) 47
2 Counterspell
96 Island
"""
    owned = {"sarek, federation ambassador": 1, "delver of secrets": 1, "counterspell": 1}
    result = a.audit_deck(deck, owned)
    assert result["required_slots"] == 100
    assert result["commander_slots"] == 1
    assert result["missing_slots"] == 1
    assert result["missing"][0]["name"] == "Counterspell"
    assert result["is_100_cards"] is True


def test_audit_no_ownership_claim_of_legality():
    result = a.audit_deck("Deck\n1 City of Brass", {})
    assert result["missing_slots"] == 1
    assert "legality" in result["note"]
    assert result["is_100_cards"] is False


def test_memory_scan_requires_opt_in():
    with pytest.raises(PermissionError):
        a.run_macos_export(exporter="mtga-collection-export", output_csv="/tmp/no.csv")


def test_memory_scan_rejects_nonmac(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(a.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="macOS"):
        a.run_macos_export(exporter="mtga-collection-export", output_csv="/tmp/no.csv", confirm_memory_scan=True)


@pytest.mark.parametrize("export_fails", [False, True])
def test_exporter_preserves_inputs_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_fails: bool,
):
    binary = tmp_path / "mtga-collection-export"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(a.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(a.shutil, "which", lambda name: str(binary))
    destination = tmp_path / "out" / "my.csv"
    destination.parent.mkdir()
    destination.write_text("previous collection", encoding="utf-8")
    scratch_paths = []

    def fake_run(args, **kwargs):
        scratch = Path(kwargs.get("env", {}).get("TMPDIR", tmp_path))
        scratch_paths.append(scratch)
        inputs = scratch / "exporter-inputs"
        inputs.mkdir()
        snapshot = inputs / "fixture.core"
        snapshot.write_bytes(b"synthetic snapshot")
        if "--keep-core" not in args:
            snapshot.unlink()  # Current bynov releases remove inputs before processing.
        assert snapshot.is_file(), "Exporter inputs were deleted before processing"
        assert scratch.stat().st_mode & 0o077 == 0
        assert args[args.index("--format") + 1] == "raw"
        if export_fails:
            raise a.subprocess.CalledProcessError(1, args, stderr="synthetic exporter failure")
        Path(args[args.index("--out") + 1]).write_text(RAW, encoding="utf-8")

    monkeypatch.setattr(a.subprocess, "run", fake_run)
    def export():
        return a.run_macos_export(
            exporter=str(binary), output_csv=destination, confirm_memory_scan=True,
        )

    if export_fails:
        with pytest.raises(RuntimeError, match="synthetic exporter failure"):
            export()
        assert destination.read_text(encoding="utf-8") == "previous collection"
    else:
        cards, dst = export()
        assert len(cards) == 4 and dst == destination
        assert dst.read_text(encoding="utf-8") == RAW
        assert dst.stat().st_mode & 0o077 == 0
    assert len(scratch_paths) == 1 and not scratch_paths[0].exists()


def test_ranked_tapps_only_and_private_data_excluded():
    data = [
        {"format_label": "Brawl (Ranked)", "deck_name": "Sarek", "outcome": "win", "is_flood": True, "opponent_name": "Secret"},
        {"format_label": "Brawl (Ranked)", "deck_name": "Sarek", "outcome": "loss", "is_screw": True},
        {"format_label": "Brawl", "deck_name": "Sarek", "outcome": "loss"},
        {"format_label": "Standard Ranked", "deck_name": "Aggro", "outcome": "loss"},
        {"format_label": "Competitive Brawl", "deck_name": "Aang", "outcome": "win"},
    ]
    report = a.summarize_tapps_games(data)
    assert (report["decided_games"], report["wins"], report["losses"]) == (3, 2, 1)
    assert report["win_rate_pct"] == 66.7
    assert report["mana_flood_flags"] == 1 and report["mana_screw_flags"] == 1
    assert "Secret" not in json.dumps(report)
    filtered = a.summarize_tapps_games(data, deck_name="sarek")
    assert filtered["decided_games"] == 2 and filtered["win_rate_pct"] == 50.0


def test_tapps_request_loopback_and_schema(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({"games": [{"format_label": "Brawl (Ranked)", "outcome": "win"}], "total": 1}).encode()

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            assert timeout == 5
            return Response()

    monkeypatch.setattr(a, "build_opener", lambda *args: Opener())
    report = a.tapps_ranked_report(8765)
    assert captured["url"] == "http://127.0.0.1:8765/api/games"
    assert report["wins"] == 1
    with pytest.raises(ValueError, match="port"):
        a._read_local_tapps_games(0)


@pytest.mark.parametrize("location", ["/another-path", "http://example.invalid/games"])
def test_tapps_rejects_redirects(monkeypatch: pytest.MonkeyPatch, location: str):
    requests = []

    class FixtureTransport(HTTPHandler):
        def http_open(self, request):
            requests.append(request.full_url)
            assert len(requests) == 1, "Tapps must not issue a second request"
            headers = Message()
            headers["Location"] = location
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(a, "build_opener", lambda *handlers: build_opener(*handlers, FixtureTransport()))
    with pytest.raises(HTTPError) as error:
        a.tapps_ranked_report()
    assert error.value.code == 302
    assert requests == ["http://127.0.0.1:8765/api/games"]


def test_no_games_is_not_a_zero_percent_winrate():
    r = a.summarize_tapps_games([])
    assert r["win_rate_pct"] is None and r["decided_games"] == 0

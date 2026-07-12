"""account_creds.resolve_password — shared credential truth (P0, 2026-07-12).

Precedence password_cmd > password_env > bw_item(→life-mail-passwd). All hermetic
(subprocess mocked — no real bw / life-mail-passwd / file / IMAP touched), plus a
parity check that production_worker._resolve_password delegates to the same logic.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import account_creds as ac  # noqa: E402


def _fake_run(recorder, stdout):
    def run(*args, **kwargs):
        recorder.append((args, kwargs))
        return SimpleNamespace(stdout=stdout, returncode=0)
    return run


# ---- password_cmd (highest precedence) --------------------------------------

def test_password_cmd_wins_no_bw(monkeypatch):
    calls: list = []
    monkeypatch.setattr(ac.subprocess, "run", _fake_run(calls, "  secret16charss  \n"))
    pw = ac.resolve_password({"password_cmd": "cat /x", "bw_item": "ignored", "password_env": "IGN"})
    assert pw == "secret16charss"  # stripped
    # the shell command was run, not life-mail-passwd
    (args, kwargs) = calls[0]
    assert args[0] == "cat /x" and kwargs.get("shell") is True
    assert len(calls) == 1  # bw never reached


def test_password_cmd_over_env(monkeypatch):
    monkeypatch.setenv("IGN", "envval")
    monkeypatch.setattr(ac.subprocess, "run", _fake_run([], "cmdval\n"))
    assert ac.resolve_password_or_none({"password_cmd": "c", "password_env": "IGN"}) == "cmdval"


# ---- password_env -----------------------------------------------------------

def test_password_env_used(monkeypatch):
    monkeypatch.setenv("YAHOO_PW", "fromenv")
    assert ac.resolve_password_or_none({"password_env": "YAHOO_PW"}) == "fromenv"


def test_password_env_unset_raises(monkeypatch):
    monkeypatch.delenv("YAHOO_PW", raising=False)
    with pytest.raises(RuntimeError):
        ac.resolve_password_or_none({"password_env": "YAHOO_PW"})


# ---- bw_item fallback -------------------------------------------------------

def test_or_none_returns_none_for_bw_only():
    # IMAPSession-based callers rely on None → lazy bw resolution.
    assert ac.resolve_password_or_none({"bw_item": "IMAP Yahoo App Password"}) is None


def test_resolve_password_bw_fallback(monkeypatch):
    calls: list = []
    monkeypatch.setattr(ac.subprocess, "run", _fake_run(calls, "bwsecret\n"))
    pw = ac.resolve_password({"bw_item": "IMAP Yahoo App Password"})
    assert pw == "bwsecret"
    (args, kwargs) = calls[0]
    assert args[0] == ["life-mail-passwd", "IMAP Yahoo App Password"]


def test_no_source_raises():
    with pytest.raises(RuntimeError):
        ac.resolve_password({"login": "x"})


# ---- parity: production_worker delegates to account_creds --------------------

def test_production_worker_delegates(monkeypatch):
    import production_worker as pw  # requests available in .venv
    monkeypatch.setattr(ac.subprocess, "run", _fake_run([], "cmdpw\n"))
    acct = {"password_cmd": "cat /y", "bw_item": "b"}
    assert pw._resolve_password(acct) == ac.resolve_password_or_none(acct) == "cmdpw"
    assert pw._resolve_password({"bw_item": "b"}) is None  # same None-for-bw semantics

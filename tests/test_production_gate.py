"""
שער הייצור נכשל סגור.

הדרישה: ב-PORTAL_MODE=production, רכיב חובה חסר אינו נופל
חזרה להתנהגות demo - הוא מסרב.
"""

import importlib
import os

import pytest


def _reload_production(monkeypatch, **env):
    monkeypatch.setenv("PORTAL_MODE", "production")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import server.app
    return importlib.reload(server.app)


@pytest.fixture(autouse=True)
def _restore():
    yield
    os.environ["PORTAL_MODE"] = "demo"
    import server.app
    importlib.reload(server.app)


def test_production_refuses_to_start_with_demo_accounts(monkeypatch):
    """
    הבדיקה החשובה: nahmani@... עם office2026 ששרד לייצור.
    נתוני הזרע קיימים במסד, ולכן השער חייב לחסום.
    """
    module = _reload_production(
        monkeypatch, PORTAL_ALLOWED_ORIGINS="https://portal.example.com")
    with pytest.raises(RuntimeError) as err:
        module._assert_production_ready()
    assert "חשבונות הדגמה" in str(err.value)


def test_production_refuses_without_allowed_origins(monkeypatch):
    module = _reload_production(monkeypatch, PORTAL_ALLOWED_ORIGINS=None)
    with pytest.raises(RuntimeError) as err:
        module._assert_production_ready()
    assert "PORTAL_ALLOWED_ORIGINS" in str(err.value)


def test_production_refuses_without_encryption_keys(monkeypatch):
    module = _reload_production(monkeypatch, PORTAL_ID_HMAC_KEY=None)
    with pytest.raises(RuntimeError) as err:
        module._assert_production_ready()
    assert "PORTAL_ID_HMAC_KEY" in str(err.value)


def test_otp_is_never_logged_in_production(monkeypatch):
    """קוד OTP שנכתב ללוג בייצור הוא קוד שדלף."""
    monkeypatch.setenv("PORTAL_MODE", "production")
    import server.notify
    module = importlib.reload(server.notify)

    sender = module.get_sender()
    assert isinstance(sender, module.NullSender)
    assert sender.production_safe is True
    with pytest.raises(module.SmsNotConfigured):
        sender.send_otp("050-0000000", "123456")

    monkeypatch.setenv("PORTAL_MODE", "demo")
    importlib.reload(server.notify)


def test_demo_sender_is_never_production_safe():
    import server.notify as notify
    assert notify.ConsoleSender.production_safe is False


def test_scanner_refuses_upload_in_production(monkeypatch):
    monkeypatch.setenv("PORTAL_MODE", "production")
    import server.scan as scan
    module = importlib.reload(scan)
    with pytest.raises(module.ScannerNotConfigured):
        module.assert_upload_allowed()
    monkeypatch.setenv("PORTAL_MODE", "demo")
    importlib.reload(scan)


def test_no_scanner_is_not_production_safe():
    import server.scan as scan
    assert scan.NoScanner.production_safe is False

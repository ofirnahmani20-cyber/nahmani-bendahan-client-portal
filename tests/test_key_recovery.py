"""
תרגיל התאוששות מאסון - על נתונים סינתטיים בלבד.

זהו התרחיש שנוהל 07 מתאר, מקצה לקצה:
  1. מפתחות נוצרים, מסמכים נשמרים מוצפנים.
  2. חבילת נאמנות מיוצאת (מוצפנת בסיסמה).
  3. השרת "נשרף": תיקיית הסודות נמחקת. הנתונים שרדו בגיבוי.
  4. verify מזהה שהמפתחות חסרים - לפני שמנסים לפענח.
  5. שחזור מהנאמנות לתיקייה חדשה, verify עובר, וכל קובץ נפתח.

ובנוסף - מה שאסור שיעבוד: סיסמה שגויה, חבילה ששונתה, ודריסת
מפתח קיים בשחזור.
"""

import json
import shutil

import pytest

from server import crypto, escrow, storage

PASS = "correct horse battery staple - synthetic"
DOCS = [b"%%PDF-1.4\n%% synthetic document %d\n" % i for i in range(5)]


@pytest.fixture
def site(tmp_path, monkeypatch):
    secrets_dir = tmp_path / "secrets"
    storage_dir = tmp_path / "uploads"
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(storage_dir))
    crypto.generate_keys(secrets_dir)
    keys = [storage.validate_and_store(d, firm_id="firm-x", case_id="case-%d" % i,
                                       original_name="doc")["storage_key"]
            for i, d in enumerate(DOCS)]
    return {"tmp": tmp_path, "secrets": secrets_dir, "storage": storage_dir,
            "keys": keys}


def test_full_disaster_recovery_drill(site, monkeypatch):
    bundle = escrow.export_bundle(site["secrets"], PASS)
    serialized = json.dumps(bundle)
    for raw in crypto.FileKeyProvider(site["secrets"]).all_keys().values():
        assert raw.hex() not in serialized
        assert crypto.kcv(raw).hex() in serialized   # KCV גלוי, המפתח לא

    # האסון.
    shutil.rmtree(site["secrets"])
    restored = site["tmp"] / "restored-secrets"
    restored.mkdir()
    report = escrow.verify(restored, site["storage"])
    assert not report["ok"]
    assert report["missing_key"] == {"files_kek_v1": len(DOCS)}

    # השחזור.
    written = escrow.import_bundle(bundle, PASS, restored)
    assert "files_kek_v1" in written and "text_kek_v1" in written
    report = escrow.verify(restored, site["storage"])
    assert report["ok"], report
    assert report["files_ok"] == len(DOCS)

    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(restored))
    for key, original in zip(site["keys"], DOCS):
        assert storage.read(key) == original


def test_wrong_passphrase_is_refused(site):
    bundle = escrow.export_bundle(site["secrets"], PASS)
    with pytest.raises(escrow.EscrowError):
        escrow.open_bundle(bundle, PASS + "x")


def test_tampered_bundle_is_refused(site):
    bundle = escrow.export_bundle(site["secrets"], PASS)
    bundle["kcv"]["files_kek_v1"] = "00" * 8
    with pytest.raises(escrow.EscrowError):
        escrow.open_bundle(bundle, PASS)


def test_short_passphrase_is_refused(site):
    with pytest.raises(escrow.EscrowError):
        escrow.export_bundle(site["secrets"], "short")


def test_import_never_overwrites_a_different_key(site):
    bundle = escrow.export_bundle(site["secrets"], PASS)
    other = site["tmp"] / "other"
    crypto.generate_keys(other)            # מפתחות אחרים באותם שמות
    with pytest.raises(FileExistsError):
        escrow.import_bundle(bundle, PASS, other)


def test_verify_flags_legacy_plaintext(site):
    legacy = site["storage"] / "firms" / "f" / "cases" / "c" / "legacy"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"%PDF-1.4 legacy plaintext")
    report = escrow.verify(site["secrets"], site["storage"])
    assert report["plaintext"] == 1 and not report["ok"]


def test_keygen_cli_round_trip(site, monkeypatch, tmp_path, capsys):
    """הפקודות שהנוהל מורה להריץ - עובדות, ואינן מדפיסות מפתח."""
    from server import maintenance
    from server.db import keygen

    monkeypatch.setenv("PORTAL_ESCROW_PASSPHRASE", PASS)
    out = tmp_path / "escrow.json"
    assert keygen.main(["export-escrow", "--dir", str(site["secrets"]),
                        "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    for raw in crypto.FileKeyProvider(site["secrets"]).all_keys().values():
        import base64
        assert base64.b64encode(raw).decode() not in printed

    fresh = tmp_path / "fresh"
    assert keygen.main(["import-escrow", "--in", str(out), "--dir", str(fresh)]) == 0
    assert keygen.main(["verify", "--dir", str(fresh)]) == 0

    # encrypt-legacy: קובץ גלוי הופך לצופן שנקרא חזרה.
    legacy_key = "firms/f/cases/c/legacy"
    legacy = site["storage"] / legacy_key
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"%PDF-1.4 legacy")
    assert maintenance.encrypt_legacy(apply=False)["plaintext"] == 1
    assert crypto.is_encrypted(legacy.read_bytes()) is False
    maintenance.encrypt_legacy(apply=True)
    assert crypto.is_encrypted(legacy.read_bytes())
    assert storage.read(legacy_key) == b"%PDF-1.4 legacy"

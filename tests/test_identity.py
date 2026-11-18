from healthbot import detectors
from healthbot.identity import Identity, load_identity, normalize
from healthbot.scrub import scrub_text, verify_text

ID = Identity(
    names=["Jonathan Q. Testperson"], aliases=["Jon Testperson"], dob="1984-07-09",
    addresses=["123 Fake Street, Springfield, ON A1B 2C3"], phones=["+1 555-010-0199"],
    emails=["jon@example.com"],
    ids=[("health_card", "1234-567-890-AB"), ("mrn", "MRN0042")],
    extra_terms=["Acme Corp"],
)


def texts(tag=None):
    return {v.text.lower() for v in ID.variants() if tag is None or v.tag == tag}


def test_name_variants():
    t = texts("NAME")
    for want in ["jonathan q. testperson", "testperson, jonathan", "jonathan testperson",
                 "j. q. testperson", "jq testperson", "jonathan", "testperson", "jon"]:
        assert want in t, want
    assert "q" not in t  # single letters are never redacted on their own


def test_dob_variants():
    t = texts("DOB")
    for want in ["1984-07-09", "09/07/1984", "07/09/1984", "09-07-1984", "jul 9, 1984",
                 "july 9, 1984", "9 jul 1984", "19840709", "9/7/1984", "jul 09, 1984"]:
        assert want in t, want


def test_phone_id_postal_address_variants():
    assert {"555-010-0199", "5550100199", "(555) 010-0199", "555.010.0199",
            "+1 555-010-0199"} <= texts("PHONE")
    assert {"1234-567-890-ab", "1234 567 890 ab", "1234567890ab"} <= texts("ID:health_card")
    a = texts("ADDRESS")
    assert {"123 fake street", "a1b 2c3", "a1b2c3"} <= a


def test_longest_first():
    lens = [len(v.text) for v in ID.variants()]
    assert lens == sorted(lens, reverse=True)


def test_replacer_handles_linebreak_markdown_and_case():
    src = ("Patient: Jonathan\nQ. Testperson / TESTPERSON, JONATHAN / **Jon** **Testperson**\n"
           "DOB Jul 9, 1984 and 09/07/1984; HC 1234 567 890 AB; call (555) 010-0199; "
           "[mail](mailto:jon@example.com); 123 Fake  Street; Acme Corp")
    out, counts = scrub_text(src, ID)
    assert verify_text(out, ID) == []
    for bad in ["jonathan", "testperson", "1984", "1234", "010-0199", "example.com",
                "fake", "acme"]:
        assert bad not in out.lower()
    assert counts["name"] >= 3 and counts["dob"] == 2
    assert counts["health_card"] == 1 and counts["phone"] == 1 and counts["email"] == 1
    assert "[NAME]" in out and "[ID:health_card]" in out


def test_clinical_content_untouched():
    src = "LDL 4.1 mmol/L (H). Tdap 2016-05-02. HbA1c 6.0%. BP 138/88. LVEF 60%."
    out, counts = scrub_text(src, ID)
    assert out == src and counts == {}


def test_verifier_flags_leftover_without_value():
    findings = verify_text("seen on 9 Jul 1984 by testperson", ID)
    assert "dob" in findings and "name" in findings
    assert all("1984" not in f and "testperson" not in f for f in findings)


def test_verifier_ignores_normalization_noise():
    assert verify_text("[NAME] [DOB] [ID:health_card] LDL 4.1 on 2026-01-15", ID) == []


def test_detectors():
    out, counts = detectors.redact(
        "a@b.org 416-555-0123 K1A 0B1 12345-6789 123456789 date 2026-01-15 BP 138/88")
    assert out == "[EMAIL] [PHONE] [POSTAL] [POSTAL] [ID:auto] date 2026-01-15 BP 138/88"
    assert set(counts) == {"auto:email", "auto:phone", "auto:postal", "auto:zip4", "auto:id"}
    assert detectors.scan(out) == []


def test_normalize():
    assert normalize("A1B-2c3 !") == "a1b2c3"


def test_load_identity(tmp_path):
    p = tmp_path / "id.yaml"
    p.write_text("names: ['A B']\ndob: '2000-01-02'\nids:\n  - {label: health card, value: X1}\n")
    i = load_identity(p)
    assert i.names == ["A B"] and i.ids == [("health_card", "X1")] and i.phones == []

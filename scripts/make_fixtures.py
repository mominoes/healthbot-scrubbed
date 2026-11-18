"""Generate synthetic fixtures (fake persona only). Run: .venv/bin/python scripts/make_fixtures.py"""
import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
PDFS = FIX / "pdfs"
NAME, DOB_ISO = "Jonathan Q. Testperson", "1984-07-09"
HC, MRN = "1234-567-890-AB", "MRN0042"
PHONE, EMAIL = "+1 555-010-0199", "jon@example.com"
ADDR = "123 Fake Street, Springfield, ON A1B 2C3"
SS = getSampleStyleSheet()

IDENTITY_YAML = f"""names: ["{NAME}"]
aliases: ["Jon Testperson"]
dob: "{DOB_ISO}"
addresses: ["{ADDR}"]
phones: ["{PHONE}"]
emails: ["{EMAIL}"]
ids:
  - {{label: health_card, value: "{HC}"}}
  - {{label: mrn, value: "{MRN}"}}
extra_terms: []
""".replace("names:", "names:", 1)


def P(t, s="BodyText"):
    return Paragraph(t, SS[s])


def table(rows, widths=None):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    return t


def build(name, story, author="", title="", footer=None):
    def deco(c, d):
        if footer:
            c.setFont("Helvetica", 8)
            c.drawString(50, 30, footer)
    doc = SimpleDocTemplate(str(PDFS / name), pagesize=letter, author=author, title=title)
    doc.build(story, onFirstPage=deco, onLaterPages=deco)


def main():
    PDFS.mkdir(parents=True, exist_ok=True)
    # Note: identity.example.yaml uses "names"; keep same schema.
    (FIX / "identity.yaml").write_text(IDENTITY_YAML)

    build("lab-2025-03-lipid.pdf", [
        P("Springfield Medical Laboratory", "Title"),
        P(f"Patient: {NAME}<br/>DOB: 09/07/1984<br/>Health Card: {HC}<br/>Collected: 2025-03-14"),
        Spacer(1, 12), P("Lipid Panel", "Heading2"),
        table([["Test", "Result", "Units", "Reference", "Flag"],
               ["LDL Cholesterol", "4.1", "mmol/L", "< 3.4", "H"],
               ["HDL Cholesterol", "1.2", "mmol/L", "> 1.0", ""],
               ["Triglycerides", "1.6", "mmol/L", "< 1.7", ""],
               ["Total Cholesterol", "6.0", "mmol/L", "< 5.2", "H"]]),
    ], author=NAME, title="Lipid panel")

    build("lab-2026-01-lipid-a1c.pdf", [
        P("Springfield Medical Laboratory", "Title"),
        P("Patient: TESTPERSON, JONATHAN<br/>DOB: Jul 9, 1984<br/>Collected: 2026-01-20"),
        Spacer(1, 12), P("Lipid Panel and HbA1c", "Heading2"),
        table([["Test", "Result", "Units", "Reference", "Flag"],
               ["LDL Cholesterol", "3.4", "mmol/L", "< 3.4", ""],
               ["HDL Cholesterol", "1.3", "mmol/L", "> 1.0", ""],
               ["Triglycerides", "1.4", "mmol/L", "< 1.7", ""],
               ["Total Cholesterol", "5.3", "mmol/L", "< 5.2", "H"],
               ["HbA1c", "6.0", "%", "4.0 - 5.6", "H"]]),
    ])

    build("clinic-2026-01-annual-physical.pdf", [
        P("Springfield Family Clinic: Annual Physical", "Title"),
        P("Patient: Jonathan Q.<br/>Testperson<br/>Date of visit: 2026-01-28"),
        P("<b>S:</b> Annual exam. Family history: father had myocardial infarction at age 52. "
          "Current smoker, about 5 cigarettes per day. Patient can be reached at "
          f"{EMAIL} for follow-up."),
        P("<b>O:</b> BP 138/88 mmHg. BMI 28. HR 72. Heart and lungs normal."),
        P("<b>A:</b> Elevated blood pressure reading; borderline lipids; tobacco use."),
        P("<b>P:</b> Recheck lipids in 3 months. Smoking cessation counselling offered. "
          "Home BP monitoring."),
    ], footer=f"Springfield Family Clinic | Tel {PHONE} | Fax +1 555-010-0100")

    # mailto link: build with a canvas link annotation
    buf = PDFS / "immunization-record.pdf"
    c = canvas.Canvas(str(buf), pagesize=letter)
    c.setTitle("Immunization record")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 740, "Provincial Immunization Record")
    c.setFont("Helvetica", 11)
    c.drawString(50, 715, f"Name: {NAME}    DOB: 1984-07-09")
    rows = [["Vaccine", "Date", "Notes"],
            ["Tdap", "2016-05-02", ""],
            ["Influenza", "2025-10", "Seasonal"],
            ["COVID-19 (booster)", "2022-11-05", "Bivalent"],
            ["COVID-19 (booster)", "2024-10-18", "XBB"]]
    y = 680
    for r in rows:
        c.drawString(50, y, r[0]); c.drawString(250, y, r[1]); c.drawString(350, y, r[2])
        y -= 20
    c.drawString(50, y - 10, "Questions: ")
    c.setFillColor(colors.blue)
    c.drawString(105, y - 10, EMAIL)
    c.linkURL(f"mailto:{EMAIL}", (105, y - 14, 190, y - 2), relative=0)
    c.save()

    build("imaging-2025-11-echo.pdf", [
        P("Springfield Cardiology: Echocardiogram Report", "Title"),
        P(f"Patient: {NAME}<br/>Health Card: {HC}<br/>Study date: 2025-11-10"),
        P("<b>Findings:</b> LVEF 60%. Normal left ventricular size and wall thickness. "
          "No significant valvular disease. No pericardial effusion."),
        P("<b>Impression:</b> Normal transthoracic echocardiogram."),
    ])

    # scanned page: image only, no text layer
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (850, 1100), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 60), f"SCANNED REPORT  Patient: {NAME}  HC {HC}", fill="black")
    for i in range(10):
        d.line((60, 120 + i * 30, 780, 120 + i * 30), fill="gray")
    b = io.BytesIO(); img.save(b, "PNG"); b.seek(0)
    from reportlab.lib.utils import ImageReader
    c = canvas.Canvas(str(PDFS / "scanned-page.pdf"), pagesize=letter)
    c.drawImage(ImageReader(b), 0, 0, width=612, height=792)
    c.save()
    print("wrote", len(list(PDFS.glob("*.pdf"))), "pdfs")


if __name__ == "__main__":
    main()

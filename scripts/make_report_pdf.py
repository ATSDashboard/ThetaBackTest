"""Combine 002 + 003 summaries + charts + findings into one PDF."""
from pathlib import Path
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                 PageBreak, Table, TableStyle, KeepTogether)

ROOT = Path(__file__).resolve().parent.parent
OUT  = ROOT / "results" / "backtest_report.pdf"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="H1x", parent=styles["Heading1"], fontSize=18, spaceAfter=10, textColor=colors.HexColor("#0f172a")))
styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontSize=13, spaceAfter=6, textColor=colors.HexColor("#1e293b")))
styles.add(ParagraphStyle(name="H3x", parent=styles["Heading3"], fontSize=11, spaceAfter=4, textColor=colors.HexColor("#334155")))
styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], fontSize=9.5, leading=13))
styles.add(ParagraphStyle(name="Mono", parent=styles["BodyText"], fontName="Courier", fontSize=8, leading=10))
styles.add(ParagraphStyle(name="Warn", parent=styles["BodyText"], fontSize=9.5, leading=13, textColor=colors.HexColor("#b91c1c")))


def md_table_to_flowables(md_text):
    """Convert md tables + paragraphs into reportlab flowables (simple parser)."""
    flows = []
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln:
            flows.append(Spacer(1, 4)); i += 1; continue
        # headings
        if ln.startswith("# "):
            flows.append(Paragraph(ln[2:], styles["H1x"])); i += 1; continue
        if ln.startswith("## "):
            flows.append(Paragraph(ln[3:], styles["H2x"])); i += 1; continue
        if ln.startswith("### "):
            flows.append(Paragraph(ln[4:], styles["H3x"])); i += 1; continue
        # table block
        if ln.startswith("|"):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            rows = [[c.strip() for c in r.strip("|").split("|")] for r in block]
            # drop the separator row (---)
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            if rows:
                tbl = Table(rows, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                    ("FONTNAME",   (0,0), (-1,-1), "Helvetica"),
                    ("FONTSIZE",   (0,0), (-1,-1), 7),
                    ("ALIGN",      (0,0), (-1,-1), "CENTER"),
                    ("GRID",       (0,0), (-1,-1), 0.25, colors.HexColor("#cbd5e1")),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f1f5f9")]),
                    ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
                    ("LEFTPADDING",(0,0), (-1,-1), 3),
                    ("RIGHTPADDING",(0,0), (-1,-1), 3),
                ]))
                flows.append(tbl)
                flows.append(Spacer(1, 6))
            continue
        # bullet
        if ln.lstrip().startswith("- "):
            text = ln.lstrip()[2:]
            flows.append(Paragraph("• " + text, styles["Body"])); i += 1; continue
        # blockquote/warning
        if ln.startswith("> "):
            flows.append(Paragraph(ln[2:], styles["Warn"])); i += 1; continue
        # paragraph
        # simple bold **...**
        text = ln
        text = text.replace("**", "<b>", 1)
        while "**" in text:
            text = text.replace("**", "</b>", 1)
            if "**" in text:
                text = text.replace("**", "<b>", 1)
        flows.append(Paragraph(text, styles["Body"]))
        i += 1
    return flows


def add_image(flows, path, width_cm=17):
    p = Path(path)
    if not p.exists():
        flows.append(Paragraph(f"<i>(missing: {p.name})</i>", styles["Body"]))
        return
    img = Image(str(p))
    w, h = img.drawWidth, img.drawHeight
    scale = (width_cm * cm) / w
    img.drawWidth = w * scale
    img.drawHeight = h * scale
    flows.append(img)
    flows.append(Spacer(1, 6))


def build():
    flows = []
    # COVER
    flows.append(Paragraph("NIFTY Non-Expiry Deep OTM — Backtest Report", styles["H1x"]))
    flows.append(Paragraph(f"Generated: {date.today().isoformat()} · Author: Rohan Shah · Engine: local Parquet / DuckDB", styles["Body"]))
    flows.append(Spacer(1, 8))
    flows.append(Paragraph(
        "This report compiles three analyses run against the project's NIFTY minute-bar store "
        "(2025-04-17 → 2026-04-21, 151 non-expiry days, weekly-expiry legs).", styles["Body"]))
    flows.append(Spacer(1, 6))
    flows.append(Paragraph("<b>Executive takeaway</b>", styles["H3x"]))
    for b in [
        "<b>RECOMMENDED STRATEGY (after analyses 001–006):</b> NIFTY E-1 · sell ~2.5% OTM CE+PE at 10:00 · hold to expiry 15:25 next day · 55 lots/Cr at ~₹100/lot friction → <b>~29% annualized on ₹1 Cr · 100% win rate · 0 breaches of ₹7K/Cr cap in 46-day sample · mean ₹62K/Cr per event.</b>  Backup at 3% OTM same rules = 19% annualized, equally clean breach record.",
        "Friction is the dominant lever. At ₹400/lot/day (placeholder) nothing is net-positive; at ₹100/lot real-world cost the deep distances (2.5–3%) flip to 19–29% annualized.  Confirm Rohan's actual broker round-trip cost before treating any number as final.",
        "Per-lot ₹5K target is unreachable at any distance that simultaneously gives 98% worthless on NIFTY — premium math doesn't support it.  At portfolio scale (55 lots/Cr) the strategy still meets/beats Rohan's profile via volume of small wins.",
        "Selling ~3% OTM NIFTY CE+PE on non-expiry days (001/002) has 64.9% raw win rate but fat left tail — worst day −₹10.8K wiped 3 months.  No SL/PT/skip-filter combination tested in 002 turned net-positive at placeholder friction.",
        "E-1 (DTE=1) and E-0 (expiry day) surveys (004/005): 100% of days expire worthless at ≥2.5% OTM.  E-0 has tighter MAE distribution (only 2.1% breach the ₹1.95-stop at 4-5% OTM) but smaller premium captured per lot.",
        "SENSEX still has only 8 fragmented days of minute data (source folders confirmed).  Cannot backtest anything SENSEX-specific until historical weekly-expiry data is ingested back to ~2024-04.",
    ]:
        flows.append(Paragraph("• " + b, styles["Body"]))
    flows.append(PageBreak())

    # ANALYSIS 001 — quick header
    flows.append(Paragraph("Analysis 001 — Baseline (3% OTM, 09:30–10:30 → 15:00)", styles["H1x"]))
    a001 = (ROOT / "results/001_non_expiry_intraday_deep_otm/summary.md").read_text()
    flows.extend(md_table_to_flowables(a001))
    add_image(flows, ROOT / "results/001_non_expiry_intraday_deep_otm/equity_curve.png")
    flows.append(PageBreak())

    # ANALYSIS 002
    flows.append(Paragraph("Analysis 002 — Rule Sweep (SL / PT / skip-filters / distance / time)", styles["H1x"]))
    a002 = (ROOT / "results/002_non_expiry_rule_sweep/summary.md").read_text()
    flows.extend(md_table_to_flowables(a002))
    add_image(flows, ROOT / "results/002_non_expiry_rule_sweep/equity_curves.png")
    add_image(flows, ROOT / "results/002_non_expiry_rule_sweep/winner_equity.png")
    add_image(flows, ROOT / "results/002_non_expiry_rule_sweep/distance_sweep.png")
    flows.append(PageBreak())

    # ANALYSIS 003
    flows.append(Paragraph("Analysis 003 — 10:00 entry · ₹2 combined target · DTE slabs", styles["H1x"]))
    a003 = (ROOT / "results/003_10am_entry_rs2_target/summary.md").read_text()
    flows.extend(md_table_to_flowables(a003))
    add_image(flows, ROOT / "results/003_10am_entry_rs2_target/equity_curves.png")
    flows.append(PageBreak())

    # ANALYSIS 004
    flows.append(Paragraph("Analysis 004 — E-1 Deep OTM Premium Survey (NIFTY)", styles["H1x"]))
    a004 = (ROOT / "results/004_e_minus_1_premium_survey/summary.md").read_text()
    flows.extend(md_table_to_flowables(a004))
    add_image(flows, ROOT / "results/004_e_minus_1_premium_survey/premium_mae_by_distance.png")
    flows.append(PageBreak())

    # ANALYSIS 005
    flows.append(Paragraph("Analysis 005 — E-0 (Expiry-Day) Deep OTM Premium Survey (NIFTY)", styles["H1x"]))
    a005 = (ROOT / "results/005_e_zero_premium_survey/summary.md").read_text()
    flows.extend(md_table_to_flowables(a005))
    add_image(flows, ROOT / "results/005_e_zero_premium_survey/premium_mae_by_distance.png")
    flows.append(PageBreak())

    # ANALYSIS 006
    flows.append(Paragraph("Analysis 006 — Portfolio-Scale Simulation + Friction Sensitivity", styles["H1x"]))
    a006 = (ROOT / "results/006_portfolio_scale_friction_sensitivity/summary.md").read_text()
    flows.extend(md_table_to_flowables(a006))
    add_image(flows, ROOT / "results/006_portfolio_scale_friction_sensitivity/annualized_return_by_friction.png")
    add_image(flows, ROOT / "results/006_portfolio_scale_friction_sensitivity/distribution_per_event.png")
    flows.append(PageBreak())

    # FINDINGS LOG
    flows.append(Paragraph("Findings Log", styles["H1x"]))
    fl = (ROOT / "FINDINGS_LOG.md").read_text()
    flows.extend(md_table_to_flowables(fl))

    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                             leftMargin=1.8*cm, rightMargin=1.8*cm,
                             topMargin=1.5*cm, bottomMargin=1.5*cm,
                             title="NIFTY Backtest Report")
    doc.build(flows)
    print(f"✓ PDF written: {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    build()

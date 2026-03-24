import base64
from datetime import datetime

def score_color(score: int) -> str:
    if score >= 80:
        return "#22c55e"  # green
    elif score >= 60:
        return "#f59e0b"  # yellow
    elif score >= 40:
        return "#f97316"  # orange
    else:
        return "#ef4444"  # red

def score_label(score: int) -> str:
    if score >= 80:
        return "Strong"
    elif score >= 60:
        return "Moderate"
    elif score >= 40:
        return "Needs Work"
    else:
        return "Critical"

def render_list(items: list) -> str:
    if not items:
        return "<li>None identified</li>"
    return "".join(f"<li>{item}</li>" for item in items)

def render_wow_findings(findings: list) -> str:
    if not findings:
        return ""
    html = ""
    for f in findings:
        headline = f.get("headline", "")
        detail = f.get("detail", "")
        html += f"""
        <div class="wow-item">
            <div class="wow-headline">⚡ {headline}</div>
            <div class="wow-detail">{detail}</div>
        </div>
        """
    return html

def generate_pdf_html(audit_data: dict) -> str:
    report = audit_data.get("report", {})
    brand = report.get("brand_snapshot", {})
    
    business_name = audit_data.get("business_name", "Your Business")
    contact_name = audit_data.get("contact_name", "")
    url_analyzed = audit_data.get("url_analyzed", "")
    date_str = datetime.now().strftime("%B %d, %Y")

    seo_score = report.get("seo_score", 0)
    aeo_score = report.get("aeo_score", 0)
    geo_score = report.get("geo_score", 0)
    overall_score = report.get("overall_score", 0)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: 'Inter', Arial, sans-serif;
    background: #ffffff;
    color: #1a1a2e;
    font-size: 13px;
    line-height: 1.6;
  }}

  /* ── HEADER ── */
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: white;
    padding: 36px 48px 28px;
    position: relative;
  }}
  .header-logo {{
    font-size: 11px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: #00d4ff;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .header-title {{
    font-size: 26px;
    font-weight: 800;
    margin-bottom: 4px;
    color: #ffffff;
  }}
  .header-subtitle {{
    font-size: 13px;
    color: #94a3b8;
  }}
  .header-meta {{
    margin-top: 16px;
    display: flex;
    gap: 32px;
  }}
  .header-meta-item {{
    font-size: 11px;
    color: #cbd5e1;
  }}
  .header-meta-item span {{
    color: #00d4ff;
    font-weight: 600;
  }}

  /* ── OVERALL SCORE BANNER ── */
  .score-banner {{
    background: #f8fafc;
    border-bottom: 1px solid #e2e8f0;
    padding: 24px 48px;
    display: flex;
    align-items: center;
    gap: 48px;
  }}
  .overall-score {{
    text-align: center;
    min-width: 100px;
  }}
  .overall-score-number {{
    font-size: 52px;
    font-weight: 800;
    color: {score_color(overall_score)};
    line-height: 1;
  }}
  .overall-score-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #64748b;
    margin-top: 4px;
  }}
  .score-divider {{
    width: 1px;
    height: 60px;
    background: #e2e8f0;
  }}
  .score-grid {{
    display: flex;
    gap: 32px;
    flex: 1;
  }}
  .score-item {{
    text-align: center;
  }}
  .score-item-number {{
    font-size: 28px;
    font-weight: 700;
    line-height: 1;
  }}
  .score-item-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #64748b;
    margin-top: 2px;
  }}
  .score-item-tag {{
    font-size: 9px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    display: inline-block;
    margin-top: 4px;
    color: white;
  }}

  /* ── CONTENT ── */
  .content {{
    padding: 32px 48px;
  }}

  .section {{
    margin-bottom: 28px;
  }}
  .section-title {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #00d4ff;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }}

  /* ── BRAND SNAPSHOT ── */
  .brand-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }}
  .brand-item {{
    background: #f8fafc;
    border-radius: 8px;
    padding: 14px 16px;
    border-left: 3px solid #00d4ff;
  }}
  .brand-item-label {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #64748b;
    margin-bottom: 4px;
  }}
  .brand-item-value {{
    font-size: 12px;
    color: #1a1a2e;
    line-height: 1.5;
  }}
  .brand-gap {{
    grid-column: 1 / -1;
    background: #fff7ed;
    border-left-color: #f97316;
  }}

  /* ── AUDIT SECTIONS ── */
  .audit-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 28px;
  }}
  .audit-card {{
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    overflow: hidden;
  }}
  .audit-card-header {{
    padding: 12px 16px;
    color: white;
    font-weight: 700;
    font-size: 12px;
  }}
  .audit-card-body {{
    padding: 14px 16px;
  }}
  .audit-card-summary {{
    font-size: 11px;
    color: #475569;
    margin-bottom: 10px;
    line-height: 1.5;
  }}
  .wins-label {{
    font-size: 10px;
    font-weight: 700;
    color: #22c55e;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 4px;
  }}
  .gaps-label {{
    font-size: 10px;
    font-weight: 700;
    color: #ef4444;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 8px;
    margin-bottom: 4px;
  }}
  .audit-card-body ul {{
    padding-left: 14px;
    font-size: 11px;
    color: #334155;
  }}
  .audit-card-body li {{
    margin-bottom: 3px;
  }}

  /* ── WOW FINDINGS ── */
  .wow-item {{
    background: #1a1a2e;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
    border-left: 4px solid #00d4ff;
  }}
  .wow-headline {{
    font-size: 13px;
    font-weight: 700;
    color: #ffffff;
    margin-bottom: 6px;
  }}
  .wow-detail {{
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.5;
  }}

  /* ── PRIORITY + CTA ── */
  .priority-box {{
    background: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 20px;
  }}
  .priority-label {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #f97316;
    margin-bottom: 6px;
  }}
  .priority-text {{
    font-size: 13px;
    color: #1a1a2e;
    line-height: 1.6;
  }}

  .cta-box {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 10px;
    padding: 20px 24px;
    color: white;
  }}
  .cta-label {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #00d4ff;
    margin-bottom: 8px;
  }}
  .cta-text {{
    font-size: 13px;
    color: #e2e8f0;
    line-height: 1.6;
  }}

  /* ── FOOTER ── */
  .footer {{
    margin-top: 32px;
    padding: 16px 48px;
    background: #f8fafc;
    border-top: 1px solid #e2e8f0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10px;
    color: #94a3b8;
  }}
  .footer-brand {{
    font-weight: 700;
    color: #1a1a2e;
  }}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-logo">Immersive Agentics</div>
  <div class="header-title">Website Intelligence Report</div>
  <div class="header-subtitle">AI-Powered SEO · AEO · GEO Audit</div>
  <div class="header-meta">
    <div class="header-meta-item">Business: <span>{business_name}</span></div>
    <div class="header-meta-item">Website: <span>{url_analyzed}</span></div>
    <div class="header-meta-item">Prepared for: <span>{contact_name}</span></div>
    <div class="header-meta-item">Date: <span>{date_str}</span></div>
  </div>
</div>

<!-- SCORE BANNER -->
<div class="score-banner">
  <div class="overall-score">
    <div class="overall-score-number">{overall_score}</div>
    <div class="overall-score-label">Overall Score</div>
  </div>
  <div class="score-divider"></div>
  <div class="score-grid">
    <div class="score-item">
      <div class="score-item-number" style="color:{score_color(seo_score)}">{seo_score}</div>
      <div class="score-item-label">SEO</div>
      <div class="score-item-tag" style="background:{score_color(seo_score)}">{score_label(seo_score)}</div>
    </div>
    <div class="score-item">
      <div class="score-item-number" style="color:{score_color(aeo_score)}">{aeo_score}</div>
      <div class="score-item-label">AEO</div>
      <div class="score-item-tag" style="background:{score_color(aeo_score)}">{score_label(aeo_score)}</div>
    </div>
    <div class="score-item">
      <div class="score-item-number" style="color:{score_color(geo_score)}">{geo_score}</div>
      <div class="score-item-label">GEO</div>
      <div class="score-item-tag" style="background:{score_color(geo_score)}">{score_label(geo_score)}</div>
    </div>
  </div>
</div>

<!-- CONTENT -->
<div class="content">

  <!-- BRAND SNAPSHOT -->
  <div class="section">
    <div class="section-title">Brand Snapshot</div>
    <div class="brand-grid">
      <div class="brand-item">
        <div class="brand-item-label">What Your Site Says</div>
        <div class="brand-item-value">{brand.get('what_they_say', '')}</div>
      </div>
      <div class="brand-item">
        <div class="brand-item-label">What It Actually Communicates</div>
        <div class="brand-item-value">{brand.get('what_it_actually_communicates', '')}</div>
      </div>
      <div class="brand-item brand-gap">
        <div class="brand-item-label">⚠ Brand Gap Identified</div>
        <div class="brand-item-value">{brand.get('brand_gap', '')}</div>
      </div>
    </div>
  </div>

  <!-- AUDIT CARDS -->
  <div class="section">
    <div class="section-title">Detailed Audit Results</div>
    <div class="audit-grid">

      <div class="audit-card">
        <div class="audit-card-header" style="background:{score_color(seo_score)}">
          🔍 SEO Score: {seo_score}/100
        </div>
        <div class="audit-card-body">
          <div class="audit-card-summary">{report.get('seo_summary', '')}</div>
          <div class="wins-label">✓ Wins</div>
          <ul>{render_list(report.get('seo_wins', []))}</ul>
          <div class="gaps-label">✗ Critical Gaps</div>
          <ul>{render_list(report.get('seo_gaps', []))}</ul>
        </div>
      </div>

      <div class="audit-card">
        <div class="audit-card-header" style="background:{score_color(aeo_score)}">
          🤖 AEO Score: {aeo_score}/100
        </div>
        <div class="audit-card-body">
          <div class="audit-card-summary">{report.get('aeo_summary', '')}</div>
          <div class="wins-label">✓ Wins</div>
          <ul>{render_list(report.get('aeo_wins', []))}</ul>
          <div class="gaps-label">✗ Critical Gaps</div>
          <ul>{render_list(report.get('aeo_gaps', []))}</ul>
        </div>
      </div>

      <div class="audit-card">
        <div class="audit-card-header" style="background:{score_color(geo_score)}">
          🌐 GEO Score: {geo_score}/100
        </div>
        <div class="audit-card-body">
          <div class="audit-card-summary">{report.get('geo_summary', '')}</div>
          <div class="wins-label">✓ Wins</div>
          <ul>{render_list(report.get('geo_wins', []))}</ul>
          <div class="gaps-label">✗ Critical Gaps</div>
          <ul>{render_list(report.get('geo_gaps', []))}</ul>
        </div>
      </div>

    </div>
  </div>

  <!-- WOW FINDINGS -->
  <div class="section">
    <div class="section-title">🔥 What You Probably Didn't Know</div>
    {render_wow_findings(report.get('wow_findings', []))}
  </div>

  <!-- TOP PRIORITY -->
  <div class="priority-box">
    <div class="priority-label">🎯 Your #1 Priority Fix</div>
    <div class="priority-text">{report.get('top_priority', '')}</div>
  </div>

  <!-- CTA -->
  <div class="cta-box">
    <div class="cta-label">Recommended Next Step</div>
    <div class="cta-text">{report.get('cta', '')}</div>
  </div>

</div>

<!-- FOOTER -->
<div class="footer">
  <div><span class="footer-brand">Immersive Agentics</span> · AI Marketing Department · immersiveagentics.com</div>
  <div>Confidential · Generated {date_str}</div>
</div>

</body>
</html>
"""

def generate_pdf_base64(audit_data: dict) -> str:
    """Generate PDF from audit data and return as base64 string."""
    try:
        from weasyprint import HTML
        html_content = generate_pdf_html(audit_data)
        pdf_bytes = HTML(string=html_content).write_pdf()
        return base64.b64encode(pdf_bytes).decode("utf-8")
    except Exception as e:
        raise Exception(f"PDF generation failed: {str(e)}")

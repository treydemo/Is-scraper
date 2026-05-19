import base64
from datetime import datetime


def _score_color(s) -> str:
    if s is None: return "#94a3b8"
    s = int(s)
    if s >= 90: return "#00C2A0"
    if s >= 80: return "#34d399"
    if s >= 70: return "#F5A623"
    if s >= 60: return "#f97316"
    return "#ef4444"


def _score_label(s) -> str:
    if s is None: return "N/A"
    s = int(s)
    if s >= 90: return "Excellent"
    if s >= 80: return "Good"
    if s >= 70: return "Fair"
    if s >= 60: return "Poor"
    return "Critical"


def _li(items: list) -> str:
    if not items: return "<li style='color:#94a3b8'>None identified</li>"
    return "".join(f"<li>{item}</li>" for item in items)


def _schema_checklist(schema_audit: dict) -> str:
    present = set(schema_audit.get("schema_present", []))
    missing = schema_audit.get("schema_missing", [])
    rows = ""
    for t in present:
        rows += f"<div class='schema-row'><span class='schema-tick tick-yes'>✓</span> {t}</div>"
    for t in missing:
        rows += f"<div class='schema-row'><span class='schema-tick tick-no'>✗</span> {t}</div>"
    return rows


def _competitor_rows(competitors: list) -> str:
    if not competitors:
        return "<tr><td colspan='5' style='color:#94a3b8;text-align:center'>No competitor data retrieved</td></tr>"
    rows = ""
    for c in competitors:
        rating = c.get("competitor_gbp_rating") or "—"
        reviews = c.get("competitor_gbp_reviews") or "—"
        lsa = "Yes" if c.get("competitor_has_lsa") else "No"
        lsa_color = "#ef4444" if c.get("competitor_has_lsa") else "#94a3b8"
        rows += f"""<tr>
          <td>{c.get('competitor_name','—')}</td>
          <td>{rating}</td>
          <td>{reviews}</td>
          <td style='color:{lsa_color};font-weight:600'>{lsa}</td>
          <td>{c.get('competitor_local_pack_position') or '—'}</td>
        </tr>"""
    return rows


def generate_pdf_html(data: dict) -> str:
    report = data.get("report", {})
    bi = data.get("brand_intelligence", {})
    gbp = data.get("gbp", {})
    lsa = data.get("lsa", {})
    yt = data.get("youtube", {})
    soc = data.get("social", {})
    ps = data.get("pagespeed", {})
    schema = data.get("schema_audit", {})
    freshness = data.get("content_freshness", {})
    ai_cite = data.get("ai_citation", {})
    competitors = data.get("competitors", [])
    cs = report.get("channel_scores", {})

    biz = data.get("business_name", "Your Business")
    contact = data.get("contact_name", "")
    url = data.get("url_analyzed", "")
    location = data.get("location", "")
    date_str = datetime.now().strftime("%B %d, %Y")

    seo = data.get("seo_trust_score", 0)
    ai_vis = data.get("ai_visibility_score", 0)
    primary = bi.get("brand_colors", {}).get("primary", "#1a1a2e") if isinstance(bi.get("brand_colors"), dict) else "#1a1a2e"

    cite_status = ai_cite.get("ai_citation_status", "error")
    cite_color = {"strong": "#00C2A0", "weak": "#F5A623", "invisible": "#ef4444", "error": "#94a3b8"}.get(cite_status, "#94a3b8")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:Arial,sans-serif; background:#fff; color:#1a1a2e; font-size:12px; line-height:1.6; }}

  .header {{ background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%); color:#fff; padding:32px 44px 24px; }}
  .ia-tag {{ font-size:10px; letter-spacing:3px; text-transform:uppercase; color:#00d4ff; margin-bottom:6px; font-weight:700; }}
  .report-title {{ font-size:24px; font-weight:800; margin-bottom:3px; }}
  .report-sub {{ font-size:12px; color:#94a3b8; margin-bottom:14px; }}
  .meta-row {{ display:flex; gap:28px; font-size:11px; color:#cbd5e1; flex-wrap:wrap; }}
  .meta-row span {{ color:#00d4ff; font-weight:600; }}

  .score-banner {{ background:#f8fafc; border-bottom:1px solid #e2e8f0; padding:20px 44px; display:flex; align-items:center; gap:40px; }}
  .score-box {{ text-align:center; min-width:90px; }}
  .score-num {{ font-size:48px; font-weight:800; line-height:1; }}
  .score-lbl {{ font-size:10px; text-transform:uppercase; letter-spacing:2px; color:#64748b; margin-top:3px; }}
  .score-grade {{ font-size:14px; font-weight:700; margin-top:2px; }}
  .divider {{ width:1px; height:60px; background:#e2e8f0; }}
  .score-pair {{ display:flex; gap:32px; flex:1; }}

  .body {{ padding:28px 44px; }}
  .section {{ margin-bottom:24px; }}
  .sec-title {{ font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:2px; color:#00d4ff; padding-bottom:7px; border-bottom:2px solid #e2e8f0; margin-bottom:12px; }}

  .brand-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .brand-card {{ background:#f8fafc; border-left:3px solid #00d4ff; border-radius:7px; padding:12px 14px; }}
  .brand-card.gap {{ background:#fff7ed; border-left-color:#f97316; grid-column:1/-1; }}
  .bc-label {{ font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:1px; color:#64748b; margin-bottom:3px; }}
  .bc-value {{ font-size:11px; color:#1a1a2e; line-height:1.5; }}

  .ai-cite-box {{ background:#1a1a2e; border-left:4px solid {cite_color}; border-radius:8px; padding:14px 18px; margin-bottom:14px; }}
  .ac-status {{ font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:1px; color:{cite_color}; margin-bottom:6px; }}
  .ac-quote {{ font-size:11px; color:#cbd5e1; line-height:1.6; font-style:italic; }}
  .ac-summary {{ font-size:11px; color:#94a3b8; margin-top:8px; }}

  .channels {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:20px; }}
  .ch-card {{ border:1px solid #e2e8f0; border-radius:9px; overflow:hidden; }}
  .ch-head {{ padding:10px 14px; color:#fff; font-weight:700; font-size:11px; display:flex; justify-content:space-between; align-items:center; }}
  .ch-body {{ padding:12px 14px; }}
  .win-lbl {{ font-size:9px; font-weight:700; color:#22c55e; text-transform:uppercase; letter-spacing:1px; margin-bottom:3px; }}
  .gap-lbl {{ font-size:9px; font-weight:700; color:#ef4444; text-transform:uppercase; letter-spacing:1px; margin-top:7px; margin-bottom:3px; }}
  .rec-lbl {{ font-size:9px; font-weight:700; color:#00d4ff; text-transform:uppercase; letter-spacing:1px; margin-top:7px; margin-bottom:3px; }}
  .ch-body ul {{ padding-left:12px; font-size:10px; color:#334155; }}
  .ch-body li {{ margin-bottom:2px; }}

  .schema-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:4px; }}
  .schema-row {{ font-size:10px; color:#334155; display:flex; align-items:center; gap:5px; }}
  .schema-tick {{ font-size:11px; font-weight:700; }}
  .tick-yes {{ color:#22c55e; }}
  .tick-no {{ color:#ef4444; }}

  .stats-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
  .stat-card {{ background:#f8fafc; border-radius:7px; padding:10px 12px; text-align:center; }}
  .stat-num {{ font-size:20px; font-weight:700; color:#1a1a2e; }}
  .stat-lbl {{ font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#64748b; }}

  table {{ width:100%; border-collapse:collapse; font-size:10px; }}
  th {{ background:#f8fafc; color:#64748b; font-size:9px; text-transform:uppercase; letter-spacing:1px; padding:7px 10px; border-bottom:2px solid #e2e8f0; text-align:left; }}
  td {{ padding:7px 10px; border-bottom:1px solid #f1f5f9; color:#334155; }}

  .priority-box {{ background:#fff7ed; border:1px solid #fed7aa; border-radius:9px; padding:14px 18px; margin-bottom:16px; }}
  .priority-lbl {{ font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:1px; color:#f97316; margin-bottom:5px; }}
  .priority-text {{ font-size:12px; color:#1a1a2e; line-height:1.6; }}

  .pitch-box {{ background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%); border-radius:9px; padding:18px 22px; color:#fff; }}
  .pitch-lbl {{ font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:2px; color:#00d4ff; margin-bottom:7px; }}
  .pitch-text {{ font-size:12px; color:#e2e8f0; line-height:1.7; }}

  .footer {{ margin-top:28px; padding:14px 44px; background:#f8fafc; border-top:1px solid #e2e8f0; display:flex; justify-content:space-between; align-items:center; font-size:9px; color:#94a3b8; }}
  .footer strong {{ color:#1a1a2e; }}
</style>
</head>
<body>

<div class="header">
  <div class="ia-tag">Immersive Agentics</div>
  <div class="report-title">Authority &amp; Visibility Audit</div>
  <div class="report-sub">AI-Powered 5-Channel Local Business Intelligence Report v4.0</div>
  <div class="meta-row">
    <div>Business: <span>{biz}</span></div>
    <div>URL: <span>{url}</span></div>
    {"<div>Contact: <span>" + contact + "</span></div>" if contact else ""}
    {"<div>Location: <span>" + location + "</span></div>" if location else ""}
    <div>Date: <span>{date_str}</span></div>
  </div>
</div>

<div class="score-banner">
  <div class="score-box">
    <div class="score-num" style="color:{_score_color(seo)}">{seo}</div>
    <div class="score-lbl">SEO Trust Score</div>
    <div class="score-grade" style="color:{_score_color(seo)}">{data.get('seo_trust_grade','F')} — {_score_label(seo)}</div>
  </div>
  <div class="divider"></div>
  <div class="score-box">
    <div class="score-num" style="color:{_score_color(ai_vis)}">{ai_vis}</div>
    <div class="score-lbl">AI Visibility Score</div>
    <div class="score-grade" style="color:{_score_color(ai_vis)}">{data.get('ai_visibility_grade','F')} — {_score_label(ai_vis)}</div>
  </div>
</div>

<div class="body">

  <!-- EXECUTIVE SUMMARY -->
  <div class="section">
    <div class="sec-title">Executive Summary</div>
    <p style="font-size:12px;color:#334155;line-height:1.7">{report.get('executive_summary','')}</p>
  </div>

  <!-- BRAND INTELLIGENCE -->
  <div class="section">
    <div class="sec-title">Brand Intelligence</div>
    <div class="brand-grid">
      <div class="brand-card">
        <div class="bc-label">What They Say</div>
        <div class="bc-value">{bi.get('what_they_say','')}</div>
      </div>
      <div class="brand-card">
        <div class="bc-label">Voice &amp; Tone</div>
        <div class="bc-value">{bi.get('voice_tone','').title()}</div>
      </div>
      <div class="brand-card">
        <div class="bc-label">Color Assessment</div>
        <div class="bc-value">{bi.get('brand_color_assessment','')}</div>
      </div>
      <div class="brand-card gap">
        <div class="bc-label">⚠ Brand Gap Identified</div>
        <div class="bc-value">{bi.get('brand_gap','')}</div>
      </div>
    </div>
  </div>

  <!-- AI CITATION -->
  <div class="section">
    <div class="sec-title">AI Citation Check — Claude knows your business?</div>
    <div class="ai-cite-box">
      <div class="ac-status">Status: {cite_status.upper()}</div>
      <div class="ac-quote">"{ai_cite.get('ai_citation_result','')}"</div>
      <div class="ac-summary">{ai_cite.get('ai_citation_summary','')}</div>
    </div>
  </div>

  <!-- CHANNEL SCORES -->
  <div class="section">
    <div class="sec-title">5-Channel Audit Results</div>
    <div class="channels">

      {"".join([f'''<div class="ch-card">
        <div class="ch-head" style="background:{_score_color(cs.get(ch,{{}}).get('score',0))}">
          <span>{ch.upper()}</span><span>{cs.get(ch,{{}}).get('score',0)}/100</span>
        </div>
        <div class="ch-body">
          <div class="win-lbl">✓ Wins</div>
          <ul>{_li(cs.get(ch,{{}}).get('wins',[]))}</ul>
          <div class="gap-lbl">✗ Issues</div>
          <ul>{_li(cs.get(ch,{{}}).get('issues',[]))}</ul>
          <div class="rec-lbl">→ Recommendations</div>
          <ul>{_li(cs.get(ch,{{}}).get('recommendations',[]))}</ul>
        </div>
      </div>''' for ch in ["website","gbp","lsa","social","youtube"]])}

    </div>
  </div>

  <!-- PAGESPEED + GBP STATS -->
  <div class="section">
    <div class="sec-title">Key Metrics</div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-num" style="color:{_score_color(ps.get('mobile_score'))}">{ps.get('mobile_score') or '—'}</div>
        <div class="stat-lbl">Mobile PageSpeed</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{gbp.get('gbp_rating') or '—'}</div>
        <div class="stat-lbl">GBP Rating</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{gbp.get('gbp_review_count') or '—'}</div>
        <div class="stat-lbl">GBP Reviews</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{soc.get('total_social_reach') or '0'}</div>
        <div class="stat-lbl">Social Reach</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{ps.get('lcp') or '—'}</div>
        <div class="stat-lbl">LCP</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{ps.get('fcp') or '—'}</div>
        <div class="stat-lbl">FCP</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{yt.get('yt_subscriber_count') or '—'}</div>
        <div class="stat-lbl">YT Subscribers</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{freshness.get('freshness_status','—').title()}</div>
        <div class="stat-lbl">Content Freshness</div>
      </div>
    </div>
  </div>

  <!-- SCHEMA CHECKLIST -->
  <div class="section">
    <div class="sec-title">Schema Markup Checklist — Score: {schema.get('schema_score',0)}/100</div>
    <div class="schema-grid">
      {_schema_checklist(schema)}
    </div>
    <p style="margin-top:10px;font-size:11px;color:#f97316">
      <strong>Priority fix:</strong> {schema.get('schema_priority_fix','')}
    </p>
  </div>

  <!-- TOP GAPS -->
  <div class="section">
    <div class="sec-title">Top SEO Gaps</div>
    <ul style="padding-left:16px;font-size:11px;color:#334155">{_li(report.get('top_seo_gaps',[]))}</ul>
  </div>
  <div class="section">
    <div class="sec-title">Top Visibility Gaps</div>
    <ul style="padding-left:16px;font-size:11px;color:#334155">{_li(report.get('top_visibility_gaps',[]))}</ul>
  </div>

  <!-- QUICK WINS -->
  <div class="section">
    <div class="sec-title">Quick Wins — This Week</div>
    <ul style="padding-left:16px;font-size:11px;color:#334155">{_li(report.get('quick_wins',[]))}</ul>
  </div>

  <!-- NEXT STEPS -->
  <div class="section">
    <div class="sec-title">Recommended Next Steps — 30 Days</div>
    <ul style="padding-left:16px;font-size:11px;color:#334155">{_li(report.get('recommended_next_steps',[]))}</ul>
  </div>

  <!-- COMPETITORS -->
  <div class="section">
    <div class="sec-title">Competitor Landscape</div>
    <table>
      <thead><tr>
        <th>Competitor</th><th>GBP Rating</th><th>Reviews</th>
        <th>Has LSA</th><th>Local Pack Position</th>
      </tr></thead>
      <tbody>{_competitor_rows(competitors)}</tbody>
    </table>
  </div>

  <!-- IA PITCH -->
  <div class="pitch-box">
    <div class="pitch-lbl">Immersive Agentics Recommendation</div>
    <div class="pitch-text">{report.get('ia_pitch','')}</div>
  </div>

</div>

<div class="footer">
  <div><strong>Immersive Agentics</strong> · AI Marketing · immersiveagentics.com</div>
  <div>Confidential · Generated {date_str}</div>
</div>

</body>
</html>"""


def generate_pdf_base64(audit_data: dict) -> str:
    try:
        from weasyprint import HTML
        html = generate_pdf_html(audit_data)
        pdf_bytes = HTML(string=html).write_pdf()
        return base64.b64encode(pdf_bytes).decode("utf-8")
    except Exception as e:
        raise Exception(f"PDF generation failed: {str(e)}")

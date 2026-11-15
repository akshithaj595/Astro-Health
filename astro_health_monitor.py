"""ASTRO HEALTH MONITOR - astronaut health monitoring & decision support.
Install: pip install PySide6 plotly reportlab pypdf      Run: python astro_health_monitor.py
Includes CycleCare (menstrual health module, female astronauts only).
Decision-support only. Not a diagnosis; always consult the ground flight surgeon."""
import sys, os, re, json, hmac, hashlib, sqlite3, tempfile, math, statistics, functools, datetime as dt
from xml.sax.saxutils import escape as xesc
from PySide6.QtCore import Qt, QUrl, QDate
from PySide6.QtGui import QDesktopServices, QColor, QTextCharFormat, QBrush
from PySide6.QtWidgets import *
from PySide6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()

# ---------------------------------------------------------------- database
con = sqlite3.connect(os.path.join(HERE, "astro_health.db"))
con.row_factory = sqlite3.Row
con.executescript("""
CREATE TABLE IF NOT EXISTS astronauts(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS baseline(aid INT, key TEXT, value REAL, ts TEXT, PRIMARY KEY(aid,key));
CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY, aid INT, ts TEXT, day TEXT, key TEXT, value REAL);
CREATE TABLE IF NOT EXISTS mental(id INTEGER PRIMARY KEY, aid INT, ts TEXT, day TEXT, answers TEXT, note TEXT, idx REAL);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, aid INT, ts TEXT, path TEXT);""")
for _c in ("code", "gender", "pw", "salt"):
    if _c not in {r[1] for r in con.execute("PRAGMA table_info(astronauts)")}: con.execute(f"ALTER TABLE astronauts ADD COLUMN {_c} TEXT")
con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_code ON astronauts(code COLLATE NOCASE)"); con.commit()
hpw = lambda pw, salt: hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
now = lambda: dt.datetime.now().isoformat(sep=" ", timespec="seconds")
today = lambda: dt.date.today().isoformat()

SEC = {1: "Holter ECG Monitor & Bio-Monitor Smart Shirt", 2: "Portable Clinical Blood Analyzer (PCBA / i-STAT)",
       3: "Ultrasound 2 / Butterfly iQ+ & Optical Coherence Tomography", 4: "GASMAP / Portable Pulmonary Function System",
       5: "Actiwatch Spectrum / Crew Wearable", 6: "Radiation Dosimeters (CPAD / HERA)",
       7: "Anthropometric Measurements (SLAMMD body mass, height, age)", 8: "Mental Well-being"}
# key, label, unit, section, NASA catalog alert rules (hi/lo/le absolute, rise/drop % vs baseline, persist days)
P = [
 ("hr", "Heart Rate (resting)", "bpm", 1, {"lo": 40, "hi": 150}),
 ("rmssd", "HRV RMSSD", "ms", 1, {}), ("sdnn", "HRV SDNN", "ms", 1, {}),
 ("qtc", "QTc Interval", "ms", 1, {"hi": 470}),
 ("rr", "Respiratory Rate", "breaths/min", 1, {"lo": 8, "hi": 25}),
 ("spo2", "Blood Oxygen Saturation (SpO2)", "%", 1, {"lo": 90}),
 ("na", "Sodium (Na+)", "mmol/L", 2, {"lo": 135, "hi": 145}), ("k", "Potassium (K+)", "mmol/L", 2, {"lo": 3.5, "hi": 5.5}),
 ("ica", "Ionized Calcium (iCa2+)", "mmol/L", 2, {"hi": 1.35}), ("glu", "Glucose", "mg/dL", 2, {"lo": 70, "hi": 180}),
 ("ph", "Blood pH", "", 2, {"lo": 7.30, "hi": 7.50}), ("po2", "pO2", "mmHg", 2, {}), ("pco2", "pCO2", "mmHg", 2, {}),
 ("hct", "Hematocrit (% PCV)", "%", 2, {}), ("hgb", "Hemoglobin (calculated)", "g/dL", 2, {"lo": 11.5}),
 ("bun", "Blood Urea Nitrogen", "mg/dL", 2, {}), ("creat", "Creatinine", "mg/dL", 2, {}),
 ("onsd", "Optic Nerve Sheath Diameter", "mm", 3, {"ge": 5.8}), ("rnfl", "RNFL Thickness", "um", 3, {"rise": 10}),
 ("ijv", "IJV Cross-sectional Area", "cm2", 3, {}), ("ijvv", "IJV Peak Flow Velocity", "cm/s", 3, {"le": 0}),
 ("vo2", "VO2 max", "mL/kg/min", 4, {"drop": 15}), ("rer", "Respiratory Exchange Ratio", "", 4, {}),
 ("ve", "Minute Ventilation", "L/min", 4, {}), ("petco2", "End-tidal CO2 (PetCO2)", "mmHg", 4, {"hi": 45}),
 ("tst", "Total Sleep Time", "hrs", 5, {"lo": 6.0, "persist": 3}), ("se", "Sleep Efficiency", "%", 5, {"lo": 75, "persist": 3}),
 ("waso", "Wake After Sleep Onset", "min", 5, {}), ("phase", "Circadian Phase Shift", "hrs", 5, {}),
 ("align", "Biological Clock Alignment", "%", 5, {}), ("blue", "Blue-light Irradiance", "uW/cm2", 5, {}),
 ("dose", "Radiation Dose Rate", "mSv/hr", 6, {"hi": 0.5}), ("bfo", "30-day BFO Dose", "mGy-Eq", 6, {"hi": 250}),
 ("mass", "Body Weight / Mass (SLAMMD)", "kg", 7, {"drop": 5}),
 ("height", "Height", "cm", 7, {}), ("age", "Age", "years", 7, {}), ("waist", "Waist Circumference", "cm", 7, {}),
 ("bodyfat", "Body Fat", "%", 7, {}), ("bmd", "Bone Mineral Density (T-score)", "SD", 7, {"lo": -2.5}),
 ("mind", "Mental Well-being Index", "%", 8, {"lo": 40}),
]
PD = {p[0]: p for p in P}
PHYS = [p for p in P if p[3] < 8]

# key: (condition, treatment, immediate action) - from catalog alert rules / NASA HRR risks
COND = {
 "hr": ("Cardiac rhythm risk (bradycardia / tachycardia)", "Rest, hydrate, review electrolytes (K+, Ca2+), ECG review by flight surgeon.", "Stop exercise, restrain safely, repeat measurement after 5 min, call ground flight surgeon."),
 "qtc": ("QT-interval prolongation (arrhythmia risk)", "Check K+/Ca2+, review medications, continuous Holter monitoring.", "Stop strenuous activity, keep Holter on, notify flight surgeon immediately."),
 "k": ("Potassium imbalance", "Repeat PCBA test; dietary potassium adjustment per flight surgeon.", "Repeat test, record ECG, contact flight surgeon (hyperkalemia >5.5 is urgent)."),
 "ica": ("Elevated ionized calcium: bone resorption / renal-stone risk", "Increase fluids, resistive exercise (ARED), review vitamin D/calcium intake.", "Hydrate now, repeat test, report to ground medicine."),
 "hgb": ("Spaceflight anemia", "Review iron/nutrition, repeat hematology, ground review.", "Limit exertion/EVA, repeat test, notify flight surgeon."),
 "onsd": ("Spaceflight-Associated Neuro-ocular Syndrome (SANS)", "Ground ophthalmology review, OCT follow-up, fluid-shift countermeasures.", "Report visual changes/headache, repeat ONSD ultrasound with ground guidance."),
 "rnfl": ("SANS - retinal nerve fiber layer thickening", "OCT trend review by ground ophthalmology.", "Report to flight surgeon, repeat OCT."),
 "ijvv": ("Stagnant/retrograde jugular flow: venous thrombosis (VTE) risk", "Doppler re-scan, ground vascular review; anticoagulation only per flight surgeon.", "Repeat Doppler, avoid neck compression, contact flight surgeon urgently."),
 "vo2": ("Aerobic deconditioning", "Increase CEVIS/treadmill aerobic sessions, interval training.", "Re-test, review exercise countermeasure log with ground."),
 "petco2": ("Hypercapnia (CO2 exposure)", "Check cabin CO2 scrubbers, ventilation.", "Move to well-ventilated module, check cabin CO2, notify ground."),
 "tst": ("Sleep loss / circadian desynchronization", "Sleep hygiene, light schedule (SSLA), reduced evening blue light.", "Fatigue countermeasure: protected sleep period, report to flight surgeon."),
 "se": ("Poor sleep efficiency", "Consistent sleep schedule, limit evening blue light.", "Fatigue countermeasure prompt; avoid critical tasks when fatigued."),
 "dose": ("Elevated radiation exposure (SPE risk)", "Cumulative-dose review by SRAG.", "SOLAR STORM SHELTER ALERT: move to radiation shelter, mount active dosimeter on bulkhead."),
 "bfo": ("Blood-forming-organ dose near 30-day limit (250 mGy-Eq)", "Limit EVA, SRAG dose planning.", "Move to shielded area, notify SRAG/flight surgeon."),
 "spo2": ("Low blood oxygen saturation (hypoxemia)", "Check cabin O2/CO2 and ventilation, repeat PPG measurement.", "Repeat measurement, check cabin atmosphere, notify flight surgeon."),
 "bmd": ("Low bone mineral density (microgravity bone loss)", "Resistive exercise (ARED), calcium/vitamin D review, ground bone-health review.", "Report to flight surgeon; review exercise countermeasure compliance."),
 "mass": ("Nutritional deficit / muscle catabolism", "Increase caloric intake, protein, resistive exercise.", "Re-measure with SLAMMD, review nutrition with ground dietitian."),
 "mind": ("Reduced psychological well-being", "Supportive conversation, rest, BHP resources, structured routine.", "Reach out to the flight psychologist / crew surgeon on a private conference."),
}
LIFE = {"diet": "Balanced calories and protein; 2-3 L fluids/day; adequate vitamin D, calcium and omega-3.",
        "exercise": "~2 h/day countermeasures: aerobic (CEVIS/treadmill) + resistive (ARED).",
        "sleep": "8 h sleep opportunity; fixed lights-out; dim blue light 2 h before sleep."}
GUIDE = {
 1: ["Wipe sternum/ribcage with alcohol swab (or saline on Astroskin pads).", "Don vest/leads; snug elastic straps so sensors stay on skin in zero-G.",
     "Clip transmitter puck, confirm green impedance, press Event Marker before exercise/sleep.", "After 24-48 h dock the module in the HRF drawer to offload .edf files."],
 2: ["Warm cartridge 5 min, scan barcode.", "Lance fingertip side; fill heparinized capillary (65 uL) at 45 deg using surface tension.",
     "Load cartridge without bubbles, close port, insert in analyzer.", "Dispose sharps in yellow container, Velcro analyzer 120 s, sync results."],
 3: ["Restrain subject and operator in CMRS.", "Close eye; place bubble-free sterile gel dome/standoff pad.", "Rest probe lightly (no eyeball pressure) following AMOS/ground cues; freeze optic-nerve frame.", "Wipe gel with dry towels; seal in wet-trash bag."],
 4: ["Power GASMAP 30 min early; run 2-point gas + 3 L syringe calibration.", "Fit filter + mouthpiece on RVU, clamp nose clip.", "Clip into CEVIS pedals, hold handles, pedal at prompted cadence, breathe only via mouthpiece.", "Cool down, sanitize mouthpiece, run dry-air purge."],
 5: ["Strap on non-dominant wrist, snug.", "Keep optical face uncovered (roll sleeve behind bezel).", "Press Event Marker at lights-out and on waking.", "Weekly: place face-down on HRF optical reader for 5 min."],
 6: ["Insert badge into left chest/thigh Velcro pocket after waking.", "At night clip badge to sleep-restraint tab at chest level.", "On SPE alarm mount active dosimeter on shelter bulkhead bracket.", "Dock CPAD in charging/data cradle every 7 days."],
 7: ["Pull SLAMMD strut, lock mono-pod, run empty-carriage tare.", "Fetal posture on carriage, grip headrest handles.", "Hold breath, joints rigid, trigger 3 automated pulls.", "Confirm 3 results agree within +/-0.2 kg, save log, retract."],
}
NEG = ["Not at all", "A little (mild)", "Moderate", "Quite a lot (marked)", "Severe"]
EN = ["Excellent", "Good", "Fair", "Low", "Very low"]
MOOD = ["Very positive", "Positive", "Neutral", "Low", "Very low"]
COMF = ["Very comfortable", "Comfortable", "Somewhat comfortable", "Uncomfortable", "Very uncomfortable"]
Q = [("Are you experiencing unusual fatigue?", NEG), ("Are you experiencing dizziness?", NEG),
     ("Are you experiencing breathing difficulties?", NEG), ("Are you experiencing muscle weakness?", NEG),
     ("Are you experiencing joint or bone discomfort?", NEG), ("Have you experienced any changes in your appetite?", NEG),
     ("Are you experiencing sleep disturbances?", NEG), ("How would you rate your current physical energy?", EN),
     ("How would you describe your current mood?", MOOD), ("Are you experiencing unusual stress?", NEG),
     ("Are you experiencing anxiety?", NEG), ("Are you finding it difficult to concentrate?", NEG),
     ("Are you experiencing mental fatigue?", NEG), ("Do you feel emotionally comfortable?", COMF),
     ("How would you rate your current mental well-being?", EN)]

# ---------------------------------------------------------------- analysis
def series(aid, key):
    d = {}
    for r in con.execute("SELECT day,value FROM readings WHERE aid=? AND key=? ORDER BY ts", (aid, key)):
        d[r["day"]] = r["value"]
    return sorted(d.items())

def base_of(aid, key):
    r = con.execute("SELECT value FROM baseline WHERE aid=? AND key=?", (aid, key)).fetchone()
    return r["value"] if r else None

def viol(r, v, b):
    return (("hi" in r and v > r["hi"]) or ("lo" in r and v < r["lo"]) or ("le" in r and v <= r["le"]) or ("ge" in r and v >= r["ge"])
            or (b is not None and (("rise" in r and v > b * (1 + r["rise"] / 100)) or ("drop" in r and v < b * (1 - r["drop"] / 100)))))

def classify(p, s, b):
    if not s: return "No data", ""
    if p[0] == "age": return "Info", "Demographic reference (used for energy and heart-rate targets)"
    v, r = s[-1][1], p[4]
    bad = viol(r, v, b)
    if r.get("persist"):
        bad = len(s) >= r["persist"] and all(viol(r, x, b) for _, x in s[-r["persist"]:])
    if bad: return "Abnormal", "Exceeded medical safety threshold - requires attention"
    if b is None: return "Normal", "No baseline set (add baseline for trend comparison)"
    tol = max(abs(b) * 0.10, 1e-9)
    out = lambda x: 1 if x > b + tol else (-1 if x < b - tol else 0)
    if len(s) >= 2 and out(s[-2][1]) != 0 and abs(v - b) < abs(s[-2][1] - b):
        return "Improving", "Gradually returning to personal baseline"
    side, n = out(v), 0
    if side:
        for _, x in reversed(s):
            if out(x) == side: n += 1
            else: break
        if n >= 3: return ("Above Baseline" if side > 0 else "Below Baseline"), f"{'Above' if side > 0 else 'Below'} baseline for {n} consecutive days"
    return "Normal", "Within established baseline range"

SCORE = {"Normal": 100, "Improving": 85, "Above Baseline": 60, "Below Baseline": 60, "Abnormal": 20}
COL = {"Info": "#7f8c8d", "Normal": "#2ecc71", "Above Baseline": "#f39c12", "Below Baseline": "#3498db", "Abnormal": "#e74c3c", "Improving": "#1abc9c", "No data": "#7f8c8d"}

def lastval(aid, key):
    x = series(aid, key); return x[-1][1] if x else base_of(aid, key)

def anthro(aid):
    r = con.execute("SELECT gender FROM astronauts WHERE id=?", (aid,)).fetchone()
    d = dict(gender=r["gender"] if r else None, age=lastval(aid, "age"), h=lastval(aid, "height"), w=lastval(aid, "mass"), bmi=None, cat="", kcal=None, protein=None, fluid=None, hrmax=None, zone=None, wchg=None)
    if d["h"] and d["w"]:
        d["bmi"] = d["w"] / (d["h"] / 100) ** 2; b = d["bmi"]
        d["cat"] = "Underweight" if b < 18.5 else "Normal" if b < 25 else "Overweight" if b < 30 else "Obese range"
    if d["age"]: d["hrmax"] = 208 - 0.7 * d["age"]; d["zone"] = (0.6 * d["hrmax"], 0.85 * d["hrmax"])
    if d["age"] and d["h"] and d["w"]:
        bmr = 10 * d["w"] + 6.25 * d["h"] - 5 * d["age"] + {"Male": 5, "Female": -161}.get(d["gender"], -78)
        d["kcal"] = bmr * 1.6; d["protein"] = 0.8 * d["w"]; d["fluid"] = max(2000, d["kcal"])
    b = base_of(aid, "mass")
    if b and d["w"]: d["wchg"] = (d["w"] - b) / b * 100
    return d

def anthro_rows(a):
    g = lambda x, u="", f="g": "-" if x is None else format(x, f) + u
    return [("Gender", a["gender"] or "-"), ("Age", g(a["age"], " years")), ("Height", g(a["h"], " cm")),
            ("Body weight / mass", g(a["w"], " kg") + ("" if a["wchg"] is None else f" ({a['wchg']:+.1f}% vs baseline)")),
            ("BMI", "-" if a["bmi"] is None else f"{a['bmi']:.1f} ({a['cat']})"),
            ("Est. daily energy need", g(a["kcal"], " kcal", ".0f")), ("Protein (min 0.8 g/kg) / fluid", "-" if a["kcal"] is None else f"{a['protein']:.0f} g / {a['fluid'] / 1000:.1f} L"),
            ("Est. max HR / aerobic zone (60-85%)", "-" if not a["hrmax"] else f"{a['hrmax']:.0f} bpm / {a['zone'][0]:.0f}-{a['zone'][1]:.0f} bpm")]

def anthro_html(a):
    note = "" if a["kcal"] else "<p style='color:#f39c12'>Enter age, height and body weight (Anthropometric section) for personalised energy, protein, fluid and heart-rate targets.</p>"
    return "<h2>Anthropometric profile</h2><table>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in anthro_rows(a)) + "</table>" + note

def analyse(aid):
    rows = []
    for p in P:
        s = series(aid, p[0])
        if not s: continue
        b = base_of(aid, p[0])
        st, msg = classify(p, s, b)
        rows.append(dict(key=p[0], label=p[1], unit=p[2], val=s[-1][1], base=b, status=st, msg=msg, day=s[-1][0]))
    ph = [r for r in rows if PD[r["key"]][3] < 8 and r["status"] != "Info"]
    pi = sum(SCORE[r["status"]] for r in ph) / len(ph) if ph else None
    mi = next((r["val"] for r in rows if r["key"] == "mind"), None)
    vals = [x for x in (pi, mi) if x is not None]
    overall = sum(vals) / len(vals) if vals else None
    abn = [r for r in rows if r["status"] == "Abnormal"]
    sev = "NO DATA" if overall is None else ("SEVERE" if len(abn) >= 2 or overall < 50 else ("ALERT" if abn else "STABLE"))
    warn, act = [], []
    for r in rows:
        if r["status"] in ("Abnormal", "Above Baseline", "Below Baseline"):
            c = COND.get(r["key"], ("Out-of-range value", "Repeat measurement, consult ground.", "Repeat measurement and consult flight surgeon."))
            warn.append(f"{r['label']} = {r['val']:g} {r['unit']} [{r['status']}] - {c[0]}")
            if r["status"] == "Abnormal": act.append(f"{r['label']}: {c[2]}  Treatment: {c[1]}")
    m = con.execute("SELECT answers FROM mental WHERE aid=? ORDER BY ts DESC LIMIT 1", (aid,)).fetchone()
    if m:
        for i, a in enumerate(json.loads(m["answers"])):
            if a >= 3: warn.append(f"Reported: {Q[i][0]} - {Q[i][1][a]}")
    if sev == "SEVERE": act.insert(0, "SEVERE HEALTH INDEX: stop non-essential tasks, notify ground flight surgeon now, repeat key measurements, follow crew medical checklist.")
    an = anthro(aid); life = dict(LIFE)
    if an["bmi"] and an["cat"] != "Normal":
        warn.append(f"BMI {an['bmi']:.1f} ({an['cat']}) - BMI does not separate muscle from fat; review with body fat % and ground dietitian")
        life["diet"] += " Priority: BMI is low - increase calories and protein." if an["cat"] == "Underweight" else " Priority: balance energy intake with exercise load."
    if an["kcal"]: life["diet"] += f" Estimated need ~{an['kcal']:.0f} kcal/day, protein >= {an['protein']:.0f} g/day (0.8 g/kg), fluid ~{an['fluid'] / 1000:.1f} L/day (1 mL/kcal, min 2 L)."
    if an["zone"]: life["exercise"] += f" Aerobic target heart rate ~{an['zone'][0]:.0f}-{an['zone'][1]:.0f} bpm (60-85% of est. max {an['hrmax']:.0f})."
    if any(r["key"] in ("tst", "se") for r in abn): life["sleep"] += " Priority: protected sleep period tonight."
    if any(r["key"] in ("vo2", "mass") for r in abn): life["exercise"] += " Priority: increase aerobic + resistive load."
    if any(r["key"] in ("ica", "mass", "hgb") for r in abn): life["diet"] += " Priority: nutrition review with ground dietitian."
    return dict(an=an, rows=rows, pi=pi, mi=mi, overall=overall, sev=sev, warn=warn, act=act, life=life)

# ---------------------------------------------------------------- html / pdf
CSS = "body{background:#0a1326;color:#e6eefc;font-family:Segoe UI,Arial;margin:14px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #20335a;padding:6px;text-align:left}h2{color:#6cb6ff}.b{display:inline-block;padding:8px 12px;border:1px solid #3b6fd1;border-radius:8px;background:#10224a;margin:3px}.a{color:#6cb6ff;font-size:20px}"
def fig_html(fig): 
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0a1326", plot_bgcolor="#0a1326")
    return fig.to_html(full_html=False, include_plotlyjs=True)

def section_chart(aid, sec):
    ps = [p for p in P if p[3] == sec and series(aid, p[0])]
    if not ps: return f"<style>{CSS}</style><h2>No data yet for this section</h2>"
    fig = make_subplots(rows=len(ps), cols=1, subplot_titles=[f"{p[1]} ({p[2]})" for p in ps])
    for i, p in enumerate(ps, 1):
        s = series(aid, p[0]); b = base_of(aid, p[0])
        fig.add_trace(go.Scatter(x=[d for d, _ in s], y=[v for _, v in s], mode="lines+markers", name=p[1]), row=i, col=1)
        if b is not None:
            fig.add_hrect(y0=b * .9, y1=b * 1.1, fillcolor="#2ecc71", opacity=.12, line_width=0, row=i, col=1)
            fig.add_hline(y=b, line_dash="dash", line_color="#2ecc71", row=i, col=1)
    fig.update_layout(height=max(320, 260 * len(ps)), showlegend=False)
    return f"<style>{CSS}</style>" + fig_html(fig)

bs = lambda r: '' if r['base'] is None else format(r['base'], 'g')

def report_html(aid, name):
    a = analyse(aid)
    if not a["rows"]: return f"<style>{CSS}</style><h2>Physical & Mental Health Analysis - {name}</h2><h1 style='color:#f39c12;margin-top:60px'>No values entered</h1><p>There are no physical readings or questionnaire answers saved for this astronaut yet. Enter values on the Physical Health or Mental Health page and press Save to generate the analysis.</p>"
    g = make_subplots(rows=1, cols=3, specs=[[{"type": "indicator"}] * 3])
    for i, (t, v) in enumerate([("Physical", a["pi"]), ("Mental", a["mi"]), ("Overall health", a["overall"])], 1):
        g.add_trace(go.Indicator(mode="gauge+number", value=v or 0, title={"text": t}, number={"suffix": "%"},
                    gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#3498db"}, "steps": [{"range": [0, 50], "color": "#6b1f1f"}, {"range": [50, 80], "color": "#6b5a1f"}, {"range": [80, 100], "color": "#1f6b3a"}]}), row=1, col=i)
    g.update_layout(height=280)
    cnt = {k: sum(r["status"] == k for r in a["rows"]) for k in COL if k != "No data"}
    flow = " <span class=a>&rarr;</span> ".join(f"<span class=b>{t}</span>" for t in ["Instrument readings", "Compare with personal baseline (+/-10%)",
           "Classify: " + ", ".join(f"{k} {v}" for k, v in cnt.items() if v), "NASA threshold rules", f"Severity: <b>{a['sev']}</b>"])
    chains = "".join(f"<div><span class=b>{r['label']} {r['val']:g}</span><span class=a>&rarr;</span><span class=b>{COND.get(r['key'], ('Out of range',))[0]}</span><span class=a>&rarr;</span><span class=b style='border-color:#e74c3c'>{COND.get(r['key'], ('', '', 'Repeat & consult'))[2]}</span></div>"
                     for r in a["rows"] if r["status"] == "Abnormal")
    tr = "".join(f"<tr><td>{r['label']}</td><td>{r['val']:g} {r['unit']}</td><td>{bs(r)}</td><td style='color:{COL[r['status']]}'><b>{r['status']}</b></td><td>{r['msg']}</td></tr>" for r in a["rows"])
    li = lambda L: "<ul>" + "".join(f"<li>{x}</li>" for x in L) + "</ul>" if L else "<p>None.</p>"
    return (f"<style>{CSS}</style><h2>Physical & Mental Health Analysis - {name}</h2><p>Generated {now()} | Severity: <b style='color:{'#e74c3c' if a['sev'] in ('SEVERE', 'ALERT') else '#2ecc71'}'>{a['sev']}</b></p>"
            + fig_html(g) + anthro_html(a["an"]) + f"<h2>Analysis flow</h2><div>{flow}</div><h2>Decision chains (abnormal)</h2>{chains or '<p>No abnormal parameters.</p>'}"
            f"<h2>Parameter status</h2><table><tr><th>Parameter</th><th>Latest</th><th>Baseline</th><th>Status</th><th>Trend</th></tr>{tr}</table>" + entries_html(aid) +
            f"<h2>Warnings</h2>{li(a['warn'])}<h2>Immediate actions & treatment</h2>{li(a['act'])}"
            f"<h2>Health actions</h2><ul>" + "".join(f"<li><b>{k.title()}:</b> {v}</li>" for k, v in a["life"].items()) + "</ul>"
            "<p style='color:#8aa'>Decision support based on NASA HRP/LSDA catalog thresholds. Not a diagnosis.</p>")

def export_pdf(aid, name, path):
    a = analyse(aid); st = getSampleStyleSheet()
    el = [Paragraph(f"Ground Medical Summary - {name}", st["Title"]), Paragraph(f"Generated {now()} | Severity: {a['sev']} | Physical {a['pi'] and round(a['pi'])}% | Mental {a['mi'] and round(a['mi'])}% | Overall {a['overall'] and round(a['overall'])}%", st["Normal"]), Spacer(1, 10)]
    at = Table([[k, v] for k, v in anthro_rows(a["an"])], colWidths=[170, 300]); at.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .3, colors.grey)]))
    el += [Paragraph("Anthropometric profile", st["Heading2"]), at, Spacer(1, 10)]
    data = [["Parameter", "Latest", "Baseline", "Status", "Trend"]] + [[Paragraph(r["label"], st["Normal"]), f"{r['val']:g} {r['unit']}", "" if r["base"] is None else f"{r['base']:g}", r["status"], Paragraph(r["msg"], st["Normal"])] for r in a["rows"]]
    t = Table(data, colWidths=[130, 70, 55, 80, 170], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b3a7a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .3, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")] +
                           [("TEXTCOLOR", (3, i), (3, i), colors.HexColor(COL[r["status"]])) for i, r in enumerate(a["rows"], 1)]))
    el += [t]
    for title, L in (("Warnings", a["warn"]), ("Immediate actions & treatment", a["act"]), ("Health actions", [f"{k.title()}: {v}" for k, v in a["life"].items()])):
        el += [Spacer(1, 10), Paragraph(title, st["Heading2"])] + [Paragraph("- " + x, st["Normal"]) for x in (L or ["None"])]
    el += [Spacer(1, 10), Paragraph("Entered values (latest 60)", st["Heading2"])]
    et = Table([["Date / time", "Parameter", "Value"]] + [[t, p[1], f"{v:g} {p[2]}"] for t, p, v in entries(aid)], repeatRows=1)
    et.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b3a7a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .3, colors.grey)]))
    el += [et]
    el += [Spacer(1, 10), Paragraph("Decision support only; not a diagnosis. Thresholds from NASA LSDA/HRR equipment catalog.", st["Italic"])]
    SimpleDocTemplate(path, pagesize=A4).build(el)

def entries(aid, limit=60):
    return [(r["ts"], PD[r["key"]], r["value"]) for r in con.execute("SELECT ts,key,value FROM readings WHERE aid=? ORDER BY ts DESC,id DESC LIMIT ?", (aid, limit)) if r["key"] in PD]

def entries_html(aid):
    tr = "".join(f"<tr><td>{t}</td><td>{SEC[p[3]]}</td><td>{p[1]}</td><td>{v:g} {p[2]}</td></tr>" for t, p, v in entries(aid))
    m = con.execute("SELECT ts,answers,note FROM mental WHERE aid=? ORDER BY ts DESC LIMIT 1", (aid,)).fetchone()
    qa = "" if not m else f"<h2>Latest questionnaire answers ({m['ts']})</h2><table>" + "".join(f"<tr><td>{i + 1}. {Q[i][0]}</td><td>{Q[i][1][a]}</td></tr>" for i, a in enumerate(json.loads(m["answers"]))) + "</table>" + (f"<p>Note: {m['note']}</p>" if m["note"] else "")
    return f"<h2>Entered values (latest 60 entries)</h2><table><tr><th>Date / time</th><th>Equipment</th><th>Parameter</th><th>Value</th></tr>{tr}</table>" + qa

def show(view, html, name):
    f = os.path.join(TMP, name + ".html")
    open(f, "w", encoding="utf-8").write(f"<html><head><meta charset='utf-8'></head><body>{html}</body></html>")
    view.load(QUrl.fromLocalFile(f))

# ---------------------------------------------------------------- CycleCare: data & analysis
# New tables live in the SAME database (astro_health.db) and are keyed by the existing astronauts.id (aid).
# Existing tables are only READ (astronauts, readings, mental); nothing existing is altered.
CC_OK, CC_ERR = True, ""
try:
    con.executescript("""
CREATE TABLE IF NOT EXISTS cc_cycles(id INTEGER PRIMARY KEY, aid INT, start_day TEXT, end_day TEXT, ts TEXT, UNIQUE(aid,start_day));
CREATE TABLE IF NOT EXISTS cc_daily(id INTEGER PRIMARY KEY, aid INT, day TEXT, flow INT, symptoms TEXT, severity INT, mood INT, work INT, exercise REAL, note TEXT, ts TEXT, UNIQUE(aid,day));
CREATE TABLE IF NOT EXISTS cc_settings(aid INT PRIMARY KEY, share INT DEFAULT 0, usual_cycle REAL, usual_period REAL, ts TEXT);
CREATE TABLE IF NOT EXISTS cc_audit(id INTEGER PRIMARY KEY, aid INT, ts TEXT, action TEXT);""")
    con.commit()
except sqlite3.Error as _ex: CC_OK, CC_ERR = False, str(_ex)

CC_SYMPTOMS = ["Cramps", "Fatigue", "Headache", "Bloating", "Mood changes", "Back pain", "Breast tenderness", "Nausea", "Acne", "Food cravings", "Sleep trouble", "Dizziness"]
CC_FLOW = ["None", "Spotting", "Light", "Medium", "Heavy"]
CC_FLOWCOL = ["#20335a", "#ffb3d1", "#ff8fc0", "#ff5fa8", "#d6226f"]
CC_SEV = ["None", "Mild", "Moderate", "Marked", "Severe"]
CC_WORK = ["Very light", "Light", "Moderate", "Heavy", "Very heavy"]
CC_PHASES = ("Menstrual", "Follicular", "Luteal")
# key, label, unit. Sources: tst/se/hr/mind = existing `readings`; fatigue/stress = existing `mental` questionnaire; work/ex = CycleCare daily log
CC_METRICS = [("tst", "Sleep time", "hrs"), ("se", "Sleep efficiency", "%"), ("fatigue", "Fatigue (questionnaire, 0-4)", ""), ("stress", "Stress (questionnaire, 0-4)", ""),
              ("work", "Workload (CycleCare log, 0-4)", ""), ("ex", "Exercise (CycleCare log)", "min"), ("hr", "Resting heart rate", "bpm"), ("mind", "Well-being index", "%")]
CCC = dict(period="#ff7eb6", pred="#a06bff", blue="#6cb6ff", teal="#5de0e6", amber="#f39c12", green="#2ecc71", red="#e74c3c")
CC_PHCOL = {"Menstrual": CCC["period"], "Follicular": CCC["blue"], "Luteal": CCC["pred"]}

def cc_date(x):
    try: return dt.date.fromisoformat(x)
    except (TypeError, ValueError): return None

# ----- storage helpers (all parameterised)
def cc_cycle_rows(aid):
    out = []
    for r in con.execute("SELECT id,start_day,end_day FROM cc_cycles WHERE aid=? ORDER BY start_day", (aid,)):
        a, b = cc_date(r["start_day"]), cc_date(r["end_day"])
        if not a: continue
        if b and b < a: b = None
        out.append(dict(id=r["id"], start=a, end=b, plen=(b - a).days + 1 if b else None))
    return out

def cc_logs(aid):
    d = {}
    for r in con.execute("SELECT day,flow,symptoms,severity,mood,work,exercise,note FROM cc_daily WHERE aid=? ORDER BY day", (aid,)):
        x = cc_date(r["day"])
        if not x: continue
        try: sy = [k for k in json.loads(r["symptoms"] or "[]") if k in CC_SYMPTOMS]
        except (ValueError, TypeError): sy = []
        d[x] = dict(flow=r["flow"] or 0, sym=sy, sev=r["severity"] or 0, mood=r["mood"], work=r["work"], ex=r["exercise"], note=r["note"] or "")
    return d

def cc_settings(aid):
    r = con.execute("SELECT share,usual_cycle,usual_period FROM cc_settings WHERE aid=?", (aid,)).fetchone()
    return dict(share=bool(r["share"]) if r else False, cycle=r["usual_cycle"] if r else None, period=r["usual_period"] if r else None)

def cc_save_settings(aid, **kw):
    cur = cc_settings(aid); cur.update(kw)
    con.execute("INSERT OR REPLACE INTO cc_settings(aid,share,usual_cycle,usual_period,ts) VALUES(?,?,?,?,?)", (aid, int(cur["share"]), cur["cycle"], cur["period"], now())); con.commit()

def cc_audit(aid, action):
    con.execute("INSERT INTO cc_audit(aid,ts,action) VALUES(?,?,?)", (aid, now(), action)); con.commit()

def cc_verify(aid, pw):
    """Re-check the astronaut's password with the app's existing hashing (hpw) before health data leaves the app."""
    try:
        r = con.execute("SELECT pw,salt FROM astronauts WHERE id=?", (aid,)).fetchone()
        return bool(r and r["pw"] and r["salt"] and hmac.compare_digest(hpw(pw, r["salt"]), r["pw"]))
    except (ValueError, sqlite3.Error): return False

def cc_add_period(aid, start, end=None):
    """Validate and store a period (dt.date values). Raises ValueError with a friendly message."""
    t = dt.date.today()
    if start > t: raise ValueError("The period start date cannot be in the future.")
    if end is not None:
        if end < start: raise ValueError("The end date is before the start date.")
        if end > t: raise ValueError("The period end date cannot be in the future.")
        if (end - start).days > 14: raise ValueError("A period longer than 15 days is unlikely - please check the dates.")
    for c in cc_cycle_rows(aid):
        if abs((c["start"] - start).days) < 7: raise ValueError(f"A period starting {c['start']:%d %b %Y} is already recorded within 7 days of this date.")
        if end is not None and start < c["start"] <= end: raise ValueError("The end date overlaps the start of a later recorded period.")
    con.execute("INSERT INTO cc_cycles(aid,start_day,end_day,ts) VALUES(?,?,?,?)", (aid, start.isoformat(), end.isoformat() if end else None, now())); con.commit()

def cc_set_end(aid, day):
    if day > dt.date.today(): raise ValueError("The period end date cannot be in the future.")
    prev = [c for c in cc_cycle_rows(aid) if c["start"] <= day]
    if not prev: raise ValueError("Record a period START on or before this date first.")
    c = prev[-1]
    if (day - c["start"]).days > 14: raise ValueError(f"This is more than 15 days after the period that started {c['start']:%d %b %Y}. Please check the dates.")
    con.execute("UPDATE cc_cycles SET end_day=? WHERE id=? AND aid=?", (day.isoformat(), c["id"], aid)); con.commit()

def cc_save_day(aid, day, flow, sym, sev, mood, work, ex, note):
    if day > dt.date.today(): raise ValueError("Future dates cannot be logged.")
    if flow not in range(5) or sev not in range(5): raise ValueError("Flow and severity must be between 0 and 4.")
    if mood is not None and mood not in range(5): raise ValueError("Invalid mood value.")
    if work is not None and work not in range(5): raise ValueError("Invalid workload value.")
    if ex is not None and not (0 <= ex <= 1000): raise ValueError("Exercise minutes must be between 0 and 1000.")
    sym = [k for k in sym if k in CC_SYMPTOMS]
    con.execute("INSERT OR REPLACE INTO cc_daily(aid,day,flow,symptoms,severity,mood,work,exercise,note,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, day.isoformat(), flow, json.dumps(sym), sev, mood, work, ex, (note or "").strip()[:300], now())); con.commit()

# ----- cycle statistics, personal baseline and pattern deviation
def cc_stats(aid):
    cyc, cs, t = cc_cycle_rows(aid), cc_settings(aid), dt.date.today()
    lens = [dict(start=cyc[i]["start"], n=(cyc[i + 1]["start"] - cyc[i]["start"]).days) for i in range(len(cyc) - 1)]
    good = [x["n"] for x in lens if x["n"] <= 90]          # >90 days = most likely an unrecorded period, ignored for averages
    recent = good[-6:]
    avg = statistics.mean(recent) if recent else cs["cycle"]
    sd = statistics.stdev(recent) if len(recent) >= 2 else None
    plens = [c["plen"] for c in cyc if c["plen"]][-6:]
    aplen = statistics.mean(plens) if plens else cs["period"]
    dev, prior = [], []                                       # each cycle compared with the baseline built from the cycles BEFORE it
    for x in lens:
        row = dict(start=x["start"], n=x["n"], base=None, dev=None, thr=None, flag=False)
        if len(prior) >= 3: b, thr = statistics.mean(prior), max(5.0, 2 * statistics.stdev(prior))
        elif cs["cycle"]: b, thr = cs["cycle"], 7.0
        else: b = thr = None
        if b is not None: row.update(base=b, dev=x["n"] - b, thr=thr, flag=abs(x["n"] - b) > thr)
        dev.append(row)
        if x["n"] <= 90: prior.append(x["n"])
    if len(good) >= 3: bmean, bsd = statistics.mean(good), statistics.stdev(good); bthr = max(5.0, 2 * bsd)
    elif cs["cycle"]: bmean, bsd, bthr = cs["cycle"], None, 7.0
    else: bmean = bsd = bthr = None
    st = dict(cycles=cyc, lens=lens, dev=dev, avg=avg, sd=sd, aplen=aplen, n_len=len(good), settings=cs, bmean=bmean, bsd=bsd, bthr=bthr,
              last=cyc[-1] if cyc else None, next=None, win=None, late=None, since=None, day=None, on_period=False, phase=None, phases={},
              reg="Not enough data" if sd is None or len(recent) < 3 else ("Consistent" if sd <= 4 else "Somewhat variable" if sd <= 7 else "Highly variable"))
    last = st["last"]
    if last:
        st["since"] = (t - last["start"]).days
        st["day"] = st["since"] + 1 if st["since"] >= 0 else None
        if avg:
            st["win"] = min(10, max(2, math.ceil(sd))) if sd is not None else 3
            st["next"] = last["start"] + dt.timedelta(days=round(avg))
            st["late"] = (t - st["next"]).days
        st["on_period"] = (last["end"] is None and 0 <= st["since"] <= 9) or (last["end"] is not None and last["start"] <= t <= last["end"])
    st["phases"] = cc_phase_map(st)
    st["phase"] = st["phases"].get(t, (None, None, None))[1]
    return st

def cc_phase_map(st):
    """{date: (cycle_day, phase, cycle_length)}. Calendar-based ESTIMATE (luteal = last 14 days of the cycle); not hormone-tested."""
    out, cyc, t = {}, st["cycles"], dt.date.today()
    avg, ap = st["avg"] or 28, st["aplen"] or 5
    one = dt.timedelta(days=1)
    for i, c in enumerate(cyc):
        nxt = cyc[i + 1]["start"] if i + 1 < len(cyc) else None
        L = (nxt - c["start"]).days if nxt else round(avg)
        Leff = L if L <= 90 else round(avg)
        if nxt and L <= 90: end = nxt - one
        elif nxt: end = c["start"] + dt.timedelta(days=Leff - 1)
        else: end = min(t, c["start"] + dt.timedelta(days=89))
        plen = c["plen"] or round(ap)
        d = c["start"]
        while d <= end:
            cd = (d - c["start"]).days + 1
            out[d] = (cd, "Menstrual" if cd <= plen else ("Follicular" if cd <= Leff - 14 else "Luteal"), Leff)
            d += one
    return out

# ----- cross-system data: existing readings / questionnaire + CycleCare log
def cc_metric_series(aid, logs):
    """{metric: {date: value}} built from the EXISTING readings (series()) and mental tables plus the CycleCare daily log."""
    m = {k: {} for k, _, _ in CC_METRICS}
    for k in ("tst", "se", "hr", "mind"):
        for day, v in series(aid, k):
            d = cc_date(day)
            if d: m[k][d] = v
    for r in con.execute("SELECT day,answers FROM mental WHERE aid=? ORDER BY ts", (aid,)):
        d = cc_date(r["day"])
        try:
            a = json.loads(r["answers"])
            if d and isinstance(a, list) and len(a) >= 13: m["fatigue"][d] = (float(a[0]) + float(a[12])) / 2; m["stress"][d] = float(a[9])
        except (ValueError, TypeError): continue
    for d, l in logs.items():
        if l["work"] is not None: m["work"][d] = l["work"]
        if l["ex"] is not None: m["ex"][d] = l["ex"]
    return m

def cc_pearson(xs, ys):
    n = len(xs)
    if n < 6: return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    if sxx < 1e-12 or syy < 1e-12: return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)

def cc_strength(r, n):
    if r is None: return "Not enough data (needs 6+ days with variation)" if n < 6 else "No variation"
    a = abs(r)
    return ("Weak" if a < .3 else "Moderate" if a < .5 else "Strong") + (" positive" if r > 0 else " negative")

def cc_assoc(ms, logs, phases, since=None):
    """Association of every metric with daily symptom burden (count of symptoms) + mean of each metric per estimated cycle phase."""
    since, out = since or dt.date.min, []
    for key, label, unit in CC_METRICS:
        xs, ys, ph = [], [], {}
        for d, v in ms.get(key, {}).items():
            if d < since: continue
            if d in logs: xs.append(v); ys.append(len(logs[d]["sym"]))
            if d in phases: ph.setdefault(phases[d][1], []).append(v)
        out.append(dict(key=key, label=label, unit=unit, n=len(xs), r=cc_pearson(xs, ys), phase={p: (statistics.mean(v), len(v)) for p, v in ph.items()}))
    return out

# ----- health alerts (observations only - never a diagnosis)
def cc_alerts(aid, st, logs, ms):
    t, out = dt.date.today(), []
    def add(lv, ti, tx, step="Mention it at your next private medical conference if it continues or worries you."): out.append(dict(level=lv, title=ti, text=tx, step=step))
    if not st["cycles"]: return out
    last, dv = st["last"], (st["dev"][-1] if st["dev"] else None)
    if st["next"] and st["late"] is not None and st["late"] > st["win"] and not st["on_period"]:
        add("attention" if st["since"] >= 90 else "notice", "Period later than your usual pattern",
            f"Your estimated start was around {st['next']:%d %b}; it is now {st['late']} days past that ({st['since']} days since your last recorded start). Stress, sleep changes, workload and exercise can all shift timing. If you simply have not logged a period, add it in the calendar.")
    if dv and dv["flag"]:
        add("notice", "Latest cycle differs from your personal baseline", f"The cycle that began {dv['start']:%d %b} lasted {dv['n']} days, {abs(dv['dev']):.0f} days {'longer' if dv['dev'] > 0 else 'shorter'} than your baseline of {dv['base']:.0f} days (flagged beyond +/-{dv['thr']:.0f} days).")
    elif dv and dv["n"] <= 90 and (dv["n"] < 21 or dv["n"] > 38):
        add("notice", "Cycle length outside the commonly quoted 21-38 day range", f"The cycle that began {dv['start']:%d %b} lasted {dv['n']} days. This is a general reference range only; your own baseline matters most.")
    if st["sd"] is not None and st["n_len"] >= 4 and st["sd"] > 7:
        add("notice", "Cycle length varies a lot", f"Your last {min(6, st['n_len'])} cycles vary by about {st['sd']:.1f} days (standard deviation).")
    if last["plen"] and last["plen"] > 7:
        add("notice", "Longer period than usual", f"Your latest period lasted {last['plen']} days.")
    heavy_now = sum(1 for d, l in logs.items() if l["flow"] == 4 and last["start"] <= d <= last["start"] + dt.timedelta(days=9))
    if heavy_now >= 3: add("notice", "Several heavy-flow days logged", f"Heavy flow was logged on {heavy_now} days of your current/latest period.")
    heavy90 = sum(1 for d, l in logs.items() if l["flow"] == 4 and d >= t - dt.timedelta(days=90))
    hg, lo = series(aid, "hgb"), PD["hgb"][4].get("lo")
    if hg and lo and hg[-1][1] < lo and (heavy90 >= 2 or (last["plen"] or 0) > 7):
        add("attention", "Heavy or long flow recorded together with low haemoglobin",
            f"Your latest haemoglobin in the Physical Health data is {hg[-1][1]:g} g/dL (reference threshold {lo:g}) and heavy or long flow has been recorded recently. These two records may be related; this is an observation, not a diagnosis.",
            "Share both records with the flight surgeon.")
    sev = sum(1 for d, l in logs.items() if l["sev"] >= 4 and d >= t - dt.timedelta(days=13))
    if sev >= 2: add("notice", "Severe symptoms on several recent days", f"Severe overall symptoms were logged on {sev} of the last 14 days.", "If symptoms interfere with your duties, contact the flight surgeon.")
    wk = [v for d, v in ms["tst"].items() if d >= t - dt.timedelta(days=6)]
    nsym = sum(1 for d, l in logs.items() if l["sym"] and d >= t - dt.timedelta(days=6))
    if wk and statistics.mean(wk) < 6 and nsym >= 3:
        add("info", "Short sleep and symptoms in the same week", f"Average sleep over the last 7 days is {statistics.mean(wk):.1f} h and symptoms were logged on {nsym} days. Protecting sleep may help you feel better.", "Try a protected sleep period and reduced evening blue light.")
    for a in sorted([a for a in cc_assoc(ms, logs, st["phases"]) if a["r"] is not None and abs(a["r"]) >= .5 and a["n"] >= 10], key=lambda a: -abs(a["r"]))[:2]:
        add("info", f"Pattern: {a['label']} and symptoms", f"On the {a['n']} days you logged, {a['label'].lower()} and your symptom count moved {'together' if a['r'] > 0 else 'in opposite directions'} (r = {a['r']:+.2f}). An association is not proof of cause.", "Keep logging - patterns become clearer over several cycles.")
    out.sort(key=lambda a: {"attention": 0, "notice": 1, "info": 2}[a["level"]])
    return out

# ----- Plotly figures (all return None when there is nothing to draw)
def cc_html(figs, empty="No data yet"):
    figs = [f for f in figs if f is not None]
    if not figs: return f"<style>{CSS}</style><h2>{empty}</h2>"
    out = []
    for i, f in enumerate(figs):
        f.update_layout(template="plotly_dark", paper_bgcolor="#0a1326", plot_bgcolor="#0a1326", margin=dict(l=55, r=35, t=60, b=45))
        out.append(f.to_html(full_html=False, include_plotlyjs=(i == 0)))     # Plotly JS is embedded once - works offline
    return f"<style>{CSS}</style>" + "".join(out)

def cc_fig_cycles(st):
    if not st["lens"]: return None
    fig = make_subplots(rows=2, cols=1, vertical_spacing=.2, subplot_titles=("Cycle length (days) - green band = your personal baseline range", "Period duration (days)"))
    if st["bmean"] is not None:
        fig.add_hrect(y0=st["bmean"] - st["bthr"], y1=st["bmean"] + st["bthr"], fillcolor=CCC["green"], opacity=.12, line_width=0, row=1, col=1)
        fig.add_hline(y=st["bmean"], line_dash="dash", line_color=CCC["green"], row=1, col=1)
    fig.add_trace(go.Scatter(x=[x["start"].isoformat() for x in st["lens"]], y=[x["n"] for x in st["lens"]], mode="lines+markers", line=dict(color=CCC["blue"]),
                             marker=dict(size=11, color=[CCC["red"] if d["flag"] else CCC["period"] for d in st["dev"]]), hovertemplate="Cycle starting %{x}<br>%{y} days<extra></extra>"), row=1, col=1)
    pl = [(c["start"].isoformat(), c["plen"]) for c in st["cycles"] if c["plen"]]
    if pl: fig.add_trace(go.Bar(x=[a for a, _ in pl], y=[b for _, b in pl], marker_color=CCC["period"], hovertemplate="Period starting %{x}<br>%{y} days<extra></extra>"), row=2, col=1)
    fig.update_layout(height=540, showlegend=False)
    return fig

def cc_fig_flow(logs, since):
    days = sorted(d for d in logs if d >= since)
    if not days: return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=[d.isoformat() for d in days], y=[logs[d]["flow"] for d in days], marker_color=[CC_FLOWCOL[logs[d]["flow"]] for d in days], name="Flow",
                         text=[CC_FLOW[logs[d]["flow"]] for d in days], hovertemplate="%{x}<br>Flow: %{text}<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=[d.isoformat() for d in days], y=[len(logs[d]["sym"]) for d in days], mode="lines+markers", name="Symptoms logged", line=dict(color=CCC["teal"])), secondary_y=True)
    fig.update_yaxes(title_text="Flow (0 none - 4 heavy)", range=[0, 4.3], secondary_y=False)
    fig.update_yaxes(title_text="Symptoms logged", rangemode="tozero", secondary_y=True)
    fig.update_layout(title="Flow intensity and symptom count", height=380, hovermode="x unified", legend=dict(orientation="h", y=-0.2))
    return fig

def cc_fig_symptoms(logs, since):
    cnt = {k: 0 for k in CC_SYMPTOMS}
    for d, l in logs.items():
        if d >= since:
            for k in l["sym"]: cnt[k] += 1
    cnt = sorted(((k, v) for k, v in cnt.items() if v), key=lambda x: x[1])
    if not cnt: return None
    fig = go.Figure(go.Bar(x=[v for _, v in cnt], y=[k for k, _ in cnt], orientation="h", marker_color=CCC["period"], hovertemplate="%{y}: %{x} days<extra></extra>"))
    fig.update_layout(title="Most frequent symptoms (days logged)", height=max(300, 60 + 34 * len(cnt)))
    return fig

def cc_fig_cycle_day(logs, phases, since):
    agg = {}
    for d, l in logs.items():
        if d >= since and d in phases: agg.setdefault((phases[d][1], phases[d][0]), []).append(len(l["sym"]))
    if not agg: return None
    fig = go.Figure()
    for ph in CC_PHASES:
        pts = sorted((cd, statistics.mean(v)) for (p, cd), v in agg.items() if p == ph)
        if pts: fig.add_trace(go.Bar(x=[a for a, _ in pts], y=[b for _, b in pts], name=ph + " (est.)", marker_color=CC_PHCOL[ph], hovertemplate="Cycle day %{x}<br>%{y:.1f} symptoms<extra></extra>"))
    fig.update_layout(title="Average symptoms by cycle day (phase colours are calendar estimates)", xaxis_title="Cycle day", yaxis_title="Avg symptoms logged", height=380, barmode="relative", legend=dict(orientation="h", y=-0.25))
    return fig

def cc_fig_timeline(key, label, unit, ms, logs, st, since):
    pts = sorted((d, v) for d, v in ms.get(key, {}).items() if d >= since)
    sy = sorted((d, len(l["sym"])) for d, l in logs.items() if d >= since)
    if not pts and not sy: return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if pts: fig.add_trace(go.Scatter(x=[d.isoformat() for d, _ in pts], y=[v for _, v in pts], mode="lines+markers", name=label, line=dict(color=CCC["blue"])), secondary_y=False)
    if sy: fig.add_trace(go.Bar(x=[d.isoformat() for d, _ in sy], y=[v for _, v in sy], name="Symptoms logged", opacity=.45, marker_color=CCC["period"]), secondary_y=True)
    t, one = dt.date.today(), dt.timedelta(days=1)
    for c in st["cycles"]:
        e = min(c["end"] or (c["start"] + dt.timedelta(days=round(st["aplen"] or 5) - 1)), t)
        if e >= since: fig.add_vrect(x0=max(c["start"], since).isoformat(), x1=(e + one).isoformat(), fillcolor=CCC["period"], opacity=.16, line_width=0, layer="below")
    fig.update_yaxes(title_text=f"{label} {('(' + unit + ')') if unit else ''}", secondary_y=False)
    fig.update_yaxes(title_text="Symptoms logged", rangemode="tozero", secondary_y=True)
    fig.update_layout(title=f"{label} vs symptoms over time (pink bands = period days)", height=400, hovermode="x unified", legend=dict(orientation="h", y=-0.2))
    return fig

def cc_fig_phase(a):
    ph = [(p, a["phase"][p]) for p in CC_PHASES if p in a["phase"]]
    if len(ph) < 2: return None
    fig = go.Figure(go.Bar(x=[p for p, _ in ph], y=[v[0] for _, v in ph], marker_color=[CC_PHCOL[p] for p, _ in ph], text=[f"n={v[1]}" for _, v in ph], textposition="outside",
                           hovertemplate="%{x}: %{y:.2f}<extra></extra>"))
    fig.update_layout(title=f"{a['label']} by estimated cycle phase (average)", yaxis_title=a["unit"] or "score", height=340)
    return fig

def cc_fig_assoc(rows):
    ok = [a for a in rows if a["r"] is not None]
    if not ok: return None
    fig = go.Figure(go.Bar(x=[a["r"] for a in ok], y=[a["label"] for a in ok], orientation="h", marker_color=[CCC["teal"] if a["r"] > 0 else CCC["amber"] for a in ok],
                           text=[f"r={a['r']:+.2f}, n={a['n']}" for a in ok], hovertemplate="%{y}<br>%{text}<extra></extra>"))
    fig.update_layout(title="Association with daily symptom count (Pearson r, days with both records)", xaxis=dict(range=[-1, 1], title_text="r  (-1 opposite ... +1 together)"), height=380)
    return fig

# ----- ReportLab PDF
def cc_export_pdf(aid, name, path, medical=False):
    st, logs = cc_stats(aid), cc_logs(aid)
    ms = cc_metric_series(aid, logs); al = cc_alerts(aid, st, logs, ms); asc = cc_assoc(ms, logs, st["phases"])
    ss = getSampleStyleSheet(); N = ss["Normal"]; t = dt.date.today()
    para = lambda x, s=N: Paragraph(xesc(str(x)), s)
    head = lambda tb: tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b3a7a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .3, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    aud = "For authorised medical review - shared with the astronaut's consent (free-text notes and mood omitted)" if medical else "Personal copy"
    f = lambda x, u="", fm=".0f": "-" if x is None else format(x, fm) + u
    el = [para(f"CycleCare Health Summary - {name}", ss["Title"]),
          para(f"Generated {now()} | {aud}", N),
          para("CONFIDENTIAL reproductive-health information. Observations from self-recorded data; not a diagnosis.", ss["Italic"]), Spacer(1, 10)]
    rows = [["Cycles recorded", str(len(st["cycles"]))], ["Average cycle length", f(st["avg"], " days") + ("" if st["sd"] is None else f" (SD {st['sd']:.1f})")], ["Regularity", st["reg"]],
            ["Average period length", f(st["aplen"], " days")], ["Last period start", f"{st['last']['start']:%d %b %Y}" if st["last"] else "-"],
            ["Next period estimate", "-" if not st["next"] else f"{st['next']:%d %b %Y} (+/-{st['win']} days)"],
            ["Today", "-" if not st["day"] or st["day"] > 120 else f"Cycle day {st['day']}" + (f", {st['phase']} phase (estimate)" if st["phase"] else "")],
            ["Personal baseline", "-" if st["bmean"] is None else f"{st['bmean']:.0f} days, flagged beyond +/-{st['bthr']:.0f} days"]]
    tb = Table(rows, colWidths=[150, 340]); tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .3, colors.grey)])); el += [para("Summary", ss["Heading2"]), tb]
    lens = st["lens"][-12:]
    if lens:
        d = Drawing(480, 170); bc = VerticalBarChart(); bc.x, bc.y, bc.width, bc.height = 40, 40, 420, 105
        bc.data = [[x["n"] for x in lens]]; bc.categoryAxis.categoryNames = [f"{x['start']:%d %b}" for x in lens]
        bc.categoryAxis.labels.fontSize = 6; bc.categoryAxis.labels.angle = 45; bc.categoryAxis.labels.boxAnchor = "ne"
        bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = max([x["n"] for x in lens] + [35]) + 5; bc.valueAxis.labels.fontSize = 7; bc.bars[0].fillColor = colors.HexColor("#d9548f")
        d.add(bc); el += [Spacer(1, 8), para("Cycle length (days), most recent 12 cycles", ss["Heading3"]), d]
    hist = [["Start", "End", "Period days", "Cycle length", "vs baseline"]]
    dv = {x["start"]: x for x in st["dev"]}
    for c in st["cycles"][-12:][::-1]:
        x = dv.get(c["start"])
        hist.append([f"{c['start']:%d %b %Y}", f"{c['end']:%d %b %Y}" if c["end"] else "-", f(c["plen"]), f(x["n"] if x else None, " d"), "-" if not x or x["dev"] is None else f"{x['dev']:+.0f} d" + (" (flagged)" if x["flag"] else "")])
    if len(hist) > 1: tb = Table(hist, repeatRows=1); head(tb); el += [para("Cycle history", ss["Heading2"]), tb]
    el += [para("Health alerts (observations, not diagnoses)", ss["Heading2"])] + [para(f"[{a['level'].upper()}] {a['title']}: {a['text']}") for a in al] if al else [para("Health alerts (observations, not diagnoses)", ss["Heading2"]), para("No alerts.")]
    cnt = {k: sum(1 for d, l in logs.items() if k in l["sym"] and d >= t - dt.timedelta(days=90)) for k in CC_SYMPTOMS}
    sy = [[k, str(v)] for k, v in sorted(cnt.items(), key=lambda x: -x[1]) if v]
    if sy: tb = Table([["Symptom (last 90 days)", "Days logged"]] + sy, colWidths=[250, 100], repeatRows=1); head(tb); el += [para("Symptom frequency", ss["Heading2"]), tb]
    ar = [[a["label"], str(a["n"]), "-" if a["r"] is None else f"{a['r']:+.2f}", cc_strength(a["r"], a["n"])] for a in asc]
    tb = Table([["Cross-system measure", "Days", "r vs symptoms", "Reading"]] + ar, colWidths=[190, 40, 80, 180], repeatRows=1); head(tb)
    el += [para("Cross-system associations (sleep, fatigue, workload, activity)", ss["Heading2"]), tb, para("Associations show records that move together; they do not show cause. Sleep and heart rate come from the Physical Health data, fatigue and stress from the daily questionnaire, workload and exercise from the CycleCare daily log.", ss["Italic"])]
    recent = [(d, l) for d, l in sorted(logs.items(), reverse=True) if d >= t - dt.timedelta(days=30)]
    if recent:
        hd = ["Date", "Flow", "Symptoms", "Severity"] + ([] if medical else ["Mood", "Note"])
        data = [hd] + [[f"{d:%d %b}", CC_FLOW[l["flow"]], para(", ".join(l["sym"]) or "-"), CC_SEV[l["sev"]]] + ([] if medical else [MOOD[l["mood"]] if l["mood"] is not None else "-", para(l["note"] or "-")]) for d, l in recent]
        tb = Table(data, repeatRows=1, colWidths=[45, 55, 170, 60] if medical else [45, 50, 130, 55, 70, 140]); head(tb); el += [para("Daily log (last 30 days)", ss["Heading2"]), tb]
    el += [Spacer(1, 10), para("Decision support only; not a diagnosis. Please discuss any concerns with the flight surgeon.", ss["Italic"])]
    SimpleDocTemplate(path, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40, title=f"CycleCare - {name}").build(el)

# ---------------------------------------------------------------- UI
QSS = """QWidget{background:#0a1326;color:#e6eefc;font-size:13px}
QLineEdit,QComboBox,QPlainTextEdit{background:#0f1d3d;border:1px solid #2b4a88;border-radius:6px;padding:5px}
QPushButton{background:#10224a;border:1px solid #3b6fd1;border-radius:8px;padding:8px 12px}QPushButton:hover{background:#1a3570}
QPushButton:checked{background:#2a2a7a;border-color:#a06bff}
QGroupBox{border:1px solid #2b4a88;border-radius:10px;margin-top:16px;padding-top:12px;font-weight:bold;color:#6cb6ff}
QGroupBox::title{subcontrol-origin:margin;left:12px}QHeaderView::section{background:#10224a;padding:4px;border:0}"""

class Home(QWidget):
    def __init__(s, w):
        super().__init__(); L = QVBoxLayout(s)
        t = QLabel("<h1 style='color:#fff'>Welcome to the Astronaut Health Monitoring System</h1><p style='color:#5de0e6;font-size:16px'><i>Track health. Ensure mission success.</i></p>"); t.setAlignment(Qt.AlignCenter); L.addWidget(t)
        s.sel = QLabel(); s.sel.setStyleSheet("font-size:22px;padding:14px;border:1px solid #3b6fd1;border-radius:14px"); L.addWidget(s.sel)
        g = QGridLayout(); L.addLayout(g)
        for i, (txt, col, pg) in enumerate([("Physical Health\nMonitor vital signs, biometrics and physical condition  \u2192", "#12306e", 3), ("Mental Health\nTrack stress, mood and psychological well-being  \u2192", "#2a1e6e", 4),
                                            ("Add Baseline Health Data\nEnter initial health metrics for an astronaut  +", "#0f6b5a", 2), ("Physical and Mental Health Analysis Report\nView comprehensive health reports  \u2192", "#4a2a8a", 5)]):
            b = QPushButton(txt); b.setMinimumHeight(110); b.setStyleSheet(f"QPushButton{{background:{col};font-size:16px;text-align:left;padding:18px;border-radius:14px}}")
            b.clicked.connect(lambda _, p=pg: w.go(p)); g.addWidget(b, i // 2, i % 2)
        s.cc = QPushButton("CycleCare\nMenstrual calendar, symptoms, predictions and reports  \u2192"); s.cc.setMinimumHeight(110)
        s.cc.setStyleSheet("QPushButton{background:#7a1f4f;font-size:16px;text-align:left;padding:18px;border-radius:14px}"); s.cc.clicked.connect(lambda: w.go(6)); g.addWidget(s.cc, 2, 0); s.cc.hide()
        s.w = w
    def refresh(s):
        s.sel.setText(f"Selected Astronaut:  <b>{s.w.name() or '- none (choose or add an astronaut) -'}</b>"); s.cc.setVisible(s.w.is_female())

class Dash(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; L = QVBoxLayout(s)
        s.t = QTableWidget(0, 6); s.t.setHorizontalHeaderLabels(["Parameter", "Latest", "Baseline", "Status", "Date", "Trend"]); s.t.horizontalHeader().setStretchLastSection(True); s.t.setMaximumHeight(260)
        s.c = QComboBox(); [s.c.addItem(v, k) for k, v in SEC.items()]; s.c.currentIndexChanged.connect(s.chart); s.v = QWebEngineView(); s.v.setMinimumHeight(420)
        vb = QPushButton("VIEW ALL ENTERED VALUES"); vb.clicked.connect(lambda: Values(w).exec())
        L.addWidget(QLabel("<h2>Personal Dashboard</h2>")); L.addWidget(vb); L.addWidget(s.t); L.addWidget(QLabel("Charts (dashed = baseline, green band = +/-10%)")); L.addWidget(s.c); L.addWidget(s.v, 1)
    def refresh(s):
        rows = analyse(s.w.aid)["rows"]; s.t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, x in enumerate([r["label"], f"{r['val']:g} {r['unit']}", "" if r["base"] is None else f"{r['base']:g}", r["status"], r["day"], r["msg"]]):
                it = QTableWidgetItem(x)
                if j == 3: it.setForeground(Qt.GlobalColor.white); it.setBackground(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor(COL[r["status"]]))
                s.t.setItem(i, j, it)
        s.t.resizeColumnsToContents(); s.chart()
    def chart(s):
        if s.w.aid: show(s.v, section_chart(s.w.aid, s.c.currentData()), "dash")

class Fields(QWidget):
    """Grid of labelled numeric inputs grouped by equipment section."""
    def __init__(s, w, guides=True, secs=range(1, 8)):
        super().__init__(); s.w = w; s.e = {}; L = QVBoxLayout(s)
        for sec in secs:
            gb = QGroupBox(f"{sec}  {SEC[sec].upper()}"); g = QGridLayout(gb)
            if guides:
                b = QPushButton("USER GUIDE"); b.clicked.connect(lambda _, x=sec: QMessageBox.information(s, SEC[x], "\n".join(f"{i}. {t}" for i, t in enumerate(GUIDE[x], 1)))); g.addWidget(b, 0, 4)
            for n, p in enumerate([p for p in P if p[3] == sec]):
                r, c = n // 2 + 1, (n % 2) * 3; e = QLineEdit(); e.setPlaceholderText("value"); s.e[p[0]] = e
                g.addWidget(QLabel(p[1]), r, c); g.addWidget(e, r, c + 1); g.addWidget(QLabel(p[2]), r, c + 2)
            if sec == 3:
                u = QPushButton("Upload / Select DICOM (cine loops, 2D frames)"); u.clicked.connect(s.upload); g.addWidget(u, 20, 0, 1, 3)
            L.addWidget(gb)
    def upload(s):
        f, _ = QFileDialog.getOpenFileName(s, "Select DICOM / image", "", "DICOM/Images (*.dcm *.png *.jpg *.mp4);;All (*)")
        if f: con.execute("INSERT INTO files(aid,ts,path) VALUES(?,?,?)", (s.w.aid, now(), f)); con.commit(); QMessageBox.information(s, "Saved", "File linked to this astronaut.")
    def values(s):
        out = {}
        for k, e in s.e.items():
            if e.text().strip():
                try: out[k] = float(e.text().replace(",", "."))
                except ValueError: raise ValueError(f"'{PD[k][1]}' must be a number")
        return out

class Values(QDialog):
    """View, filter and delete (correct) previously entered values and questionnaire answers."""
    def __init__(s, w, tab=0):
        super().__init__(w); s.aid = w.aid; s.setWindowTitle(f"Entered values - {w.name()}"); s.resize(980, 620); L = QVBoxLayout(s)
        s.tabs = QTabWidget(); L.addWidget(s.tabs)
        pw = QWidget(); pl = QVBoxLayout(pw); s.f = QComboBox(); s.f.addItem("All equipment", 0)
        for k, v in SEC.items():
            if k < 8: s.f.addItem(v, k)
        s.f.currentIndexChanged.connect(s.fill); s.t = QTableWidget(0, 5); s.t.setHorizontalHeaderLabels(["Date / time", "Equipment", "Parameter", "Value", "Unit"])
        s.t.setSelectionBehavior(QAbstractItemView.SelectRows); s.t.setEditTriggers(QAbstractItemView.NoEditTriggers); s.t.horizontalHeader().setStretchLastSection(True)
        d = QPushButton("Delete selected entry (wrong value)"); d.clicked.connect(s.delete); pl.addWidget(s.f); pl.addWidget(s.t); pl.addWidget(d)
        mw = QWidget(); ml = QVBoxLayout(mw); s.m = QTableWidget(0, 4); s.m.setHorizontalHeaderLabels(["Date / time", "Well-being %", "Note", "Answers"]); s.m.horizontalHeader().setStretchLastSection(True); s.m.setEditTriggers(QAbstractItemView.NoEditTriggers); ml.addWidget(s.m)
        s.tabs.addTab(pw, "Physical readings"); s.tabs.addTab(mw, "Questionnaire answers"); s.tabs.setCurrentIndex(tab); s.fill()
    def fill(s):
        sec = s.f.currentData(); q = "SELECT id,ts,key,value FROM readings WHERE aid=? AND key!='mind' ORDER BY ts DESC,id DESC LIMIT 1000"
        rows = [r for r in con.execute(q, (s.aid,)) if not sec or PD[r["key"]][3] == sec]; s.t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            p = PD[r["key"]]
            for j, x in enumerate([r["ts"], SEC[p[3]], p[1], f"{r['value']:g}", p[2]]): s.t.setItem(i, j, QTableWidgetItem(x))
            s.t.item(i, 0).setData(Qt.UserRole, r["id"])
        s.t.resizeColumnsToContents()
        ms = list(con.execute("SELECT ts,idx,note,answers FROM mental WHERE aid=? ORDER BY ts DESC", (s.aid,))); s.m.setRowCount(len(ms))
        for i, r in enumerate(ms):
            ans = "; ".join(f"Q{n + 1}: {Q[n][1][a]}" for n, a in enumerate(json.loads(r["answers"])))
            for j, x in enumerate([r["ts"], f"{r['idx']:.0f}", r["note"] or "", ans]): s.m.setItem(i, j, QTableWidgetItem(x))
        s.m.resizeColumnsToContents()
    def delete(s):
        r = s.t.currentRow()
        if r < 0: return QMessageBox.information(s, "Select", "Select a row first.")
        if QMessageBox.question(s, "Delete", "Delete this entry? Analysis will be recalculated.") == QMessageBox.Yes:
            con.execute("DELETE FROM readings WHERE id=? AND aid=?", (s.t.item(r, 0).data(Qt.UserRole), s.aid)); con.commit(); s.fill()

class Phys(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; L = QVBoxLayout(s)
        L.addWidget(QLabel("<h2>PHYSICAL HEALTH DATA ENTRY</h2>Enter measured values from the instruments. Most recent measurement; units to the right of each field."))
        s.f = Fields(w); L.addLayout(s.bar()); sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(s.f); L.addWidget(sc)
        L.addLayout(s.bar())
    def bar(s):
        r = QHBoxLayout(); v = QPushButton("VIEW ENTERED VALUES"); v.clicked.connect(lambda: Values(s.w, 0).exec())
        c = QPushButton("SAVE READINGS & VIEW ANALYSIS  \u2192"); c.setStyleSheet("background:#0f6b3a;border-color:#2ecc71;font-weight:bold;font-size:15px"); c.clicked.connect(s.save_view); r.addWidget(v); r.addWidget(c, 1); return r
    def save_view(s):
        if not any(e.text().strip() for e in s.f.e.values()): return s.w.go(5)
        n = lambda: con.execute("SELECT COUNT(*) FROM readings WHERE aid=?", (s.w.aid,)).fetchone()[0]; before = n(); s.save()
        if n() > before: s.w.go(5)
    def save(s):
        try: v = {k: x for k, x in s.f.values().items() if k != "mind"}
        except ValueError as ex: return QMessageBox.warning(s, "Invalid", str(ex))
        if not v: return QMessageBox.information(s, "No values entered", "No values entered. Enter at least one reading before saving.")
        t = now()
        con.executemany("INSERT INTO readings(aid,ts,day,key,value) VALUES(?,?,?,?,?)", [(s.w.aid, t, t[:10], k, x) for k, x in v.items()]); con.commit()
        for e in s.f.e.values(): e.clear()
        a = analyse(s.w.aid); QMessageBox.information(s, "Saved", f"{len(v)} readings stored at {t}.\nSeverity: {a['sev']}. Open the Analysis Report for details.")
    def refresh(s): pass

class Base(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; L = QVBoxLayout(s)
        L.addWidget(QLabel("<h2>ADD BASELINE HEALTH DATA</h2>Baselines are permanent per astronaut: once saved, a value is locked. Enter manually or import from a Personal Health Report PDF."))
        tb = QPushButton("SAVE BASELINE (permanent)"); tb.setStyleSheet("background:#0f6b3a;border-color:#2ecc71;font-weight:bold;font-size:15px"); tb.clicked.connect(s.save); L.addWidget(tb); s.f = Fields(w, guides=False, secs=range(1, 8)); sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(s.f); L.addWidget(sc)
        r = QHBoxLayout(); a = QPushButton("IMPORT FROM PDF"); a.clicked.connect(s.pdf); b = QPushButton("SAVE BASELINE (permanent)"); b.clicked.connect(s.save); r.addWidget(a); r.addWidget(b); L.addLayout(r)
    def refresh(s):
        for k, e in s.f.e.items():
            b = base_of(s.w.aid, k); e.setReadOnly(b is not None); e.setText("" if b is None else f"{b:g}"); e.setStyleSheet("color:#6c8;" if b is not None else "")
    def pdf(s):
        f, _ = QFileDialog.getOpenFileName(s, "Personal Health Report PDF", "", "PDF (*.pdf)")
        if not f: return
        try:
            from pypdf import PdfReader
            txt = " ".join((pg.extract_text() or "") for pg in PdfReader(f).pages)
        except Exception as ex: return QMessageBox.warning(s, "PDF error", f"Could not read PDF (pip install pypdf): {ex}")
        n = 0
        for k, e in s.f.e.items():
            if e.isReadOnly(): continue
            m = re.search(re.escape(PD[k][1]) + r"[^0-9\-]{0,40}(-?\d+(?:\.\d+)?)", txt, re.I)
            if m: e.setText(m.group(1)); n += 1
        QMessageBox.information(s, "Import", f"{n} values found. Review them, then press SAVE BASELINE.")
    def save(s):
        try: v = s.f.values()
        except ValueError as ex: return QMessageBox.warning(s, "Invalid", str(ex))
        new = {k: x for k, x in v.items() if base_of(s.w.aid, k) is None}
        if not new: return QMessageBox.information(s, "Nothing new", "No new baseline values to save.")
        if QMessageBox.question(s, "Confirm", f"Save {len(new)} baseline values permanently?") != QMessageBox.Yes: return
        con.executemany("INSERT OR IGNORE INTO baseline(aid,key,value,ts) VALUES(?,?,?,?)", [(s.w.aid, k, x, now()) for k, x in new.items()]); con.commit(); s.refresh()

class Ment(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; L = QVBoxLayout(s)
        L.addWidget(QLabel("<h2>DAILY PHYSICAL & MENTAL HEALTH QUESTIONNAIRE</h2><span style='color:#5de0e6'>Confidential \u2022 Screening only \u2022 Not a diagnosis. There are no wrong answers - answer as you feel today.</span>"))
        tb = QPushButton("SAVE & SUBMIT QUESTIONNAIRE  \u2192"); tb.setStyleSheet("background:#0f6b3a;border-color:#2ecc71;font-weight:bold;font-size:15px"); tb.clicked.connect(s.submit); L.addWidget(tb)
        s.c = []; g = QGridLayout(); host = QWidget(); host.setLayout(g)
        for i, (q, o) in enumerate(Q):
            c = QComboBox(); c.addItem("Select answer"); c.addItems(o); s.c.append(c); g.addWidget(QLabel(f"<b>{i + 1}</b>  {q}"), i, 0); g.addWidget(c, i, 1)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(host); L.addWidget(sc)
        s.n = QPlainTextEdit(); s.n.setPlaceholderText("Anything else you want to record? (optional, short text)"); s.n.setMaximumHeight(70); L.addWidget(s.n)
        r = QHBoxLayout(); s.o = QCheckBox("Overwrite today's answers"); b = QPushButton("SAVE & SUBMIT QUESTIONNAIRE  \u2192"); b.clicked.connect(s.submit); v = QPushButton("VIEW ENTERED ANSWERS"); v.clicked.connect(lambda: Values(w, 1).exec()); r.addWidget(v); r.addWidget(s.o); r.addWidget(b); L.addLayout(r)
    def submit(s):
        if any(c.currentIndex() == 0 for c in s.c): return QMessageBox.warning(s, "Incomplete", "Please answer all 15 questions.")
        a = [c.currentIndex() - 1 for c in s.c]; aid = s.w.aid
        if con.execute("SELECT 1 FROM mental WHERE aid=? AND day=?", (aid, today())).fetchone():
            if not s.o.isChecked(): return QMessageBox.information(s, "Already submitted", "Today's answers exist. Tick 'Overwrite today's answers' to replace them.")
            con.execute("DELETE FROM mental WHERE aid=? AND day=?", (aid, today())); con.execute("DELETE FROM readings WHERE aid=? AND day=? AND key='mind'", (aid, today()))
        idx = 100 * (1 - sum(a) / (4 * len(a))); t = now()
        con.execute("INSERT INTO mental(aid,ts,day,answers,note,idx) VALUES(?,?,?,?,?,?)", (aid, t, t[:10], json.dumps(a), s.n.toPlainText(), idx))
        con.execute("INSERT INTO readings(aid,ts,day,key,value) VALUES(?,?,?,?,?)", (aid, t, t[:10], "mind", idx)); con.commit()
        msg = "Thank you for checking in. Your answers are recorded." + ("\nYou reported some strong concerns - please consider a private conversation with the crew surgeon or flight psychologist. You are not alone." if max(a) >= 4 or idx < 50 else "")
        QMessageBox.information(s, "Submitted", f"{msg}\nWell-being index: {idx:.0f}%")
        for c in s.c: c.setCurrentIndex(0)
        s.n.clear(); s.o.setChecked(False); s.w.go(5)
    def refresh(s): pass

class Rep(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; L = QVBoxLayout(s); r = QHBoxLayout(); s.v = QWebEngineView()
        a = QPushButton("REFRESH ANALYSIS"); a.clicked.connect(s.refresh); b = QPushButton("EXPORT PRINTABLE PDF FOR GROUND DOCTORS"); b.clicked.connect(s.pdf); r.addWidget(a); r.addWidget(b); L.addLayout(r); L.addWidget(s.v)
    def refresh(s): show(s.v, report_html(s.w.aid, s.w.name()), "report")
    def pdf(s):
        if not analyse(s.w.aid)["rows"]: return QMessageBox.information(s, "No values entered", "No values entered - nothing to export.")
        f, _ = QFileDialog.getSaveFileName(s, "Save PDF", f"{s.w.name().replace(' ', '_')}_health_report.pdf", "PDF (*.pdf)")
        if f: export_pdf(s.w.aid, s.w.name(), f); QDesktopServices.openUrl(QUrl.fromLocalFile(f))

# ---------------------------------------------------------------- CycleCare: UI
CC_QSS = """
QLabel#ccBadge{background:#10224a;border:1px solid #2ecc71;border-radius:12px;padding:6px 12px;color:#9be7b5;font-weight:bold}
QLabel#ccHint{background:#2a1e6e;border:1px solid #a06bff;border-radius:10px;padding:10px;font-size:14px}
QFrame#ccCard{background:#10224a;border:1px solid #3b6fd1;border-radius:14px}
QFrame#ccAlert{background:#0f1d3d;border:1px solid #2b4a88;border-radius:12px}
QLabel#ccCardT{color:#8fb4ff;font-size:11px;font-weight:bold;background:transparent}
QLabel#ccCardV{font-size:20px;font-weight:bold;background:transparent}
QLabel#ccCardS{color:#8aa;font-size:11px;background:transparent}
QLabel#ccDate{background:transparent;padding:2px 0}
QTabWidget::pane{border:1px solid #2b4a88;border-radius:10px;top:-1px}
QTabBar::tab{background:#10224a;border:1px solid #2b4a88;padding:9px 16px;border-top-left-radius:8px;border-top-right-radius:8px;margin-right:2px}
QTabBar::tab:selected{background:#4a1a45;border-color:#ff7eb6;font-weight:bold}
QCheckBox{spacing:8px;background:transparent}
QCheckBox::indicator{width:16px;height:16px;border:1px solid #3b6fd1;border-radius:4px;background:#0f1d3d}
QCheckBox::indicator:checked{background:#ff7eb6;border-color:#ff7eb6}
QSpinBox,QDateEdit{background:#0f1d3d;border:1px solid #2b4a88;border-radius:6px;padding:5px}
QPushButton#ccPrimary{background:#0f6b3a;border-color:#2ecc71;font-weight:bold}
QPushButton#ccRose{background:#7a1f4f;border-color:#ff7eb6;font-weight:bold}
QPushButton#ccDanger{background:#4a1a1a;border-color:#e74c3c}
QPushButton:disabled{background:#0f1830;border-color:#20335a;color:#5a6a8a}
QTableWidget{background:#0f1d3d;gridline-color:#20335a;border:1px solid #2b4a88}
QCalendarWidget QToolButton{color:#e6eefc;background:#10224a;border:1px solid #2b4a88;border-radius:6px;padding:4px 10px}
QCalendarWidget QSpinBox{background:#10224a;color:#e6eefc}
QCalendarWidget QMenu{background:#10224a;color:#e6eefc}
QCalendarWidget QWidget#qt_calendar_navigationbar{background:#10224a}
QCalendarWidget QAbstractItemView:enabled{background:#0f1d3d;color:#e6eefc;selection-background-color:#2b7bff;selection-color:#ffffff;outline:0}
QCalendarWidget QAbstractItemView:disabled{color:#5a6a8a}
"""
CC_RANGES = [("Last 30 days", 30), ("Last 90 days", 90), ("Last 180 days", 180), ("All records", None)]
CC_SRC = {"tst": "Physical Health data (Actiwatch / crew wearable)", "se": "Physical Health data (Actiwatch / crew wearable)", "hr": "Physical Health data (Holter ECG)",
          "mind": "Mental Health questionnaire (well-being index)", "fatigue": "Daily questionnaire: unusual fatigue + mental fatigue (averaged)", "stress": "Daily questionnaire: unusual stress",
          "work": "CycleCare daily log (the monitor has no separate workload table)", "ex": "CycleCare daily log (the monitor has no separate exercise log)"}
CC_LV = {"ok": ("#2ecc71", "ALL CLEAR"), "attention": ("#e74c3c", "ATTENTION"), "notice": ("#f39c12", "NOTICE"), "info": ("#6cb6ff", "INFO")}

def cc_qd(q): return dt.date(q.year(), q.month(), q.day())
def cc_since(days): return dt.date.today() - dt.timedelta(days=days) if days else dt.date(2000, 1, 1)

def cc_fmt(bg=None, fg=None, bold=False, under=False, tip=""):
    f = QTextCharFormat()
    if bg: f.setBackground(QBrush(QColor(bg)))
    if fg: f.setForeground(QBrush(QColor(fg)))
    if bold: f.setFontWeight(700)
    if under: f.setFontUnderline(True)
    if tip: f.setToolTip(tip)
    return f

def cc_safe(fn):
    """Slots never crash the app: bad input -> friendly warning, database problem -> rollback + warning."""
    @functools.wraps(fn)
    def wrap(s, *a, **k):
        try: return fn(s, *a, **k)
        except ValueError as ex: QMessageBox.warning(s, "CycleCare", str(ex))
        except sqlite3.Error as ex:
            try: con.rollback()
            except sqlite3.Error: pass
            QMessageBox.warning(s, "CycleCare - database error", f"Your data could not be read or saved safely. Nothing was changed.\n\n{ex}")
    return wrap

class Cycle(QWidget):
    """CycleCare page (page index 6 in Win). Shown only for female astronauts; all queries are filtered by the selected astronaut's id (w.aid)."""
    def __init__(s, w):
        super().__init__(); s.w = w; s.D = None; s.sel = dt.date.today(); s.setStyleSheet(CC_QSS); L = QVBoxLayout(s)
        h = QHBoxLayout(); t = QLabel("<span style='font-size:26px;font-weight:bold;color:#ff7eb6'>CycleCare</span>&nbsp;&nbsp;<span style='font-size:15px;color:#5de0e6'>Menstrual &amp; Reproductive Health</span>")
        s.badge = QLabel("\U0001F512  Private - visible only to this astronaut"); s.badge.setObjectName("ccBadge"); h.addWidget(t, 1); h.addWidget(s.badge); L.addLayout(h)
        s.hint = QLabel(); s.hint.setObjectName("ccHint"); s.hint.setWordWrap(True); s.hint.hide(); L.addWidget(s.hint)
        cr = QHBoxLayout(); s.cards = {}
        for k, ti, col in (("day", "CYCLE DAY", "#ff7eb6"), ("next", "NEXT PERIOD (ESTIMATE)", "#a06bff"), ("avg", "AVERAGE CYCLE", "#6cb6ff"), ("plen", "AVERAGE PERIOD", "#5de0e6"), ("reg", "REGULARITY", "#2ecc71"), ("al", "HEALTH ALERTS", "#f39c12")):
            cr.addWidget(s.card(k, ti, col))
        L.addLayout(cr)
        s.tabs = QTabWidget(); L.addWidget(s.tabs, 1)
        for fn, ti in ((s.tab_cal, "Calendar & Daily Log"), (s.tab_hist, "Cycle History & Baseline"), (s.tab_ins, "Insights"), (s.tab_cor, "Correlations"), (s.tab_alerts, "Health Alerts"), (s.tab_priv, "Reports & Privacy")):
            s.tabs.addTab(fn(), ti)
        s.tabs.currentChanged.connect(lambda _: s.render())

    def card(s, key, title, color):
        f = QFrame(); f.setObjectName("ccCard"); f.setStyleSheet(f"QFrame#ccCard{{border-left:5px solid {color}}}"); v = QVBoxLayout(f); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(2)
        a, b, c = QLabel(title), QLabel("-"), QLabel(""); a.setObjectName("ccCardT"); b.setObjectName("ccCardV"); c.setObjectName("ccCardS"); c.setWordWrap(True)
        for x in (a, b, c): v.addWidget(x)
        s.cards[key] = (b, c); return f

    def status(s, m): s.w.statusBar().showMessage(m, 6000)

    # ------------------------------------------------ tab builders
    def tab_cal(s):
        w = QWidget(); h = QHBoxLayout(w); lv = QVBoxLayout(); s.cal = QCalendarWidget(); s.cal.setGridVisible(True); s.cal.setMinimumSize(480, 300)
        s.cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader); s.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        for d in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday): s.cal.setWeekdayTextFormat(d, cc_fmt(fg="#e6eefc"))
        s.cal.selectionChanged.connect(s.pick)
        lg = QLabel("<span style='color:#b5306f'>&#9632;</span> Period &nbsp; <span style='color:#ff5fa8'>&#9632;</span> Flow logged &nbsp; <span style='color:#6a4bd8'>&#9632;</span> Estimated next period (darker = most likely day) &nbsp; <u>Underline</u> = symptoms logged")
        lg.setWordWrap(True); lv.addWidget(s.cal, 1); lv.addWidget(lg); h.addLayout(lv, 3)
        sc = QScrollArea(); sc.setWidgetResizable(True); fw = QWidget(); sc.setWidget(fw); fl = QVBoxLayout(fw)
        s.dl = QLabel(); s.dl.setObjectName("ccDate"); fl.addWidget(s.dl)
        qb = QHBoxLayout(); b1 = QPushButton("Period STARTED this day"); b1.setObjectName("ccRose"); b1.clicked.connect(lambda: s.mark_start())
        b2 = QPushButton("Period ENDED this day"); b2.clicked.connect(lambda: s.mark_end()); qb.addWidget(b1); qb.addWidget(b2); fl.addLayout(qb)
        gb = QGroupBox("Daily log"); g = QGridLayout(gb)
        s.flow, s.sev, s.mood, s.work = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        s.flow.addItems(CC_FLOW); s.sev.addItems(CC_SEV); s.mood.addItems(["Not set"] + MOOD); s.work.addItems(["Not set"] + CC_WORK)
        s.ex = QSpinBox(); s.ex.setRange(-1, 600); s.ex.setValue(-1); s.ex.setSpecialValueText("Not set"); s.ex.setSuffix(" min")
        for r, (lab, wd) in enumerate((("Menstrual flow", s.flow), ("Overall symptom severity", s.sev), ("Mood", s.mood), ("Workload today", s.work), ("Exercise today", s.ex))):
            g.addWidget(QLabel(lab), r, 0); g.addWidget(wd, r, 1)
        g.addWidget(QLabel("Symptoms"), 5, 0, 1, 2); s.sy = {}
        for i, k in enumerate(CC_SYMPTOMS): c = QCheckBox(k); s.sy[k] = c; g.addWidget(c, 6 + i // 2, i % 2)
        s.note = QPlainTextEdit(); s.note.setPlaceholderText("Private note (optional, max 300 characters)"); s.note.setMaximumHeight(64); g.addWidget(s.note, 6 + (len(CC_SYMPTOMS) + 1) // 2, 0, 1, 2); fl.addWidget(gb)
        sb = QPushButton("SAVE DAY LOG"); sb.setObjectName("ccPrimary"); sb.clicked.connect(lambda: s.save_day())
        cb = QPushButton("Clear this day"); cb.setObjectName("ccDanger"); cb.clicked.connect(lambda: s.clear_day())
        r = QHBoxLayout(); r.addWidget(sb, 2); r.addWidget(cb, 1); fl.addLayout(r); fl.addStretch(1); h.addWidget(sc, 2); return w

    def tab_hist(s):
        w = QWidget(); L = QVBoxLayout(w); gb = QGroupBox("Add or back-fill a period"); g = QHBoxLayout(gb)
        s.hs, s.he = QDateEdit(), QDateEdit()
        for e in (s.hs, s.he): e.setCalendarPopup(True); e.setDisplayFormat("dd MMM yyyy"); e.setMaximumDate(QDate.currentDate()); e.setDate(QDate.currentDate())
        s.hc = QCheckBox("Period has ended"); s.hc.setChecked(True); s.hc.toggled.connect(s.he.setEnabled)
        ab = QPushButton("Add period"); ab.setObjectName("ccPrimary"); ab.clicked.connect(lambda: s.add_hist())
        for x in (QLabel("Start"), s.hs, s.hc, QLabel("End"), s.he): g.addWidget(x)
        g.addWidget(ab, 1); L.addWidget(gb)
        s.ht = QTableWidget(0, 6); s.ht.setHorizontalHeaderLabels(["Period start", "Period end", "Period days", "Cycle length", "vs baseline", "Pattern"]); s.ht.horizontalHeader().setStretchLastSection(True)
        s.ht.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); s.ht.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); L.addWidget(s.ht, 1)
        db = QPushButton("Delete selected period"); db.setObjectName("ccDanger"); db.clicked.connect(lambda: s.del_hist()); L.addWidget(db)
        gb2 = QGroupBox("Personal baseline"); g2 = QGridLayout(gb2)
        s.bl = QLabel(); s.bl.setWordWrap(True); s.uc, s.up = QSpinBox(), QSpinBox()
        s.uc.setRange(0, 90); s.uc.setSpecialValueText("Unknown"); s.uc.setSuffix(" days"); s.up.setRange(0, 15); s.up.setSpecialValueText("Unknown"); s.up.setSuffix(" days")
        sv = QPushButton("Save usual values"); sv.clicked.connect(lambda: s.save_usual())
        g2.addWidget(s.bl, 0, 0, 1, 4); g2.addWidget(QLabel("Usual cycle length"), 1, 0); g2.addWidget(s.uc, 1, 1); g2.addWidget(QLabel("Usual period length"), 1, 2); g2.addWidget(s.up, 1, 3); g2.addWidget(sv, 2, 0, 1, 4); L.addWidget(gb2)
        return w

    def tab_ins(s):
        w = QWidget(); L = QVBoxLayout(w); r = QHBoxLayout(); s.ir = QComboBox()
        for t, d in CC_RANGES: s.ir.addItem(t, d)
        s.ir.setCurrentIndex(1); s.ir.currentIndexChanged.connect(lambda _: s.render()); r.addWidget(QLabel("Show:")); r.addWidget(s.ir); r.addStretch(1); L.addLayout(r)
        s.v1 = QWebEngineView(); s.v1.setMinimumHeight(280); L.addWidget(s.v1, 1); return w

    def tab_cor(s):
        w = QWidget(); L = QVBoxLayout(w); r = QHBoxLayout(); s.mc, s.cr = QComboBox(), QComboBox()
        for k, lab, _ in CC_METRICS: s.mc.addItem(lab, k)
        for t, d in CC_RANGES: s.cr.addItem(t, d)
        s.cr.setCurrentIndex(1)
        for x in (QLabel("Compare symptoms with:"), s.mc, QLabel("Period:"), s.cr): r.addWidget(x)
        r.addStretch(1); L.addLayout(r)
        s.cs = QLabel(); s.cs.setWordWrap(True); s.cs.setObjectName("ccHint"); L.addWidget(s.cs)
        s.ct = QTableWidget(0, 4); s.ct.setHorizontalHeaderLabels(["Measure (source: existing monitor data)", "Days compared", "r", "Association"]); s.ct.horizontalHeader().setStretchLastSection(True)
        s.ct.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); s.ct.setMaximumHeight(190); L.addWidget(s.ct)
        s.v2 = QWebEngineView(); s.v2.setMinimumHeight(260); L.addWidget(s.v2, 1)
        s.mc.currentIndexChanged.connect(lambda _: s.render()); s.cr.currentIndexChanged.connect(lambda _: s.render()); return w

    def tab_alerts(s):
        w = QWidget(); L = QVBoxLayout(w)
        L.addWidget(QLabel("<span style='color:#5de0e6'>Alerts are observations from your own records compared with your personal baseline. They are <b>not a diagnosis</b> - talk to the flight surgeon about any concern.</span>"))
        sc = QScrollArea(); sc.setWidgetResizable(True); host = QWidget(); s.al_l = QVBoxLayout(host); sc.setWidget(host); L.addWidget(sc, 1); return w

    def tab_priv(s):
        w = QWidget(); L = QVBoxLayout(w); gb = QGroupBox("Privacy and authorised medical access"); g = QVBoxLayout(gb)
        g.addWidget(QLabel("<ul><li>CycleCare data is stored only in this application's local database, linked to your astronaut ID. No internet connection is used.</li>"
                           "<li>It is <b>never</b> included in the general Physical &amp; Mental Health report or its PDF.</li>"
                           "<li>Exporting any CycleCare PDF requires your password and is written to the access log below.</li>"
                           "<li>Medical staff only receive a CycleCare summary if <b>you</b> consent. You can withdraw consent at any time.</li></ul>"))
        s.share = QCheckBox("I consent to share my CycleCare summary with authorised medical staff (flight surgeon)"); s.share.clicked.connect(lambda on: s.toggle_share(on)); g.addWidget(s.share); L.addWidget(gb)
        r = QHBoxLayout(); b1 = QPushButton("EXPORT MY FULL REPORT (PDF)"); b1.setObjectName("ccPrimary"); b1.clicked.connect(lambda: s.export(False))
        s.b2 = QPushButton("EXPORT SUMMARY FOR AUTHORISED MEDICAL STAFF (PDF)"); s.b2.clicked.connect(lambda: s.export(True))
        b3 = QPushButton("Delete all my CycleCare data"); b3.setObjectName("ccDanger"); b3.clicked.connect(lambda: s.wipe())
        for x in (b1, s.b2, b3): r.addWidget(x)
        L.addLayout(r); L.addWidget(QLabel("<b>Access log</b> (exports, consent changes, deletions)"))
        s.at = QTableWidget(0, 2); s.at.setHorizontalHeaderLabels(["Date / time", "Action"]); s.at.horizontalHeader().setStretchLastSection(True); s.at.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); L.addWidget(s.at, 1); return w

    # ------------------------------------------------ lifecycle
    def reset(s):
        """Called on login/logout so nothing from the previous astronaut stays on screen."""
        s.D = None; s.sel = dt.date.today(); s.tabs.setCurrentIndex(0); s.hint.hide()
        for v in (s.v1, s.v2): v.setHtml("")
        s.cal.setDateTextFormat(QDate(), QTextCharFormat()); s.cal.setSelectedDate(QDate.currentDate()); s.ht.setRowCount(0); s.ct.setRowCount(0); s.at.setRowCount(0)
        for k, (a, b) in s.cards.items(): a.setText("-"); b.setText("")
        s.note.clear(); s.share.setChecked(False)

    @cc_safe
    def refresh(s):
        if not s.w.aid: return
        if not CC_OK:
            s.hint.setText(f"CycleCare could not prepare its database tables: {xesc(CC_ERR)}. The rest of the application is not affected."); s.hint.show(); s.tabs.setEnabled(False); return
        aid = s.w.aid; st = cc_stats(aid); logs = cc_logs(aid); ms = cc_metric_series(aid, logs)
        s.D = dict(st=st, logs=logs, ms=ms, al=cc_alerts(aid, st, logs, ms))
        s.paint_cards(); s.paint_cal(); s.paint_hist(); s.paint_alerts(); s.paint_priv(); s.pick(); s.render()

    def paint_cards(s):
        st, al, t = s.D["st"], s.D["al"], dt.date.today(); C = s.cards
        if not st["cycles"]:
            s.hint.setText("<b>Welcome to CycleCare.</b> Select the first day of your most recent period on the calendar and press <b>Period STARTED this day</b>. "
                           "Earlier periods can be added in <b>Cycle History</b> - three cycles unlock your personal baseline and predictions."); s.hint.show()
        else: s.hint.hide()
        day = st["day"]
        C["day"][0].setText(f"Day {day}" if day and day <= 120 else "-")
        C["day"][1].setText("Period in progress" if st["on_period"] else (f"{st['phase']} phase (estimate)" if st["phase"] else "No recent period recorded"))
        if st["next"]:
            dd = (st["next"] - t).days
            C["next"][0].setText(f"{st['next']:%d %b}"); C["next"][1].setText(f"in {dd} days (+/-{st['win']})" if dd > 0 else ("expected today" if dd == 0 else f"{-dd} days past estimate"))
        else: C["next"][0].setText("-"); C["next"][1].setText("Needs 1+ completed cycle or your usual length")
        C["avg"][0].setText("-" if not st["avg"] else f"{st['avg']:.0f} days"); C["avg"][1].setText(f"{st['n_len']} cycle(s) recorded" + ("" if st["sd"] is None else f", SD {st['sd']:.1f}"))
        C["plen"][0].setText("-" if not st["aplen"] else f"{st['aplen']:.0f} days"); C["plen"][1].setText("From periods with an end date")
        C["reg"][0].setText(st["reg"]); C["reg"][1].setText("Needs 4+ recorded periods" if st["sd"] is None or st["n_len"] < 3 else f"Cycle length SD {st['sd']:.1f} days")
        n = sum(1 for a in al if a["level"] != "info"); C["al"][0].setText(str(n) if n else "All clear"); C["al"][1].setText("worth a look" if n else (f"{len(al)} info note(s)" if al else "No alerts"))

    def paint_cal(s):
        st, logs, t, one = s.D["st"], s.D["logs"], dt.date.today(), dt.timedelta(days=1); cell = {}
        def mark(day, tip=None, **k):
            c = cell.setdefault(day, dict(bg=None, fg=None, bold=False, under=False, tip=[])); c.update(k)
            if tip: c["tip"].append(tip)
        for c in st["cycles"]:
            e, d = c["end"] or min(t, c["start"] + dt.timedelta(days=int(round(st["aplen"] or 5)) - 1)), c["start"]
            while d <= e: mark(d, "Period", bg="#b5306f", fg="#ffffff"); d += one
        if st["next"]:
            for k in (0, 1):
                ctr = st["next"] + dt.timedelta(days=int(round(st["avg"])) * k)
                for off in range(-st["win"], st["win"] + 1):
                    d = ctr + dt.timedelta(days=off)
                    if d >= t and not (cell.get(d) and cell[d]["bg"]): mark(d, "Estimated next period" if off == 0 else "Possible start window", bg="#6a4bd8" if off == 0 else "#33276e", fg="#ffffff", bold=off == 0)
        for d, l in logs.items():
            tip = f"Flow: {CC_FLOW[l['flow']]}" + (f"; Symptoms: {', '.join(l['sym'])}" if l["sym"] else "") + (f"; Severity: {CC_SEV[l['sev']]}" if l["sev"] else "")
            if l["flow"] > 0: mark(d, tip, bg=CC_FLOWCOL[l["flow"]], fg="#111111" if l["flow"] < 3 else "#ffffff")
            else: mark(d, tip)
            if l["sym"]: mark(d, under=True)
        mark(t, "Today", bold=True)
        if not cell[t]["bg"]: cell[t]["fg"] = "#5de0e6"
        s.cal.setDateTextFormat(QDate(), QTextCharFormat())
        for d, c in cell.items(): s.cal.setDateTextFormat(QDate(d.year, d.month, d.day), cc_fmt(c["bg"], c["fg"], c["bold"], c["under"], "\n".join(c["tip"])))

    def paint_hist(s):
        st = s.D["st"]; dv = {x["start"]: x for x in st["dev"]}; rows = st["cycles"][::-1]; s.ht.setRowCount(len(rows))
        for i, c in enumerate(rows):
            x = dv.get(c["start"])
            vals = [f"{c['start']:%d %b %Y}", f"{c['end']:%d %b %Y}" if c["end"] else "not set", "-" if not c["plen"] else str(c["plen"]), "-" if not x else f"{x['n']} days",
                    "-" if not x or x["dev"] is None else f"{x['dev']:+.0f} days", "Outside your baseline" if x and x["flag"] else ("Within your baseline" if x and x["dev"] is not None else "-")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 5 and x and x["flag"]: it.setForeground(QBrush(QColor("#ff8a80")))
                s.ht.setItem(i, j, it)
            s.ht.item(i, 0).setData(Qt.ItemDataRole.UserRole, c["id"])
        s.ht.resizeColumnsToContents(); cs = st["settings"]
        s.uc.setValue(int(cs["cycle"] or 0)); s.up.setValue(int(cs["period"] or 0))
        s.bl.setText("Your baseline is learned from your own history once <b>3 cycle lengths</b> are recorded; until then the usual values below are used. "
                     + ("Current baseline: <b>%.0f days</b>, deviations flagged beyond +/-%.0f days." % (st["bmean"], st["bthr"]) if st["bmean"] is not None else "No baseline yet."))

    def paint_alerts(s):
        while s.al_l.count():
            it = s.al_l.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        al = s.D["al"]
        if not al: al = [dict(level="ok", title="No alerts", text="Nothing unusual compared with your personal baseline. Keep logging to improve the accuracy of your predictions.", step="")]
        for a in al:
            col, lab = CC_LV[a["level"]]; f = QFrame(); f.setObjectName("ccAlert"); f.setStyleSheet(f"QFrame#ccAlert{{border-left:6px solid {col}}}"); v = QVBoxLayout(f)
            l = QLabel(f"<b style='color:{col}'>{lab}</b>&nbsp; <b style='font-size:15px'>{xesc(a['title'])}</b><br>{xesc(a['text'])}" + (f"<br><i style='color:#8aa'>Suggested step: {xesc(a['step'])}</i>" if a["step"] else ""))
            l.setWordWrap(True); v.addWidget(l); s.al_l.addWidget(f)
        s.al_l.addStretch(1)

    def paint_priv(s):
        aid = s.w.aid; sh = cc_settings(aid)["share"]; s.share.blockSignals(True); s.share.setChecked(sh); s.share.blockSignals(False); s.b2.setEnabled(sh)
        rows = list(con.execute("SELECT ts,action FROM cc_audit WHERE aid=? ORDER BY id DESC LIMIT 50", (aid,))); s.at.setRowCount(len(rows))
        for i, r in enumerate(rows): s.at.setItem(i, 0, QTableWidgetItem(r["ts"])); s.at.setItem(i, 1, QTableWidgetItem(r["action"]))
        s.at.resizeColumnsToContents()

    @cc_safe
    def render(s):
        """Plotly pages are only drawn when their tab is visible, which keeps the app light."""
        if not s.D: return
        d, i = s.D, s.tabs.currentIndex(); st, logs, ms = d["st"], d["logs"], d["ms"]
        if i == 2:
            since = cc_since(s.ir.currentData())
            show(s.v1, cc_html([cc_fig_cycles(st), cc_fig_flow(logs, since), cc_fig_symptoms(logs, since), cc_fig_cycle_day(logs, st["phases"], since)], "Record a few periods and daily logs to see your charts"), "cc_ins")
        elif i == 3:
            since, key = cc_since(s.cr.currentData()), s.mc.currentData(); rows = cc_assoc(ms, logs, st["phases"], since); a = next(x for x in rows if x["key"] == key)
            s.ct.setRowCount(len(rows))
            for n, x in enumerate(rows):
                for j, v in enumerate((x["label"], str(x["n"]), "-" if x["r"] is None else f"{x['r']:+.2f}", cc_strength(x["r"], x["n"]))): s.ct.setItem(n, j, QTableWidgetItem(v))
            s.ct.resizeColumnsToContents()
            nm = len(ms[key]); nl = len(logs)
            summ = (f"{a['n']} day(s) have both this measure and a CycleCare log; association with symptom count: <b>{cc_strength(a['r'], a['n'])}</b>" + ("" if a["r"] is None else f" (r = {a['r']:+.2f})") + ".")
            s.cs.setText(f"<b>{xesc(a['label'])}</b> - source: {xesc(CC_SRC[key])}.<br>{summ}<br><i>{nm} day(s) of this measure and {nl} day(s) of CycleCare logs found. Associations are not proof of cause.</i>")
            show(s.v2, cc_html([cc_fig_timeline(key, a["label"], a["unit"], ms, logs, st, since), cc_fig_phase(a), cc_fig_assoc(rows)], "Not enough data yet - keep logging sleep, questionnaires and CycleCare daily logs"), "cc_cor")

    # ------------------------------------------------ calendar / form actions
    def pick(s):
        s.sel = cc_qd(s.cal.selectedDate()); l = ((s.D or {}).get("logs") or {}).get(s.sel)
        s.dl.setText(f"<b style='font-size:17px'>{s.sel:%A, %d %B %Y}</b>" + ("" if s.sel <= dt.date.today() else " <span style='color:#f39c12'>(future date - cannot be logged)</span>"))
        s.flow.setCurrentIndex(l["flow"] if l else 0); s.sev.setCurrentIndex(l["sev"] if l else 0)
        s.mood.setCurrentIndex(0 if not l or l["mood"] is None else l["mood"] + 1); s.work.setCurrentIndex(0 if not l or l["work"] is None else l["work"] + 1)
        s.ex.setValue(-1 if not l or l["ex"] is None else int(l["ex"])); s.note.setPlainText(l["note"] if l else "")
        for k, c in s.sy.items(): c.setChecked(bool(l) and k in l["sym"])

    @cc_safe
    def save_day(s):
        if not s.w.aid: return
        e = s.ex.value()
        cc_save_day(s.w.aid, s.sel, s.flow.currentIndex(), [k for k, c in s.sy.items() if c.isChecked()], s.sev.currentIndex(), None if s.mood.currentIndex() == 0 else s.mood.currentIndex() - 1,
                    None if s.work.currentIndex() == 0 else s.work.currentIndex() - 1, None if e < 0 else e, s.note.toPlainText())
        s.status(f"Saved log for {s.sel:%d %b %Y}"); s.refresh()

    @cc_safe
    def clear_day(s):
        if not s.w.aid: return
        if QMessageBox.question(s, "Clear day", f"Remove the log for {s.sel:%d %b %Y}?") != QMessageBox.StandardButton.Yes: return
        con.execute("DELETE FROM cc_daily WHERE aid=? AND day=?", (s.w.aid, s.sel.isoformat())); con.commit(); s.refresh()

    @cc_safe
    def mark_start(s):
        if not s.w.aid: return
        cc_add_period(s.w.aid, s.sel); s.status(f"Period start recorded: {s.sel:%d %b %Y}"); s.refresh()

    @cc_safe
    def mark_end(s):
        if not s.w.aid: return
        cc_set_end(s.w.aid, s.sel); s.status(f"Period end recorded: {s.sel:%d %b %Y}"); s.refresh()

    @cc_safe
    def add_hist(s):
        if not s.w.aid: return
        cc_add_period(s.w.aid, cc_qd(s.hs.date()), cc_qd(s.he.date()) if s.hc.isChecked() else None); s.status("Period added"); s.refresh()

    @cc_safe
    def del_hist(s):
        r = s.ht.currentRow()
        if r < 0: return QMessageBox.information(s, "Select", "Select a period in the table first.")
        if QMessageBox.question(s, "Delete period", "Delete this period? Averages and predictions will be recalculated.") != QMessageBox.StandardButton.Yes: return
        con.execute("DELETE FROM cc_cycles WHERE id=? AND aid=?", (s.ht.item(r, 0).data(Qt.ItemDataRole.UserRole), s.w.aid)); con.commit(); s.refresh()

    @cc_safe
    def save_usual(s):
        c, p = s.uc.value(), s.up.value()
        if 0 < c < 15: raise ValueError("A usual cycle length should be between 15 and 90 days (or Unknown).")
        cc_save_settings(s.w.aid, cycle=float(c) if c else None, period=float(p) if p else None); s.status("Personal baseline values saved"); s.refresh()

    # ------------------------------------------------ privacy, consent, reports
    def auth(s, why):
        pw, ok = QInputDialog.getText(s, "Confirm your identity", f"Enter your password to {why}:", QLineEdit.EchoMode.Password)
        if not ok: return False
        if not cc_verify(s.w.aid, pw): QMessageBox.warning(s, "Not authorised", "Incorrect password."); return False
        return True

    @cc_safe
    def toggle_share(s, on):
        aid = s.w.aid
        msg = ("Allow authorised medical staff (flight surgeon) to receive your CycleCare summary? Only a summary is shared (no free-text notes or mood). You can withdraw consent at any time."
               if on else "Withdraw consent? Medical-staff summaries can no longer be exported.")
        if QMessageBox.question(s, "Consent", msg) != QMessageBox.StandardButton.Yes: s.share.setChecked(not on); return
        if not s.auth("change your sharing consent"): s.share.setChecked(not on); return
        cc_save_settings(aid, share=on); cc_audit(aid, "Consent to share summary with authorised medical staff " + ("GRANTED" if on else "WITHDRAWN")); s.paint_priv()

    @cc_safe
    def export(s, medical):
        aid = s.w.aid
        if not cc_cycle_rows(aid) and not cc_logs(aid): return QMessageBox.information(s, "Nothing to export", "No CycleCare data has been recorded yet.")
        if medical and not cc_settings(aid)["share"]: return QMessageBox.information(s, "Consent required", "Tick the consent box first to allow sharing with authorised medical staff.")
        if not s.auth("export your CycleCare data"): return
        f, _ = QFileDialog.getSaveFileName(s, "Save CycleCare report", f"{s.w.name().replace(' ', '_')}_cyclecare_{'medical' if medical else 'personal'}.pdf", "PDF (*.pdf)")
        if not f: return
        if not f.lower().endswith(".pdf"): f += ".pdf"
        try: cc_export_pdf(aid, s.w.name(), f, medical)
        except Exception as ex: return QMessageBox.warning(s, "PDF error", f"The report could not be created:\n{ex}")
        cc_audit(aid, "PDF exported for authorised medical staff (consent on file)" if medical else "PDF exported (personal copy)"); s.paint_priv(); QDesktopServices.openUrl(QUrl.fromLocalFile(f))

    @cc_safe
    def wipe(s):
        aid = s.w.aid
        if QMessageBox.question(s, "Delete CycleCare data", "Permanently delete ALL CycleCare periods, daily logs and settings for this astronaut? This cannot be undone.") != QMessageBox.StandardButton.Yes: return
        if not s.auth("delete your CycleCare data"): return
        for q in ("DELETE FROM cc_cycles WHERE aid=?", "DELETE FROM cc_daily WHERE aid=?", "DELETE FROM cc_settings WHERE aid=?"): con.execute(q, (aid,))
        con.commit(); cc_audit(aid, "All CycleCare data deleted"); s.status("All CycleCare data deleted"); s.refresh()

SPACE = """QWidget{background:transparent;color:#e6eefc}
#bg{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #050b1c,stop:.55 #0a1f4d,stop:1 #1a5fd0)}
#hdr{background:#050c1e;border-bottom:2px solid #1e90ff}
#card{background:#08205a;border:2px solid #1e90ff;border-radius:28px}
#fld{background:#0c2a66;border:2px solid #2b7bff;border-radius:24px}
#fld QLineEdit,#fld QComboBox{background:transparent;border:0;font-size:16px;padding:8px}
#fld QToolButton,#fld QLabel{border:0;background:transparent}
QComboBox QAbstractItemView{background:#0c2a66;selection-background-color:#2b7bff}
QPushButton#main{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1e7bff,stop:1 #0a4fd0);border:2px solid #5ab0ff;border-radius:24px;font-size:20px;font-weight:bold;padding:12px}
QPushButton#alt{background:transparent;border:2px solid #2b7bff;border-radius:22px;font-size:16px;font-weight:bold;padding:10px}
QPushButton#alt:hover,QPushButton#main:hover{border-color:#fff}"""

def fld(icon, ph, pw=False, combo=None):
    f = QFrame(); f.setObjectName("fld"); f.setMinimumHeight(56); h = QHBoxLayout(f); h.setContentsMargins(16, 2, 16, 2)
    i = QLabel(icon); i.setStyleSheet("font-size:22px"); h.addWidget(i)
    if combo: e = QComboBox(); e.addItems(combo)
    else: e = QLineEdit(); e.setPlaceholderText(ph)
    h.addWidget(e, 1)
    if pw:
        e.setEchoMode(QLineEdit.Password); t = QToolButton(); t.setText("\U0001F441"); t.setCheckable(True)
        t.toggled.connect(lambda on, e=e: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)); h.addWidget(t)
    return f, e

class SpacePage(QWidget):
    def __init__(s, w):
        super().__init__(); s.w = w; s.setObjectName("bg"); s.setStyleSheet(SPACE); L = QVBoxLayout(s); L.setContentsMargins(0, 0, 0, 0)
        hd = QWidget(); hd.setObjectName("hdr"); h = QHBoxLayout(hd); h.setContentsMargins(24, 10, 24, 10)
        logo = QLabel("NASA"); logo.setAlignment(Qt.AlignCenter); logo.setFixedSize(76, 76); logo.setStyleSheet("background:#1747c8;border-radius:38px;font-weight:bold;font-style:italic;font-size:18px;color:#fff")
        t = QLabel("<span style='font-size:28px;font-weight:bold'>Astronaut Health Monitoring System</span><br><i style='color:#5de0e6;font-size:16px'>Monitor \u2022 Analyze \u2022 Support</i>")
        sat = QLabel("\U0001F6F0"); sat.setStyleSheet("font-size:44px"); h.addWidget(logo); h.addWidget(t, 1); h.addWidget(sat); L.addWidget(hd)
        s.card = QFrame(); s.card.setObjectName("card"); s.card.setFixedWidth(640); s.cl = QVBoxLayout(s.card); s.cl.setContentsMargins(40, 30, 40, 30); s.cl.setSpacing(14)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(s.card); row.addStretch(); L.addStretch(1); L.addLayout(row); L.addStretch(1)
    def msg(s, kind, t, m): getattr(QMessageBox, kind)(s.w, t, m)

class Login(SpacePage):
    def __init__(s, w):
        super().__init__(w); top = QHBoxLayout(); av = QLabel("\U0001F9D1\u200D\U0001F680"); av.setAlignment(Qt.AlignCenter); av.setFixedSize(130, 130)
        av.setStyleSheet("font-size:60px;border:3px solid #1e90ff;border-radius:65px;background:#0c2a66")
        top.addWidget(av); top.addWidget(QLabel("<span style='font-size:44px;font-weight:bold'>Login</span><br><i style='color:#5de0e6;font-size:18px'>Access your astronaut dashboard</i>"), 1); s.cl.addLayout(top)
        f1, s.id = fld("\U0001F468\u200D\U0001F680", "Astronaut ID"); f2, s.pw = fld("\U0001F512", "Password", pw=True)
        s.id.returnPressed.connect(s.pw.setFocus); s.pw.returnPressed.connect(s.go)
        b = QPushButton("\u21AA  Login"); b.setObjectName("main"); b.clicked.connect(s.go)
        o = QLabel("\u2014\u2014\u2014\u2014  or  \u2014\u2014\u2014\u2014"); o.setAlignment(Qt.AlignCenter)
        n = QPushButton("\u2795  Add New Astronaut"); n.setObjectName("alt"); n.clicked.connect(lambda: w.root.setCurrentIndex(1))
        for x in (f1, f2, b, o, n): s.cl.addWidget(x)
    def go(s):
        r = con.execute("SELECT * FROM astronauts WHERE code=? COLLATE NOCASE", (s.id.text().strip(),)).fetchone()
        if not r or not r["pw"] or not hmac.compare_digest(hpw(s.pw.text(), r["salt"]), r["pw"]): return s.msg("warning", "Login failed", "Invalid Astronaut ID or password.")
        s.pw.clear(); s.w.enter(r["id"], r["name"], r["code"])

class Register(SpacePage):
    def __init__(s, w):
        super().__init__(w); s.cl.addWidget(QLabel("<span style='font-size:36px;font-weight:bold'>Add New Astronaut</span><br><i style='color:#5de0e6;font-size:16px'>Create a private health profile</i>"))
        f1, s.nm = fld("\U0001F464", "Astronaut full name"); f2, s.code = fld("\U0001F194", "Astronaut ID (e.g. AST-001)")
        f3, s.g = fld("\u26A5", "", combo=["Select gender", "Male", "Female", "Other", "Prefer not to say"])
        f4, s.p1 = fld("\U0001F512", "Password (min 6 characters)", pw=True); f5, s.p2 = fld("\U0001F512", "Confirm password", pw=True)
        b = QPushButton("Create Astronaut Account"); b.setObjectName("main"); b.clicked.connect(s.create)
        k = QPushButton("\u2190  Back to Login"); k.setObjectName("alt"); k.clicked.connect(lambda: w.root.setCurrentIndex(0))
        for x in (f1, f2, f3, f4, f5, b, k): s.cl.addWidget(x)
    def create(s):
        n, c = s.nm.text().strip(), s.code.text().strip()
        if not n or not c: return s.msg("warning", "Missing details", "Name and Astronaut ID are required.")
        if s.g.currentIndex() == 0: return s.msg("warning", "Missing details", "Please select a gender.")
        if len(s.p1.text()) < 6: return s.msg("warning", "Weak password", "Password must be at least 6 characters.")
        if s.p1.text() != s.p2.text(): return s.msg("warning", "Password mismatch", "The two passwords do not match.")
        salt = os.urandom(16).hex()
        try: con.execute("INSERT INTO astronauts(name,code,gender,salt,pw) VALUES(?,?,?,?,?)", (n, c, s.g.currentText(), salt, hpw(s.p1.text(), salt))); con.commit()
        except sqlite3.IntegrityError: return s.msg("warning", "Already exists", "That Astronaut ID or name is already registered.")
        s.msg("information", "Account created", f"Astronaut {n} registered. Please log in."); s.w.login.id.setText(c)
        for e in (s.nm, s.code, s.p1, s.p2): e.clear()
        s.g.setCurrentIndex(0); s.w.root.setCurrentIndex(0)

class Win(QMainWindow):
    def __init__(s):
        super().__init__(); s.setWindowTitle("ASTRO HEALTH MONITOR"); g = QApplication.primaryScreen().availableGeometry(); s.resize(min(1280, g.width()), min(900, g.height() - 40)); s.aid = None; s.nm = ""; s.gen = ""
        s.root = QStackedWidget(); c = QWidget(); L = QVBoxLayout(c)
        # BACK button (visible on every screen): closes this module -> main_launcher.py shows the front page again
        _w = QWidget(); _v = QVBoxLayout(_w); _v.setContentsMargins(8, 6, 8, 0); _bk = QPushButton("\u2190  BACK TO MAIN MENU"); _bk.setMinimumHeight(38); _bk.setCursor(Qt.PointingHandCursor); _bk.clicked.connect(s.close)
        _tr = QHBoxLayout(); _tr.addWidget(_bk); _tr.addStretch(1); _v.addLayout(_tr); _v.addWidget(s.root, 1); s.setCentralWidget(_w)
        L.addWidget(QLabel("<span style='font-size:24px;font-weight:bold'>ASTRO HEALTH MONITOR</span>  <span style='color:#8aa'>Advanced health monitoring for space missions</span>"))
        r = QHBoxLayout(); s.who = QLabel(); s.who.setStyleSheet("font-size:18px;padding:8px;border:1px solid #3b6fd1;border-radius:10px")
        d = QPushButton("VIEW MY DASHBOARD"); d.clicked.connect(lambda: s.go(1)); o = QPushButton("Logout"); o.clicked.connect(s.logout)
        r.addWidget(s.who, 1); r.addWidget(d); r.addWidget(o); L.addLayout(r)
        s.pages = [Home(s), Dash(s), Base(s), Phys(s), Ment(s), Rep(s), Cycle(s)]; s.st = QStackedWidget(); [s.st.addWidget(p) for p in s.pages]
        nv = QHBoxLayout(); s.nb = []
        for i, t in enumerate(["HOME", "PERSONAL DASHBOARD", "ADD BASELINE HEALTH DATA", "PHYSICAL HEALTH", "MENTAL HEALTH", "PHYSICAL AND MENTAL HEALTH ANALYSIS REPORT", "CYCLECARE"]):
            b = QPushButton(t); b.setCheckable(True); b.setMinimumHeight(48); b.clicked.connect(lambda _, x=i: s.go(x)); nv.addWidget(b); s.nb.append(b)
        s.nb[6].setStyleSheet("QPushButton{border-color:#ff7eb6}QPushButton:checked{background:#7a1f4f;border-color:#ff7eb6}"); s.nb[6].hide()   # CycleCare: female astronauts only
        L.addLayout(nv); L.addWidget(s.st, 1)
        s.login = Login(s); s.root.addWidget(s.login); s.root.addWidget(Register(s)); s.root.addWidget(c); s.root.setCurrentIndex(0)
    def name(s): return s.nm
    def is_female(s): return s.gen.strip().lower() == "female"
    def enter(s, aid, name, code):
        s.aid, s.nm = aid, name; r = con.execute("SELECT gender FROM astronauts WHERE id=?", (aid,)).fetchone(); s.gen = (r["gender"] or "") if r else ""
        s.nb[6].setVisible(s.is_female()); s.pages[6].reset()
        s.who.setText(f"SELECTED ASTRONAUT:  <b>{name}</b>  (ID {code})"); s.root.setCurrentIndex(2); s.go(0)
    def logout(s): s.aid, s.nm, s.gen = None, "", ""; s.nb[6].hide(); s.pages[6].reset(); s.login.pw.clear(); s.root.setCurrentIndex(0)
    def go(s, i):
        if i and not s.aid: return
        if i == 6 and not s.is_female(): return          # CycleCare is available for female astronauts only
        s.st.setCurrentIndex(i); [b.setChecked(k == i) for k, b in enumerate(s.nb)]; s.pages[i].refresh()

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyleSheet(QSS); w = Win(); w.showMaximized(); sys.exit(app.exec())

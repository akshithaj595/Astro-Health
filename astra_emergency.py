#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ASTRA EMERGENCY RESPONSE - Astronaut Emergency Medical Decision-Support System
==============================================================================
Single-file PySide6 application (Python + PySide6 + SQLite + Plotly + ReportLab).

Companion module of the "Astro Health Monitor". Both modules share ONE SQLite file:
  * astronaut list / selection  -> read from the health-monitor astronaut table
  * health-monitor records      -> read (never duplicated) from the monitor's tables
  * emergency data              -> written to tables prefixed `em_` (+ view
                                   `emergency_incident_summary`) in the same file,
                                   so the monitor can read incidents with plain SQL.

Run:   python astra_emergency.py [--db path/to/health_monitor.db]

!! SAFETY !!  Decision support only. NOT a medical device, NOT a diagnosis, NOT an
approved NASA procedure. Content comes from the supplied reference dataset (ISS SODF
2015 historical checklist, AHA/Red Cross first aid, DGLRM/ESAM CPR guideline) and is
evidence-labelled. Always follow current mission procedures and contact ground
(flight surgeon / MCC-H).
"""
import sys, os, re, json, math, time, sqlite3, struct, wave, tempfile, random, datetime as dt
from xml.sax.saxutils import escape as xesc

# ============================================================================
# 1. KNOWLEDGE BASE
# ============================================================================
APP_NAME = "ASTRA EMERGENCY RESPONSE"
DISCLAIMER = ("ASTRA is an educational decision-support prototype built from public sources. It is not an "
              "approved medical device and does not replace flight-surgeon direction, current mission "
              "procedures or crew medical training. It suggests POSSIBLE conditions - it never gives a "
              "definitive diagnosis. Items marked UNVERIFIED are general first-aid knowledge not confirmed "
              "in a source. Medication guidance is shown only where a source states it; otherwise consult "
              "authorised medical support.")

TIERS = {1: ("CRITICAL", "#ff4d5e"), 2: ("URGENT", "#ffb020"), 3: ("NON-URGENT", "#4da3ff")}

# (id, label, group)
SYMPTOMS = [
 ("unresp", "Unresponsive - no reaction to voice or touch", "Consciousness & brain"),
 ("faint", "Fainting or about to faint", "Consciousness & brain"),
 ("lost_brief", "Lost consciousness briefly", "Consciousness & brain"),
 ("confusion", "Confusion / disorientation", "Consciousness & brain"),
 ("dizzy", "Dizziness / lightheaded", "Consciousness & brain"),
 ("seizure_mv", "Convulsions / uncontrolled shaking", "Consciousness & brain"),
 ("face_droop", "Face drooping on one side", "Consciousness & brain"),
 ("limb_weak", "Weakness or numbness in arm/leg/one side", "Consciousness & brain"),
 ("speech", "Slurred speech or trouble speaking", "Consciousness & brain"),
 ("thunder_hd", "Sudden, severe headache", "Consciousness & brain"),
 ("headache", "Headache", "Consciousness & brain"),
 ("head_hit", "Hit or bumped the head", "Consciousness & brain"),
 ("pupils_uneq", "Pupils of unequal size", "Consciousness & brain"),
 ("pinpoint", "Very small (pinpoint) pupils", "Consciousness & brain"),
 ("vomit_rep", "Repeated vomiting", "Consciousness & brain"),
 ("motion_sick", "Nausea / cold sweat when moving the head", "Consciousness & brain"),
 ("anxious", "Sudden anxiety or panic", "Consciousness & brain"),
 ("no_breath", "Not breathing, or only gasping", "Breathing"),
 ("sob", "Short of breath / hard to breathe", "Breathing"),
 ("choke", "Cannot speak or cough - grabbing throat", "Breathing"),
 ("blue", "Blue-gray lips, face or hands", "Breathing"),
 ("wheeze", "Wheezing", "Breathing"),
 ("rr_fast", "Very fast breathing (over 30 per minute)", "Breathing"),
 ("rr_slow", "Very slow breathing (under 12 per minute)", "Breathing"),
 ("snore", "Snoring or noisy breathing", "Breathing"),
 ("cough", "Coughing", "Breathing"),
 ("throat_tight", "Throat tightness / tongue or lip swelling", "Breathing"),
 ("chest_pain", "Chest pain or pressure", "Heart & circulation"),
 ("pain_spread", "Pain spreading to arm, jaw or back", "Heart & circulation"),
 ("palp", "Palpitations / skipped heartbeats", "Heart & circulation"),
 ("hr_fast", "Racing heartbeat", "Heart & circulation"),
 ("pulse_weak", "Weak or no pulse", "Heart & circulation"),
 ("pale_cold", "Pale, cold, clammy skin", "Heart & circulation"),
 ("sweat", "Cold sweat", "Heart & circulation"),
 ("bp_low", "Low blood pressure (top number under 90)", "Heart & circulation"),
 ("bp_high", "Very high blood pressure (180/120 or more)", "Heart & circulation"),
 ("ortho", "Dizzy / racing heart on exertion or when active", "Heart & circulation"),
 ("bleed_heavy", "Heavy bleeding that will not stop", "Bleeding & injury"),
 ("bleed_soak", "Blood spurting or soaking through dressing", "Bleeding & injury"),
 ("wound_deep", "Deep wound", "Bleeding & injury"),
 ("wound_chest", "Open wound in the chest", "Bleeding & injury"),
 ("nosebleed", "Nosebleed", "Bleeding & injury"),
 ("burn", "Burn on the skin", "Bleeding & injury"),
 ("hives", "Hives / widespread itchy rash", "Allergy & skin"),
 ("face_swell", "Swelling of face, lips or tongue", "Allergy & skin"),
 ("rash_new", "New rash after a medicine, food or contact", "Allergy & skin"),
 ("skin_red", "Skin reddening (sunburn-like)", "Allergy & skin"),
 ("sore_blister", "Painful cold sore or tingling skin patch", "Allergy & skin"),
 ("slow_heal", "Cut or wound slow to heal", "Allergy & skin"),
 ("vis_loss", "Sudden loss of vision (one or both eyes)", "Eyes & vision"),
 ("vis_blur", "Gradual blurred vision / eye strain", "Eyes & vision"),
 ("eye_chem", "Chemical in the eye", "Eyes & vision"),
 ("eye_pain", "Eye pain", "Eyes & vision"),
 ("eye_obj", "Something stuck in the eye", "Eyes & vision"),
 ("dry_eyes", "Dry eyes", "Eyes & vision"),
 ("puffy", "Puffy eyelids or face", "Eyes & vision"),
 ("alarm", "Toxic-atmosphere or fire alarm sounded", "Environment & exposure"),
 ("odor", "Unusual chemical smell", "Environment & exposure"),
 ("smoke", "Smoke exposure / soot around nose or mouth", "Environment & exposure"),
 ("burn_airway", "Burning eyes, nose or throat", "Environment & exposure"),
 ("chem_skin", "Chemical on the skin", "Environment & exposure"),
 ("overdose", "Possible overdose of opioid / sedative medicine", "Environment & exposure"),
 ("flank", "Severe flank / lower-back pain", "Digestive & urinary"),
 ("urine_blood", "Blood in urine", "Digestive & urinary"),
 ("cramp", "Muscle cramps", "Digestive & urinary"),
 ("tooth_pain", "Tooth pain", "Mouth & teeth"),
 ("tooth_lost", "Tooth knocked out, broken or filling/crown lost", "Mouth & teeth"),
]
SYM_LABEL = {s[0]: s[1] for s in SYMPTOMS}

# inventory item-name constants (steps reference these exact names)
EPI = "Epinephrine auto-injector (EpiPen) 0.3 mL"; EPI_IO = "Epinephrine 1:10,000 IO syringe (10 mL)"
ATR = "Atropine 0.1 mg/mL IO syringe (10 mL)"; LIDO_IO = "Lidocaine 2% IO syringe (5 mL)"
AMBU = "AMBU bag (manual resuscitator)"; IOK = "IO device start kit"; AED = "AED (defibrillator) with pads"
CMRS = "Crew Medical Restraint System (CMRS)"; OXY = "Oxygen mask - non-rebreather (Respiratory Support Pack)"
OXI = "Pulse oximeter"; BPM = "Blood pressure cuff & monitor"; STETH = "Stethoscope"; GLOVE = "Nitrile gloves (pairs)"
GAUZE = "Gauze pads 4x4"; TQ = "Tourniquet"; WPACK = "Wound packing"; NPACK = "Nasal packing"; BURNB = "Burn bandages"
NSB = "Non-stick bandages"; COLD = "Hot/cold pack"; DIAZ = "Diazepam 5 mg/mL injection (2 mL = 10 mg)"
PHEN = "Phenytoin ER 300 mg capsules"; ALB = "Albuterol (Proventil HFA) 90 mcg inhaler"
DPH = "Diphenhydramine 25 mg capsules"; DPHI = "Diphenhydramine 50 mg/mL injection"; ASA = "Aspirin 325 mg tablets"
NTG = "Nitroglycerin 0.4 mg tablets"; IBU = "Ibuprofen 400 mg tablets"; HYD = "Hydrocodone/acetaminophen 10/300 mg tablets"
MECL = "Meclizine 25 mg tablets"; ONDA = "Ondansetron ODT 8 mg"; PROM = "Promethazine 25 mg tablets"
VALA = "Valacyclovir 1 g tablets"; TAMS = "Tamsulosin 0.4 mg capsules"; HCORT = "Hydrocortisone 1% cream"
BACI = "Bacitracin ointment"; EYEW = "Eyewash station"; EYED = "Lubricating eye drops"; NALOX = "Naloxone (opioid antidote)"
BLOOD = "Portable Clinical Blood Analyzer (i-STAT)"; HOLT = "Holter ECG Monitor & Bio-Monitor Smart Shirt"
USO = "Ultrasound 2 / Butterfly iQ+ & OCT"; DOSI = "Personal active dosimeter"; URINE = "Urine reagent strips"
EUG = "Eugenol 1 mL"; TEMPF = "Dental temporary filling"; DTK = "Dental tool kit"; SUCT = "Suction device"
EYECH = "Eye chart / vision test"; ACE = "ACE bandages (2/3/4 in)"; SCISS = "Trauma scissors"; ADH = "Dental adhesive"

# (name, category, min_required, available, location, source)
INVENTORY_SEED = [
 (EPI, "Emergency drug", 2, 3, "Emergency Medical Treatment Pack (RED)", "S1 3.001 (qty); min assumed"),
 (EPI_IO, "Emergency drug (trained)", 2, 2, "Emergency Medical Treatment Pack (RED) - ALS Medication Kit", "S1 3.001"),
 (ATR, "Emergency drug (trained)", 2, 2, "Emergency Medical Treatment Pack (RED) - ALS Medication Kit", "S1 3.001"),
 (LIDO_IO, "Emergency drug (trained)", 2, 2, "Emergency Medical Treatment Pack (RED) - ALS Medication Kit", "S1 3.001"),
 (AMBU, "Airway", 1, 1, "Emergency Medical Treatment Pack (RED)", "S1 3.001"),
 (IOK, "Airway / access", 2, 2, "Emergency Medical Treatment Pack (RED)", "S1 3.001"),
 (SCISS, "Equipment", 1, 1, "Emergency Medical Treatment Pack (RED)", "S1 3.001"),
 (STETH, "Diagnostic", 1, 1, "Emergency Medical Treatment Pack (RED)", "S1 3.001"),
 (AED, "Emergency equipment", 1, 1, "AED case - SET EXACT LOCATION (not in dataset)", "S1 1.102 (procedure); qty assumed"),
 (CMRS, "Emergency equipment", 1, 1, "CMRS - SET EXACT LOCATION (not in dataset)", "S2; qty assumed"),
 (OXY, "Oxygen", 1, 1, "Respiratory Support Pack (cue card 3.201) - SET LOCATION", "S1 1.108; qty assumed"),
 (OXI, "Diagnostic", 1, 1, "Medical Diagnostic Pack (BLUE)", "S1 3.001"),
 (BPM, "Diagnostic", 1, 1, "Medical Diagnostic Pack (BLUE)", "S1 3.001"),
 (COLD, "Equipment", 1, 2, "Medical Diagnostic Pack (BLUE)", "S1 3.001; qty assumed"),
 (EYECH, "Diagnostic", 1, 1, "Medical Diagnostic Pack (BLUE)", "S1 3.001"),
 (TQ, "Bleeding control", 1, 1, "IV Supply Pack (GRAY)", "S1 3.001"),
 (GLOVE, "Protection", 10, 30, "Medical Supply Pack (GREEN)", "S1 3.001"),
 (GAUZE, "Bleeding control", 10, 36, "Medical Supply Pack (GREEN)", "S1 3.001"),
 (BURNB, "Wound care", 4, 10, "Minor Treatment Pack (PINK)", "S1 3.001"),
 (NSB, "Wound care", 6, 20, "Minor Treatment Pack (PINK)", "S1 3.001"),
 (NPACK, "Bleeding control", 3, 10, "Minor Treatment Pack (PINK)", "S1 3.001"),
 (WPACK, "Bleeding control", 2, 3, "Minor Treatment Pack (PINK)", "S1 3.001"),
 (ACE, "Wound care", 2, 3, "Minor Treatment Pack (PINK)", "S1 3.001; qty assumed"),
 (DTK, "Dental", 1, 1, "Minor Treatment Pack (PINK)", "S1 3.001"),
 (TEMPF, "Dental", 1, 1, "Topical & Injectable Pack (BROWN)", "S1 3.001; qty assumed"),
 (ADH, "Dental", 1, 1, "Topical & Injectable Pack (BROWN)", "S1 3.001; qty assumed"),
 (EUG, "Dental", 1, 2, "Topical & Injectable Pack (BROWN)", "S1 3.001"),
 (SUCT, "Equipment", 1, 1, "Physician Equipment Pack (YELLOW)", "S1 3.001"),
 (ASA, "Oral medicine", 12, 60, "Convenience Medication Pack (WHITE)", "S1 3.001; min assumed"),
 (IBU, "Oral medicine", 60, 400, "Convenience Medication Pack (WHITE)", "S1 3.001; min assumed"),
 (EYED, "Eye", 2, 4, "Convenience Medication Pack (WHITE)", "S1 3.001; qty assumed"),
 (HCORT, "Topical", 1, 2, "Convenience Medication Pack (WHITE)", "S1 3.001; qty assumed"),
 (BACI, "Topical", 1, 2, "Convenience Medication Pack (WHITE)", "S1 3.001; qty assumed"),
 (DPH, "Oral medicine", 10, 60, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (PHEN, "Oral medicine", 4, 30, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (NTG, "Oral medicine", 5, 25, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (MECL, "Oral medicine", 6, 20, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (ONDA, "Oral medicine", 6, 30, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (PROM, "Oral medicine", 10, 100, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (HYD, "Oral medicine (opioid)", 6, 30, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (VALA, "Oral medicine", 10, 60, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (TAMS, "Oral medicine", 3, 10, "Oral Medication Pack (PURPLE)", "S1 3.001; min assumed"),
 (DIAZ, "Injectable", 2, 4, "Topical & Injectable Pack (BROWN)", "S1 3.001; min assumed"),
 (DPHI, "Injectable", 2, 6, "Topical & Injectable Pack (BROWN)", "S1 3.001; min assumed"),
 (ALB, "Breathing", 2, 3, "Topical & Injectable Pack (BROWN)", "S1 3.001; min assumed"),
 (URINE, "Diagnostic", 5, 10, "Topical & Injectable Pack (BROWN)", "S1 3.001; qty assumed"),
 (EYEW, "Eye / decontamination", 1, 1, "Service Module, port CBO-3B", "S1 1.106"),
 (BLOOD, "Diagnostic device", 1, 1, "Health-monitoring equipment - SET LOCATION", "Equipment catalog IDP-LSDA_HARDWARE-669"),
 (HOLT, "Diagnostic device", 1, 1, "Health-monitoring equipment - SET LOCATION", "Equipment catalog IDP-LSDA_HARDWARE-543"),
 (USO, "Diagnostic device", 1, 1, "Health-monitoring equipment - SET LOCATION", "Equipment catalog EXMC-GAP-4.02"),
 (DOSI, "Radiation", 1, 1, "Crew personal kit - SET LOCATION", "Symptom checklist 5.2"),
 (NALOX, "Antidote", 2, 0, "NOT STOCKED - absent from the 2015 kit list", "S1 gap; stock if your mission carries it"),
]

def C(cid, name, tier, ev, blurb, sym, steps, warn=(), gap="", need=None, veto=(), cpr=False, unv=False):
    return dict(id=cid, name=name, tier=tier, ev=ev, blurb=blurb, sym=sym, steps=steps, warn=list(warn),
                gap=gap, need=need, veto=set(veto), cpr=cpr, unv=unv)

NASA = "OFFICIAL NASA - ISS SODF checklist (2015, historical)"
AHA = "AHA / Red Cross first aid 2024 (Earth-based)"
GEN = "GENERAL FIRST AID - UNVERIFIED, no NASA/AHA text loaded"
CHK = "Symptom checklist document (NASA HRP / LSAH summary)"

CONDITIONS = {c["id"]: c for c in [
 C("cardiac_arrest", "Cardiac arrest - unresponsive and not breathing normally", 1,
   NASA + " + PEER-REVIEWED microgravity CPR guideline", "Heart has stopped or breathing has stopped. Start CPR immediately.",
   {"unresp": 3, "no_breath": 3, "pulse_weak": 2, "blue": 1},
   [("SHOUT for help and PAGE the crew. Check the area is safe.", None),
    ("Tap shoulders and shout. Look at the chest for up to 10 seconds. Not breathing, or only gasping = START CPR NOW.", None),
    ("Hold patient and yourself steady. Fastest start: Evetts-Russomano method - wrap your legs round the patient, ankles locked behind their back. If that does not work, use the Reverse-Bear-Hug. Restrain the patient on the CMRS as soon as a second crew member can help.", CMRS),
    ("Press OPEN CPR METRONOME (button at right). Push HARD and FAST in the centre of the chest: 100-120 per minute, about 5 cm (2 in) deep, let the chest rise fully each time.", None),
    ("Alone: 30 compressions, then 2 breaths (1 second each). Repeat 5 times (about 2 minutes). Do not stop for more than 10 seconds.", None),
    ("Second crew member: bring the AED. Bare the chest, stick on the pads, press ON and follow its voice prompts. Shock ONLY when the patient is restrained on an electrically isolated surface and nobody is touching them.", AED),
    ("Each time the AED says 'Start CPR': check pulse for 10 seconds. Pulse = stop compressions and check breathing. No pulse = keep doing CPR 30:2 until the AED speaks again.", None),
    ("If trained: give breaths with the AMBU bag - one breath every 5 seconds, with oxygen if available.", AMBU),
    ("TRAINED CMO ONLY: IO access, then medicines in the order the checklist gives (epinephrine first; then atropine if the AED says no shock, lidocaine if shock advised).", IOK),
    ("Call ground (MCC-H) as soon as a second person is free. Write down the start time and the time of every medicine.", None),
    ("Do NOT stop CPR on your own. Stop only with the ground surgeon / most qualified crew member. NASA 2015 criteria: at least 10 min since last drug AND no pulse/breathing AND at least 45 min since CPR began.", None)],
   ["Defibrillate only a restrained patient on an electrically isolated surface.",
    "The IO needle can eject like a projectile - follow the IO steps exactly.",
    "CPR has never been done on a human in space; rates/depth are Earth values not validated in microgravity."],
   gap="Post-resuscitation care: NOT FOUND (guideline made no recommendation).", cpr=True),
 C("unconscious", "Unconscious but breathing normally", 1, NASA, "Patient does not respond but is breathing normally.",
   {"unresp": 3, "lost_brief": 2, "faint": 1, "pale_cold": 1, "confusion": 1}, veto=["no_breath"],
   steps=[("Page the crew. Check the area is safe.", None),
    ("Watch the chest for 10 seconds. If breathing is NOT normal (absent or only gasping) -> switch to CARDIAC ARREST / CPR now.", None),
    ("Restrain the patient on the CMRS so they do not float; keep the airway open.", CMRS),
    ("Call ground (MCC-H) and request a private medical conference (PMC).", None),
    ("Breathing check: blue-gray lips/face/hands, breathing under 12 or over 30 per minute, or snoring = give oxygen by mask.", OXY),
    ("Count pulse and breaths for 15 seconds each and multiply by 4. Record the numbers and the time.", None),
    ("Check oxygen level with the pulse oximeter and blood pressure with the cuff. Write them down.", OXI),
    ("If the blood analyzer is at hand and it does not delay anything, check blood glucose (low sugar can cause unconsciousness).", BLOOD),
    ("Look for the cause: allergic reaction, toxic exposure, head injury, bleeding, seizure. Open the matching protocol.", None),
    ("Stay with the patient and re-check breathing every minute.", None)],
   warn=["Never give food, drink or tablets to an unresponsive person."],
   gap="Cause-finding steps beyond the NASA respiratory assessment are general knowledge (UNVERIFIED)."),
 C("choking", "Choking - blocked airway", 1, NASA, "Something is blocking the airway.",
   {"choke": 3, "blue": 2, "sob": 1, "wheeze": 1, "cough": 1},
   [("Ask: 'Are you choking?' Awake and unable to speak, cough or breathe = act now.", None),
    ("AWAKE: stand behind, make a fist above the belly button and below the ribs, pull sharply inward and upward. Repeat until the object comes out or the patient becomes unconscious.", None),
    ("UNCONSCIOUS: open the airway (head tilt, chin lift), look in the mouth and remove the object ONLY if you can see and reach it. Pinch the nose and try breaths, then start CPR (use the CPR protocol and metronome).", None),
    ("When the object is out: measure pulse, breathing rate and blood pressure; write them down.", BPM),
    ("Call ground (MCC-H) and request a private medical conference.", None)],
   ["Do not blindly sweep the mouth with your fingers."],
   gap="How abdominal thrusts are adapted in free-floating microgravity: NOT FOUND - brace both of you against a structure."),
 C("breathing", "Difficulty breathing", 1, NASA, "Hard to breathe, abnormal breathing rate or blue-gray skin.",
   {"sob": 3, "blue": 3, "rr_fast": 2, "rr_slow": 2, "wheeze": 2, "snore": 2, "cough": 1, "throat_tight": 1, "burn_airway": 1, "smoke": 1, "chest_pain": 1},
   [("Help the patient stay calm and restrained. Count pulse and breaths for 15 seconds each, multiply by 4, record.", None),
    ("Give oxygen by non-rebreather mask (needed if lips/face/hands are blue-gray, breathing under 12 or over 30 per minute, or snoring/hard to breathe in).", OXY),
    ("If wheezing / asthma-type: shake the inhaler, patient breathes out fully, then breathes in slowly as you press it; hold breath 8-10 seconds; then give a second dose.", ALB),
    ("Clip on the pulse oximeter and record the oxygen level.", OXI),
    ("Listen to both lungs (front and back) with the stethoscope. Is the sound higher or lower than normal on one side?", STETH),
    ("Check blood pressure and ask: what started it? medicines? fever or cough? how bad is it?", BPM),
    ("Call ground (MCC-H) and request a private medical conference.", None),
    ("If breathing stops, start CPR with the CPR protocol.", None)],
   ["Do not give extra oxygen on your own judgement if the patient is known to have COPD - ask ground (AHA note, exact wording to verify).",
    "Oximeter readings can be wrong (cold hands, movement, nail polish)."],
   gap="Oxygen flow rate: NOT FOUND (cue card 3.201 not in dataset)."),
 C("anaphylaxis", "Severe allergic reaction (anaphylaxis)", 1, NASA, "Whole-body allergic reaction that can close the airway.",
   {"face_swell": 3, "throat_tight": 3, "hives": 2, "wheeze": 2, "sob": 1, "faint": 1, "dizzy": 1, "bp_low": 1, "rash_new": 1, "vomit_rep": 1},
   [("Page the crew. Stop whatever may have caused it (medicine, food, contact).", None),
    ("Take the EpiPen out of its tube. Make a fist round it with your thumb next to the grey/blue safety cap. Pull the cap off.", EPI),
    ("Press the needle end firmly against the OUTER THIGH at a right angle until you hear/feel a click. HOLD for 10 seconds.", EPI),
    ("Put the used pen back in its tube and tape it shut. Call ground (MCC-H) for a private medical conference.", None),
    ("Watch closely. If symptoms return or get worse after 20 minutes, a second EpiPen may be given (the kit holds 3).", EPI),
    ("Give oxygen by mask if breathing is hard.", OXY),
    ("Antihistamines are in the kit (diphenhydramine capsule / injection) but NO dose is in the sources - ask ground before giving.", DPH),
    ("Keep checking breathing. If it stops, start CPR.", None)],
   ["Never put fingers over the needle end.", "Do not inject through clothing."],
   gap="NASA procedure 2.0.403 (Allergic Reaction) and symptom list: NOT in dataset. Signs listed are general knowledge (UNVERIFIED)."),
 C("toxic", "Toxic gas / chemical exposure", 1, NASA + " (1.106); atmosphere events from NASA papers", "Breathing, eyes or skin exposed to a harmful substance.",
   {"alarm": 3, "odor": 3, "burn_airway": 3, "chem_skin": 2, "eye_chem": 2, "smoke": 1, "cough": 1, "sob": 1, "dizzy": 1, "confusion": 1, "headache": 1},
   [("Protect yourself FIRST: breathing mask, eye and skin protection. Limit how many crew are exposed.", GLOVE),
    ("If the toxic-atmosphere alarm sounded: follow your station emergency procedure (put on mask, move to a clean module, close the hatch).", None),
    ("SKIN: quickly take off contaminated clothing. Brush off powder, wipe liquid with a towel, then rinse with water.", None),
    ("EYES: remove contact lenses, then flush at once with water from a drink bag; use the eyewash station and keep rinsing until the pain is gone or ground says stop.", EYEW),
    ("INHALED and hard to breathe: open the DIFFICULTY BREATHING protocol (oxygen, oximeter).", OXY),
    ("Call ground (MCC-H) and request a private medical conference.", None),
    ("Write down which chemical, any medicines, other exposures. Photograph affected skin/eyes and keep each crew member's photos in a separate folder labelled with name and GMT day.", None)],
   ["Do not rush in without protection - rescuers can become patients."],
   gap="Gas-specific treatment (CO, CO2, ammonia, smoke), exposure limits (SMAC): NOT FOUND."),
 C("seizure", "Seizure / convulsions", 1, NASA, "Uncontrolled shaking with loss of awareness.",
   {"seizure_mv": 3, "confusion": 1, "lost_brief": 1, "unresp": 1},
   [("Note the start time. Move the patient to the centre of a module, away from hard structures. Hold them gently so they cannot hit anything.", None),
    ("NEVER put anything in the mouth. Seizures usually stop within 1 minute.", None),
    ("If it lasts MORE than 5 minutes or comes back: diazepam 10 mg (2 mL of 5 mg/mL) into the muscle; may repeat after 10 minutes (NASA checklist).", DIAZ),
    ("Clip on the pulse oximeter and record oxygen level. Give oxygen by mask.", OXI),
    ("After it stops: expect several minutes of confusion. Check head, arms/legs, teeth and tongue for injury and photograph them.", None),
    ("Call ground (MCC-H) for a private medical conference. When awake, thinking clearly and able to swallow, phenytoin may be given as ground directs.", PHEN),
    ("Write down what happened and what medicines the patient takes.", None)],
   ["Seizure with injury, choking or breathing trouble, or not back to normal after 5-10 minutes = urgent medical help (AHA)."]),
 C("bleeding", "Severe bleeding", 1, AHA + " (no NASA bleeding procedure found); kit contents from NASA S1", "Life-threatening blood loss.",
   {"bleed_heavy": 3, "bleed_soak": 3, "wound_deep": 2, "pale_cold": 1, "hr_fast": 1, "bp_low": 1, "pulse_weak": 1},
   [("Put on gloves (and eye protection). Blood floats as droplets in microgravity - catch it with gauze/towels.", GLOVE),
    ("Press HARD directly on the wound with gauze, using both hands. Do not lift to look. Keep pressing.", GAUZE),
    ("Arm or leg and bleeding will not stop: apply the tourniquet between the wound and the heart and tighten until bleeding stops. Note the time.", TQ),
    ("Deep wound where a tourniquet cannot be used: pack the wound firmly with wound packing / gauze and keep pressing.", WPACK),
    ("Keep the patient still and warm; check pulse and breathing.", None),
    ("Call ground (MCC-H) now.", None),
    ("If the patient becomes unresponsive and not breathing, start CPR.", None)],
   ["Do not remove objects stuck in a wound.", "Tourniquet is painful - that is expected; do not loosen it."],
   gap="NASA bleeding-control procedure and microgravity suction handling: NOT FOUND. Tourniquet placement detail is general knowledge (UNVERIFIED).", unv=True),
 C("head_injury", "Head injury / concussion", 2, GEN, "Blow to the head - the brain may be injured.",
   {"head_hit": 3, "pupils_uneq": 3, "vomit_rep": 2, "confusion": 2, "lost_brief": 2, "thunder_hd": 1, "headache": 1, "seizure_mv": 1, "dizzy": 1},
   [("Keep the patient still and restrained. Assume a neck injury if the impact was hard.", CMRS),
    ("Page the crew and CALL GROUND NOW - NASA/AHA head-injury protocol is not loaded in this app.", None),
    ("Ask the name, where they are, what happened. Note any confusion or memory loss.", None),
    ("Look for DANGER SIGNS: passed out, vomiting again and again, seizure, getting worse headache, confusion, unequal pupils, weakness. Any = emergency; tell ground.", None),
    ("Stop bleeding on the scalp with gentle pressure and gauze.", GAUZE),
    ("Check pulse, breathing rate, blood pressure and oxygen; record every 5-10 minutes.", BPM),
    ("A cold pack may be placed on a swollen area.", COLD)],
   ["Do not give medicines for this condition without ground instruction.", "If unresponsive, use the UNCONSCIOUS or CPR protocol."],
   gap="Head-injury protocol NOT FOUND in NASA/AHA sources read. Warning signs are general knowledge (UNVERIFIED).", unv=True),
 C("chest_pain", "Chest pain - possible heart attack", 2, AHA + " + NASA kit data", "Chest pain or pressure that may come from the heart.",
   {"chest_pain": 3, "pain_spread": 3, "sweat": 2, "sob": 1, "palp": 1, "pale_cold": 1, "dizzy": 1},
   [("Stop all activity. Restrain the patient so they are comfortable. Page the crew.", None),
    ("Call ground (MCC-H) NOW for urgent medical support.", None),
    ("If the patient is NOT allergic and ground has not said no: they may chew and swallow one aspirin 325 mg (the AHA range is 162-325 mg). Take with food if possible.", ASA),
    ("Put on the chest-strap ECG / smart shirt so ground can see the heart rhythm.", HOLT),
    ("Count pulse and breathing; measure blood pressure and oxygen. Record with times.", BPM),
    ("Do NOT give nitroglycerin or other heart drugs unless ground instructs - NASA instructions are not in the dataset.", NTG),
    ("Be ready to start CPR if the patient collapses and stops breathing.", None)],
   ["Aspirin: do not give if allergic or if told not to take it."],
   gap="NASA chest-pain procedure and nitroglycerin instructions: NOT FOUND."),
 C("stroke", "Stroke signs", 2, AHA, "Face droop, arm weakness or speech trouble - brain blood-flow problem.",
   {"face_droop": 3, "limb_weak": 3, "speech": 3, "vis_loss": 1, "thunder_hd": 1, "confusion": 1, "dizzy": 1},
   [("Note the TIME the signs began - ground needs it.", None),
    ("FAST check: Face (ask to smile - one side droops?), Arms (raise both - one drifts down?), Speech (repeat a sentence - slurred?).", None),
    ("Any sign = suspected stroke. Call ground (MCC-H) NOW.", None),
    ("If it does not delay help, check blood glucose with the blood analyzer.", BLOOD),
    ("Record blood pressure, pulse, breathing and oxygen.", BPM),
    ("Give nothing to eat or drink (general first-aid advice, UNVERIFIED). Restrain the patient comfortably and watch breathing.", None)],
   ["Do not give aspirin or any medicine unless ground instructs."],
   gap="NASA stroke procedure NOT FOUND."),
 C("vision_loss", "Sudden vision loss", 2, GEN, "Vision suddenly lost or greatly reduced in one or both eyes.",
   {"vis_loss": 3, "eye_pain": 1, "headache": 1, "thunder_hd": 1, "face_droop": 1, "limb_weak": 1},
   [("Keep the patient calm and restrained. Note the TIME it started and which eye(s).", None),
    ("Call ground (MCC-H) NOW - sudden loss of vision is an emergency.", None),
    ("Check for stroke signs (face droop, arm weakness, slurred speech). If any are present, open the STROKE protocol.", None),
    ("Chemical in the eye? Open the EYE INJURY protocol and flush immediately.", None),
    ("Do not rub the eye. Do not give medicines.", None),
    ("Record what the patient can see (light? hand movement? shapes?). Use the vision test if available.", EYECH),
    ("Ground may ask for an ultrasound/OCT scan of the eye - only do it under remote guidance.", USO)],
   ["Slow blurring over days is different - see 'Blurred vision / eye pressure'."],
   gap="NASA procedure NOT FOUND; steps are general first aid (UNVERIFIED).", unv=True),
 C("bp_change", "Sudden blood pressure change (very low or very high)", 2, GEN + "; kit data NASA S1", "Blood pressure far outside normal.",
   {"bp_low": 3, "bp_high": 3, "dizzy": 1, "faint": 1, "pale_cold": 1, "hr_fast": 1, "chest_pain": 1, "headache": 1},
   [("Keep the patient still and restrained or seated. Measure blood pressure again after 1-2 minutes to confirm.", BPM),
    ("Count pulse and breathing for 15 seconds each (x4). Check oxygen level.", OXI),
    ("LOW pressure (top number under 90) with pale, cold, clammy skin or fast weak pulse = possible shock. Look for bleeding, an allergic reaction (open that protocol) or heart trouble.", None),
    ("VERY HIGH pressure (180/120 or more) with chest pain, severe headache, vision change or weakness = emergency; open the matching protocol.", None),
    ("Call ground (MCC-H) with the readings and the time.", None),
    ("Do NOT give blood-pressure medicines on your own - emergency doses are not in the sources. Medication guidance unavailable - consult authorised medical support.", None),
    ("Recheck and record blood pressure every 5 minutes.", None)],
   gap="NASA procedure NOT FOUND; thresholds are app design values (ASSUMPTION).", unv=True),
 C("burns", "Burns / smoke inhalation", 2, AHA, "Skin burn, or breathing in smoke.",
   {"burn": 3, "smoke": 2, "burn_airway": 1, "sob": 1, "cough": 1},
   [("Move away from the fire/heat source. Make sure the area is safe.", None),
    ("Smoke signs (face burns, hard to breathe, singed nostril hairs, soot round nose/mouth) = urgent: give oxygen and call ground.", OXY),
    ("Remove rings, belts and tight clothing near the burn (not stuck cloth).", None),
    ("Cool the burn gently with a cool, wet cloth. (Cooling time/temperature not found in the sources - ask ground.)", None),
    ("Cover loosely with a clean non-stick or burn bandage. Cover intact blisters loosely; do not pop them.", BURNB),
    ("Call ground if the burn is larger than the patient's palm, deep, or on face, hands, feet or genitals.", None)],
   ["Do not put ice directly on the burn."], gap="NASA burn procedure NOT FOUND."),
 C("chest_wound", "Open chest wound", 2, AHA, "A hole in the chest wall.",
   {"wound_chest": 3, "sob": 1, "blue": 1, "bleed_heavy": 1},
   [("Call ground now - this is a medical emergency.", None),
    ("Leave the wound open to air OR cover it loosely with a clean non-occluding dry dressing (a vented chest seal if you have one).", NSB),
    ("Watch breathing. If breathing gets worse, loosen or remove the dressing.", None),
    ("Give oxygen if breathing is hard.", OXY)]),
 C("eye_injury", "Eye injury / chemical or foreign body in eye", 2, NASA + " (chemical) + " + AHA, "Eye damaged by chemical or object.",
   {"eye_chem": 3, "eye_obj": 3, "eye_pain": 3, "vis_loss": 1},
   [("CHEMICAL: remove contact lenses and flush the eye immediately with water; keep flushing until pain stops or ground says stop.", EYEW),
    ("SMALL particle: do not rub. Let tears wash it out or rinse gently.", None),
    ("Seek urgent help if it was high-speed or penetrating, the pupil looks odd, there is bleeding, vision loss, or it still hurts.", None),
    ("Tape a shield or paper cup over the eye (do not press on it).", None),
    ("Call ground (MCC-H) and request a private medical conference.", None)]),
 C("overdose", "Suspected opioid / sedative overdose", 1, AHA, "Very slow breathing and tiny pupils after a medicine.",
   {"overdose": 3, "pinpoint": 3, "rr_slow": 2, "unresp": 1, "no_breath": 1, "blue": 1},
   [("Page the crew. Check breathing.", None),
    ("Not breathing normally: start CPR (CPR protocol and metronome).", None),
    ("Give naloxone if you have it.", NALOX),
    ("Call ground (MCC-H) at once.", None)],
   ["Naloxone does NOT appear in the 2015 ISS kit list although the kit holds opioid medicines - check what your kit really carries."],
   gap="NASA naloxone procedure NOT FOUND."),
 C("nosebleed", "Nosebleed", 3, AHA, "Bleeding from the nose.",
   {"nosebleed": 3, "dizzy": 1},
   [("Pinch the soft part of the nose firmly for 10-15 minutes without letting go. Lean the head slightly forward.", GAUZE),
    ("Catch floating blood with gauze or a towel.", None),
    ("Still bleeding after 15 minutes, lightheaded, or head injury before the bleed: call ground. Nasal packing is available.", NPACK)]),
 C("dental", "Dental emergency (pain, lost filling/crown, lost tooth)", 3, NASA + " (2.6.xxx dental)", "Tooth or filling problem.",
   {"tooth_pain": 3, "tooth_lost": 3},
   [("TOOTH PAIN (NASA checklist): ibuprofen 400 mg x2 tablets with food, plus hydrocodone/acetaminophen 10/300 mg x1 tablet. Max ibuprofen: 8 tablets in 24 hours.", IBU),
    ("The painkiller above is sedating and an opioid - do not combine with sleep medicines. Ask ground.", HYD),
    ("LOST FILLING: clean the cavity, roll the temporary filling into a ball, press in, bite 2 minutes. No drinking for about 1 hour.", TEMPF),
    ("LOST CROWN: reseat with dental adhesive if painful or sensitive.", ADH),
    ("TOOTH KNOCKED OUT: press gauze on the socket until bleeding stops. Keep the tooth safe and ask ground how to store it (NASA says zip bag; AHA recommends special media - sources differ).", GAUZE),
    ("EXPOSED NERVE: eugenol on gauze 1-2 minutes. Never swallow; keep off skin/gums.", EUG),
    ("Photograph and downlink to ground.", None)]),
 C("sans", "Blurred vision / eye pressure (SANS pattern)", 3, CHK, "Slow vision change from fluid shifting to the head.",
   {"vis_blur": 3, "puffy": 2, "headache": 1, "dry_eyes": 1},
   [("Log vision metrics every day (reading test or chart).", EYECH),
    ("Use lubricating eye drops for dry or strained eyes.", EYED),
    ("Fluid-shift countermeasure: lower-body negative pressure (LBNP) as scheduled by ground.", None),
    ("An OCT / ultrasound scan of the eye can be done when ground asks.", USO),
    ("Tell ground. If vision suddenly worsens, open SUDDEN VISION LOSS.", None)]),
 C("motion", "Space motion sickness", 3, CHK, "Nausea and cold sweat with head movement.",
   {"motion_sick": 3, "vomit_rep": 1, "pale_cold": 1, "sweat": 1, "dizzy": 1},
   [("Move slowly. Avoid sudden head turns, yaw and pitch motions.", None),
    ("Anti-nausea medicine only as prescribed by the flight surgeon - no doses are in the sources.", MECL),
    ("Other kit options (ask flight surgeon first):", ONDA),
    ("Log symptoms in the vestibular self-assessment log.", None)]),
 C("orthostatic", "Orthostatic intolerance (dizzy / racing heart when active)", 3, CHK, "Low-pressure adaptation causing dizziness.",
   {"ortho": 3, "dizzy": 1, "hr_fast": 1, "pale_cold": 1, "faint": 1},
   [("Rest and restrain yourself. Measure blood pressure and pulse.", BPM),
    ("Salt-loading protocol (salt tablets + lots of water) 24-48 h BEFORE heavy exertion or landing preparation, as ground directs.", None),
    ("Wear the ECG shirt/monitor during exertion so rhythm and heart rate are logged.", HOLT),
    ("If fainting happens, open UNCONSCIOUS; if blood pressure is under 90 open BLOOD PRESSURE CHANGE.", None)]),
 C("arrhythmia", "Irregular heartbeat / palpitations", 2, CHK, "Skipped or fluttering heartbeats.",
   {"palp": 3, "hr_fast": 1, "anxious": 1, "dizzy": 1, "chest_pain": 1},
   [("Sit/restrain and breathe slowly and deeply.", None),
    ("Put on the ECG shirt/Holter monitor and mark the event with the Event Button.", HOLT),
    ("Check potassium with the blood analyzer (checklist also names magnesium).", BLOOD),
    ("Palpitations with chest pain, fainting or breathlessness = open CHEST PAIN protocol and call ground.", None),
    ("If palpitations persist, alert the ground medical team.", None)]),
 C("kidney", "Kidney strain / possible kidney stone", 2, CHK, "Flank pain linked to bone loss and calcium in urine.",
   {"flank": 3, "urine_blood": 2, "cramp": 1, "vomit_rep": 1},
   [("Drink water at once to dilute the urine.", None),
    ("Test urine with the reagent strip (pH and specific gravity).", URINE),
    ("Prescribed countermeasures (potassium citrate, bisphosphonates) only as ground directs. Tamsulosin is in the kit - no dose in sources, ask ground.", TAMS),
    ("Alert ground. Severe pain, fever or vomiting = call at once.", None)]),
 C("radiation", "Radiation exposure / skin sensitivity", 3, CHK, "Skin reddening, dry eyes, fatigue after radiation.",
   {"skin_red": 3, "dry_eyes": 2},
   [("Read your personal dosimeter and note the dose.", DOSI),
    ("During a solar particle event move to the shielded storm shelter.", None),
    ("Wear the radiation shielding vest while sleeping.", None),
    ("Lubricating drops for dry eyes. Tell ground about any skin change.", EYED)]),
 C("immune", "Immune suppression - slow healing / cold sores", 3, CHK, "Slow-healing cuts, tingling patches or cold sores.",
   {"sore_blister": 3, "slow_heal": 3, "rash_new": 1},
   [("Clean hands and surfaces; isolate the affected crew member if needed.", None),
    ("Cover small cuts with bacitracin and a clean bandage.", BACI),
    ("Antiviral for a cold-sore flare-up only as ground directs (valacyclovir 1 g is in the kit; dosing not in sources).", VALA),
    ("Report to ground.", None)]),
]}

def status_of(avail, minimum):
    if avail <= 0: return "Unavailable"
    if avail < minimum: return "Low Stock"
    return "Available"

def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def derive_from_vitals(v):
    d = set()
    try:
        if v.get("rr") is not None:
            d |= {"rr_fast"} if v["rr"] > 30 else ({"rr_slow"} if v["rr"] < 12 else set())
        if v.get("sys") is not None and v["sys"] < 90: d.add("bp_low")
        if (v.get("sys") or 0) >= 180 or (v.get("dia") or 0) >= 120: d.add("bp_high")
        if v.get("hr") is not None and v["hr"] > 120: d.add("hr_fast")
    except TypeError: pass
    return d

def analyze(selected, vitals=None):
    derived = derive_from_vitals(vitals or {}) - set(selected)
    allsel = set(selected) | derived
    out = []
    for c in CONDITIONS.values():
        raw = sum(w for s, w in c["sym"].items() if s in allsel)
        if not raw: continue
        top = sum(sorted(c["sym"].values(), reverse=True)[:3])
        score = min(1.0, raw / top)
        if c["need"] and c["need"] not in allsel: score *= 0.35
        if c["veto"] & allsel: score *= 0.4
        if score < 0.3: continue
        out.append(dict(id=c["id"], name=c["name"], tier=c["tier"], score=round(score, 2),
                        strength=("Strong match" if score >= 0.7 else "Possible" if score >= 0.45 else "Less likely"),
                        matched=[SYM_LABEL[s] for s in c["sym"] if s in allsel], derived=[SYM_LABEL[s] for s in c["sym"] if s in derived]))
    out.sort(key=lambda r: -r["score"] * (1 + 0.25 * (3 - r["tier"])))
    return out, derived

# ============================================================================
# 2. SHARED DATABASE LAYER
# ============================================================================
ASTRO_TABLES = ["astronauts", "astronaut", "crew", "crew_members", "users", "user"]
ID_COLS = ["id", "astronaut_id", "user_id", "crew_id"]
NAME_COLS = ["name", "full_name", "astronaut_name", "username", "display_name"]
FK_COLS = ["astronaut_id", "user_id", "crew_id", "crew_member_id", "astronaut"]
DEMO_CREW = [("David Williams", "Commander"), ("Alex Johnson", "Flight Engineer"),
             ("Maria Chen", "Mission Specialist"), ("Ravi Menon", "Crew Medical Officer")]

class Store:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=OFF")
        self._detect_astronaut_table()
        self._schema()

    # ---- astronaut table adapter (reads the existing health-monitor DB) ----
    def tables(self):
        return [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    def cols(self, table):
        return [r["name"] for r in self.db.execute(f'PRAGMA table_info("{table}")')]

    def _detect_astronaut_table(self):
        self.at = self.idc = self.namex = None
        tl = {t.lower(): t for t in self.tables()}
        for cand in ASTRO_TABLES:
            if cand in tl:
                cols = self.cols(tl[cand]); low = {c.lower(): c for c in cols}
                idc = next((low[c] for c in ID_COLS if c in low), None)
                nc = next((low[c] for c in NAME_COLS if c in low), None)
                if idc and nc: self.at, self.idc, self.namex = tl[cand], idc, f'"{nc}"'; return
                if idc and "first_name" in low and "last_name" in low:
                    self.at, self.idc, self.namex = tl[cand], idc, f'("{low["first_name"]}"||\' \'||"{low["last_name"]}")'; return
        self.db.execute("CREATE TABLE IF NOT EXISTS astronauts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, role TEXT, created_at TEXT)")
        if not self.db.execute("SELECT COUNT(*) FROM astronauts").fetchone()[0]:
            self.db.executemany("INSERT INTO astronauts(name,role,created_at) VALUES(?,?,?)", [(n, r, now_utc()) for n, r in DEMO_CREW])
        self.db.commit()
        self.at, self.idc, self.namex = "astronauts", "id", '"name"'

    def astronauts(self):
        return [(r[0], r[1]) for r in self.db.execute(f'SELECT "{self.idc}", {self.namex} FROM "{self.at}" ORDER BY 2')]

    def astronaut(self, aid):
        r = self.db.execute(f'SELECT * FROM "{self.at}" WHERE "{self.idc}"=?', (aid,)).fetchone()
        return dict(r) if r else {}

    def astro_name(self, aid):
        r = self.db.execute(f'SELECT {self.namex} FROM "{self.at}" WHERE "{self.idc}"=?', (aid,)).fetchone()
        return r[0] if r else f"Astronaut #{aid}"

    # ---- health-monitor records (read-only, never duplicated) ----
    def monitor_tables(self):
        res = []
        for t in self.tables():
            if t.startswith("em_") or t == self.at: continue
            fk = next((c for c in self.cols(t) if c.lower() in FK_COLS), None)
            if fk: res.append((t, fk))
        return res

    def monitor_latest(self, aid, per_table=1):
        out = []
        for t, fk in self.monitor_tables():
            cols = self.cols(t)
            dc = next((c for c in cols if re.search(r"date|time|created|stamp", c, re.I)), None)
            order = f'"{dc}" DESC' if dc else "rowid DESC"
            try:
                rows = self.db.execute(f'SELECT * FROM "{t}" WHERE "{fk}"=? ORDER BY {order} LIMIT ?', (aid, per_table)).fetchall()
            except sqlite3.Error: continue
            for r in rows: out.append((t, dict(r)))
        return out

    def monitor_vitals(self, aid):
        v = {}
        for _, row in self.monitor_latest(aid):
            for k, val in row.items():
                if not isinstance(val, (int, float)): continue
                kl = k.lower()
                if "resp" in kl and "rr" not in v: v["rr"] = val
                elif re.search(r"heart_rate|^hr$|bpm", kl) and "hr" not in v: v["hr"] = val
                elif "systolic" in kl and "sys" not in v: v["sys"] = val
                elif "diastolic" in kl and "dia" not in v: v["dia"] = val
        return v

    # ---- emergency schema (namespaced em_, same file) ----
    def _schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS em_settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS em_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, category TEXT,
            min_required INTEGER NOT NULL DEFAULT 0, available INTEGER NOT NULL DEFAULT 0, location TEXT, status TEXT,
            source TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS em_incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, astronaut_id INTEGER NOT NULL, astronaut_name TEXT,
            started_at TEXT NOT NULL, ended_at TEXT, status TEXT DEFAULT 'OPEN', symptoms_json TEXT, symptoms_text TEXT, vitals_json TEXT,
            conditions_json TEXT, top_condition TEXT, top_condition_id TEXT, severity INTEGER, protocol_id TEXT, actions_json TEXT, outcome_notes TEXT);
        CREATE INDEX IF NOT EXISTS ix_em_inc_astro ON em_incidents(astronaut_id, started_at);
        CREATE TABLE IF NOT EXISTS em_medication_log (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER, astronaut_id INTEGER,
            item_id INTEGER, item_name TEXT, quantity INTEGER, given_at TEXT, notes TEXT);
        CREATE VIEW IF NOT EXISTS emergency_incident_summary AS
            SELECT id, astronaut_id, astronaut_name, started_at, ended_at, status, top_condition, severity, symptoms_text, outcome_notes FROM em_incidents;
        """)
        if not self.db.execute("SELECT COUNT(*) FROM em_inventory").fetchone()[0]:
            for n, cat, mn, av, loc, src in INVENTORY_SEED:
                self.db.execute("INSERT INTO em_inventory(name,category,min_required,available,location,status,source,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                (n, cat, mn, av, loc, status_of(av, mn), src, now_utc()))
        self.db.commit()

    def setting(self, k, default=None):
        r = self.db.execute("SELECT value FROM em_settings WHERE key=?", (k,)).fetchone()
        return r[0] if r else default

    def set_setting(self, k, v):
        self.db.execute("INSERT INTO em_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        self.db.commit()

    # ---- inventory ----
    def inv_all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM em_inventory ORDER BY category, name")]

    def inv_item(self, name):
        r = self.db.execute("SELECT * FROM em_inventory WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def inv_update(self, iid, **f):
        cur = self.db.execute("SELECT * FROM em_inventory WHERE id=?", (iid,)).fetchone()
        if not cur: return
        d = dict(cur); d.update(f)
        d["available"] = max(0, int(d["available"])); d["min_required"] = max(0, int(d["min_required"]))
        d["status"] = status_of(d["available"], d["min_required"])
        self.db.execute("UPDATE em_inventory SET name=?,category=?,min_required=?,available=?,location=?,status=?,updated_at=? WHERE id=?",
                        (d["name"], d["category"], d["min_required"], d["available"], d["location"], d["status"], now_utc(), iid))
        self.db.commit()

    def inv_add(self, name, category, mn, av, loc):
        self.db.execute("INSERT INTO em_inventory(name,category,min_required,available,location,status,source,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (name, category, mn, av, loc, status_of(av, mn), "user added", now_utc()))
        self.db.commit()

    def inv_delete(self, iid):
        self.db.execute("DELETE FROM em_inventory WHERE id=?", (iid,)); self.db.commit()

    # ---- incidents ----
    def new_incident(self, aid, symptoms, vitals, ranked):
        top = ranked[0] if ranked else None
        cur = self.db.execute(
            "INSERT INTO em_incidents(astronaut_id,astronaut_name,started_at,symptoms_json,symptoms_text,vitals_json,conditions_json,top_condition,top_condition_id,severity,actions_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (aid, self.astro_name(aid), now_utc(), json.dumps(sorted(symptoms)), "; ".join(SYM_LABEL[s] for s in sorted(symptoms) if s in SYM_LABEL),
             json.dumps(vitals), json.dumps(ranked), top["name"] if top else None, top["id"] if top else None, top["tier"] if top else None, "{}"))
        self.db.commit(); return cur.lastrowid

    def update_incident(self, iid, **f):
        for k, v in f.items():
            if isinstance(v, (dict, list)): v = json.dumps(v)
            self.db.execute(f"UPDATE em_incidents SET {k}=? WHERE id=?", (v, iid))
        self.db.commit()

    def incident(self, iid):
        r = self.db.execute("SELECT * FROM em_incidents WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def incidents(self, aid=None):
        q, a = "SELECT * FROM em_incidents", ()
        if aid is not None: q += " WHERE astronaut_id=?"; a = (aid,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY started_at DESC", a)]

    def set_step(self, iid, cond_id, idx, text, done):
        inc = self.incident(iid); acts = json.loads(inc["actions_json"] or "{}")
        lst = acts.setdefault(cond_id, [])
        lst[:] = [s for s in lst if s["idx"] != idx]
        if done: lst.append(dict(idx=idx, text=text, done_at=now_utc()))
        lst.sort(key=lambda s: s["idx"])
        self.update_incident(iid, actions_json=acts, protocol_id=cond_id)

    def give_medicine(self, iid, aid, item, qty, notes=""):
        self.db.execute("INSERT INTO em_medication_log(incident_id,astronaut_id,item_id,item_name,quantity,given_at,notes) VALUES(?,?,?,?,?,?,?)",
                        (iid, aid, item["id"], item["name"], qty, now_utc(), notes))
        self.db.commit()
        self.inv_update(item["id"], available=item["available"] - qty)

    def meds(self, iid=None, aid=None):
        q, a = "SELECT * FROM em_medication_log", ()
        if iid is not None: q += " WHERE incident_id=?"; a = (iid,)
        elif aid is not None: q += " WHERE astronaut_id=?"; a = (aid,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY given_at", a)]

def similar_incidents(store, aid, symset, top_id, exclude=None):
    res = []
    for r in store.incidents(aid):
        if r["id"] == exclude: continue
        prev = set(json.loads(r["symptoms_json"] or "[]")); inter = len(prev & symset)
        if not prev or not symset: continue
        jac = inter / len(prev | symset)
        if (jac >= 0.4 and inter >= 2) or (r["top_condition_id"] == top_id and top_id and inter >= 1):
            res.append((jac, r))
    res.sort(key=lambda x: x[1]["started_at"], reverse=True)
    return [r for _, r in res[:3]]

def find_db():
    if "--db" in sys.argv:
        return sys.argv[sys.argv.index("--db") + 1]
    if os.environ.get("ASTRA_DB"): return os.environ["ASTRA_DB"]
    for c in ("astro_health.db", "health_monitor.db", "astronaut_health.db", "health.db", "astromedic.db"):
        if os.path.exists(c): return os.path.abspath(c)
    return os.path.abspath("astro_health.db")

# ============================================================================
# 3. PDF REPORT (ReportLab)
# ============================================================================
def build_report(store, aid, path, incident_ids=None, include_monitor=True, include_inventory=True):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=ss["Title"], textColor=colors.HexColor("#0b2a5b"), fontSize=19, alignment=0)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#0b2a5b"), spaceBefore=10)
    B = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    S = ParagraphStyle("s", parent=B, fontSize=8, textColor=colors.HexColor("#444444"), leading=10)
    def P(t, st=B): return Paragraph(xesc(str(t if t is not None else "-")).replace("\n", "<br/>"), st)
    def tbl(rows, widths, head=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
        st = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa7bd")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if head: st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe8f7"))]
        else: st += [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef3fb"))]
        t.setStyle(TableStyle(st)); return t
    W = A4[0] - 30 * mm
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title="ASTRA Emergency Medical Summary", author="ASTRA Emergency Response")
    def footer(c, d):
        c.saveState(); c.setFont("Helvetica", 7); c.setFillColor(colors.grey)
        c.drawString(15 * mm, 8 * mm, "ASTRA decision-support output - not a diagnosis. For review by ground medical professionals.")
        c.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {d.page}"); c.restoreState()
    name = store.astro_name(aid)
    el = [Paragraph("ASTRA Emergency Medical Summary", H1),
          P(f"Generated {now_utc()} UTC  |  Astronaut: {name}  |  Source database: {os.path.basename(store.path)}", S),
          Spacer(1, 4), P(DISCLAIMER, S), Paragraph("1. Astronaut details", H2)]
    det = store.astronaut(aid)
    rows = [[P(k), P(v)] for k, v in det.items()] or [[P("ID"), P(aid)]]
    el.append(tbl(rows, [45 * mm, W - 45 * mm], head=False))
    incs = [store.incident(i) for i in incident_ids] if incident_ids else store.incidents(aid)
    incs = [i for i in incs if i]
    el.append(Paragraph("2. Emergency history overview", H2))
    if incs:
        rows = [[P("#"), P("Date/time (UTC)"), P("Top possible condition"), P("Severity"), P("Status")]]
        for i in incs: rows.append([P(i["id"]), P(i["started_at"]), P(i["top_condition"] or "-"), P(TIERS.get(i["severity"], ("-",))[0]), P(i["status"])])
        el.append(tbl(rows, [10 * mm, 38 * mm, W - 98 * mm, 28 * mm, 22 * mm]))
    else:
        el.append(P("No emergency incidents recorded."))
    el.append(Paragraph("3. Incident details and hand-over notes", H2))
    for i in incs:
        sy = json.loads(i["symptoms_json"] or "[]"); vit = json.loads(i["vitals_json"] or "{}")
        rk = json.loads(i["conditions_json"] or "[]"); acts = json.loads(i["actions_json"] or "{}")
        meds = store.meds(iid=i["id"])
        vt = ", ".join(f"{k}: {v}" for k, v in vit.items() if v is not None) or "not recorded"
        possible = "\n".join(f"{r['name']} - {r['strength']} ({int(r['score']*100)}% symptom match)" for r in rk[:5]) or "none"
        done = []
        for cid, lst in acts.items():
            for s in lst: done.append(f"{s['done_at'][11:19]} UTC  [{CONDITIONS[cid]['name'][:30] if cid in CONDITIONS else cid}] {s['text'][:110]}")
        rows = [[P("Time of onset"), P(i["started_at"] + " UTC")],
                [P("Symptoms"), P("; ".join(SYM_LABEL.get(s, s) for s in sy) or "-")],
                [P("Vitals entered"), P(vt)],
                [P("Possible conditions (decision support, not a diagnosis)"), P(possible)],
                [P("Actions taken (checked steps, with time)"), P("\n".join(done) or "No steps recorded as done")],
                [P("Medicines administered"), P("\n".join(f"{m['given_at']} UTC - {m['item_name']} x{m['quantity']} {m['notes'] or ''}" for m in meds) or "None recorded")],
                [P("Status / outcome"), P(f"{i['status']}  {i['ended_at'] or ''}\n{i['outcome_notes'] or ''}")],
                [P("Open questions for ground"), P("")]]
        el.append(KeepTogether([P(f"Incident #{i['id']} - {i['top_condition'] or 'unclassified'}", ParagraphStyle('ih', parent=B, fontName='Helvetica-Bold', fontSize=10, spaceBefore=6, spaceAfter=3)),
                                tbl(rows, [48 * mm, W - 48 * mm], head=False)]))
    if len(incs) > 1 or not incids_single(incident_ids):
        allm = store.meds(aid=aid)
        el.append(Paragraph("4. Medicines administered (all incidents)", H2))
        if allm:
            el.append(tbl([[P("Time (UTC)"), P("Incident"), P("Item"), P("Qty")]] + [[P(m["given_at"]), P(m["incident_id"]), P(m["item_name"]), P(m["quantity"])] for m in allm],
                          [38 * mm, 18 * mm, W - 66 * mm, 10 * mm]))
        else: el.append(P("None recorded."))
    if include_monitor:
        el.append(Paragraph("5. Health-monitoring records (latest, from shared database)", H2))
        recs = store.monitor_latest(aid)
        if not recs: el.append(P("No health-monitoring tables/records found for this astronaut in the shared database."))
        for t, row in recs:
            kv = [[P(k), P(v)] for k, v in row.items() if v not in (None, "")]
            el.append(KeepTogether([P(f"Table: {t}", ParagraphStyle('th', parent=B, fontName='Helvetica-Bold', spaceBefore=4)), tbl(kv, [60 * mm, W - 60 * mm], head=False)]))
    if include_inventory:
        el.append(Paragraph("6. Medical kit - items low or unavailable", H2))
        bad = [r for r in store.inv_all() if r["status"] != "Available"]
        el.append(tbl([[P("Item"), P("Min"), P("Avail."), P("Status"), P("Location")]] + [[P(r["name"]), P(r["min_required"]), P(r["available"]), P(r["status"]), P(r["location"])] for r in bad],
                      [60 * mm, 12 * mm, 14 * mm, 24 * mm, W - 110 * mm]) if bad else P("All tracked kit items are at or above minimum."))
    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return path

def incids_single(ids): return bool(ids) and len(ids) == 1

# ===== GUI BELOW =====

from collections import Counter
from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QSettings, Signal, QPointF, QRectF
from PySide6.QtGui import (QPainter, QColor, QFont, QPen, QBrush, QLinearGradient, QRadialGradient, QPixmap,
                           QDesktopServices, QPolygonF)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
                               QFrame, QComboBox, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QCheckBox, QSpinBox,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QTextBrowser, QTabWidget,
                               QMessageBox, QFileDialog, QDialog, QFormLayout, QSlider, QRadioButton, QButtonGroup,
                               QSizePolicy, QDialogButtonBox)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:
    QWebEngineView = None
try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:
    QSoundEffect = None

# ============================================================================
# 4. THEME (matches the Astro Health Monitor look: dark space navy, glowing cards, colour-coded tiles)
# ============================================================================
BLUE, GREEN, PURPLE, TEAL, RED, AMBER = "#2f7bff", "#19c39a", "#8b5cf6", "#14b8c4", "#ff4d5e", "#ffb020"
TXT, MUTED, CARD = "#e8f0ff", "#8fa8d6", "rgba(8,22,54,0.90)"

def rgba(h, a):
    h = h.lstrip("#"); return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"

QSS = f"""
* {{ font-family: 'Segoe UI','Roboto','Helvetica Neue',Arial; color:{TXT}; font-size:13px; }}
QLabel {{ background:transparent; }}
QToolTip {{ background:#0a1a3d; color:{TXT}; border:1px solid {BLUE}; }}
QLineEdit, QComboBox, QSpinBox {{ background:rgba(5,15,40,0.95); border:1px solid #27508f; border-radius:8px; padding:7px 10px; selection-background-color:{BLUE}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border:1px solid {BLUE}; }}
QComboBox::drop-down {{ border:none; width:26px; }}
QComboBox QAbstractItemView {{ background:#07122e; border:1px solid {BLUE}; selection-background-color:{BLUE}; }}
QPushButton {{ background:{rgba(BLUE,0.25)}; border:1px solid {BLUE}; border-radius:9px; padding:8px 16px; font-weight:600; }}
QPushButton:hover {{ background:{rgba(BLUE,0.45)}; }}
QPushButton:disabled {{ color:#5d6f93; border-color:#2a3a5e; background:rgba(20,30,60,0.5); }}
QPushButton#danger {{ background:{rgba(RED,0.30)}; border:1px solid {RED}; font-size:15px; padding:12px 18px; }}
QPushButton#danger:hover {{ background:{rgba(RED,0.55)}; }}
QPushButton#go {{ background:{rgba(GREEN,0.28)}; border:1px solid {GREEN}; }}
QPushButton#go:hover {{ background:{rgba(GREEN,0.5)}; }}
QListWidget, QTextBrowser, QTableWidget {{ background:rgba(4,12,34,0.95); border:1px solid #27508f; border-radius:8px; }}
QListWidget::item {{ padding:5px 6px; }} QListWidget::item:hover {{ background:{rgba(BLUE,0.25)}; }}
QHeaderView::section {{ background:#0d2350; color:{TXT}; border:none; border-bottom:1px solid #27508f; padding:7px; font-weight:700; }}
QTableWidget {{ gridline-color:#14305f; alternate-background-color:rgba(12,30,70,0.6); }}
QTableWidget::item:selected {{ background:{rgba(BLUE,0.45)}; }}
QCheckBox::indicator {{ width:18px; height:18px; border:1px solid {BLUE}; border-radius:5px; background:#06122c; }}
QCheckBox::indicator:checked {{ background:{GREEN}; border-color:{GREEN}; }}
QScrollArea {{ border:none; background:transparent; }} QScrollArea > QWidget > QWidget {{ background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:11px; }} QScrollBar::handle:vertical {{ background:#27508f; border-radius:5px; min-height:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QTabWidget::pane {{ border:1px solid #27508f; border-radius:10px; }}
QTabBar::tab {{ background:rgba(10,26,61,0.9); padding:9px 18px; border:1px solid #27508f; border-top-left-radius:8px; border-top-right-radius:8px; margin-right:3px; }}
QTabBar::tab:selected {{ background:{rgba(BLUE,0.45)}; }}
QSlider::groove:horizontal {{ height:6px; background:#12306a; border-radius:3px; }}
QSlider::handle:horizontal {{ background:{RED}; width:20px; margin:-8px 0; border-radius:10px; }}
"""

def card(color="#1f4a9a", fill=CARD):
    f = QFrame(); f.setObjectName("card")
    f.setStyleSheet(f"QFrame#card{{background:{fill}; border:1.5px solid {color}; border-radius:14px;}}")
    return f

def chip(text, color, size=11):
    l = QLabel(text); l.setWordWrap(True)
    l.setStyleSheet(f"background:{rgba(color,0.22)}; color:{color}; border:1px solid {color}; border-radius:9px; padding:3px 9px; font-size:{size}px; font-weight:700;")
    return l

def title(text, size=22, color=TXT, bold=True):
    l = QLabel(text); l.setStyleSheet(f"font-size:{size}px; font-weight:{'700' if bold else '400'}; color:{color};"); l.setWordWrap(True); return l

def scrolled(w):
    s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(w); s.setFrameShape(QFrame.Shape.NoFrame); return s

def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget(): it.widget().deleteLater()
        elif it.layout(): clear_layout(it.layout())

def logo_pixmap(sz=58):
    p = QPixmap(sz, sz); p.fill(Qt.GlobalColor.transparent); q = QPainter(p); q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setPen(QPen(QColor("#9fc4ff"), 2, Qt.PenStyle.DotLine)); q.drawEllipse(2, 2, sz - 4, sz - 4)
    q.setPen(QPen(QColor(RED), 3)); c = sz / 2
    pts = [(10, c), (20, c), (25, c - 14), (31, c + 14), (36, c - 6), (40, c), (sz - 10, c)]
    q.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts])); q.end(); return p

def astronaut_pixmap(sz=64):
    p = QPixmap(sz, sz); p.fill(Qt.GlobalColor.transparent); q = QPainter(p); q.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QRadialGradient(sz * .4, sz * .35, sz * .6); g.setColorAt(0, QColor("#ffffff")); g.setColorAt(1, QColor("#b9c4dc"))
    q.setBrush(QBrush(g)); q.setPen(QPen(QColor("#7d8cab"), 2)); q.drawEllipse(QRectF(sz * .08, sz * .06, sz * .84, sz * .84))
    v = QLinearGradient(0, sz * .25, sz, sz * .7); v.setColorAt(0, QColor("#0a1230")); v.setColorAt(1, QColor("#274b9a"))
    q.setBrush(QBrush(v)); q.setPen(Qt.PenStyle.NoPen); q.drawEllipse(QRectF(sz * .2, sz * .24, sz * .6, sz * .46))
    q.setBrush(QColor(255, 255, 255, 120)); q.drawEllipse(QRectF(sz * .3, sz * .3, sz * .14, sz * .08)); q.end(); return p

class Background(QWidget):
    def __init__(self):
        super().__init__(); rnd = random.Random(7)
        self.stars = [(rnd.random(), rnd.random(), rnd.random()) for _ in range(160)]
    def paintEvent(self, e):
        p = QPainter(self); w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h); g.setColorAt(0, QColor("#030816")); g.setColorAt(.6, QColor("#07163a")); g.setColorAt(1, QColor("#0a2a63"))
        p.fillRect(self.rect(), g)
        for x, y, s in self.stars:
            p.setPen(QPen(QColor(200, 220, 255, int(70 + 150 * s)), 1 + (s > .9))); p.drawPoint(int(x * w), int(y * h * .85))
        glow = QRadialGradient(w / 2, h * 1.15, h * .55); glow.setColorAt(0, QColor(40, 120, 255, 90)); glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

class Tile(QFrame):
    clicked = Signal()
    def __init__(self, glyph, head, sub, color, big=True):
        super().__init__(); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("tile")
        self.setStyleSheet(f"QFrame#tile{{background:{rgba(color,0.16)}; border:1.5px solid {color}; border-radius:14px;}} QFrame#tile:hover{{background:{rgba(color,0.34)};}}")
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 12, 16, 12)
        g = QLabel(glyph); g.setStyleSheet(f"font-size:{34 if big else 26}px; color:{color};"); g.setFixedWidth(56 if big else 44); g.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QVBoxLayout(); t.setSpacing(2); a = QLabel(head); a.setStyleSheet(f"font-size:{17 if big else 14}px; font-weight:700;")
        b = QLabel(sub); b.setStyleSheet(f"color:{MUTED}; font-size:12px;"); b.setWordWrap(True); t.addWidget(a); t.addWidget(b)
        ar = QLabel("\u279C"); ar.setStyleSheet(f"font-size:22px; color:{color};")
        lay.addWidget(g); lay.addLayout(t, 1); lay.addWidget(ar)
        self.setMinimumHeight(86 if big else 70)
    def mousePressEvent(self, e): self.clicked.emit()

# ============================================================================
# 5. SOUND (generated tones - no external files)
# ============================================================================
def make_wav(path, freq, ms, vol=0.6):
    sr = 44100; n = int(sr * ms / 1000)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(32767 * vol * math.sin(2 * math.pi * freq * i / sr) * min(1, i / (sr * .003 + 1), (n - i) / (sr * .01)))) for i in range(n)))

class Beeper:
    def __init__(self):
        self.fx = {}
        if QSoundEffect:
            d = tempfile.gettempdir()
            for k, f, ms in (("tick", 1000, 60), ("swap", 600, 350)):
                p = os.path.join(d, f"astra_{k}.wav"); make_wav(p, f, ms)
                s = QSoundEffect(); s.setSource(QUrl.fromLocalFile(p)); s.setVolume(0.9); self.fx[k] = s
    def play(self, k="tick"):
        if k in self.fx: self.fx[k].play()
        else: QApplication.beep()

# ============================================================================
# 6. SYMPTOM PICKER (searchable multi-select dropdown)
# ============================================================================
class ClickLine(QLineEdit):
    clicked = Signal()
    def mousePressEvent(self, e): super().mousePressEvent(e); self.clicked.emit()

class SymptomPicker(QWidget):
    changed = Signal()
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        row = QHBoxLayout(); self.search = ClickLine(); self.search.setPlaceholderText("Search & select symptoms...  (e.g. bleeding, breathing, vision, chest)")
        self.btn = QPushButton("\u25BE"); self.btn.setFixedWidth(44); row.addWidget(self.search, 1); row.addWidget(self.btn); lay.addLayout(row)
        self.list = QListWidget(); self.list.setMinimumHeight(230); self.list.setMaximumHeight(300); lay.addWidget(self.list)
        self.chipbox = QGridLayout(); self.chipbox.setSpacing(6); lay.addLayout(self.chipbox)
        self.items = {}; group = None; self.headers = {}
        for sid, label, g in SYMPTOMS:
            if g != group:
                group = g; h = QListWidgetItem(g.upper()); h.setFlags(Qt.ItemFlag.NoItemFlags); f = h.font(); f.setBold(True); h.setFont(f)
                h.setForeground(QColor(BLUE)); self.list.addItem(h); self.headers[g] = h
            it = QListWidgetItem(label); it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Unchecked); it.setData(Qt.ItemDataRole.UserRole, sid); it.setData(Qt.ItemDataRole.UserRole + 1, g)
            self.list.addItem(it); self.items[sid] = it
        self.list.itemClicked.connect(self._toggle); self.list.itemChanged.connect(self._rebuild)
        self.search.textChanged.connect(self._filter); self.search.clicked.connect(lambda: self.list.setVisible(True))
        self.btn.clicked.connect(lambda: self.list.setVisible(not self.list.isVisible()))
        self.list.setVisible(False)
    def _toggle(self, it):
        if it.data(Qt.ItemDataRole.UserRole) is None: return
        it.setCheckState(Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
    def _filter(self, text):
        self.list.setVisible(True); t = text.strip().lower(); vis = set()
        for sid, it in self.items.items():
            ok = (t in it.text().lower()) or (t in sid); it.setHidden(not ok)
            if ok: vis.add(it.data(Qt.ItemDataRole.UserRole + 1))
        for g, h in self.headers.items(): h.setHidden(g not in vis)
    def selected(self):
        return {sid for sid, it in self.items.items() if it.checkState() == Qt.CheckState.Checked}
    def clear(self):
        for it in self.items.values(): it.setCheckState(Qt.CheckState.Unchecked)
        self.search.clear()
    def _rebuild(self, *_):
        clear_layout(self.chipbox)
        for i, sid in enumerate(sorted(self.selected(), key=lambda s: list(self.items).index(s))):
            b = QPushButton(f"{SYM_LABEL[sid]}  \u2715"); b.setStyleSheet(f"background:{rgba(RED,0.2)}; border:1px solid {RED}; border-radius:12px; padding:4px 10px; font-size:12px;")
            b.clicked.connect(lambda _=False, s=sid: self.items[s].setCheckState(Qt.CheckState.Unchecked)); self.chipbox.addWidget(b, i // 2, i % 2)
        self.changed.emit()

# ============================================================================
# 7. PAGES
# ============================================================================
def avail_chip(store, name):
    it = store.inv_item(name)
    if not it: return chip("NOT IN KIT LIST - not available. Ask ground for an alternative.", RED), False
    if it["available"] <= 0: return chip(f"\u26D4 NOT AVAILABLE - do not rely on it. Ask ground for an alternative.  (listed at: {it['location']})", RED), False
    if it["available"] < it["min_required"]: return chip(f"\u26A0 LOW STOCK ({it['available']} left)  \u2022  Location: {it['location']}", AMBER), True
    return chip(f"\u2714 Available \u00D7{it['available']}  \u2022  Location: {it['location']}", GREEN), True

class HomePage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app
        v = QVBoxLayout(self); v.setSpacing(14)
        v.addWidget(title("Emergency Response Console", 28), 0, Qt.AlignmentFlag.AlignHCenter)
        sub = QLabel("Identify  \u2022  Act  \u2022  Stabilise  -  step-by-step help for crew without medical training"); sub.setStyleSheet(f"color:#7fd0ff; font-size:15px; font-style:italic;"); v.addWidget(sub, 0, Qt.AlignmentFlag.AlignHCenter)
        hero = card(BLUE); h = QHBoxLayout(hero); h.setContentsMargins(20, 12, 20, 12)
        av = QLabel(); av.setPixmap(astronaut_pixmap(84)); self.name = title("", 30); self.role = QLabel(""); self.role.setStyleSheet(f"color:{MUTED};")
        col = QVBoxLayout(); lab = QLabel("Selected Astronaut"); lab.setStyleSheet(f"color:{MUTED}; font-size:14px;"); col.addWidget(lab); col.addWidget(self.name); col.addWidget(self.role)
        h.addWidget(av); h.addSpacing(10); h.addLayout(col, 1)
        self.stat = QLabel(); self.stat.setStyleSheet(f"color:{MUTED};"); self.stat.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); h.addWidget(self.stat)
        v.addWidget(hero)
        v.addWidget(app.quick_row())
        g = QGridLayout(); g.setSpacing(12)
        specs = [("\u271A", "Emergency Assessment", "Select symptoms -> possible conditions -> step-by-step help", RED, 1),
                 ("\u2665", "CPR & Procedures", "Metronome, AED and microgravity CPR technique", AMBER, 3),
                 ("\u2695", "Medical Kit Inventory", "Counts, storage locations and automatic status", GREEN, 4),
                 ("\u29D6", "Incident History", "Past emergencies, patterns and trends", PURPLE, 5),
                 ("\u2637", "Medical Report (PDF)", "Summary for ground-based medical professionals", TEAL, 6)]
        for i, (gl, hd, sb, col_, pg) in enumerate(specs):
            t = Tile(gl, hd, sb, col_); t.clicked.connect(lambda p=pg: app.go(p)); g.addWidget(t, i // 2, i % 2)
        v.addLayout(g)
        row = QHBoxLayout(); row.setSpacing(12)
        self.c_mon, self.b_mon = self._info("Health-monitor snapshot (shared database)", BLUE)
        self.c_hist, self.b_hist = self._info("Emergency history", PURPLE)
        self.c_kit, self.b_kit = self._info("Medical-kit alerts", AMBER)
        for c in (self.c_mon, self.c_hist, self.c_kit): row.addWidget(c)
        v.addLayout(row)
        foot = QHBoxLayout(); self.dbl = QLabel(); self.dbl.setStyleSheet(f"color:{MUTED}; font-size:11px;"); b = QPushButton("Change shared database..."); b.clicked.connect(app.change_db)
        foot.addWidget(self.dbl, 1); foot.addWidget(b); v.addLayout(foot)
        dis = QLabel(DISCLAIMER); dis.setWordWrap(True); dis.setStyleSheet(f"color:#6b82b0; font-size:11px;"); v.addWidget(dis)
    def _info(self, head, color):
        c = card(color); l = QVBoxLayout(c); l.addWidget(title(head, 14, color)); b = QTextBrowser(); b.setMinimumHeight(150); b.setMaximumHeight(190); l.addWidget(b); return c, b
    def refresh(self):
        a = self.app; st = a.store; d = st.astronaut(a.aid)
        self.name.setText(st.astro_name(a.aid))
        self.role.setText(" \u2022 ".join(str(d[k]) for k in d if k.lower() in ("role", "position", "rank", "mission", "title") and d[k]) or f"Crew ID {a.aid}")
        incs = st.incidents(a.aid); self.stat.setText(f"{len(incs)} recorded incident(s)\nDB: {os.path.basename(st.path)}")
        rec = st.monitor_latest(a.aid); html = ""
        for t, row in rec[:4]:
            kv = [(k, v) for k, v in row.items() if v not in (None, "") and not k.lower().endswith("id") and k.lower() != "id"][:7]
            html += f"<b style='color:{BLUE}'>{xesc(t)}</b><br>" + "<br>".join(f"&nbsp;{xesc(k)}: {xesc(str(v))}" for k, v in kv) + "<br>"
        self.b_mon.setHtml(html or f"<span style='color:{MUTED}'>No health-monitor records found for this astronaut in the shared database yet.</span>")
        self.b_hist.setHtml("".join(f"<b>{i['started_at'][:16]}</b> - {xesc(i['top_condition'] or 'unclassified')} <span style='color:{TIERS.get(i['severity'],('','#888'))[1]}'>[{TIERS.get(i['severity'],('-',))[0]}]</span><br>" for i in incs[:5])
                           or f"<span style='color:{MUTED}'>No emergencies recorded.</span>")
        bad = [r for r in st.inv_all() if r["status"] != "Available"]
        self.b_kit.setHtml("".join(f"<span style='color:{RED if r['status']=='Unavailable' else AMBER}'>\u25CF {r['status']}</span> {xesc(r['name'])} ({r['available']}/{r['min_required']})<br>" for r in bad[:8])
                           or f"<span style='color:{GREEN}'>All kit items at or above minimum.</span>")
        self.dbl.setText("Shared database: " + st.path)

class EmergencyPage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app
        v = QVBoxLayout(self); v.setSpacing(12)
        v.addWidget(self.app.quick_row())
        row = QHBoxLayout(); row.setSpacing(14)
        left = card(RED); l = QVBoxLayout(left); l.setContentsMargins(16, 14, 16, 14)
        l.addWidget(title("SYMPTOM CHECKER", 19, RED)); n = QLabel("Select every symptom you see. The combination is analysed. This suggests POSSIBLE conditions only - it is not a diagnosis."); n.setWordWrap(True); n.setStyleSheet(f"color:{MUTED};"); l.addWidget(n)
        self.picker = SymptomPicker(); l.addWidget(self.picker)
        l.addWidget(title("Measured values (optional)", 14, BLUE))
        g = QGridLayout(); self.vs = {}
        for i, (k, lab, mx) in enumerate((("rr", "Breaths/min", 80), ("hr", "Pulse (bpm)", 250), ("sys", "BP top", 300), ("dia", "BP bottom", 200), ("spo2", "SpO2 %", 100))):
            sp = QSpinBox(); sp.setRange(0, mx); sp.setSpecialValueText("-"); self.vs[k] = sp
            g.addWidget(QLabel(lab), 0, i); g.addWidget(sp, 1, i)
        l.addLayout(g)
        self.mon = QLabel(); self.mon.setStyleSheet(f"color:{MUTED}; font-size:11px;"); l.addWidget(self.mon)
        b = QHBoxLayout(); a = QPushButton("ANALYSE SYMPTOMS"); a.setObjectName("danger"); a.clicked.connect(self.run)
        c = QPushButton("Clear"); c.clicked.connect(self.clear); nw = QPushButton("New incident"); nw.clicked.connect(app.new_incident_clicked)
        b.addWidget(a, 1); b.addWidget(c); b.addWidget(nw); l.addLayout(b); l.addStretch()
        right = card(AMBER); r = QVBoxLayout(right); r.setContentsMargins(16, 14, 16, 14); r.addWidget(title("POSSIBLE CONDITIONS", 19, AMBER))
        self.res = QVBoxLayout(); w = QWidget(); w.setLayout(self.res); r.addWidget(scrolled(w), 1)
        row.addWidget(left, 5); row.addWidget(right, 6); v.addLayout(row, 1)
        self.empty()
    def empty(self):
        clear_layout(self.res); t = QLabel("Results appear here after you analyse symptoms.\nIf someone is unresponsive and not breathing, do not wait - use the red CPR button above."); t.setStyleSheet(f"color:{MUTED};"); t.setWordWrap(True); self.res.addWidget(t); self.res.addStretch()
    def vitals(self):
        return {k: (s.value() or None) for k, s in self.vs.items()}
    def clear(self):
        self.picker.clear(); [s.setValue(0) for s in self.vs.values()]; self.empty()
    def refresh(self):
        mv = self.app.store.monitor_vitals(self.app.aid)
        self.mon.setText("Latest from health monitor: " + ", ".join(f"{k.upper()} {v:g}" for k, v in mv.items()) if mv else "No recent vitals found in the health monitor database.")
        self.empty() if not self.picker.selected() else None
    def run(self):
        sel = self.picker.selected(); vit = self.vitals()
        if not sel and not derive_from_vitals(vit):
            QMessageBox.information(self, "Select symptoms", "Please select at least one symptom (or enter measured values)."); return
        ranked, derived = analyze(sel, vit); self.app.save_analysis(sel, vit, ranked)
        clear_layout(self.res)
        if not ranked:
            m = QLabel("No clear match for this combination. Add more symptoms, or contact ground (MCC-H) if you are worried.\nSymptoms recorded."); m.setWordWrap(True); self.res.addWidget(m); self.res.addStretch(); return
        if any(r["tier"] == 1 and r["score"] >= 0.45 for r in ranked):
            self.res.addWidget(chip("\u26A0 POSSIBLE LIFE-THREATENING EMERGENCY - ACT NOW. Page the crew and contact ground (MCC-H).", RED, 13))
        if derived: self.res.addWidget(chip("Also considered from your measured values: " + ", ".join(SYM_LABEL[d] for d in derived), BLUE))
        top = ranked[0]
        alert = self.app.history_alert(sel | derived, top["id"])
        if alert: self.res.addWidget(alert)
        for r in ranked[:6]:
            tn, tc = TIERS[r["tier"]]; f = card(tc); l = QVBoxLayout(f); hdr = QHBoxLayout()
            hdr.addWidget(chip(tn, tc)); hdr.addWidget(chip(r["strength"] + f"  ({int(r['score']*100)}% symptom match)", MUTED)); hdr.addStretch(); l.addLayout(hdr)
            l.addWidget(title(r["name"], 16)); m = QLabel("Matches: " + "; ".join(r["matched"])); m.setWordWrap(True); m.setStyleSheet(f"color:{MUTED}; font-size:12px;"); l.addWidget(m)
            bt = QPushButton("OPEN STEP-BY-STEP PROTOCOL  \u279C"); bt.setObjectName("danger" if r["tier"] == 1 else "go"); bt.clicked.connect(lambda _=False, c=r["id"]: self.app.open_protocol(c)); l.addWidget(bt)
            self.res.addWidget(f)
        self.res.addWidget(chip("Decision support only - not a diagnosis. Order reflects severity and symptom match.", MUTED, 10)); self.res.addStretch()

class ProtocolPage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app; self.cid = None; self.body = None
        self.outer = QVBoxLayout(self); self.outer.setContentsMargins(0, 0, 0, 0)
    def refresh(self):
        if self.cid and self.app.inc_id is not None and self.app.store.incident(self.app.inc_id): self.show_condition(self.cid)
    def show_condition(self, cid):
        a = self.app; st = a.store; self.cid = cid; c = CONDITIONS[cid]; iid = a.ensure_incident(cid)
        if self.body: self.body.setParent(None); self.body.deleteLater()
        self.body = QWidget(); v = QVBoxLayout(self.body); v.setSpacing(10)
        tn, tc = TIERS[c["tier"]]
        top = QHBoxLayout(); back = QPushButton("\u2190 Back to assessment"); back.clicked.connect(lambda: a.go(1)); top.addWidget(back)
        top.addWidget(chip(tn, tc, 13)); top.addWidget(title(c["name"], 21), 1); top.addWidget(chip(f"Incident #{iid}", BLUE)); v.addLayout(top)
        if c["tier"] <= 2: v.addWidget(chip("\u260E CALL GROUND (MCC-H) as early as the steps say - request a private medical conference (PMC). Page the crew.", tc, 13))
        inc = st.incident(iid); alert = a.history_alert(set(json.loads(inc["symptoms_json"] or "[]")), cid, exclude=iid)
        if alert: v.addWidget(alert)
        row = QHBoxLayout(); row.setSpacing(12)
        done = {s["idx"] for s in json.loads(inc["actions_json"] or "{}").get(cid, [])}
        sw = QWidget(); sl = QVBoxLayout(sw); sl.setSpacing(8)
        for i, (txt, item) in enumerate(c["steps"]):
            f = card(tc if i == 0 else "#27508f"); fl = QVBoxLayout(f); hl = QHBoxLayout()
            n = QLabel(str(i + 1)); n.setFixedSize(38, 38); n.setAlignment(Qt.AlignmentFlag.AlignCenter); n.setStyleSheet(f"background:{rgba(tc,0.25)}; border:2px solid {tc}; border-radius:19px; font-size:17px; font-weight:800;")
            t = QLabel(txt); t.setWordWrap(True); t.setStyleSheet("font-size:15px;"); hl.addWidget(n, 0, Qt.AlignmentFlag.AlignTop); hl.addWidget(t, 1)
            cb = QCheckBox("Done"); cb.setChecked(i in done); cb.toggled.connect(lambda on, ix=i, tx=txt: st.set_step(iid, cid, ix, tx, on)); hl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
            fl.addLayout(hl)
            if item: ch, _ = avail_chip(st, item); lab = QLabel(f"Uses: {item}"); lab.setStyleSheet(f"color:{MUTED}; font-size:11px;"); fl.addWidget(lab); fl.addWidget(ch)
            sl.addWidget(f)
        sl.addStretch(); row.addWidget(scrolled(sw), 6)
        pw = QWidget(); pl = QVBoxLayout(pw); pl.setSpacing(10)
        if c["cpr"]:
            bt = QPushButton("\u25B6  OPEN CPR METRONOME"); bt.setObjectName("danger"); bt.clicked.connect(lambda: a.go(3)); pl.addWidget(bt)
        ev = card(BLUE); el = QVBoxLayout(ev); el.addWidget(title("Evidence", 13, BLUE)); e = QLabel(c["ev"]); e.setWordWrap(True); el.addWidget(e)
        if c["unv"] or "UNVERIFIED" in c["ev"]: el.addWidget(chip("UNVERIFIED general first-aid content - confirm with ground.", AMBER))
        if c["gap"]: g = QLabel("Gaps: " + c["gap"]); g.setWordWrap(True); g.setStyleSheet(f"color:{AMBER}; font-size:12px;"); el.addWidget(g)
        pl.addWidget(ev)
        if c["warn"]:
            wc = card(RED); wl = QVBoxLayout(wc); wl.addWidget(title("WARNINGS", 13, RED))
            for w_ in c["warn"]: x = QLabel("\u26A0 " + w_); x.setWordWrap(True); wl.addWidget(x)
            pl.addWidget(wc)
        kc = card(GREEN); kl = QVBoxLayout(kc); kl.addWidget(title("Items needed - live kit status", 13, GREEN))
        seen = []
        for _, it in c["steps"]:
            if it and it not in seen:
                seen.append(it); ch, _ok = avail_chip(st, it); x = QLabel(it); x.setWordWrap(True); x.setStyleSheet("font-weight:600;"); kl.addWidget(x); kl.addWidget(ch)
        pl.addWidget(kc)
        mc = card(PURPLE); ml = QVBoxLayout(mc); ml.addWidget(title("Record medicine / item used", 13, PURPLE))
        self.cmb = QComboBox(); avail = [r for r in st.inv_all() if r["available"] > 0]
        for it in sorted(avail, key=lambda r: (r["name"] not in seen, r["name"])): self.cmb.addItem(f"{it['name']}  (x{it['available']})", it["id"])
        q = QSpinBox(); q.setRange(1, 99); notes = QLineEdit(); notes.setPlaceholderText("Dose / route / notes (as directed by ground)")
        gv = QPushButton("Record as administered (reduces kit count)"); gv.setObjectName("go")
        def give():
            iid_ = self.cmb.currentData()
            if iid_ is None: return
            item = next(r for r in st.inv_all() if r["id"] == iid_)
            st.give_medicine(iid, a.aid, item, q.value(), notes.text()); self.show_condition(cid)
        gv.clicked.connect(give); ml.addWidget(self.cmb); ml.addWidget(q); ml.addWidget(notes); ml.addWidget(gv)
        for m in st.meds(iid=iid): x = QLabel(f"{m['given_at'][11:19]} UTC - {m['item_name']} \u00D7{m['quantity']}"); x.setStyleSheet(f"color:{MUTED}; font-size:12px;"); ml.addWidget(x)
        pl.addWidget(mc)
        cc = card("#27508f"); cl = QVBoxLayout(cc); out = QLineEdit(); out.setPlaceholderText("Outcome / notes for ground"); out.setText(inc["outcome_notes"] or ""); cl.addWidget(out)
        cb2 = QPushButton("Close incident (save notes)")
        def close():
            st.update_incident(iid, status="CLOSED", ended_at=now_utc(), outcome_notes=out.text()); a.inc_id = None; a.go(1); a.refresh_all(); QMessageBox.information(self, "Saved", "Incident closed and saved to the shared database.")
        cb2.clicked.connect(close); cl.addWidget(cb2); pl.addWidget(cc); pl.addStretch()
        row.addWidget(scrolled(pw), 4); v.addLayout(row, 1); self.outer.addWidget(self.body)

class PulseWidget(QWidget):
    def __init__(self):
        super().__init__(); self.setMinimumSize(300, 300); self.phase = 0.0; self.main = "READY"; self.small = "press START"
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); w, h = self.width(), self.height(); r = min(w, h) * .36; c = QPointF(w / 2, h / 2)
        for k, a in ((1.45, 25), (1.25, 55), (1.0, 140)):
            rr = r * (k + .12 * self.phase * (1 if k > 1 else 0)); g = QRadialGradient(c, rr); col = QColor(RED); col.setAlpha(int(a + 90 * self.phase)); g.setColorAt(.75, col); g.setColorAt(1, QColor(255, 77, 94, 0))
            p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(c, rr, rr)
        p.setBrush(QColor(60, 8, 20)); p.setPen(QPen(QColor(RED), 4)); p.drawEllipse(c, r, r)
        p.setPen(QColor("white")); p.setFont(QFont("Segoe UI", int(r / 2.2), QFont.Weight.Bold)); p.drawText(QRectF(c.x() - r, c.y() - r * .6, 2 * r, r), Qt.AlignmentFlag.AlignCenter, self.main)
        p.setFont(QFont("Segoe UI", max(9, int(r / 8)))); p.setPen(QColor("#ffd0d6")); p.drawText(QRectF(c.x() - r, c.y() + r * .25, 2 * r, r * .5), Qt.AlignmentFlag.AlignCenter, self.small)

class CPRPage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app; self.beep = Beeper(); self.running = False; self.comp = 0; self.beat = 0; self.sec = 0.0; self.session_logged = 0
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer); self.timer.timeout.connect(self.tick)
        self.ui = QTimer(self); self.ui.setInterval(30); self.ui.timeout.connect(self.anim)
        v = QHBoxLayout(self); v.setSpacing(14)
        left = card(RED); l = QVBoxLayout(left); l.setContentsMargins(16, 14, 16, 14); l.addWidget(title("CPR METRONOME", 20, RED))
        self.pw = PulseWidget(); l.addWidget(self.pw, 1)
        self.info = QLabel("Compressions: 0   |   Time: 00:00"); self.info.setStyleSheet("font-size:17px; font-weight:700;"); self.info.setAlignment(Qt.AlignmentFlag.AlignCenter); l.addWidget(self.info)
        self.alertl = QLabel(""); self.alertl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.alertl.setStyleSheet(f"color:{AMBER}; font-size:15px; font-weight:700;"); self.alertl.setWordWrap(True); l.addWidget(self.alertl)
        r1 = QHBoxLayout(); self.sl = QSlider(Qt.Orientation.Horizontal); self.sl.setRange(100, 120); self.sl.setValue(110); self.bl = QLabel("110 / min"); self.sl.valueChanged.connect(self.rate)
        r1.addWidget(QLabel("Rate")); r1.addWidget(self.sl, 1); r1.addWidget(self.bl); l.addLayout(r1)
        r2 = QHBoxLayout(); self.mode = QComboBox(); self.mode.addItem("30 compressions : 2 breaths", 30); self.mode.addItem("Continuous compressions (advanced airway / hands-only)", 0); self.mute = QCheckBox("Mute sound"); self.train = QCheckBox("Training mode (do not record)")
        r2.addWidget(self.mode, 1); r2.addWidget(self.mute); l.addLayout(r2); l.addWidget(self.train)
        self.go_btn = QPushButton("\u25B6  START METRONOME"); self.go_btn.setObjectName("danger"); self.go_btn.clicked.connect(self.toggle); l.addWidget(self.go_btn)
        right = QWidget(); r = QVBoxLayout(right); r.setSpacing(8)
        guide = [("1  Position / restraint", "Fastest start: Evetts-Russomano (ER) method - wrap legs around the patient, lock ankles behind their back. If inadequate: Reverse-Bear-Hug. Once the patient is on the CMRS: Handstand method if the module and rescuer height allow, otherwise restrained standard/straddle position. [PEER-REVIEWED, microgravity guideline]", BLUE),
                 ("2  Compressions", "Centre of chest, straight arms. 100-120 per minute, about 5 cm (2 in) deep, full chest recoil, interruptions under 10 s. Alone: 30:2 with breaths of 1 second. Values are Earth-based and not validated in microgravity. [AHA 2025 via secondary summaries - verify]", RED),
                 ("3  AED", "Pads on bare chest, press ON, follow prompts. Shock only if the patient is restrained on an electrically isolated surface and nobody touches them. After each 'Start CPR' prompt: 10-second pulse check. [NASA 1.102 / S2]", AMBER),
                 ("4  Rotate & call", "Swap compressor about every 2 minutes. Call ground (MCC-H). Log all times. Stop CPR only with the surgeon / most qualified crew member (NASA 2015: 10 min since last drug AND 45 min since start AND no pulse/breathing).", GREEN),
                 ("5  Breaths", "If trained: AMBU bag, one breath every 5 s with oxygen. Airway devices only with two rescuers and a restrained patient. Post-resuscitation care: NOT FOUND.", PURPLE)]
        for h_, t_, c_ in guide:
            f = card(c_); fl = QVBoxLayout(f); fl.addWidget(title(h_, 14, c_)); x = QLabel(t_); x.setWordWrap(True); fl.addWidget(x); r.addWidget(f)
        r.addStretch(); v.addWidget(left, 5); v.addWidget(scrolled(right), 5)
    def refresh(self): pass
    def rate(self, v):
        self.bl.setText(f"{v} / min")
        if self.running: self.timer.setInterval(int(60000 / v))
    def toggle(self):
        if self.running: self.stop()
        else:
            if not self.train.isChecked(): self.app.ensure_incident("cardiac_arrest")
            self.running = True; self.comp = 0; self.beat = 0; self.sec = 0; self._swaps = 0; self.t0 = time.monotonic(); self.alertl.setText("")
            self.go_btn.setText("\u25A0  STOP"); self.timer.start(int(60000 / self.sl.value())); self.ui.start(); self.tick()
    def stop(self):
        self.running = False; self.timer.stop(); self.ui.stop(); self.go_btn.setText("\u25B6  START METRONOME"); self.pw.main = "STOPPED"; self.pw.small = f"{self.comp} compressions"; self.pw.update()
        if self.comp and self.app.inc_id and not self.train.isChecked():
            self.session_logged += 1; self.app.store.set_step(self.app.inc_id, "cardiac_arrest", 100 + self.session_logged, f"CPR metronome session: {self.comp} compressions, {int(self.sec)//60:02d}:{int(self.sec)%60:02d}", True)
    def tick(self):
        if not self.running: return
        n = self.mode.currentData(); self.comp += 1; self.beat += 1
        if not self.mute.isChecked(): self.beep.play("tick")
        self.pw.phase = 1.0; self.pw.main = str(self.beat if n else self.comp); self.pw.small = "PUSH" if n == 0 or self.beat < n else "PUSH (last)"
        if n and self.beat >= n:
            self.timer.stop(); self.pw.main = "2 BREATHS"; self.pw.small = "give 2 breaths (1 s each)"; self.beat = 0
            QTimer.singleShot(4000, self.resume)
    def resume(self):
        if self.running: self.timer.start(int(60000 / self.sl.value())); self.tick()
    def anim(self):
        self.sec = time.monotonic() - self.t0; self.pw.phase = max(0.0, self.pw.phase - 0.12); self.pw.update()
        self.info.setText(f"Compressions: {self.comp}   |   Time: {int(self.sec)//60:02d}:{int(self.sec)%60:02d}")
        if int(self.sec // 120) > self._swaps:
            self._swaps = int(self.sec // 120); self.alertl.setText("2 MINUTES: SWAP COMPRESSOR  \u2022  AED: pulse check if prompted")
            if not self.mute.isChecked(): self.beep.play("swap")

class InventoryPage(QWidget):
    COLS = ["Medicine / item", "Category", "Minimum required", "Available", "Storage location", "Status", "Source"]
    def __init__(self, app):
        super().__init__(); self.app = app; self.loading = False
        v = QVBoxLayout(self); v.setSpacing(10); hdr = QHBoxLayout(); hdr.addWidget(title("MEDICAL KIT INVENTORY", 21, GREEN), 1)
        self.sum = QLabel(); hdr.addWidget(self.sum); v.addLayout(hdr)
        bar = QHBoxLayout(); self.q = QLineEdit(); self.q.setPlaceholderText("Search items, categories, locations..."); self.f = QComboBox(); self.f.addItems(["All statuses", "Available", "Low Stock", "Unavailable"])
        self.q.textChanged.connect(self.refresh); self.f.currentIndexChanged.connect(self.refresh)
        add = QPushButton("+ Add item"); add.setObjectName("go"); add.clicked.connect(self.add)
        use = QPushButton("\u2212 1 (used)"); use.clicked.connect(lambda: self.adj(-1)); rs = QPushButton("+ 1 (restock)"); rs.clicked.connect(lambda: self.adj(1))
        de = QPushButton("Delete"); de.clicked.connect(self.delete)
        for w in (self.q, self.f): bar.addWidget(w, 2 if w is self.q else 1)
        for w in (use, rs, add, de): bar.addWidget(w)
        v.addLayout(bar)
        self.t = QTableWidget(0, len(self.COLS)); self.t.setHorizontalHeaderLabels(self.COLS); self.t.setAlternatingRowColors(True)
        self.t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.t.verticalHeader().setVisible(False)
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive); hh.setStretchLastSection(True)
        for i, w in enumerate((330, 150, 120, 90, 330, 110)): self.t.setColumnWidth(i, w)
        self.t.itemChanged.connect(self.edited); v.addWidget(self.t, 1)
        n = QLabel("Status updates automatically: Available (count at or above minimum) / Low Stock (below minimum) / Unavailable (zero). Edit Minimum, Available, Location directly in the table. Minimums marked 'min assumed' are app defaults - set them to your mission's real kit list."); n.setWordWrap(True); n.setStyleSheet(f"color:{MUTED}; font-size:11px;"); v.addWidget(n)
    def refresh(self):
        st = self.app.store; self.loading = True; rows = st.inv_all(); tq = self.q.text().lower(); sf = self.f.currentText()
        rows = [r for r in rows if (not tq or tq in (r["name"] + r["category"] + (r["location"] or "")).lower()) and (sf == "All statuses" or r["status"] == sf)]
        self.t.setRowCount(len(rows)); col = {"Available": GREEN, "Low Stock": AMBER, "Unavailable": RED}
        for i, r in enumerate(rows):
            vals = [r["name"], r["category"], r["min_required"], r["available"], r["location"], r["status"], r["source"]]
            for j, val in enumerate(vals):
                it = QTableWidgetItem(str(val if val is not None else "")); it.setData(Qt.ItemDataRole.UserRole, r["id"])
                if j in (2, 3): it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j in (5, 6): it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if j == 5: it.setForeground(QColor(col[r["status"]])); f = it.font(); f.setBold(True); it.setFont(f); it.setBackground(QColor(rgba_tuple(col[r["status"]])))
                self.t.setItem(i, j, it)
        self.loading = False; allr = st.inv_all()
        self.sum.setText(f"{len(allr)} items  \u2022  <span style='color:{AMBER}'>{sum(r['status']=='Low Stock' for r in allr)} low</span>  \u2022  <span style='color:{RED}'>{sum(r['status']=='Unavailable' for r in allr)} unavailable</span>"); self.sum.setTextFormat(Qt.TextFormat.RichText)
    def edited(self, it):
        if self.loading: return
        iid = it.data(Qt.ItemDataRole.UserRole); f = {0: "name", 1: "category", 2: "min_required", 3: "available", 4: "location"}.get(it.column())
        if not f: return
        val = it.text().strip()
        try:
            if f in ("min_required", "available"): val = int(val)
            self.app.store.inv_update(iid, **{f: val})
        except (ValueError, sqlite3.IntegrityError):
            QMessageBox.warning(self, "Invalid value", "Counts must be whole numbers and item names must be unique.")
        self.refresh()
    def sel_id(self):
        r = self.t.currentRow(); return self.t.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None
    def adj(self, d):
        iid = self.sel_id()
        if iid is None: return
        cur = next(r for r in self.app.store.inv_all() if r["id"] == iid); self.app.store.inv_update(iid, available=cur["available"] + d); self.refresh()
    def delete(self):
        iid = self.sel_id()
        if iid is not None and QMessageBox.question(self, "Delete item", "Remove this item from the inventory?") == QMessageBox.StandardButton.Yes: self.app.store.inv_delete(iid); self.refresh()
    def add(self):
        d = QDialog(self); d.setWindowTitle("Add inventory item"); f = QFormLayout(d); nm = QLineEdit(); ct = QLineEdit(); mn = QSpinBox(); av = QSpinBox(); lc = QLineEdit()
        for s in (mn, av): s.setRange(0, 9999)
        for lab, w in (("Name", nm), ("Category", ct), ("Minimum required", mn), ("Available", av), ("Storage location", lc)): f.addRow(lab, w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel); bb.accepted.connect(d.accept); bb.rejected.connect(d.reject); f.addRow(bb)
        if d.exec() and nm.text().strip():
            try: self.app.store.inv_add(nm.text().strip(), ct.text().strip() or "Other", mn.value(), av.value(), lc.text().strip())
            except sqlite3.IntegrityError: QMessageBox.warning(self, "Duplicate", "An item with this name already exists.")
            self.refresh()

def rgba_tuple(h):
    h = h.lstrip("#"); return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 45).name(QColor.NameFormat.HexArgb)

def trends_html(incs):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=1, cols=2, column_widths=[0.4, 0.6], subplot_titles=("Incidents by possible condition", "Incident timeline (UTC)"))
    if incs:
        cnt = Counter(i["top_condition"] or "unclassified" for i in incs)
        fig.add_trace(go.Bar(x=list(cnt.values()), y=[k[:34] for k in cnt], orientation="h", marker_color=BLUE, name="Count"), 1, 1)
        for sev, (nm, col) in TIERS.items():
            sub = [i for i in incs if i["severity"] == sev]
            if sub: fig.add_trace(go.Scatter(x=[i["started_at"] for i in sub], y=[i["astronaut_name"] for i in sub], mode="markers", name=nm, marker=dict(size=14, color=col),
                                             text=[i["top_condition"] for i in sub], hovertemplate="%{x}<br>%{text}<extra></extra>"), 1, 2)
    else:
        fig.add_annotation(text="No incidents recorded yet", showarrow=False, font=dict(size=18, color=MUTED))
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(5,13,36,1)", plot_bgcolor="rgba(10,26,61,0.7)", margin=dict(l=20, r=20, t=50, b=30), legend=dict(orientation="h", y=-0.15))
    return fig.to_html(include_plotlyjs=True, full_html=True)

class HistoryPage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app; self.rows = []
        v = QVBoxLayout(self); h = QHBoxLayout(); h.addWidget(title("INCIDENT HISTORY & SYMPTOM TRACKING", 21, PURPLE), 1)
        self.scope = QComboBox(); self.scope.addItems(["Selected astronaut", "All crew"]); self.scope.currentIndexChanged.connect(self.refresh); h.addWidget(self.scope); v.addLayout(h)
        self.tabs = QTabWidget(); v.addWidget(self.tabs, 1)
        rec = QWidget(); rl = QVBoxLayout(rec)
        self.t = QTableWidget(0, 6); self.t.setHorizontalHeaderLabels(["#", "Date / time (UTC)", "Astronaut", "Top possible condition", "Severity", "Status"]); self.t.verticalHeader().setVisible(False)
        self.t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.t.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((50, 170, 160, 360, 100)): self.t.setColumnWidth(i, w)
        self.t.itemSelectionChanged.connect(self.detail); rl.addWidget(self.t, 1)
        self.det = QTextBrowser(); rl.addWidget(self.det, 1)
        b = QPushButton("Create PDF report for this incident"); b.clicked.connect(lambda: self.app.report_for(self.sel())); rl.addWidget(b)
        self.tabs.addTab(rec, "Records")
        tr = QWidget(); tl = QVBoxLayout(tr)
        if QWebEngineView: self.web = QWebEngineView(); tl.addWidget(self.web)
        else:
            self.web = None; m = QLabel("Qt WebEngine not installed - interactive Plotly chart opens in your browser instead."); tl.addWidget(m)
        ob = QPushButton("Open trends chart in browser"); ob.clicked.connect(self.browser); tl.addWidget(ob); self.tabs.addTab(tr, "Trends (Plotly)")
        self.path = os.path.join(tempfile.gettempdir(), "astra_trends.html")
    def refresh(self):
        self.rows = self.app.store.incidents(self.app.aid if self.scope.currentIndex() == 0 else None)
        self.t.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            sev = TIERS.get(r["severity"], ("-", MUTED))
            for j, val in enumerate((r["id"], r["started_at"], r["astronaut_name"], r["top_condition"] or "unclassified", sev[0], r["status"])):
                it = QTableWidgetItem(str(val)); 
                if j == 4: it.setForeground(QColor(sev[1]))
                self.t.setItem(i, j, it)
        self.det.setHtml(f"<span style='color:{MUTED}'>Select an incident to see symptoms, possible conditions, actions and medicines.</span>")
        try: html = trends_html(self.rows)
        except Exception as ex: html = f"<body style='background:#050d24;color:#e8f0ff;font-family:sans-serif'><h3>Plotly chart unavailable</h3><p>{xesc(str(ex))}</p></body>"
        with open(self.path, "w", encoding="utf-8") as f: f.write(html)
        if self.web: self.web.load(QUrl.fromLocalFile(self.path))
    def browser(self): QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
    def sel(self):
        r = self.t.currentRow(); return self.rows[r]["id"] if 0 <= r < len(self.rows) else None
    def detail(self):
        iid = self.sel()
        if iid is None: return
        st = self.app.store; i = st.incident(iid); sy = json.loads(i["symptoms_json"] or "[]"); rk = json.loads(i["conditions_json"] or "[]"); acts = json.loads(i["actions_json"] or "{}")
        h = f"<h3>Incident #{i['id']} - {xesc(i['astronaut_name'] or '')} - {i['started_at']} UTC</h3><b>Symptoms:</b> {xesc('; '.join(SYM_LABEL.get(s, s) for s in sy) or '-')}<br>"
        h += "<b>Possible conditions:</b> " + xesc("; ".join(f"{r['name']} ({r['strength']})" for r in rk[:4]) or "-") + "<br><b>Actions taken:</b><br>"
        h += "".join(f"&nbsp;&nbsp;{s['done_at'][11:19]} - {xesc(s['text'][:140])}<br>" for l in acts.values() for s in l) or "&nbsp;none recorded<br>"
        h += "<b>Medicines:</b> " + (xesc("; ".join(f"{m['item_name']} x{m['quantity']} at {m['given_at'][11:19]}" for m in st.meds(iid=iid))) or "none") + f"<br><b>Outcome:</b> {xesc(i['outcome_notes'] or '-')}"
        self.det.setHtml(h)

class ReportPage(QWidget):
    def __init__(self, app):
        super().__init__(); self.app = app
        v = QVBoxLayout(self); v.addWidget(title("MEDICAL SUMMARY REPORT (PDF)", 21, TEAL))
        c = card(TEAL); l = QVBoxLayout(c); l.setContentsMargins(20, 16, 20, 16)
        self.info = QLabel(); self.info.setWordWrap(True); l.addWidget(self.info)
        self.r_all = QRadioButton("Complete emergency history of the selected astronaut"); self.r_one = QRadioButton("Single incident:"); self.r_all.setChecked(True)
        self.cmb = QComboBox(); l.addWidget(self.r_all); l.addWidget(self.r_one); l.addWidget(self.cmb)
        self.c_mon = QCheckBox("Include health-monitoring records from the shared database"); self.c_mon.setChecked(True)
        self.c_inv = QCheckBox("Include medical-kit shortages (low / unavailable items)"); self.c_inv.setChecked(True); l.addWidget(self.c_mon); l.addWidget(self.c_inv)
        b = QPushButton("GENERATE PDF REPORT"); b.setObjectName("go"); b.clicked.connect(self.make); l.addWidget(b)
        n = QLabel("Contains: astronaut details, symptoms, possible conditions (decision support, not diagnosis), emergency history, actions taken with times, medicines administered, health-monitoring records and a hand-over table for ground medical review."); n.setWordWrap(True); n.setStyleSheet(f"color:{MUTED}; font-size:12px;"); l.addWidget(n)
        v.addWidget(c); v.addStretch()
    def refresh(self, pick=None):
        st = self.app.store; incs = st.incidents(self.app.aid); self.cmb.clear()
        for i in incs: self.cmb.addItem(f"#{i['id']}  {i['started_at'][:16]}  {i['top_condition'] or 'unclassified'}", i["id"])
        self.info.setText(f"Astronaut: <b>{xesc(st.astro_name(self.app.aid))}</b>  \u2022  {len(incs)} incident(s)  \u2022  {len(st.meds(aid=self.app.aid))} medicine record(s)  \u2022  {len(st.monitor_tables())} health-monitor table(s) detected"); self.info.setTextFormat(Qt.TextFormat.RichText)
        if pick is not None:
            k = self.cmb.findData(pick)
            if k >= 0: self.cmb.setCurrentIndex(k); self.r_one.setChecked(True)
    def make(self):
        st = self.app.store; ids = [self.cmb.currentData()] if self.r_one.isChecked() and self.cmb.currentData() else None
        fn = f"ASTRA_report_{st.astro_name(self.app.aid).replace(' ', '_')}_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save medical summary", fn, "PDF (*.pdf)")
        if not path: return
        try: build_report(st, self.app.aid, path, ids, self.c_mon.isChecked(), self.c_inv.isChecked())
        except Exception as e: QMessageBox.critical(self, "Report failed", str(e)); return
        if QMessageBox.question(self, "Report saved", f"Saved:\n{path}\n\nOpen it now?") == QMessageBox.StandardButton.Yes: QDesktopServices.openUrl(QUrl.fromLocalFile(path))

# ============================================================================
# 8. MAIN WINDOW
# ============================================================================
class Main(QMainWindow):
    def __init__(self, store):
        super().__init__(); self.store = store; self.inc_id = None; self.setWindowTitle("ASTRA Emergency Response - Astronaut Emergency Medical Support"); self.resize(1360, 900)
        bg = Background(); self.setCentralWidget(bg); root = QVBoxLayout(bg); root.setContentsMargins(18, 12, 18, 12); root.setSpacing(10)
        # header
        h = QHBoxLayout()
        bk = QPushButton("\u2190  BACK TO MAIN MENU"); bk.setMinimumHeight(44); bk.setCursor(Qt.PointingHandCursor); bk.clicked.connect(self.close); h.addWidget(bk)   # closes this module -> main_launcher.py shows the front page again
        lg = QLabel(); lg.setPixmap(logo_pixmap(58)); h.addWidget(lg)
        tl = QVBoxLayout(); tl.setSpacing(0); tl.addWidget(title(APP_NAME, 26)); s = QLabel("CREW MEDICAL DECISION SUPPORT  \u2022  SHARES DATABASE WITH ASTRO HEALTH MONITOR"); s.setStyleSheet(f"color:{MUTED}; font-size:11px; letter-spacing:1px;"); tl.addWidget(s); h.addLayout(tl, 1)
        self.clock = QLabel(); self.clock.setStyleSheet("font-size:15px; font-weight:700; color:#7fd0ff;"); h.addWidget(self.clock); av = QLabel(); av.setPixmap(astronaut_pixmap(56)); h.addWidget(av); root.addLayout(h)
        # astronaut bar (same pattern as the health monitor: selector + dashboard button)
        bar = card(BLUE); bl = QHBoxLayout(bar); bl.setContentsMargins(18, 8, 18, 8); col = QVBoxLayout(); lab = QLabel("SELECTED ASTRONAUT"); lab.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        self.combo = QComboBox(); self.combo.setStyleSheet("font-size:20px; font-weight:700; padding:6px 10px;"); col.addWidget(lab); col.addWidget(self.combo); bl.addLayout(col, 3)
        vd = QPushButton("\u25A3  VIEW SELECTED ASTRONAUT'S EMERGENCY DASHBOARD"); vd.setMinimumHeight(52); vd.clicked.connect(lambda: self.go(0)); bl.addWidget(vd, 2); root.addWidget(bar)
        # nav tiles
        nav = QHBoxLayout(); nav.setSpacing(8); self.navs = []
        for gl, tx, c_, pg in (("\u2302", "DASHBOARD", BLUE, 0), ("\u271A", "EMERGENCY\nASSESSMENT", RED, 1), ("\u2665", "CPR &\nPROCEDURES", AMBER, 3), ("\u2695", "MEDICAL KIT\nINVENTORY", GREEN, 4), ("\u29D6", "INCIDENT\nHISTORY", PURPLE, 5), ("\u2637", "MEDICAL\nREPORT", TEAL, 6)):
            b = QPushButton(f"{gl}   {tx}"); b.setCheckable(True); b.setMinimumHeight(58)
            b.setStyleSheet(f"QPushButton{{background:{rgba(c_,0.14)}; border:1.5px solid {c_}; border-radius:12px; text-align:left; padding-left:14px; font-size:12px;}} QPushButton:hover{{background:{rgba(c_,0.30)};}} QPushButton:checked{{background:{rgba(c_,0.55)}; border:2px solid white;}}")
            b.clicked.connect(lambda _=False, p=pg: self.go(p)); nav.addWidget(b); self.navs.append((pg, b))
        root.addLayout(nav)
        self.stack = QStackedWidgetCompat(); root.addWidget(self.stack, 1)
        self.home = HomePage(self); self.emer = EmergencyPage(self); self.proto = ProtocolPage(self); self.cpr = CPRPage(self); self.inv = InventoryPage(self); self.hist = HistoryPage(self); self.rep = ReportPage(self)
        self.pages = [self.home, self.emer, self.proto, self.cpr, self.inv, self.hist, self.rep]
        for p in self.pages: self.stack.addWidget(scrolled(p) if p in (self.home, self.rep) else p)
        self.fill_combo(); self.combo.currentIndexChanged.connect(self.combo_changed)
        t = QTimer(self); t.timeout.connect(lambda: self.clock.setText("UTC  " + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d  %H:%M:%S"))); t.start(1000); self._clk = t; self.clock.setText("UTC")
        self.go(0)
    def fill_combo(self):
        self.combo.blockSignals(True); self.combo.clear()
        for aid, nm in self.store.astronauts(): self.combo.addItem(f"Astronaut {nm}", aid)
        saved = self.store.setting("active_astronaut_id"); k = self.combo.findData(int(saved)) if saved and saved.isdigit() else -1
        self.combo.setCurrentIndex(max(0, k)); self.aid = self.combo.currentData(); self.combo.blockSignals(False)
    def combo_changed(self):
        self.aid = self.combo.currentData(); self.store.set_setting("active_astronaut_id", self.aid); self.inc_id = None; self.emer.clear(); self.refresh_all()
    def go(self, page):
        self.stack.setCurrentIndex(page); nav_pg = 1 if page == 2 else page
        for pg, b in self.navs: b.setChecked(pg == nav_pg)
        p = self.pages[page]
        if page != 2: p.refresh()
    def refresh_all(self):
        for p in (self.home, self.emer, self.proto, self.inv, self.hist, self.rep): p.refresh()
    def quick_row(self):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        for txt, cid in (("\u2665  UNRESPONSIVE / NOT BREATHING -> CPR NOW", "cardiac_arrest"), ("CHOKING", "choking"), ("SEVERE BLEEDING", "bleeding"), ("ALLERGIC REACTION", "anaphylaxis"), ("TOXIC GAS / CHEMICAL", "toxic")):
            b = QPushButton(txt); b.setObjectName("danger"); b.clicked.connect(lambda _=False, c=cid: self.open_protocol(c)); l.addWidget(b, 3 if cid == "cardiac_arrest" else 2)
        return w
    # ---- incident handling (records every emergency) ----
    def save_analysis(self, sel, vit, ranked):
        inc = self.store.incident(self.inc_id) if self.inc_id else None
        if inc and inc["status"] == "OPEN" and not json.loads(inc["actions_json"] or "{}"):
            top = ranked[0] if ranked else None
            self.store.update_incident(self.inc_id, symptoms_json=json.dumps(sorted(sel)), symptoms_text="; ".join(SYM_LABEL[s] for s in sorted(sel)), vitals_json=json.dumps(vit),
                                       conditions_json=json.dumps(ranked), top_condition=top["name"] if top else None, top_condition_id=top["id"] if top else None, severity=top["tier"] if top else None)
        else: self.inc_id = self.store.new_incident(self.aid, sel, vit, ranked)
    def ensure_incident(self, cid):
        inc = self.store.incident(self.inc_id) if self.inc_id else None
        if not inc or inc["status"] != "OPEN" or inc["astronaut_id"] != self.aid:
            c = CONDITIONS[cid]; sel = self.emer.picker.selected()
            self.inc_id = self.store.new_incident(self.aid, sel, self.emer.vitals(), [dict(id=cid, name=c["name"], tier=c["tier"], score=1.0, strength="Selected directly", matched=[], derived=[])])
        self.store.update_incident(self.inc_id, protocol_id=cid); return self.inc_id
    def new_incident_clicked(self):
        self.inc_id = None; self.emer.clear(); QMessageBox.information(self, "New incident", "Cleared. The next analysis will be recorded as a new incident.")
    def open_protocol(self, cid):
        self.proto.show_condition(cid); self.go(2)
    def history_alert(self, symset, top_id, exclude=None):
        sims = similar_incidents(self.store, self.aid, set(symset), top_id, exclude or self.inc_id)
        if not sims: return None
        f = card(AMBER, rgba(AMBER, 0.12)); l = QVBoxLayout(f); l.addWidget(title("\u26A0 MEDICAL HISTORY ALERT - similar symptoms recorded before", 14, AMBER))
        for r in sims:
            acts = json.loads(r["actions_json"] or "{}"); n = sum(len(v) for v in acts.values()); meds = self.store.meds(iid=r["id"])
            t = QLabel(f"{r['started_at'][:16]} UTC - {r['top_condition'] or 'unclassified'}  |  {n} step(s) recorded"
                       + (f"  |  medicines: {', '.join(m['item_name'] for m in meds)}" if meds else "") + (f"  |  outcome: {r['outcome_notes']}" if r["outcome_notes"] else ""))
            t.setWordWrap(True); l.addWidget(t)
        l.addWidget(QLabel("Tell ground this is a repeat pattern."))
        return f
    def report_for(self, iid):
        if iid is None: QMessageBox.information(self, "Select incident", "Select an incident in the table first."); return
        self.go(6); self.rep.refresh(pick=iid)
    def change_db(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select the shared health-monitor database", os.path.dirname(self.store.path), "SQLite (*.db *.sqlite *.sqlite3);;All files (*)")
        if not p: return
        try: s = Store(p)
        except Exception as e: QMessageBox.critical(self, "Cannot open database", str(e)); return
        self.store = s; QSettings("ASTRA", "Emergency").setValue("db", p); self.inc_id = None; self.fill_combo(); self.refresh_all()
    def closeEvent(self, e):
        if self.cpr.running: self.cpr.stop()
        super().closeEvent(e)

from PySide6.QtWidgets import QStackedWidget as QStackedWidgetCompat

def main():
    app = QApplication(sys.argv); app.setStyleSheet(QSS); app.setFont(QFont("Segoe UI", 10))
    path = find_db()
    if "--db" not in sys.argv and not os.environ.get("ASTRA_DB"):
        saved = QSettings("ASTRA", "Emergency").value("db")
        if saved and os.path.exists(saved): path = saved
    try: store = Store(path)
    except Exception as e:
        QMessageBox.critical(None, "Database error", f"Cannot open {path}\n{e}"); return 1
    w = Main(store); w.showMaximized(); return app.exec()

if __name__ == "__main__":
    sys.exit(main())

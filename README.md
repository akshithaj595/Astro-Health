# ASTRO HEALTH

### Health Monitoring and Emergency Care System for Astronauts

**Team:** Astronova 6.0\
**Team members:** Akshitha, Megha, Adwaith, Haridev, Adil, Sinan

## Overview

ASTRO HEALTH is an offline Python desktop application with two main
modules: **Health Monitor** and **Medical Emergency Assistance**. It is
designed to support astronaut health tracking, health analysis, and
emergency-care workflows.

## Requirements

-   Windows 11
-   Python 3.10
-   Python packages: PySide6, Plotly, ReportLab, pypdf
-   SQLite (used for local data storage; Python includes SQLite support)

## Installation

1.  Extract the project ZIP file.
2.  Open the extracted folder.
3.  Open a terminal and install the required packages:

``` bash
python -m pip install PySide6 plotly reportlab pypdf
```

## Run the Application

Open `main launcher.py` and run the file.

Or run it from the terminal:

``` bash
python "main launcher.py"
```

## Demo Accounts

ASTRO HEALTH includes two preloaded demo accounts to explore sample health data, graphs, and reports.

| Astronaut | User ID | Password |
|---|---|---|
| William James (Male) | `1001` | `123456` |
| Jennifer James (Female, with Cycle Care details) | `1002` | `123456` |

**Note:** These accounts contain sample data for demonstration. No manual data entry is required to view the demo graphs.

## Main Features

### 1. Health Monitor

-   Astronaut registration and login
-   Individual baseline health data entry (manual entry or PDF import)
-   Physical health tracking with baseline comparison, percentage
    changes, trends, graphs, alerts, and status levels
-   Mental health questionnaires and assessment for stress, anxiety,
    mood, and sleep
-   Health scores, analysis, warnings, and recommendations
-   Cycle Care for female astronaut accounts, including period tracking,
    symptoms, predictions, and reports
-   PDF health reports for ground doctors

### 2. Medical Emergency Assistance

-   Emergency access without the Health Monitor login
-   Astronaut selection and symptom assessment
-   Emergency guidance for critical situations
-   Step-by-step CPR guidance and CPR metronome
-   Medical-kit inventory and medicine storage tracking
-   Automatic incident history
-   PDF emergency reports for ground doctors

## Project Files

-   `main launcher.py` -- starts the application
-   `astrohealth monitor.py` -- Health Monitor module
-   `Astra emergency.py` -- Emergency Assistance module
-   `frontend.png` -- image asset
-   `astrohealth.dp` -- database-related file (confirm the exact
    extension in the project folder)

## Notes

-   Health values are entered manually. Baseline data can also be
    imported from PDF.
-   The application is designed to work offline.
-   ASTRO HEALTH is a software support tool, not a substitute for
    qualified medical professionals or approved medical procedures.

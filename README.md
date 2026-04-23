# AERMOD-DASHBOARD
Interactive AERMOD dispersion modeling dashboard built with streamlit
# AERMOD Dispersion Dashboard

An interactive web dashboard for visualizing EPA AERMOD air dispersion 
modeling results. Built as part of a broader air quality modeling and 
compliance portfolio.

## Live Demo
[Launch App](https://huggingface.co/spaces/AyomiT1st/AERMOD-DASHBOARD)

## What It Does

- Parses any AERMOD `.out` file directly — no hardcoded values
- Automatically detects source locations, UTM zone, averaging periods, 
  and background concentrations from the output file
- Renders annual and 1-hour average NO2 concentration plume maps 
  on an OpenStreetMap basemap using Cartopy
- Compares modeled maximum concentrations against NAAQS standards 
  for NO2, SO2, CO, PM2.5, and PM10
- Displays background concentration profiles including hourly HROFDY 
  breakdown when present in the run
- Handles multi-page AERMOD output grids automatically by merging 
  page-split concentration tables
- PNG download for each plume map at 300 DPI

## Sample Data

## Sample Data

Includes sample AERMOD runs for hypothetical five-source facilities.
Pollutant: NO2.

Runs included:

- `run2.out` — Phoenix, AZ (UTM Zone 12N). Annual and 1-hr NO2, 
  no background. Met: Phoenix Sky Harbor 2020-2024
- `run3.out` — Phoenix, AZ (UTM Zone 12N). 1-hr NO2 with ADEQ 
  hourly background concentrations (HROFDY)
- `Prov.out` — Providence, RI. Additional facility test run
- `Cent.out` — Centennial, CO. Additional facility test run

Sources modeled: Boiler, Dryer, Oven, Generator, Fire Pump

Sources modeled:
- Boiler, Dryer, Oven, Generator, Fire Pump

Two averaging periods included:
- Annual average (NAAQS: 53 µg/m³)
- 1-hour average, 1st-highest daily max over 5 years (NAAQS: 188 µg/m³)

## How to Use

**With the live app:**
The app loads sample data by default. To use your own AERMOD output, 
paste the full path to your `.out` file in the sidebar.

**Run locally:**
```bash
git clone https://github.com/YOUR_USERNAME/aermod-dashboard.git
cd aermod-dashboard
pip install -r requirements.txt
streamlit run aermod_dashboard.py

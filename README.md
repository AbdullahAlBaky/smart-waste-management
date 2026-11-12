# Smart Waste Management System

A university capstone project built with **Python Flask, SQLite, HTML, CSS, JavaScript, Bootstrap, Chart.js, and Leaflet/OpenStreetMap**.

## Requirements
- Python 3.10 or newer
- VS Code
- Internet connection for CDN-hosted Bootstrap, Chart.js, Leaflet, and map tiles

## Run in VS Code (Windows)
1. Extract the ZIP file.
2. Open the `smart_waste_management` folder in VS Code.
3. Open **Terminal > New Terminal**.
4. Create a virtual environment:
   ```powershell
   python -m venv venv
   ```
5. Activate it:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again. Or use Command Prompt: `venv\\Scripts\\activate.bat`.
6. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
7. Start the application:
   ```powershell
   python app.py
   ```
8. Open **http://127.0.0.1:5000** in your browser.

The SQLite database, default waste categories, administrator account, priority rules, two collection teams, and 28 clearly labelled fictional demo complaints are created automatically on first run when the complaints table is empty. The demo records populate the map and dashboard immediately: 28 quartiers, 8 served, 20 not served, and 2 active teams. These sample pins are illustrative Savar-area locations and are not verified real incidents.

## Default administrator
- **Email:** `admin@smartwaste.local`
- **Password:** `Admin@123`

## Demo citizen account
- **Email:** `demo.citizen@smartwaste.local`
- **Password:** `Citizen@123`

The demo citizen owns the sample reports. You can use this account to view the citizen side, or register a new citizen account.

Citizens create their own accounts from the registration page. Change the default administrator password and `SECRET_KEY` before any public deployment. This starter uses a local development secret and Flask debug mode.

## Assignment feature checklist

### Required features
- Citizen registration and login with hashed passwords
- Submit waste complaints with title, description, category, and address
- View personal complaints and complaint details
- Admin dashboard and complaint management
- Admin can update complaint status; citizens receive in-app notifications
- Search and filter complaints by keyword, status, priority, category, and date

### Advanced features
- **Waste-site photo upload:** JPG, JPEG, PNG, and WEBP files (5 MB maximum)
- **Priority-based reporting:** rule-based score from 0–100 using severity, quantity, hazardous/sanitation category, public-health risk, and sensitive-location keywords; result is Low, Medium, High, or Critical with a visible explanation
- **Collection team assignment:** create teams, activate/deactivate teams, and assign teams to reports
- **Waste collection statistics:** totals, completion, pending reports, average resolution time, category/priority charts, and team performance

### Bonus challenge
- **Interactive map:** citizens can select a location on the report form; the admin map and dashboard show geolocated reports with priority-coloured markers
- **Intelligent priority classification:** the application automatically calculates a priority score and explains the factors used. This is a transparent rule-based classifier, not a machine-learning model.

## Admin dashboard design
The admin view uses a left navigation sidebar and top search/notification bar, with four summary cards, an interactive waste-location map, monthly complaint trend chart, priority overview, and recent citizen reports. It follows the layout of the dashboard reference supplied with the assignment. The first-run demo database shows 28 sample pins immediately; new citizen reports with coordinates are added to the same map.

## Notes
- The dashboard's **Total quartiers / Zones desservies / Zones non desservies** cards are calculated from distinct complaint addresses. A zone is served if at least one report from that address reaches Collected or Completed.
- The interactive map requires internet access to load OpenStreetMap tiles and Leaflet. If a citizen does not place a map pin, the written address is still saved, but no map marker is available for that report.
- Uploaded files are stored in `uploads/complaints`.
- For a real deployment, use HTTPS, a strong environment-based secret, a production server, stricter upload scanning, and database backups.

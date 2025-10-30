# 🐋 Unusual Whales Options Alert Tracker

A full-stack local system for **collecting, storing, and visualizing unusual options trading activity** from the Unusual Whales API.  
This project runs fully offline and supports daily incremental data collection with manual database persistence.

---

## 📦 Project Overview

This repository contains three main components:

1. **Data Collector & Processor**  
   Scripts that pull raw alert data from the Unusual Whales API and preprocess them.  
   Example files: `unusual_options.py`, `report.py`.

2. **Backend API (FastAPI)**  
   Located under `uw-alerts/apps/api/app/`.  
   Handles alert ingestion, metric computation, database persistence, and RESTful endpoints.

3. **Frontend Dashboard (Next.js + Tailwind)**  
   Located under `uw-alerts/uw-dashboard/`.  
   Provides an interactive interface to view, label, and analyze alert data in real time.

---

## ⚙️ Installation

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/Unusual_Whales_API.git
cd Unusual_Whales_API
```

### 2. Set up the backend environment
```bash
cd uw-alerts
pip install -r requirements.txt
```

Required Python packages:
- fastapi  
- uvicorn  
- sqlalchemy  
- pandas  
- requests  

### 3. Set up the frontend environment
```bash
cd uw-dashboard
npm install
```

---

## 🧠 System Architecture

| Component | Description | Technology |
|------------|-------------|-------------|
| **API Server** | Fetches data, processes alerts, and serves endpoints | FastAPI + SQLite |
| **Database** | Stores all daily alerts and computed metrics | SQLite (`uw.sqlite3`) |
| **Dashboard** | Displays alerts and allows labeling/review | Next.js + TypeScript + Tailwind |

---

## 🕓 Daily Operation Workflow

This project is designed for **manual daily execution** (no cloud automation yet).

### 1️⃣ Before Market Open

Open **two terminals**.

**Terminal 1 – Backend**
```bash
cd Unusual_Whales_API/uw-alerts
uvicorn apps.api.app.main:app --port 8000
```

**Terminal 2 – Frontend**
```bash
cd Unusual_Whales_API/uw-alerts/uw-dashboard
npm run dev
```

- Backend runs at: [http://127.0.0.1:8000](http://127.0.0.1:8000)  
- Frontend runs at: [http://localhost:3000](http://localhost:3000)

### 2️⃣ During Market Hours

- The backend automatically polls the Unusual Whales API and writes alerts into the SQLite database (`uw.sqlite3`).  
- The dashboard provides live access to stored alerts, metrics, and manual review features.

### 3️⃣ After Market Close

- Stop both servers manually using `Ctrl + C` in each terminal.  
- Copy and back up your current database file:
  ```
  uw-alerts/apps/api/app/db/data/uw.sqlite3
  ```
- Before the next market session, manually place the latest `uw.sqlite3` file back into the same directory to ensure historical data continuity.

---

## 🗄️ Database Information

- **Database**: SQLite  
- **Location**: `uw-alerts/apps/api/app/db/data/uw.sqlite3`

Schema includes:
- `alerts_raw` – Unusual Whales raw alert data  
- `metrics_*` – Computed metrics (volume, gamma, vega, etc.)  
- `review_*` – Manual labels from the dashboard interface  

If the file is missing, a new database will be initialized automatically, but prior data will not persist.

### Optional Backup Command
```bash
cp uw-alerts/apps/api/app/db/data/uw.sqlite3 backups/uw_$(date +%Y%m%d).sqlite3
```

---

## 🔗 API Endpoints

Once the FastAPI backend is running, interactive documentation is available at:  
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

Main routes include:
- `/alerts` – Retrieve or query alert data  
- `/metrics` – Access computed options metrics  
- `/review` – Fetch or update manual labeling  
- `/poller_admin` – Control or monitor polling behavior  

---

## 🪶 Utility Scripts

| Script | Description |
|--------|--------------|
| `poller_cli.py` | CLI entry for daily polling process |
| `backfill_expiry.py` | Historical expiry data backfill |
| `backfill_otm.py` | Out-of-the-money contract backfill |
| `reset_db.py` | Reinitialize SQLite database |

Example usage:
```bash
python -m apps.api.app.scripts.poller_cli
```

---

## 🚀 Future Enhancements

- Automate daily startup and shutdown (via cron or task scheduler)  
- Cloud deployment (Render / Railway / AWS Lambda)  
- Migrate SQLite to PostgreSQL or another remote database  
- Automatic backup and synchronization of `uw.sqlite3`  
- Add authentication and user roles for labeling workflow  


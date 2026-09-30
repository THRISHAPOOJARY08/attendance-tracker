# 📋 Attendance Tracker

A full-stack web application for managing employee attendance, leave requests, and generating reports — built with **FastAPI**, **MongoDB**, and plain **HTML/CSS/JS** (no frontend build step required).

---

## ✨ Features

### 👤 Employee
- Check in / Check out with duration tracking
- Request leave with 8 leave types and a reason description
- View personal attendance history (weekly / monthly)
- See today's status at a glance

### 🧑‍💼 Manager
- Approve or reject leave requests with one click
- View team attendance report by date range
- Generate individual employee reports with summary stats
- **Download PDF reports** (server-generated, properly formatted A4)
- Add new employee accounts
- Remove employees from the system

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13 + FastAPI |
| Database | MongoDB (via Motor async driver) |
| Auth | JWT tokens in HTTP-only cookies + bcrypt hashing |
| Frontend | Plain HTML / CSS / JavaScript (no npm, no build step) |
| PDF Generation | ReportLab |
| Timezone | Configurable via `COMPANY_TIMEZONE` env var |

---

## 🚀 Running Locally

### 1. Prerequisites
- Python 3.11+
- MongoDB running on `localhost:27017`

### 2. Clone the repo
```bash
git clone https://github.com/THRISHAPOOJARY08/attendance-tracker.git
cd attendance-tracker
```

### 3. Create virtual environment and install dependencies
```bash
python -m venv .venv

# Windows
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 4. Set up environment variables
```bash
cp env.txt .env
```
Edit `.env`:
```
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=attendance_web
SECRET_KEY=your-long-random-secret-key-here
COMPANY_TIMEZONE=Asia/Kolkata
```
Generate a secure secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Create the first manager account
```bash
python seed.py
```

### 6. Start the server
```bash
python -m uvicorn app.main:app --port 8000 --reload --reload-dir app
```
Open **http://localhost:8000** in your browser.

---

## 📁 Project Structure

```
attendance-web/
├── app/
│   ├── main.py        — FastAPI app entry point
│   ├── config.py      — Environment variable loading
│   ├── db.py          — MongoDB client and indexes
│   ├── auth.py        — bcrypt hashing, JWT, auth dependencies
│   └── routes.py      — All API endpoints + PDF generation
├── static/
│   ├── login.html     — Login page
│   ├── dashboard.html — Employee dashboard
│   └── manager.html   — Manager dashboard
├── seed.py            — Creates the first manager account
├── env.txt            — Environment variable template (rename to .env)
└── requirements.txt   — Python dependencies
```

---

## 🔐 Security

- Passwords hashed with **bcrypt** (never stored in plain text)
- Sessions via **JWT in HTTP-only cookies** (not accessible from JavaScript)
- All secrets in **`.env`** — never hardcoded, never committed to Git
- Role-based access — manager endpoints reject non-managers with `403`

---

## 📄 Leave Types

`Sick` · `Casual` · `Paid` · `Maternity` · `Paternity` · `Bereavement` · `Compensatory` · `Unpaid`

---

## 👩‍💻 Author

**Thrisha Poojary**  
[github.com/THRISHAPOOJARY08](https://github.com/THRISHAPOOJARY08)

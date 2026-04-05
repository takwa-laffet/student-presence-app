# 🎓 Student Attendance Management System

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-orange.svg)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A modern web application for managing student attendance, training courses, and generating detailed reports. Built with Flask, SQLAlchemy, and PostgreSQL.

![Dashboard Preview](https://via.placeholder.com/800x400?text=Attendance+Tracker+Dashboard)

## 📋 Table of Contents

- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Running the Application](#-running-the-application)
- [Project Structure](#-project-structure)
- [API Endpoints](#-api-endpoints)
- [Key Features Guide](#-key-features-guide)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### Core Functionality
- **Student Management** - Full CRUD operations for students (élèves)
- **Course Management** - Create and manage training formations with duration tracking
- **Attendance Tracking** - Record student presence with time slots
- **Weekly Hours** - Automatic calculation of weekly attendance hours
- **Duration Tracking** - Track total training hours vs. remaining hours

### Reporting
- **Detailed Reports** - Generate comprehensive attendance reports
- **Date Range Filtering** - Filter by start/end dates
- **Student Filtering** - Generate reports for individual students
- **Absence Calculation** - Automatic absence calculation for weekdays
- **PDF Export** - Download reports in PDF format (monthly, weekly, detailed)

### User Interface
- **Dashboard** - Overview with key statistics
- **Responsive Design** - Works on desktop and mobile devices
- **Quick Actions** - Fast presence entry via checklist
- **Search Functionality** - Search students and courses

---

## 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.10+ / Flask 3.x |
| Database | PostgreSQL 14+ |
| ORM | SQLAlchemy + Flask-SQLAlchemy |
| Forms | WTForms + Flask-WTF |
| PDF Generation | ReportLab |
| Frontend | HTML5 / Bootstrap 5 / CSS3 |
| Templating | Jinja2 |

---

## 📌 Prerequisites

- **Operating System:** Windows 10/11, macOS, or Linux
- **Python:** Version 3.10 or higher
- **Database:** PostgreSQL 14+ (standalone)
- **Package Manager:** pip

---

## 🚀 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/attendance-tracker.git
cd attendance-tracker
```

### 2. Create Virtual Environment

```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1

# Linux/macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## ⚙️ Configuration

### Database Setup (PostgreSQL)

1. Install PostgreSQL and start the service
2. Create or use a PostgreSQL user (default: `postgres`)
3. Ensure port is `5432` (default)

### Environment Variables

Create a `.env` file or set environment variables:

```powershell
# PowerShell
$env:DB_USER="postgres"
$env:DB_PASSWORD="your_password"
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="5432"
$env:DB_NAME="systeme_presence"
$env:SECRET_KEY="your-secret-key-here"
```

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_USER` | root | Database username |
| `DB_PASSWORD` | (empty) | Database password |
| `DB_HOST` | 127.0.0.1 | Database host |
| `DB_PORT` | 5432 | Database port |
| `DB_NAME` | systeme_presence | Database name |
| `SECRET_KEY` | dev-secret-key | Flask secret key |
| `FLASK_DEBUG` | 0 | Enable debug mode (0 or 1) |

> **Note:** The database is created automatically on first run.

---

## ▶️ Running the Application

```bash
python app.py
```

The application will be available at: **http://127.0.0.1:5001**

---

## 📁 Project Structure

```
systeme_presense/
├── app.py                  # Main application entry point
├── config.py               # Configuration settings
├── forms.py                # WTForms definitions
├── models.py               # SQLAlchemy models
├── database.sql             # Database schema (backup)
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── static/
│   └── css/
│       └── style.css       # Custom styles
└── templates/
    ├── base.html           # Base template
    ├── dashboard.html      # Dashboard page
    ├── eleves.html         # Students list
    ├── eleve_form.html     # Student form
    ├── formations.html     # Courses list
    ├── formation_form.html # Course form
    ├── presence.html       # Attendance page
    ├── presence_form.html  # Attendance form
    └── rapport.html        # Reports page
```

---

## 🔌 API Endpoints

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/formation/eleves/<id>` | Get students by course |
| POST | `/api/presence/bulk_create` | Bulk create attendance |

### Web Routes

| Route | Description |
|-------|-------------|
| `/` | Dashboard |
| `/eleves` | Student management |
| `/formations` | Course management |
| `/presence` | Attendance tracking |
| `/rapport` | Reports & exports |
| `/rapport/pdf` | Download PDF report |
| `/rapport/pdf/semaine` | Weekly PDF report |
| `/rapport/pdf/mois` | Monthly PDF report |

---

## 📖 Key Features Guide

### Managing Students

Navigate to `/eleves` to:
- Add new students with name, email, and optional phone
- Assign students to courses
- Search and filter students
- Edit or delete student records

### Managing Courses

Navigate to `/formations` to:
- Create courses with total duration (in hours)
- Set session duration (2 or 4 hours)
- Track completed hours vs. remaining hours
- View course statistics

### Recording Attendance

Navigate to `/presence` to:
- Quick checklist entry by course
- Individual attendance recording
- Filter by student or date
- View attendance status (Present/Absent)

### Generating Reports

Navigate to `/rapport` to:
- Select date range
- Filter by specific student
- View detailed attendance history
- Export to PDF (multiple formats)

---

## 🔧 Troubleshooting

### Common Issues

| Error | Solution |
|-------|----------|
| `database "systeme_presence" does not exist` | Ensure PostgreSQL is running; database creates automatically |
| `Access denied for user 'root'@...` | Check DB_PASSWORD in environment variables |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| `Port 5001 already in use` | Change port in `app.py` or stop conflicting service |

### Development Tips

- Enable debug mode: Set `FLASK_DEBUG=1`
- Check database connection: Verify PostgreSQL service is running
- Clear session issues: Delete `.db` files if using SQLite

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👏 Acknowledgments

- [Flask](https://flask.palletsprojects.com/) - The micro framework
- [Bootstrap](https://getbootstrap.com/) - UI framework
- [ReportLab](https://www.reportlab.com/) - PDF generation
- [PostgreSQL](https://www.postgresql.org/) - Database server

---

<div align="center">

**Made with ❤️ for education management**

</div>

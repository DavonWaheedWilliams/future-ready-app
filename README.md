# FutureReady App

FutureReady is a student, parent, and counselor scholarship-readiness app.

## Features

- Student, parent, and counselor login
- Scholarship tracker
- Task planner
- Essay feedback
- Document vault
- Email reminder settings
- PDF resume export
- PDF checklist export
- Google Calendar .ics export
- Local SQLite database for the prototype

## Run locally

```bash
pip install -r requirements.txt
streamlit run future_ready_app_streamlit.py
```

## Demo accounts

Student: student@demo.com  
Password: password123

Parent: parent@demo.com  
Password: password123

Counselor: counselor@demo.com  
Password: password123

## Suggested project structure

```text
future-ready-app/
├── future_ready_app_streamlit.py
├── README.md
├── requirements.txt
└── .gitignore
```

## Important privacy note

Do not upload private student files, transcripts, FAFSA documents, real phone numbers, SMTP passwords, Gmail app passwords, or the local SQLite database to GitHub.

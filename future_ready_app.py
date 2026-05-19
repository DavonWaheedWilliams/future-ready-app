"""
FutureReady App 2.0
A forward-looking college, scholarship, and career readiness platform.

New features added:
- Login accounts for Student, Parent, and Counselor
- Role-based dashboard permissions
- Email deadline reminder settings
- Text reminder placeholder settings
- AI-style essay feedback using local scoring rules
- Scholarship CSV import
- PDF resume export
- PDF checklist export
- Google Calendar compatible .ics export
- Secure local document storage folder
- Mobile frontend roadmap for React or Flutter

Run:
1. pip install streamlit pandas plotly reportlab
2. streamlit run future_ready_app_streamlit.py

Default demo accounts:
Student:   student@demo.com   / password123
Parent:    parent@demo.com    / password123
Counselor: counselor@demo.com / password123

This MVP uses local SQLite storage. For production, move users, files, and reminders to a secure cloud backend.
"""

import base64
import csv
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

APP_TITLE = "FutureReady"
DB_PATH = Path("future_ready.db")
UPLOAD_DIR = Path("secure_uploads")
EXPORT_DIR = Path("exports")

UPLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)


# -----------------------------
# Security helpers
# -----------------------------

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return password_hash, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    new_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(new_hash, stored_hash)


def require_login():
    if "user" not in st.session_state:
        st.warning("Please log in first.")
        st.stop()


def current_user():
    return st.session_state.get("user", {})


def role_allowed(roles):
    user = current_user()
    return user.get("role") in roles


# -----------------------------
# Database setup
# -----------------------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def execute(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    conn.close()


def read_sql(query, params=()):
    conn = get_conn()
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def read_table(table_name):
    return read_sql(f"SELECT * FROM {table_name}")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Student', 'Parent', 'Counselor')),
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS student_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            student_name TEXT,
            grade_level TEXT,
            intended_major TEXT,
            target_college TEXT,
            act_score INTEGER,
            gpa_weighted REAL,
            gpa_unweighted REAL,
            career_goal TEXT,
            financial_need TEXT,
            strengths TEXT,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scholarships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL DEFAULT 0,
            deadline TEXT,
            renewable INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Not Started',
            fit_score INTEGER DEFAULT 50,
            essay_required INTEGER DEFAULT 0,
            recommendation_required INTEGER DEFAULT 0,
            transcript_required INTEGER DEFAULT 0,
            source_url TEXT,
            notes TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            due_date TEXT,
            priority TEXT DEFAULT 'Medium',
            completed INTEGER DEFAULT 0,
            assigned_to TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit TEXT NOT NULL,
            target_per_week INTEGER DEFAULT 3,
            current_streak INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            last_completed TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_name TEXT NOT NULL,
            document_type TEXT,
            owner TEXT,
            status TEXT DEFAULT 'Needed',
            file_path TEXT,
            notes TEXT,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS essays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scholarship_name TEXT,
            prompt TEXT,
            draft TEXT,
            word_count INTEGER,
            feedback TEXT,
            score INTEGER,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reminder_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email_enabled INTEGER DEFAULT 0,
            sms_enabled INTEGER DEFAULT 0,
            reminder_days_before INTEGER DEFAULT 7,
            parent_email TEXT,
            student_email TEXT,
            counselor_email TEXT,
            phone_number TEXT,
            smtp_server TEXT,
            smtp_port INTEGER,
            smtp_username TEXT,
            smtp_password TEXT,
            updated_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()
    seed_demo_users()


def seed_demo_users():
    existing = read_sql("SELECT * FROM users")
    if not existing.empty:
        return

    demo_users = [
        ("Student Demo", "student@demo.com", "Student"),
        ("Parent Demo", "parent@demo.com", "Parent"),
        ("Counselor Demo", "counselor@demo.com", "Counselor"),
    ]

    for name, email, role in demo_users:
        password_hash, salt = hash_password("password123")
        execute(
            """
            INSERT INTO users (name, email, role, password_hash, salt, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, email, role, password_hash, salt, datetime.now().isoformat()),
        )


# -----------------------------
# Recommendation logic
# -----------------------------

def deadline_urgency(deadline_text):
    if not deadline_text:
        return "No deadline", 0

    try:
        deadline = datetime.strptime(deadline_text, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date", 0

    days_left = (deadline - date.today()).days

    if days_left < 0:
        return "Past due", -10
    if days_left <= 3:
        return "Critical", 35
    if days_left <= 10:
        return "Urgent", 25
    if days_left <= 30:
        return "Soon", 15
    return "Later", 5


def calculate_recommendation_score(row):
    urgency_label, urgency_points = deadline_urgency(row.get("deadline"))
    fit = int(row.get("fit_score") or 0)
    amount = float(row.get("amount") or 0)
    amount_points = min(25, int(amount / 1000))
    renewable_points = 15 if int(row.get("renewable") or 0) == 1 else 0

    effort_penalty = 0
    if int(row.get("essay_required") or 0) == 1:
        effort_penalty += 5
    if int(row.get("recommendation_required") or 0) == 1:
        effort_penalty += 8
    if int(row.get("transcript_required") or 0) == 1:
        effort_penalty += 4

    score = fit + urgency_points + amount_points + renewable_points - effort_penalty
    return max(0, min(100, score)), urgency_label


def build_daily_plan(scholarships_df, tasks_df):
    plan = []

    if not scholarships_df.empty:
        active = scholarships_df[scholarships_df["status"].isin(["Not Started", "In Progress"])]
        if not active.empty:
            ranked = []
            for _, row in active.iterrows():
                score, urgency = calculate_recommendation_score(row)
                ranked.append((score, urgency, row["name"], row["deadline"]))
            ranked.sort(reverse=True)
            top = ranked[0]
            plan.append(
                f"Work on {top[2]} first. Priority score: {top[0]}. Deadline status: {top[1]}."
            )

    if not tasks_df.empty:
        open_tasks = tasks_df[tasks_df["completed"] == 0]
        if not open_tasks.empty:
            high_priority = open_tasks[open_tasks["priority"] == "High"]
            if not high_priority.empty:
                next_task = high_priority.sort_values("due_date", na_position="last").iloc[0]
            else:
                next_task = open_tasks.sort_values("due_date", na_position="last").iloc[0]
            plan.append(f"Complete this next task: {next_task['task']}.")

    if not plan:
        plan.append("Add scholarships, tasks, and habits to generate your daily plan.")

    return plan


# -----------------------------
# Essay feedback
# -----------------------------

def essay_feedback(prompt: str, draft: str):
    words = [w for w in draft.split() if w.strip()]
    word_count = len(words)
    draft_lower = draft.lower()
    prompt_terms = [w.strip(".,?!:;()[]").lower() for w in prompt.split() if len(w) > 4]
    overlap = sum(1 for term in set(prompt_terms) if term in draft_lower)

    score = 50
    feedback = []

    if word_count < 150:
        feedback.append("The draft is short. Add one specific story, one result, and one future goal.")
        score -= 10
    elif 250 <= word_count <= 650:
        feedback.append("The length fits many scholarship essays.")
        score += 10
    else:
        feedback.append("Check the word limit. The draft may need trimming or expansion.")

    if overlap >= 3:
        feedback.append("The draft appears connected to the prompt.")
        score += 10
    else:
        feedback.append("Use more language from the prompt so the answer feels direct.")
        score -= 5

    strong_terms = ["because", "learned", "impact", "goal", "community", "future", "career", "service"]
    used_terms = [term for term in strong_terms if term in draft_lower]
    score += min(15, len(used_terms) * 3)

    if "i" in draft_lower.split():
        feedback.append("The draft uses first person, which helps scholarship committees hear the student’s voice.")
        score += 5

    if any(term in draft_lower for term in ["actuarial", "stem", "math", "statistics", "finance"]):
        feedback.append("The draft connects well to STEM or career direction.")
        score += 10

    if any(term in draft_lower for term in ["volunteer", "tutoring", "food bank", "community", "service"]):
        feedback.append("The draft includes service or community impact. Keep this specific.")
        score += 10

    if not any(term in draft_lower for term in ["example", "when", "during", "after", "one time"]):
        feedback.append("Add a clear example. Committees remember specific moments more than general claims.")
        score -= 5

    feedback.append("Strong revision move: end with how the scholarship helps the next step, not only why the student deserves it.")
    score = max(0, min(100, score))

    return word_count, score, "\n".join(f"- {item}" for item in feedback)


# -----------------------------
# Reminders
# -----------------------------

def get_upcoming_deadlines(days_before=7):
    scholarships = read_table("scholarships")
    if scholarships.empty:
        return pd.DataFrame()

    upcoming = []
    for _, row in scholarships.iterrows():
        try:
            deadline = datetime.strptime(row["deadline"], "%Y-%m-%d").date()
        except Exception:
            continue

        days_left = (deadline - date.today()).days
        if 0 <= days_left <= days_before and row["status"] not in ["Submitted", "Awarded", "Declined"]:
            upcoming.append(
                {
                    "Scholarship": row["name"],
                    "Deadline": row["deadline"],
                    "Days Left": days_left,
                    "Status": row["status"],
                }
            )

    return pd.DataFrame(upcoming)


def build_reminder_message(upcoming_df):
    if upcoming_df.empty:
        return "No scholarship deadlines need reminders today."

    lines = ["FutureReady deadline reminder:", ""]
    for _, row in upcoming_df.iterrows():
        lines.append(f"{row['Scholarship']} is due {row['Deadline']} in {row['Days Left']} day(s). Status: {row['Status']}.")
    lines.append("")
    lines.append("Recommended action: finish the next open essay, document, or recommendation request today.")
    return "\n".join(lines)


def send_email_reminder(settings, message_body):
    """
    Uses SMTP settings saved in the app.
    For Gmail, use an app password instead of your regular Gmail password.
    """
    import smtplib

    recipients = [
        settings.get("student_email"),
        settings.get("parent_email"),
        settings.get("counselor_email"),
    ]
    recipients = [r for r in recipients if r]

    if not recipients:
        return False, "No reminder recipients saved."

    required = ["smtp_server", "smtp_port", "smtp_username", "smtp_password"]
    if any(not settings.get(field) for field in required):
        return False, "SMTP settings are incomplete."

    msg = EmailMessage()
    msg["Subject"] = "FutureReady Deadline Reminder"
    msg["From"] = settings["smtp_username"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(message_body)

    try:
        with smtplib.SMTP(settings["smtp_server"], int(settings["smtp_port"])) as server:
            server.starttls()
            server.login(settings["smtp_username"], settings["smtp_password"])
            server.send_message(msg)
        return True, "Email reminder sent."
    except Exception as exc:
        return False, f"Email reminder failed: {exc}"


# -----------------------------
# Export helpers
# -----------------------------

def make_pdf_file(filename, title, sections):
    path = EXPORT_DIR / filename
    doc = SimpleDocTemplate(str(path), pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))

    for heading, content in sections:
        story.append(Paragraph(heading, styles["Heading2"]))
        if isinstance(content, pd.DataFrame):
            if content.empty:
                story.append(Paragraph("No records found.", styles["BodyText"]))
            else:
                table_data = [content.columns.tolist()] + content.astype(str).values.tolist()
                table = Table(table_data, repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                story.append(table)
        else:
            for line in str(content).split("\n"):
                story.append(Paragraph(line, styles["BodyText"]))
        story.append(Spacer(1, 0.18 * inch))

    doc.build(story)
    return path


def make_resume_pdf():
    profile = read_sql("SELECT * FROM student_profile WHERE id = 1")
    scholarships = read_table("scholarships")
    tasks = read_table("tasks")

    if profile.empty:
        profile_text = "Complete the student profile before exporting the resume."
    else:
        p = profile.iloc[0]
        profile_text = f"""
Name: {p.get('student_name', '')}
Grade Level: {p.get('grade_level', '')}
Target College: {p.get('target_college', '')}
Intended Major: {p.get('intended_major', '')}
ACT: {p.get('act_score', '')}
Weighted GPA: {p.get('gpa_weighted', '')}
Unweighted GPA: {p.get('gpa_unweighted', '')}
Career Goal: {p.get('career_goal', '')}
Strengths: {p.get('strengths', '')}
"""

    submitted = scholarships[scholarships["status"].isin(["Submitted", "Awarded"])] if not scholarships.empty else pd.DataFrame()
    open_tasks = tasks[tasks["completed"] == 0] if not tasks.empty else pd.DataFrame()

    return make_pdf_file(
        "future_ready_resume_export.pdf",
        "FutureReady Resume Export",
        [
            ("Student Profile", profile_text),
            ("Submitted or Awarded Scholarships", submitted[["name", "amount", "status"]] if not submitted.empty else pd.DataFrame()),
            ("Open Readiness Tasks", open_tasks[["task", "category", "due_date", "priority"]] if not open_tasks.empty else pd.DataFrame()),
        ],
    )


def make_checklist_pdf():
    scholarships = read_table("scholarships")
    tasks = read_table("tasks")
    documents = read_table("documents")

    if not scholarships.empty:
        scholarship_export = scholarships[["name", "amount", "deadline", "status", "essay_required", "recommendation_required", "transcript_required", "notes"]].copy()
    else:
        scholarship_export = pd.DataFrame()

    if not tasks.empty:
        task_export = tasks[["task", "category", "due_date", "priority", "completed", "assigned_to"]].copy()
    else:
        task_export = pd.DataFrame()

    if not documents.empty:
        document_export = documents[["document_name", "document_type", "owner", "status", "notes"]].copy()
    else:
        document_export = pd.DataFrame()

    return make_pdf_file(
        "future_ready_checklist_export.pdf",
        "FutureReady Scholarship Checklist",
        [
            ("Scholarships", scholarship_export),
            ("Tasks", task_export),
            ("Documents", document_export),
        ],
    )


def download_link(path, label):
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{path.name}">{label}</a>'
    st.markdown(href, unsafe_allow_html=True)


# -----------------------------
# Calendar export
# -----------------------------

def ics_escape(text):
    return str(text).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def export_scholarship_calendar():
    scholarships = read_table("scholarships")
    path = EXPORT_DIR / "future_ready_deadlines.ics"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FutureReady//Scholarship Deadlines//EN",
        "CALSCALE:GREGORIAN",
    ]

    for _, row in scholarships.iterrows():
        try:
            deadline = datetime.strptime(row["deadline"], "%Y-%m-%d").date()
        except Exception:
            continue

        dt = deadline.strftime("%Y%m%d")
        uid = f"scholarship-{row['id']}@futureready.local"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART;VALUE=DATE:{dt}",
                f"SUMMARY:{ics_escape('Deadline: ' + row['name'])}",
                f"DESCRIPTION:{ics_escape(row.get('notes', '') or 'Scholarship deadline')}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# -----------------------------
# Pages
# -----------------------------

def login_page():
    st.title(APP_TITLE)
    st.caption("College, scholarship, and career readiness in one command center.")

    tab1, tab2 = st.tabs(["Log in", "Create account"])

    with tab1:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")

        if submitted:
            users = read_sql("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
            if users.empty:
                st.error("No account found.")
            else:
                user = users.iloc[0].to_dict()
                if verify_password(password, user["password_hash"], user["salt"]):
                    st.session_state["user"] = {
                        "id": user["id"],
                        "name": user["name"],
                        "email": user["email"],
                        "role": user["role"],
                    }
                    st.success("Logged in.")
                    st.rerun()
                else:
                    st.error("Wrong password.")

    with tab2:
        with st.form("signup_form"):
            name = st.text_input("Name")
            new_email = st.text_input("New email")
            role = st.selectbox("Role", ["Student", "Parent", "Counselor"])
            new_password = st.text_input("New password", type="password")
            submitted = st.form_submit_button("Create account")

        if submitted:
            if not name or not new_email or not new_password:
                st.error("Complete all fields.")
            else:
                password_hash, salt = hash_password(new_password)
                try:
                    execute(
                        """
                        INSERT INTO users (name, email, role, password_hash, salt, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (name.strip(), new_email.strip().lower(), role, password_hash, salt, datetime.now().isoformat()),
                    )
                    st.success("Account created. Log in with the new account.")
                except sqlite3.IntegrityError:
                    st.error("That email already exists.")


def dashboard_page():
    require_login()
    st.header("Command Center")

    user = current_user()
    st.write(f"Logged in as {user['name']} | Role: {user['role']}")

    scholarships = read_table("scholarships")
    tasks = read_table("tasks")
    habits = read_table("habits")
    documents = read_table("documents")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Scholarship pipeline", f"${scholarships['amount'].sum():,.0f}" if not scholarships.empty else "$0")
    col2.metric("Submitted", int((scholarships["status"] == "Submitted").sum()) if not scholarships.empty else 0)
    col3.metric("Open tasks", int((tasks["completed"] == 0).sum()) if not tasks.empty else 0)
    col4.metric("Progress points", int(habits["points"].sum()) if not habits.empty else 0)

    st.subheader("Today’s recommended plan")
    for item in build_daily_plan(scholarships, tasks):
        st.info(item)

    st.subheader("Scholarship priority ranking")
    if scholarships.empty:
        st.warning("Add scholarships to see rankings.")
    else:
        ranked_rows = []
        for _, row in scholarships.iterrows():
            score, urgency = calculate_recommendation_score(row)
            ranked_rows.append(
                {
                    "Scholarship": row["name"],
                    "Amount": row["amount"],
                    "Deadline": row["deadline"],
                    "Status": row["status"],
                    "Urgency": urgency,
                    "Recommended Score": score,
                }
            )
        ranked_df = pd.DataFrame(ranked_rows).sort_values("Recommended Score", ascending=False)
        st.dataframe(ranked_df, use_container_width=True)
        fig = px.bar(ranked_df, x="Scholarship", y="Recommended Score", hover_data=["Amount", "Deadline", "Urgency"])
        fig.update_layout(xaxis_title="", yaxis_title="Priority score")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Upcoming deadline reminders")
    settings = read_sql("SELECT * FROM reminder_settings WHERE id = 1")
    days = 7 if settings.empty else int(settings.iloc[0].get("reminder_days_before") or 7)
    upcoming = get_upcoming_deadlines(days)
    if upcoming.empty:
        st.success("No urgent deadlines inside the reminder window.")
    else:
        st.dataframe(upcoming, use_container_width=True)


def profile_page():
    require_login()
    st.header("Student Profile")

    if not role_allowed(["Student", "Parent", "Counselor"]):
        st.stop()

    profile_df = read_sql("SELECT * FROM student_profile WHERE id = 1")
    current = profile_df.iloc[0].to_dict() if not profile_df.empty else {}

    with st.form("profile_form"):
        student_name = st.text_input("Student name", current.get("student_name", ""))
        grade_level = st.text_input("Grade level", current.get("grade_level", "12th Grade"))
        intended_major = st.text_input("Intended major", current.get("intended_major", "Actuarial Science"))
        target_college = st.text_input("Target college", current.get("target_college", "Middle Tennessee State University"))
        act_score = st.number_input("ACT score", min_value=0, max_value=36, value=int(current.get("act_score") or 32))
        gpa_weighted = st.number_input("Weighted GPA", min_value=0.0, max_value=5.0, value=float(current.get("gpa_weighted") or 4.32), step=0.01)
        gpa_unweighted = st.number_input("Unweighted GPA", min_value=0.0, max_value=4.0, value=float(current.get("gpa_unweighted") or 3.73), step=0.01)
        career_goal = st.text_area("Career goal", current.get("career_goal", "Become an actuary and use math, statistics, and finance to help people manage risk."))
        financial_need = st.selectbox("Financial need level", ["Low", "Moderate", "High"], index=2)
        strengths = st.text_area("Strengths", current.get("strengths", "Math, statistics, leadership, service, communication, consistency"))
        submitted = st.form_submit_button("Save profile")

    if submitted:
        execute(
            """
            INSERT OR REPLACE INTO student_profile
            (id, student_name, grade_level, intended_major, target_college, act_score,
             gpa_weighted, gpa_unweighted, career_goal, financial_need, strengths, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_name,
                grade_level,
                intended_major,
                target_college,
                act_score,
                gpa_weighted,
                gpa_unweighted,
                career_goal,
                financial_need,
                strengths,
                datetime.now().isoformat(),
            ),
        )
        st.success("Profile saved.")


def scholarships_page():
    require_login()
    st.header("Scholarship Tracker")

    if role_allowed(["Student", "Parent"]):
        with st.expander("Add scholarship", expanded=True):
            with st.form("scholarship_form"):
                name = st.text_input("Scholarship name")
                amount = st.number_input("Amount", min_value=0.0, step=100.0)
                deadline = st.date_input("Deadline", value=date.today() + timedelta(days=14))
                renewable = st.checkbox("Renewable")
                status = st.selectbox("Status", ["Not Started", "In Progress", "Submitted", "Awarded", "Declined"])
                fit_score = st.slider("Fit score", 0, 100, 75)
                essay_required = st.checkbox("Essay required")
                recommendation_required = st.checkbox("Recommendation required")
                transcript_required = st.checkbox("Transcript required")
                source_url = st.text_input("Source URL")
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Add scholarship")

            if submitted and name.strip():
                execute(
                    """
                    INSERT INTO scholarships
                    (name, amount, deadline, renewable, status, fit_score, essay_required,
                     recommendation_required, transcript_required, source_url, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name.strip(), amount, str(deadline), 1 if renewable else 0, status, fit_score,
                        1 if essay_required else 0, 1 if recommendation_required else 0,
                        1 if transcript_required else 0, source_url, notes, datetime.now().isoformat(),
                    ),
                )
                st.success("Scholarship added.")

    st.subheader("Import scholarships from CSV")
    st.caption("CSV columns accepted: name, amount, deadline, renewable, status, fit_score, essay_required, recommendation_required, transcript_required, source_url, notes")
    uploaded_csv = st.file_uploader("Upload scholarship CSV", type=["csv"])
    if uploaded_csv and st.button("Import CSV"):
        df_import = pd.read_csv(uploaded_csv)
        count = 0
        for _, row in df_import.iterrows():
            if not str(row.get("name", "")).strip():
                continue
            execute(
                """
                INSERT INTO scholarships
                (name, amount, deadline, renewable, status, fit_score, essay_required,
                 recommendation_required, transcript_required, source_url, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("name", "")).strip(),
                    float(row.get("amount", 0) or 0),
                    str(row.get("deadline", "")),
                    int(row.get("renewable", 0) or 0),
                    str(row.get("status", "Not Started") or "Not Started"),
                    int(row.get("fit_score", 50) or 50),
                    int(row.get("essay_required", 0) or 0),
                    int(row.get("recommendation_required", 0) or 0),
                    int(row.get("transcript_required", 0) or 0),
                    str(row.get("source_url", "") or ""),
                    str(row.get("notes", "") or ""),
                    datetime.now().isoformat(),
                ),
            )
            count += 1
        st.success(f"Imported {count} scholarships.")

    df = read_table("scholarships")
    if df.empty:
        st.write("No scholarships yet.")
        return

    st.subheader("All scholarships")
    st.dataframe(df, use_container_width=True)

    if role_allowed(["Student", "Parent"]):
        st.subheader("Update scholarship status")
        selected = st.selectbox("Choose scholarship", df["name"].tolist())
        new_status = st.selectbox("New status", ["Not Started", "In Progress", "Submitted", "Awarded", "Declined"])
        if st.button("Update status"):
            execute("UPDATE scholarships SET status = ? WHERE name = ?", (new_status, selected))
            st.success("Status updated.")


def tasks_page():
    require_login()
    st.header("Task Planner")

    with st.form("task_form"):
        task = st.text_input("Task")
        category = st.selectbox("Category", ["Essay", "Documents", "Recommendation", "Resume", "Financial Aid", "General"])
        due_date = st.date_input("Due date", value=date.today() + timedelta(days=1))
        priority = st.selectbox("Priority", ["High", "Medium", "Low"])
        assigned_to = st.selectbox("Assigned to", ["Student", "Parent", "Counselor"])
        submitted = st.form_submit_button("Add task")

    if submitted and task.strip():
        execute(
            """
            INSERT INTO tasks (task, category, due_date, priority, completed, assigned_to, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (task.strip(), category, str(due_date), priority, assigned_to, datetime.now().isoformat()),
        )
        st.success("Task added.")

    df = read_table("tasks")
    if df.empty:
        st.write("No tasks yet.")
        return

    st.dataframe(df, use_container_width=True)
    open_df = df[df["completed"] == 0]
    if not open_df.empty:
        selected_task = st.selectbox("Mark task complete", open_df["task"].tolist())
        if st.button("Complete task"):
            execute("UPDATE tasks SET completed = 1 WHERE task = ?", (selected_task,))
            st.success("Task completed.")


def essay_page():
    require_login()
    st.header("AI Essay Feedback")
    st.write("This MVP uses local scoring rules. A production version should connect to a secure AI service with consent and privacy controls.")

    with st.form("essay_form"):
        scholarship_name = st.text_input("Scholarship name")
        prompt = st.text_area("Essay prompt")
        draft = st.text_area("Essay draft", height=300)
        submitted = st.form_submit_button("Analyze essay")

    if submitted:
        word_count, score, feedback = essay_feedback(prompt, draft)
        execute(
            """
            INSERT INTO essays (scholarship_name, prompt, draft, word_count, feedback, score, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (scholarship_name, prompt, draft, word_count, feedback, score, datetime.now().isoformat()),
        )
        st.metric("Essay score", score)
        st.metric("Word count", word_count)
        st.text_area("Feedback", feedback, height=220)

    essays = read_table("essays")
    if not essays.empty:
        st.subheader("Saved essay reviews")
        st.dataframe(essays[["scholarship_name", "word_count", "score", "updated_at"]], use_container_width=True)


def documents_page():
    require_login()
    st.header("Secure Document Vault")
    st.write("Files are copied into a local secure_uploads folder for this prototype.")

    with st.form("document_form"):
        document_name = st.text_input("Document name")
        document_type = st.selectbox("Document type", ["Resume", "Transcript", "Recommendation", "Financial Aid", "Photo", "Essay", "Other"])
        owner = st.selectbox("Owner", ["Student", "Parent", "Counselor"])
        status = st.selectbox("Status", ["Needed", "Requested", "Ready", "Submitted"])
        notes = st.text_area("Notes")
        uploaded_file = st.file_uploader("Upload file", type=["pdf", "doc", "docx", "jpg", "jpeg", "png", "txt"])
        submitted = st.form_submit_button("Save document")

    if submitted and document_name.strip():
        saved_path = ""
        if uploaded_file:
            safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uploaded_file.name}".replace(" ", "_")
            saved_file = UPLOAD_DIR / safe_name
            saved_file.write_bytes(uploaded_file.getbuffer())
            saved_path = str(saved_file)

        execute(
            """
            INSERT INTO documents (document_name, document_type, owner, status, file_path, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_name.strip(), document_type, owner, status, saved_path, notes, datetime.now().isoformat()),
        )
        st.success("Document saved.")

    df = read_table("documents")
    if df.empty:
        st.write("No documents yet.")
        return

    st.dataframe(df, use_container_width=True)


def reminders_page():
    require_login()
    st.header("Email and Text Deadline Reminders")

    settings_df = read_sql("SELECT * FROM reminder_settings WHERE id = 1")
    current = settings_df.iloc[0].to_dict() if not settings_df.empty else {}

    with st.form("reminder_settings"):
        email_enabled = st.checkbox("Enable email reminders", value=bool(current.get("email_enabled", 0)))
        sms_enabled = st.checkbox("Enable text reminder placeholder", value=bool(current.get("sms_enabled", 0)))
        reminder_days_before = st.slider("Remind this many days before deadline", 1, 30, int(current.get("reminder_days_before") or 7))
        student_email = st.text_input("Student email", current.get("student_email", ""))
        parent_email = st.text_input("Parent email", current.get("parent_email", ""))
        counselor_email = st.text_input("Counselor email", current.get("counselor_email", ""))
        phone_number = st.text_input("Phone number for future SMS", current.get("phone_number", ""))
        smtp_server = st.text_input("SMTP server", current.get("smtp_server", "smtp.gmail.com"))
        smtp_port = st.number_input("SMTP port", value=int(current.get("smtp_port") or 587))
        smtp_username = st.text_input("SMTP username", current.get("smtp_username", ""))
        smtp_password = st.text_input("SMTP app password", current.get("smtp_password", ""), type="password")
        submitted = st.form_submit_button("Save reminder settings")

    if submitted:
        execute(
            """
            INSERT OR REPLACE INTO reminder_settings
            (id, email_enabled, sms_enabled, reminder_days_before, parent_email, student_email,
             counselor_email, phone_number, smtp_server, smtp_port, smtp_username, smtp_password, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1 if email_enabled else 0,
                1 if sms_enabled else 0,
                reminder_days_before,
                parent_email,
                student_email,
                counselor_email,
                phone_number,
                smtp_server,
                smtp_port,
                smtp_username,
                smtp_password,
                datetime.now().isoformat(),
            ),
        )
        st.success("Reminder settings saved.")

    upcoming = get_upcoming_deadlines(int(current.get("reminder_days_before") or 7))
    st.subheader("Reminder preview")
    message_body = build_reminder_message(upcoming)
    st.text_area("Message", message_body, height=200)

    if st.button("Send email reminder now"):
        settings_df = read_sql("SELECT * FROM reminder_settings WHERE id = 1")
        if settings_df.empty:
            st.error("Save reminder settings first.")
        else:
            ok, result = send_email_reminder(settings_df.iloc[0].to_dict(), message_body)
            st.success(result) if ok else st.error(result)

    st.info("Text reminders need a provider such as Twilio, AWS SNS, or another SMS gateway. This page stores the future SMS fields.")


def exports_page():
    require_login()
    st.header("PDF and Calendar Exports")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Create resume PDF"):
            path = make_resume_pdf()
            st.success("Resume PDF created.")
            download_link(path, "Download resume PDF")

    with col2:
        if st.button("Create checklist PDF"):
            path = make_checklist_pdf()
            st.success("Checklist PDF created.")
            download_link(path, "Download checklist PDF")

    with col3:
        if st.button("Create Google Calendar .ics"):
            path = export_scholarship_calendar()
            st.success("Calendar file created.")
            download_link(path, "Download .ics calendar file")

    st.write("Upload the .ics file into Google Calendar to place scholarship deadlines on the calendar.")


def habits_page():
    require_login()
    st.header("Habit and Streak Builder")

    with st.form("habit_form"):
        habit = st.text_input("Habit")
        target = st.slider("Target completions per week", 1, 7, 5)
        submitted = st.form_submit_button("Add habit")

    if submitted and habit.strip():
        execute(
            """
            INSERT INTO habits (habit, target_per_week, current_streak, points, last_completed)
            VALUES (?, ?, 0, 0, ?)
            """,
            (habit.strip(), target, None),
        )
        st.success("Habit added.")

    df = read_table("habits")
    if df.empty:
        st.write("No habits yet.")
        return

    st.dataframe(df, use_container_width=True)
    selected = st.selectbox("Complete habit today", df["habit"].tolist())
    if st.button("Log habit"):
        row = df[df["habit"] == selected].iloc[0]
        today_text = str(date.today())
        if row["last_completed"] == today_text:
            st.warning("You already logged this habit today.")
        else:
            new_streak = int(row["current_streak"] or 0) + 1
            new_points = int(row["points"] or 0) + 10
            execute(
                """
                UPDATE habits
                SET current_streak = ?, points = ?, last_completed = ?
                WHERE habit = ?
                """,
                (new_streak, new_points, today_text, selected),
            )
            st.success("Habit logged. You earned 10 points.")


def roadmap_page():
    require_login()
    st.header("Product Roadmap")

    roadmap = pd.DataFrame(
        [
            {"Phase": "MVP", "Feature": "Login, roles, dashboard, scholarship tracking, tasks, documents", "Value": "Organizes the family workflow"},
            {"Phase": "MVP+", "Feature": "Essay feedback, PDF exports, CSV imports, .ics calendar export", "Value": "Turns planning into usable outputs"},
            {"Phase": "Cloud", "Feature": "PostgreSQL, encrypted cloud file storage, backups", "Value": "Supports real users across devices"},
            {"Phase": "Automation", "Feature": "Email and SMS reminders with scheduled jobs", "Value": "Reduces missed deadlines"},
            {"Phase": "AI", "Feature": "Personalized scholarship matching and stronger essay coaching", "Value": "Improves fit and application quality"},
            {"Phase": "Mobile", "Feature": "React Native or Flutter frontend", "Value": "Gives students quick daily access"},
            {"Phase": "Institution", "Feature": "Counselor portal, school dashboards, recommender tracking", "Value": "Helps schools support more students"},
        ]
    )
    st.dataframe(roadmap, use_container_width=True)

    st.subheader("Suggested production architecture")
    st.code(
        """
Frontend: React Native or Flutter
Backend API: FastAPI
Database: PostgreSQL
File Storage: AWS S3, Google Cloud Storage, or Supabase Storage
Authentication: Auth0, Firebase Auth, Clerk, or Supabase Auth
Email: SendGrid, Mailgun, Amazon SES, or Gmail SMTP for small pilots
SMS: Twilio or AWS SNS
Calendar: Google Calendar API OAuth, plus .ics fallback
AI Feedback: secure model API with consent, logging controls, and data retention rules
Hosting: Render, Railway, Fly.io, AWS, Google Cloud, or Azure
        """.strip()
    )


# -----------------------------
# Seed data
# -----------------------------

def add_seed_data():
    today = date.today()
    examples = [
        ("Local STEM Leadership Scholarship", 2500, str(today + timedelta(days=8)), 0, "Not Started", 88, 1, 1, 1, "", "Strong fit for STEM, leadership, and community service.", datetime.now().isoformat()),
        ("Future Actuary Scholarship", 5000, str(today + timedelta(days=21)), 1, "In Progress", 94, 1, 0, 1, "", "Best fit because intended major aligns directly with actuarial science.", datetime.now().isoformat()),
        ("Community Impact Award", 1000, str(today + timedelta(days=4)), 0, "Not Started", 75, 1, 0, 0, "", "Use tutoring, food bank, or youth group service as the main angle.", datetime.now().isoformat()),
    ]

    for item in examples:
        execute(
            """
            INSERT INTO scholarships
            (name, amount, deadline, renewable, status, fit_score, essay_required,
             recommendation_required, transcript_required, source_url, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )

    sample_tasks = [
        ("Draft scholarship essay outline", "Essay", str(today + timedelta(days=1)), "High", 0, "Student", datetime.now().isoformat()),
        ("Request transcript from counselor", "Documents", str(today + timedelta(days=2)), "High", 0, "Counselor", datetime.now().isoformat()),
        ("Update resume with newest honors", "Resume", str(today + timedelta(days=3)), "Medium", 0, "Student", datetime.now().isoformat()),
    ]

    for item in sample_tasks:
        execute(
            """
            INSERT INTO tasks (task, category, due_date, priority, completed, assigned_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )

    sample_habits = [
        ("Complete one scholarship action", 5, 0, 0, None),
        ("Read 20 minutes", 4, 0, 0, None),
        ("Practice math or statistics", 3, 0, 0, None),
    ]

    for item in sample_habits:
        execute(
            """
            INSERT INTO habits (habit, target_per_week, current_streak, points, last_completed)
            VALUES (?, ?, ?, ?, ?)
            """,
            item,
        )

    sample_docs = [
        ("Resume", "Resume", "Student", "Ready", "", "Keep one master version and one scholarship version.", datetime.now().isoformat()),
        ("Official Transcript", "Transcript", "Counselor", "Needed", "", "Request early when counselor upload is required.", datetime.now().isoformat()),
        ("FAFSA Submission Summary", "Financial Aid", "Parent", "Needed", "", "Needed for many need-based scholarships.", datetime.now().isoformat()),
    ]

    for item in sample_docs:
        execute(
            """
            INSERT INTO documents (document_name, document_type, owner, status, file_path, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )


# -----------------------------
# Main app
# -----------------------------

def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_db()

    if "user" not in st.session_state:
        login_page()
        return

    st.sidebar.title(APP_TITLE)
    user = current_user()
    st.sidebar.write(f"{user['name']}")
    st.sidebar.caption(user["role"])

    if st.sidebar.button("Log out"):
        st.session_state.pop("user", None)
        st.rerun()

    if st.sidebar.button("Load sample data"):
        add_seed_data()
        st.sidebar.success("Sample data loaded.")

    page = st.sidebar.radio(
        "Navigation",
        [
            "Command Center",
            "Student Profile",
            "Scholarships",
            "Tasks",
            "Essay Feedback",
            "Habits",
            "Documents",
            "Reminders",
            "Exports",
            "Roadmap",
        ],
    )

    if page == "Command Center":
        dashboard_page()
    elif page == "Student Profile":
        profile_page()
    elif page == "Scholarships":
        scholarships_page()
    elif page == "Tasks":
        tasks_page()
    elif page == "Essay Feedback":
        essay_page()
    elif page == "Habits":
        habits_page()
    elif page == "Documents":
        documents_page()
    elif page == "Reminders":
        reminders_page()
    elif page == "Exports":
        exports_page()
    elif page == "Roadmap":
        roadmap_page()


if __name__ == "__main__":
    main()

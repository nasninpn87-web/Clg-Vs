from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_wtf import CSRFProtect
from utils.db import get_conn
from utils.security import hash_password, verify_password
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from PIL import Image
import sqlite3, os, random, string, re
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ---------------- SECRETS & CONFIG ----------------
app.secret_key = os.environ.get("SECRET_KEY") or "campusease-super-secret-key-2026"
is_production = os.environ.get("FLASK_ENV") == "production"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=is_production,
    SESSION_COOKIE_SAMESITE="Lax"
)
ADMIN_PASS = os.environ.get("ADMIN_PASS") or "admin123"

# ---------------- CSRF ----------------
csrf = CSRFProtect(app)

# ---------------- FILE UPLOAD CONFIG ----------------
UPLOAD_FOLDER = os.path.join("static", "photos")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

STAGE_CONFIG = {
    1: {
        "title": "Class Election (CR)",
        "badge": "Step 1",
        "tag": "CLASS LEVEL",
        "next_stage": 2,
        "next_title": "Department Election"
    },
    2: {
        "title": "Department Election",
        "badge": "Step 2",
        "tag": "ACADEMIC FACULTY",
        "next_stage": 3,
        "next_title": "University Council"
    },
    3: {
        "title": "University Council",
        "badge": "Step 3",
        "tag": "EXECUTIVE COUNCIL",
        "next_stage": 4,
        "next_title": "Campus Policies & Committees"
    },
    4: {
        "title": "Campus Policies & Committees",
        "badge": "Step 4",
        "tag": "CAMPUS REFERENDUM",
        "next_stage": None,
        "next_title": None
    }
}

# Position titles per stage category
POSITION_TITLES = {
    1: ["Class Representative (CR)", "Joint Secretary", "Cultural Secretary", "Sports Representative"],
    2: ["Department Representative", "Department Secretary", "Technical Head", "Department Treasurer"],
    3: ["University Council President", "Vice President", "General Secretary", "Joint Secretary"],
    4: ["Policy Committee Chair", "Ethics Committee Head", "Library Committee Head", "Sports Council Head"]
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------- HELPERS ----------------
def row_to_dict(row):
    return dict(row) if row else None


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def expire_elections():
    """Automatically marks elections as inactive if their end_time has passed."""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute(
        "UPDATE elections SET is_active=0 WHERE is_active=1 AND end_time < ?",
        (now,)
    )
    conn.commit()
    conn.close()


def generate_otp(length=6):
    """Generate a numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


def get_department_aliases(dept_name, dept_code=""):
    """Returns a set of uppercase alias tokens for a department."""
    aliases = set()
    if dept_code:
        c = dept_code.strip().upper()
        aliases.add(c)
        if c.endswith("E") and len(c) > 2:
            aliases.add(c[:-1])
    if dept_name:
        n = dept_name.strip().upper()
        words = [w for w in n.replace("&", " ").replace("-", " ").split() if w]
        if words:
            initials = "".join([w[0] for w in words])
            aliases.add(initials)

    n_lower = (dept_name or "").lower()
    c_lower = (dept_code or "").lower()
    if "computer" in n_lower or c_lower in ("cse", "cs"):
        aliases.update({"CS", "CSE", "COMP"})
    elif "electronics" in n_lower or c_lower in ("ece", "ec"):
        aliases.update({"EC", "ECE", "ETC"})
    elif "mechanical" in n_lower or c_lower in ("me", "mech"):
        aliases.update({"ME", "MECH"})
    elif "civil" in n_lower or c_lower in ("ce", "civil"):
        aliases.update({"CE", "CIVIL"})
    elif "information" in n_lower or c_lower == "it":
        aliases.update({"IT", "INFO"})
    elif "electrical" in n_lower or c_lower in ("eee", "ee"):
        aliases.update({"EE", "EEE", "ELEC"})
    elif "artificial" in n_lower or c_lower in ("ai", "aiml", "aids"):
        aliases.update({"AI", "AIML", "AIDS"})

    return aliases


def detect_department_from_college_id(college_id, departments):
    """
    Given a college_id and a list of department dicts/names,
    identifies which department the ID corresponds to based on branch codes.
    Returns the matching department name, or None if indeterminate.
    """
    if not college_id:
        return None

    cid = college_id.strip().upper()
    tokens = re.findall(r'[A-Za-z]+', cid)
    if not tokens:
        return None

    # Check each department
    for d in departments:
        d_name = d["name"] if isinstance(d, dict) else d
        d_code = d.get("code", "") if isinstance(d, dict) else ""
        aliases = get_department_aliases(d_name, d_code)

        # 1. Exact token match
        for token in tokens:
            if token in aliases and token not in {"STUDENT", "VOTER", "USER", "CAND", "ADMIN"}:
                return d_name

        # 2. Check embedded alias (e.g. "0905CSE241001" or "0905CS241001")
        for alias in sorted(aliases, key=len, reverse=True):
            if len(alias) >= 2 and alias in cid and alias not in {"STUDENT", "VOTER", "USER", "CAND", "ADMIN"}:
                return d_name

    return None


def college_id_matches_department(college_id, target_dept_name, departments):
    """
    Strictly validates if a college_id corresponds to the target department.
    Returns (matches: bool, detected_dept: str or None, reason: str).
    """
    if not target_dept_name or not college_id:
        return False, None, "Department and College ID are required."

    target_d = None
    for d in departments:
        name = d["name"] if isinstance(d, dict) else d
        if name.strip().lower() == target_dept_name.strip().lower():
            target_d = d
            break

    target_code = target_d.get("code", "") if isinstance(target_d, dict) else ""
    target_aliases = get_department_aliases(target_dept_name, target_code)

    cid = college_id.strip().upper()
    tokens = re.findall(r'[A-Za-z]+', cid)

    # 1. Check if ID directly has an alias of the target department
    has_target_alias = False
    for token in tokens:
        if token in target_aliases:
            has_target_alias = True
            break
    if not has_target_alias:
        for alias in target_aliases:
            if len(alias) >= 2 and alias in cid:
                has_target_alias = True
                break

    # 2. Check if ID corresponds to a DIFFERENT department
    detected_dept = detect_department_from_college_id(college_id, departments)

    if detected_dept and detected_dept.strip().lower() != target_dept_name.strip().lower():
        return False, detected_dept, f"College ID '{college_id}' corresponds to the '{detected_dept}' department, not '{target_dept_name}'. Only the corresponding department can add this voter/candidate."

    # 3. If it doesn't match the target department
    if not has_target_alias:
        alias_hint = ", ".join(sorted([a for a in target_aliases if len(a) <= 4]))
        return False, None, f"College ID '{college_id}' does not correspond to the '{target_dept_name}' ({target_code}) department. College ID must include the department code ({alias_hint})."

    return True, target_dept_name, ""


def get_election_department(election, departments):
    """
    Returns the department name if this election is specific to a department, else None.
    """
    if not election:
        return None

    # 1. Direct column if set
    if election.get("department"):
        return election["department"]

    # 2. Check election name and category against department names and codes
    e_name = (election.get("name") or "").upper()
    tokens = re.findall(r'[A-Za-z]+', e_name)
    ignored_tokens = {
        "CR", "YEAR", "REP", "STAGE", "ROUND", "CLASS", "FACULTY",
        "STUDENT", "ELECTION", "REPRESENTATIVE", "COUNCIL", "POLICY",
        "POLICIES", "COMMITTEE", "SECRETARY", "VICE", "PRESIDENT"
    }

    for d in departments:
        d_name = d["name"] if isinstance(d, dict) else d
        d_code = d.get("code", "") if isinstance(d, dict) else ""
        aliases = get_department_aliases(d_name, d_code)

        for token in tokens:
            if token in aliases and token not in ignored_tokens:
                return d_name

        if d_name.upper() in e_name:
            return d_name

    return None


@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- DB INIT & MULTI-STAGE SCHEMA ----------------
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        code TEXT UNIQUE NOT NULL,
        total_students INTEGER DEFAULT 60,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        college_id TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        department TEXT DEFAULT 'Computer Science',
        attendance_pct REAL DEFAULT 0.0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS elections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        stage INTEGER NOT NULL DEFAULT 1,
        category_name TEXT NOT NULL,
        description TEXT,
        is_active INTEGER DEFAULT 1,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        election_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        college_id TEXT,
        department TEXT,
        position_title TEXT,
        photo TEXT,
        manifesto TEXT,
        promoted_from_stage INTEGER DEFAULT NULL,
        promotion_status TEXT DEFAULT NULL,
        is_self_registered INTEGER DEFAULT 0,
        status TEXT DEFAULT 'approved',
        FOREIGN KEY (election_id) REFERENCES elections (id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        election_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(election_id, student_id),
        FOREIGN KEY (election_id) REFERENCES elections (id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (candidate_id) REFERENCES candidates (id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS candidate_otp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        college_id TEXT NOT NULL,
        otp TEXT NOT NULL,
        election_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS voter_otp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        college_id TEXT NOT NULL,
        otp TEXT NOT NULL,
        mobile TEXT,
        email TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used INTEGER DEFAULT 0
    );
    """)

    # Add new columns to existing tables if they don't exist (migration)
    try:
        cur.execute("ALTER TABLE students ADD COLUMN attendance_pct REAL DEFAULT 0.0")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN class_section TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN role TEXT DEFAULT 'Student Elector'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN mobile TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN email TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN photo TEXT DEFAULT 'default.png'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN college_id TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN department TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN position_title TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN is_self_registered INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN status TEXT DEFAULT 'approved'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE departments ADD COLUMN coordinator_password_hash TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE elections ADD COLUMN department TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN cgpa REAL DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN class_section TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE candidates ADD COLUMN nomination_status TEXT DEFAULT 'pending'")
    except Exception:
        pass

    try:
        cur.execute("UPDATE students SET role='Student Elector' WHERE role IS NULL OR role=''")
        cur.execute("UPDATE students SET photo='default.png' WHERE photo IS NULL OR photo=''")
    except Exception:
        pass

    conn.commit()
    conn.close()


def seed_data():
    conn = get_conn()
    cur = conn.cursor()

    # Seed departments with default coordinator password (dept123)
    default_depts = [
        ("Computer Science", "CSE", 64),
        ("Electronics & Communication", "ECE", 58),
        ("Mechanical Engineering", "ME", 60),
        ("Civil Engineering", "CE", 55),
        ("Information Technology", "IT", 62),
        ("Electrical Engineering", "EEE", 50),
    ]
    default_dept_hash = hash_password("dept123")
    for name, code, total in default_depts:
        cur.execute(
            "INSERT OR IGNORE INTO departments (name, code, total_students, coordinator_password_hash) VALUES (?, ?, ?, ?)",
            (name, code, total, default_dept_hash)
        )
    cur.execute(
        "UPDATE departments SET coordinator_password_hash=? WHERE coordinator_password_hash IS NULL",
        (default_dept_hash,)
    )

    # Seed demo students with attendance
    for i in range(1, 65):
        cid = f"0905CS241{str(i).zfill(3)}"
        attendance = round(random.uniform(60, 100), 1)
        cur.execute(
            "INSERT OR IGNORE INTO students (college_id, password_hash, department, attendance_pct) VALUES (?, ?, ?, ?)",
            (cid, hash_password(f"{cid}#"), "Computer Science", attendance)
        )

    # Quick test student account
    cur.execute(
        "INSERT OR IGNORE INTO students (college_id, password_hash, department, attendance_pct) VALUES (?, ?, ?, ?)",
        ("STUDENT01", hash_password("vote123"), "Computer Science", 88.5)
    )

    # Seed the 4 Stages of Elections if table is empty
    cur.execute("SELECT COUNT(*) FROM elections")
    if cur.fetchone()[0] == 0:
        now = datetime.now()
        end_time = (now + timedelta(hours=48)).isoformat()
        start_time = now.isoformat()

        # Stage 1: Class Election (CR)
        cur.execute("""
            INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time)
            VALUES (?, 1, ?, ?, 1, ?, ?)
        """, (
            "Class Representative (CR) Election - CSE Year 3",
            "Class Election (CR)",
            "Elect your class representative. The winner directly advances to Step 2: Department Election!",
            start_time,
            end_time
        ))
        e1_id = cur.lastrowid
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (e1_id, "Aarav Sharma", "candidate1.png", "Passionate about lab equipment modernization and student advocacy.", "Class Representative (CR)", "Computer Science"))
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (e1_id, "Priya Patel", "candidate2.png", "Promoting collaborative peer workshops and semester project support.", "Joint Secretary", "Computer Science"))
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (e1_id, "Rohan Verma", "candidate3.png", "Focusing on transparent class communications and internship drives.", "Cultural Secretary", "Computer Science"))

        # Stage 2: Department Election
        cur.execute("""
            INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time)
            VALUES (?, 2, ?, ?, 1, ?, ?)
        """, (
            "Departmental Representative - Faculty of Engineering",
            "Department Election",
            "Department-wide election. Candidates include promoted winners from respective class elections.",
            start_time,
            end_time
        ))
        e2_id = cur.lastrowid
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, promoted_from_stage, promotion_status, position_title, department) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (e2_id, "Neha Gupta", "candidate2.png", "Advanced from Class Section A. Standing for engineering curriculum enrichment.", 1, "Promoted from Class Election (CR)", "Department Representative", "Computer Science"))
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (e2_id, "Vikram Malhotra", "candidate1.png", "Focusing on inter-department hackathons and research grants.", "Department Secretary", "Electronics & Communication"))

        # Stage 3: University Council
        cur.execute("""
            INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time)
            VALUES (?, 3, ?, ?, 1, ?, ?)
        """, (
            "University Council - Student Body Executive Election",
            "University Council",
            "Apex student governing body representation. Open to all students campus-wide.",
            start_time,
            end_time
        ))
        e3_id = cur.lastrowid
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, promoted_from_stage, promotion_status, position_title, department) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (e3_id, "Kavita Rao", "candidate2.png", "Former Department Representative. Advocating for campus healthcare and student fee transparency.", 2, "Promoted from Department Election", "University Council President", "Computer Science"))
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (e3_id, "Aditya Sen", "candidate3.png", "Leading university sports infrastructure and annual cultural symposium.", "Vice President", "Mechanical Engineering"))

        # Stage 4: Campus Policies & Committees
        cur.execute("""
            INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time)
            VALUES (?, 4, ?, ?, 1, ?, ?)
        """, (
            "Campus Policy Referendum - 24/7 Digital Library & Ethics Committee",
            "Campus Policies & Committees",
            "Vote on proposed campus policy amendments, student life improvements, and committee representation.",
            start_time,
            end_time
        ))
        e4_id = cur.lastrowid
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title) VALUES (?, ?, ?, ?, ?)",
                    (e4_id, "Option A: Approve 24/7 Library & Lab Access", "default.png", "Full approval with student security badge integration.", "Policy Committee Chair"))
        cur.execute("INSERT INTO candidates (election_id, name, photo, manifesto, position_title) VALUES (?, ?, ?, ?, ?)",
                    (e4_id, "Option B: Approve with Extended Weekend Hours Only", "default.png", "Alternative plan optimizing weekend and exam week facilities.", "Library Committee Head"))

    conn.commit()
    conn.close()


# ---------------- HOME PAGE ----------------
@app.route("/")
@app.route("/home")
def home():
    expire_elections()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM elections WHERE is_active=1 ORDER BY stage ASC, id ASC")
    active_elections = rows_to_dicts(cur.fetchall())
    conn.close()

    for e in active_elections:
        e["status"] = "Active"
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})

    return render_template(
        "home.html",
        active_elections=active_elections,
        stages=STAGE_CONFIG
    )


# ---------------- VOTER REGISTRATION (STEP 1: DETAILS CONFIRMATION) ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM departments ORDER BY name ASC")
    departments = [r[0] for r in cur.fetchall()]
    conn.close()

    if request.method == "POST":
        cid = request.form.get("college_id", "").strip().upper()
        pwd = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        dept = request.form.get("department", "Computer Science").strip()
        class_section = request.form.get("class_section", "").strip().upper()
        role = request.form.get("role", "Student Elector").strip() or "Student Elector"
        mobile = request.form.get("mobile", "").strip()
        email = request.form.get("email", "").strip().lower()
        attendance = request.form.get("attendance_pct", "85.0")
        photo = request.files.get("photo")

        try:
            attendance = float(attendance)
        except ValueError:
            attendance = 85.0

        if not cid or not pwd or not class_section or not mobile or not email:
            flash("All required fields (College ID, Class/Section, Mobile No, Email, Password) must be filled.", "danger")
            return redirect(url_for("register"))

        if len(mobile) < 7:
            flash("Please enter a valid mobile number for OTP delivery.", "danger")
            return redirect(url_for("register"))

        if "@" not in email:
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for("register"))

        if pwd != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        if len(pwd) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return redirect(url_for("register"))

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM departments")
        all_depts = rows_to_dicts(cur.fetchall())

        matches, detected_dept, reason = college_id_matches_department(cid, dept, all_depts)
        if not matches:
            conn.close()
            flash(f"❌ Error: {reason}", "danger")
            return redirect(url_for("register"))

        cur.execute("SELECT 1 FROM students WHERE college_id=?", (cid,))
        if cur.fetchone():
            flash(f"College ID '{cid}' is already registered as a voter. Please log in directly.", "warning")
            conn.close()
            return redirect(url_for("login"))

        # Photo handling
        unique_name = "default.png"
        if photo and photo.filename != "":
            if allowed_file(photo.filename):
                try:
                    image = Image.open(photo)
                    image.verify()
                    photo.seek(0)
                    filename = secure_filename(photo.filename)
                    base, ext = os.path.splitext(filename)
                    unique_name = f"voter_{base}_{int(datetime.now().timestamp())}{ext}"
                    photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
                except Exception:
                    unique_name = "default.png"

        # Generate OTP
        otp = generate_otp(6)
        cur.execute(
            "INSERT INTO voter_otp (college_id, otp, mobile, email) VALUES (?, ?, ?, ?)",
            (cid, otp, mobile, email)
        )
        conn.commit()
        conn.close()

        # Save pending voter details in session
        session["voter_reg"] = {
            "college_id": cid,
            "password_hash": hash_password(pwd),
            "department": dept,
            "class_section": class_section,
            "role": role,
            "mobile": mobile,
            "email": email,
            "photo": unique_name,
            "attendance_pct": attendance
        }

        flash(
            f"✅ Details confirmed! A verification OTP has been sent to your Mobile ({mobile}) & Email ({email}).<br>"
            f"Your OTP code is: <strong style='font-size:1.4em;letter-spacing:4px;color:#6366f1'>{otp}</strong> "
            f"<span class='small text-muted'>(Simulated delivery)</span>",
            "success"
        )
        return redirect(url_for("voter_verify_otp"))

    return render_template("register.html", departments=departments)


# ---------------- VOTER OTP VERIFICATION (STEP 2: CONFIRM OTP) ----------------
@app.route("/voter/verify-otp", methods=["GET", "POST"])
def voter_verify_otp():
    if "voter_reg" not in session:
        return redirect(url_for("register"))

    voter_info = session["voter_reg"]

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        if not entered_otp:
            entered_otp = "".join([request.form.get(f"otp_{i}", "") for i in range(1, 7)]).strip()

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM voter_otp
               WHERE college_id=? AND used=0
               ORDER BY created_at DESC LIMIT 1""",
            (voter_info["college_id"],)
        )
        otp_row = row_to_dict(cur.fetchone())

        if not otp_row or otp_row["otp"] != entered_otp:
            conn.close()
            flash("❌ Invalid OTP. Please check the 6-digit verification code and try again.", "danger")
            return redirect(url_for("voter_verify_otp"))

        # Mark OTP as used
        cur.execute("UPDATE voter_otp SET used=1 WHERE id=?", (otp_row["id"],))

        # Insert student record
        try:
            cur.execute("""
                INSERT INTO students
                (college_id, password_hash, department, attendance_pct, class_section, role, mobile, email, photo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                voter_info["college_id"],
                voter_info["password_hash"],
                voter_info["department"],
                voter_info["attendance_pct"],
                voter_info["class_section"],
                voter_info["role"],
                voter_info["mobile"],
                voter_info["email"],
                voter_info["photo"]
            ))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("Voter ID already registered. Please log in directly.", "warning")
            session.pop("voter_reg", None)
            return redirect(url_for("login"))

        conn.close()
        session.pop("voter_reg", None)
        flash(
            f"🎉 Congratulations! Your Student Elector account ({voter_info['college_id']}) has been successfully verified and activated. Please log in to cast your ballot.",
            "success"
        )
        return redirect(url_for("login"))

    return render_template("voter_verify_otp.html", voter_info=voter_info)


@app.route("/voter/resend-otp")
def voter_resend_otp():
    if "voter_reg" not in session:
        return redirect(url_for("register"))

    voter_info = session["voter_reg"]
    otp = generate_otp(6)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO voter_otp (college_id, otp, mobile, email) VALUES (?, ?, ?, ?)",
        (voter_info["college_id"], otp, voter_info["mobile"], voter_info["email"])
    )
    conn.commit()
    conn.close()

    flash(
        f"🔁 A new verification OTP has been generated for {voter_info['mobile']}.<br>"
        f"New OTP code: <strong style='font-size:1.4em;letter-spacing:4px;color:#6366f1'>{otp}</strong>",
        "info"
    )
    return redirect(url_for("voter_verify_otp"))


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        cid = request.form.get("college_id", "").strip().upper()
        pwd = request.form.get("password", "")

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE college_id=?", (cid,))
        student = row_to_dict(cur.fetchone())
        conn.close()

        if not student or not verify_password(student["password_hash"], pwd):
            flash("Invalid College ID or password", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["student"] = {
            "id": student["id"],
            "college_id": cid,
            "department": student.get("department", "Computer Science"),
            "attendance_pct": student.get("attendance_pct", 0.0),
            "class_section": student.get("class_section", ""),
            "role": student.get("role", "Student Elector"),
            "mobile": student.get("mobile", ""),
            "email": student.get("email", ""),
            "photo": student.get("photo", "default.png")
        }
        return redirect(url_for("vote_hub"))

    return render_template("login.html")


# ================================================================
# DEPARTMENT MODULE
# ================================================================

@app.route("/departments")
def departments():
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    depts = rows_to_dicts(cur.fetchall())
    for d in depts:
        cur.execute("SELECT COUNT(*) FROM students WHERE department=?", (d["name"],))
        d["enrolled_students"] = cur.fetchone()[0]

    cur.execute("SELECT * FROM elections WHERE is_active=1 ORDER BY stage ASC, id ASC")
    elections = rows_to_dicts(cur.fetchall())
    for e in elections:
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})
        e["position_titles"] = POSITION_TITLES.get(e["stage"], [])

    conn.close()
    return render_template("departments.html", departments=depts, elections=elections, position_titles=POSITION_TITLES)


@app.route("/departments/add_voter", methods=["POST"])
def public_add_dept_voter():
    dept_name = request.form.get("department", "").strip()
    cid = request.form.get("college_id", "").strip().upper()
    pwd = request.form.get("password", "").strip()
    class_section = request.form.get("class_section", "").strip().upper()
    mobile = request.form.get("mobile", "").strip()
    email = request.form.get("email", "").strip().lower()
    attendance = request.form.get("attendance_pct", "0")
    photo = request.files.get("photo")

    try:
        attendance = float(attendance)
    except ValueError:
        attendance = 80.0

    if not cid or not pwd or not dept_name:
        flash("Department, College ID and password are required.", "danger")
        return redirect(url_for("departments"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    # Format check: does college_id correspond to the chosen department?
    matches, detected_dept, reason = college_id_matches_department(cid, dept_name, all_depts)
    if not matches:
        conn.close()
        flash(f"❌ Error: {reason}", "danger")
        return redirect(url_for("departments"))

    # Database check: is cid already registered?
    cur.execute("SELECT id, department FROM students WHERE college_id=?", (cid,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        if existing["department"] != dept_name:
            flash(f"❌ Error: Student '{cid}' is already registered under the '{existing['department']}' department. Voters can only be added by their corresponding department.", "danger")
        else:
            flash(f"College ID '{cid}' is already registered under {dept_name}.", "warning")
        return redirect(url_for("departments"))

    # Photo handling
    unique_name = "default.png"
    if photo and photo.filename != "":
        if allowed_file(photo.filename):
            try:
                image = Image.open(photo)
                image.verify()
                photo.seek(0)
                filename = secure_filename(photo.filename)
                base, ext = os.path.splitext(filename)
                unique_name = f"voter_{base}_{int(datetime.now().timestamp())}{ext}"
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
            except Exception:
                unique_name = "default.png"

    try:
        cur.execute(
            """INSERT INTO students
               (college_id, password_hash, department, attendance_pct, class_section, role, mobile, email, photo)
               VALUES (?, ?, ?, ?, ?, 'Student Elector', ?, ?, ?)""",
            (cid, hash_password(pwd), dept_name, attendance, class_section, mobile, email, unique_name)
        )
        conn.commit()
        flash(f"🎉 Student Elector '{cid}' successfully added to {dept_name}!", "success")
    except Exception as ex:
        flash(f"Error adding voter: {ex}", "danger")

    conn.close()
    return redirect(url_for("departments"))


@app.route("/departments/add_candidate", methods=["POST"])
def public_add_dept_candidate():
    dept_name = request.form.get("department", "").strip()
    election_id = request.form.get("election_id")
    candidate_name = request.form.get("candidate_name", "").strip()
    college_id = request.form.get("college_id", "").strip().upper()
    position_title = request.form.get("position_title", "").strip()
    manifesto = request.form.get("manifesto", "").strip()
    photo = request.files.get("photo")

    if not election_id or not candidate_name or not dept_name:
        flash("Department, Election, and Candidate Name are required.", "danger")
        return redirect(url_for("departments"))

    if not college_id:
        flash("❌ Error: College ID is required to verify the candidate's department. Candidates can only be added by their corresponding department.", "danger")
        return redirect(url_for("departments"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    # 1. Format check: does college_id correspond to the chosen department?
    matches, detected_dept, reason = college_id_matches_department(college_id, dept_name, all_depts)
    if not matches:
        conn.close()
        flash(f"❌ Error: {reason}", "danger")
        return redirect(url_for("departments"))

    # 2. Database check: student must exist and belong to dept_name
    cur.execute("SELECT * FROM students WHERE college_id=?", (college_id,))
    student = row_to_dict(cur.fetchone())
    if not student:
        conn.close()
        flash(f"❌ Error: Student with College ID '{college_id}' is not enrolled in {dept_name}. A student must be registered as a voter in {dept_name} before nomination.", "danger")
        return redirect(url_for("departments"))

    if student.get("department") and student["department"] != dept_name:
        conn.close()
        flash(f"❌ Error: Candidate '{college_id}' belongs to the '{student['department']}' department, not '{dept_name}'. Candidates can only be added by their corresponding department.", "danger")
        return redirect(url_for("departments"))

    # 3. Election check: if election is department-specific, must match dept_name
    cur.execute("SELECT * FROM elections WHERE id=?", (election_id,))
    election = row_to_dict(cur.fetchone())
    if election:
        election_dept = get_election_department(election, all_depts)
        if election_dept and election_dept.lower() != dept_name.lower():
            conn.close()
            flash(f"❌ Error: Election '{election['name']}' is restricted to '{election_dept}'. Candidates can only be added by their corresponding department.", "danger")
            return redirect(url_for("departments"))

    # 4. Check duplicate
    cur.execute("SELECT id FROM candidates WHERE election_id=? AND (college_id=? OR name=?)", (election_id, college_id, candidate_name))
    if cur.fetchone():
        conn.close()
        flash(f"Candidate '{candidate_name}' ({college_id}) is already registered in this election.", "warning")
        return redirect(url_for("departments"))

    unique_name = "default.png"
    if photo and photo.filename != "":
        if allowed_file(photo.filename):
            try:
                image = Image.open(photo)
                image.verify()
                photo.seek(0)
                filename = secure_filename(photo.filename)
                base, ext = os.path.splitext(filename)
                unique_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
            except Exception:
                unique_name = "default.png"

    cur.execute("""
        INSERT INTO candidates (election_id, name, college_id, department, position_title, photo, manifesto, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')
    """, (election_id, candidate_name, college_id, dept_name, position_title, unique_name, manifesto))
    conn.commit()
    conn.close()

    flash(f"🎉 Candidate '{candidate_name}' successfully nominated for {dept_name}!", "success")
    return redirect(url_for("departments"))


@app.route("/admin/departments", methods=["POST"])
def admin_departments():
    if "admin" not in session:
        return redirect(url_for("admin"))

    action = request.form.get("dept_action")
    conn = get_conn()
    cur = conn.cursor()

    if action == "add":
        name = request.form.get("dept_name", "").strip()
        code = request.form.get("dept_code", "").strip().upper()
        total = request.form.get("total_students", 60)
        coord_pass = request.form.get("coordinator_password", "").strip() or "dept123"
        try:
            total = int(total)
        except ValueError:
            total = 60
        if name and code:
            try:
                cur.execute(
                    "INSERT INTO departments (name, code, total_students, coordinator_password_hash) VALUES (?, ?, ?, ?)",
                    (name, code, total, hash_password(coord_pass))
                )
                conn.commit()
                flash(f"Department '{name}' added successfully with coordinator login configured!", "success")
            except sqlite3.IntegrityError:
                flash("Department name or code already exists.", "warning")
        else:
            flash("Department name and code are required.", "danger")

    elif action == "delete":
        dept_id = request.form.get("dept_id")
        if dept_id:
            cur.execute("DELETE FROM departments WHERE id=?", (dept_id,))
            conn.commit()
            flash("Department removed.", "info")

    elif action == "edit":
        dept_id = request.form.get("dept_id")
        name = request.form.get("dept_name", "").strip()
        code = request.form.get("dept_code", "").strip().upper()
        total = request.form.get("total_students", 60)
        coord_pass = request.form.get("coordinator_password", "").strip()
        try:
            total = int(total)
        except ValueError:
            total = 60
        if dept_id and name and code:
            if coord_pass:
                cur.execute(
                    "UPDATE departments SET name=?, code=?, total_students=?, coordinator_password_hash=? WHERE id=?",
                    (name, code, total, hash_password(coord_pass), dept_id)
                )
            else:
                cur.execute(
                    "UPDATE departments SET name=?, code=?, total_students=? WHERE id=?",
                    (name, code, total, dept_id)
                )
            conn.commit()
            flash(f"Department '{name}' updated successfully.", "success")

    conn.close()
    return redirect(url_for("admin"))


# ================================================================
# DEPARTMENT COORDINATOR PORTAL MODULE
# (Candidates & Voters registration & overall process handled by department)
# ================================================================

@app.route("/department/login", methods=["GET", "POST"])
def department_login():
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    departments = rows_to_dicts(cur.fetchall())
    conn.close()

    if request.method == "POST":
        dept_id = request.form.get("dept_id")
        password = request.form.get("password", "")

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM departments WHERE id=?", (dept_id,))
        dept = row_to_dict(cur.fetchone())
        conn.close()

        if not dept:
            flash("Please select a valid department.", "danger")
            return redirect(url_for("department_login"))

        coord_hash = dept.get("coordinator_password_hash")
        valid = False
        if coord_hash and verify_password(coord_hash, password):
            valid = True
        elif not coord_hash and password == "dept123":
            valid = True

        if not valid:
            flash("Invalid department coordinator password.", "danger")
            return redirect(url_for("department_login"))

        session.clear()
        session["dept_coordinator"] = {
            "id": dept["id"],
            "name": dept["name"],
            "code": dept["code"]
        }
        flash(f"Welcome to the {dept['name']} Department Voting Portal!", "success")
        return redirect(url_for("dept_dashboard"))

    return render_template("department_login.html", departments=departments)


@app.route("/department/logout")
def department_logout():
    session.pop("dept_coordinator", None)
    flash("You have logged out from the Department Coordinator Portal.", "info")
    return redirect(url_for("department_login"))


@app.route("/department/dashboard")
def dept_dashboard():
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_session = session["dept_coordinator"]
    expire_elections()

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM departments WHERE id=?", (dept_session["id"],))
    dept = row_to_dict(cur.fetchone())
    if not dept:
        session.pop("dept_coordinator", None)
        conn.close()
        return redirect(url_for("department_login"))

    cur.execute("SELECT * FROM students WHERE department=? ORDER BY college_id ASC", (dept["name"],))
    voters = rows_to_dicts(cur.fetchall())

    cur.execute("""
        SELECT COUNT(v.id)
        FROM votes v
        JOIN students s ON v.student_id = s.id
        WHERE s.department=?
    """, (dept["name"],))
    dept_votes_cast = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT v.student_id)
        FROM votes v
        JOIN students s ON v.student_id = s.id
        WHERE s.department=?
    """, (dept["name"],))
    unique_dept_voters = cur.fetchone()[0]

    dept_turnout = round((unique_dept_voters / len(voters) * 100), 1) if voters else 0.0

    cur.execute("""
        SELECT c.*, e.name as election_name, e.stage, e.category_name
        FROM candidates c
        JOIN elections e ON c.election_id = e.id
        WHERE c.is_self_registered=1 AND c.status='pending' AND c.department=?
        ORDER BY c.id DESC
    """, (dept["name"],))
    pending_candidates = rows_to_dicts(cur.fetchall())

    cur.execute("""
        SELECT c.*, e.name as election_name, e.stage, e.category_name, COUNT(v.id) as votes
        FROM candidates c
        JOIN elections e ON c.election_id = e.id
        LEFT JOIN votes v ON c.id=v.candidate_id
        WHERE c.department=? AND c.status='approved'
        GROUP BY c.id
        ORDER BY e.stage ASC, votes DESC
    """, (dept["name"],))
    approved_candidates = rows_to_dicts(cur.fetchall())

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    cur.execute("SELECT * FROM elections ORDER BY stage ASC, id DESC")
    elections = rows_to_dicts(cur.fetchall())
    for e in elections:
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})
        e["position_titles"] = POSITION_TITLES.get(e["stage"], [])
        e["restricted_to"] = get_election_department(e, all_depts)

    conn.close()

    return render_template(
        "department_dashboard.html",
        dept=dept,
        voters=voters,
        pending_candidates=pending_candidates,
        approved_candidates=approved_candidates,
        dept_votes_cast=dept_votes_cast,
        dept_turnout=dept_turnout,
        elections=elections,
        stages=STAGE_CONFIG,
        position_titles=POSITION_TITLES
    )


@app.route("/department/voters/add", methods=["POST"])
def dept_add_voter():
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    cid = request.form.get("college_id", "").strip().upper()
    pwd = request.form.get("password", "").strip()
    class_section = request.form.get("class_section", "").strip().upper()
    mobile = request.form.get("mobile", "").strip()
    email = request.form.get("email", "").strip().lower()
    attendance = request.form.get("attendance_pct", "0")
    photo = request.files.get("photo")

    try:
        attendance = float(attendance)
    except ValueError:
        attendance = 80.0

    if not cid or not pwd:
        flash("College ID and password are required.", "danger")
        return redirect(url_for("dept_dashboard"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    # Format check: does college_id correspond to this department?
    matches, detected_dept, reason = college_id_matches_department(cid, dept_name, all_depts)
    if not matches:
        conn.close()
        flash(f"❌ Error: {reason}", "danger")
        return redirect(url_for("dept_dashboard"))

    # Database check: check if already registered under another department
    cur.execute("SELECT id, department FROM students WHERE college_id=?", (cid,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        if existing["department"] != dept_name:
            flash(f"❌ Error: Student '{cid}' is already registered under the '{existing['department']}' department. Voters can only be added by their corresponding department.", "danger")
        else:
            flash(f"College ID '{cid}' is already registered in {dept_name}.", "warning")
        return redirect(url_for("dept_dashboard"))

    # Photo handling
    unique_name = "default.png"
    if photo and photo.filename != "":
        if allowed_file(photo.filename):
            try:
                image = Image.open(photo)
                image.verify()
                photo.seek(0)
                filename = secure_filename(photo.filename)
                base, ext = os.path.splitext(filename)
                unique_name = f"voter_{base}_{int(datetime.now().timestamp())}{ext}"
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
            except Exception:
                unique_name = "default.png"

    try:
        cur.execute(
            """INSERT INTO students
               (college_id, password_hash, department, attendance_pct, class_section, role, mobile, email, photo)
               VALUES (?, ?, ?, ?, ?, 'Student Elector', ?, ?, ?)""",
            (cid, hash_password(pwd), dept_name, attendance, class_section, mobile, email, unique_name)
        )
        conn.commit()
        flash(f"🎉 Student Elector '{cid}' successfully registered for {dept_name}!", "success")
    except Exception as ex:
        flash(f"Error registering voter: {ex}", "danger")

    conn.close()
    return redirect(url_for("dept_dashboard"))


@app.route("/department/voters/delete/<college_id>", methods=["POST"])
def dept_delete_voter(college_id):
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, department FROM students WHERE college_id=?", (college_id,))
    row = cur.fetchone()
    if not row or row[1] != dept_name:
        conn.close()
        flash(f"❌ Error: Voter not found or does not belong to {dept_name}. Voters can only be removed by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("DELETE FROM votes WHERE student_id=?", (row[0],))
    cur.execute("DELETE FROM students WHERE id=?", (row[0],))
    conn.commit()
    conn.close()
    flash(f"Voter record '{college_id}' removed from {dept_name}.", "info")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/approve_candidate/<int:cid>", methods=["POST"])
def dept_approve_candidate(cid):
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT department FROM candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Candidate nomination record not found.", "danger")
        return redirect(url_for("dept_dashboard"))

    if row[0] != dept_name:
        conn.close()
        flash(f"❌ Error: Candidate belongs to '{row[0]}', not '{dept_name}'. Candidates can only be approved by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("UPDATE candidates SET status='approved' WHERE id=? AND department=?", (cid, dept_name))
    conn.commit()
    conn.close()
    flash("Candidate nomination approved! Candidate is now live on the ballot.", "success")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/reject_candidate/<int:cid>", methods=["POST"])
def dept_reject_candidate(cid):
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT department FROM candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Candidate nomination record not found.", "danger")
        return redirect(url_for("dept_dashboard"))

    if row[0] != dept_name:
        conn.close()
        flash(f"❌ Error: Candidate belongs to '{row[0]}', not '{dept_name}'. Candidates can only be rejected by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("UPDATE candidates SET status='rejected' WHERE id=? AND department=?", (cid, dept_name))
    conn.commit()
    conn.close()
    flash("Candidate nomination rejected.", "warning")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/candidates/add", methods=["POST"])
def dept_add_candidate():
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    election_id = request.form.get("election_id")
    candidate_name = request.form.get("candidate_name", "").strip()
    college_id = request.form.get("college_id", "").strip().upper()
    position_title = request.form.get("position_title", "").strip()
    manifesto = request.form.get("manifesto", "").strip()
    cgpa_str = request.form.get("cgpa", "").strip()
    photo = request.files.get("photo")

    cgpa = None
    if cgpa_str:
        try:
            cgpa = float(cgpa_str)
            if cgpa < 0 or cgpa > 10:
                cgpa = None
        except ValueError:
            cgpa = None

    if not election_id or not candidate_name:
        flash("Election and candidate name are required.", "danger")
        return redirect(url_for("dept_dashboard"))

    if not college_id:
        flash("❌ Error: College ID is required to verify the candidate's department. Candidates can only be added by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    # 1. Format check: does college_id correspond to this department?
    matches, detected_dept, reason = college_id_matches_department(college_id, dept_name, all_depts)
    if not matches:
        conn.close()
        flash(f"❌ Error: {reason}", "danger")
        return redirect(url_for("dept_dashboard"))

    # 2. Database check: candidate must be an enrolled voter of dept_name
    cur.execute("SELECT * FROM students WHERE college_id=?", (college_id,))
    student = row_to_dict(cur.fetchone())
    if not student:
        conn.close()
        flash(f"❌ Error: Student with College ID '{college_id}' is not enrolled in {dept_name}. A student must be registered as a voter in {dept_name} before being nominated.", "danger")
        return redirect(url_for("dept_dashboard"))

    if student.get("department") and student["department"] != dept_name:
        conn.close()
        flash(f"❌ Error: Candidate '{college_id}' belongs to the '{student['department']}' department, not '{dept_name}'. Candidates can only be added by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    # 3. Election check: verify if election is restricted to a different department
    cur.execute("SELECT * FROM elections WHERE id=?", (election_id,))
    election = row_to_dict(cur.fetchone())
    if election:
        election_dept = get_election_department(election, all_depts)
        if election_dept and election_dept.lower() != dept_name.lower():
            conn.close()
            flash(f"❌ Error: Election '{election['name']}' is restricted to '{election_dept}'. Candidates can only be added by their corresponding department.", "danger")
            return redirect(url_for("dept_dashboard"))

    # 4. Duplicate check
    cur.execute("SELECT id FROM candidates WHERE election_id=? AND (college_id=? OR name=?)", (election_id, college_id, candidate_name))
    if cur.fetchone():
        conn.close()
        flash(f"Candidate '{candidate_name}' ({college_id}) is already registered in this election.", "warning")
        return redirect(url_for("dept_dashboard"))

    unique_name = "default.png"
    if photo and photo.filename != "":
        if allowed_file(photo.filename):
            try:
                image = Image.open(photo)
                image.verify()
                photo.seek(0)
                filename = secure_filename(photo.filename)
                base, ext = os.path.splitext(filename)
                unique_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
            except Exception:
                unique_name = "default.png"

    # Get class_section from student record
    cur.execute("SELECT class_section FROM students WHERE college_id=?", (college_id,))
    stu = cur.fetchone()
    class_section = stu["class_section"] if stu and stu["class_section"] else None

    cur.execute("""
        INSERT INTO candidates (election_id, name, college_id, department, position_title, photo, manifesto, cgpa, class_section, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
    """, (election_id, candidate_name, college_id, dept_name, position_title, unique_name, manifesto, cgpa, class_section))
    conn.commit()
    conn.close()

    flash(f"🎉 Candidate '{candidate_name}' added to ballot under {dept_name}!", "success")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/delete_candidate/<int:cid>", methods=["POST"])
def dept_delete_candidate(cid):
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT department FROM candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Candidate record not found.", "danger")
        return redirect(url_for("dept_dashboard"))

    if row[0] != dept_name:
        conn.close()
        flash(f"❌ Error: Candidate belongs to '{row[0]}', not '{dept_name}'. Candidates can only be removed by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("DELETE FROM votes WHERE candidate_id=?", (cid,))
    cur.execute("DELETE FROM candidates WHERE id=? AND department=?", (cid, dept_name))
    conn.commit()
    conn.close()
    flash("Candidate removed from ballot.", "info")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/promote_candidate", methods=["POST"])
def dept_promote_candidate():
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    source_candidate_id = request.form.get("candidate_id")
    target_election_id = request.form.get("target_election_id")

    if not source_candidate_id or not target_election_id:
        flash("Source candidate and target round are required.", "danger")
        return redirect(url_for("dept_dashboard"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT c.*, e.stage, e.name as election_name
        FROM candidates c
        JOIN elections e ON c.election_id = e.id
        WHERE c.id=? AND c.department=?
    """, (source_candidate_id, dept_name))
    candidate = row_to_dict(cur.fetchone())

    cur.execute("SELECT * FROM elections WHERE id=?", (target_election_id,))
    target_election = row_to_dict(cur.fetchone())

    if not candidate or not target_election:
        conn.close()
        flash(f"❌ Error: Candidate not found or does not belong to {dept_name}. Candidates can only be promoted by their corresponding department.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())
    target_dept = get_election_department(target_election, all_depts)
    if target_dept and target_dept.lower() != dept_name.lower():
        conn.close()
        flash(f"❌ Error: Destination election '{target_election['name']}' is restricted to '{target_dept}'. Candidates can only be promoted to their corresponding department's election.", "danger")
        return redirect(url_for("dept_dashboard"))

    cur.execute("SELECT id FROM candidates WHERE election_id=? AND name=?",
                (target_election["id"], candidate["name"]))
    if cur.fetchone():
        conn.close()
        flash(f"Candidate '{candidate['name']}' is already competing in '{target_election['name']}'.", "warning")
        return redirect(url_for("dept_dashboard"))

    promotion_tag = f"Promoted Winner from {candidate['election_name']} ({dept_name})"
    target_stage = target_election.get("stage", 2)
    default_position = POSITION_TITLES.get(target_stage, ["Representative"])[0]

    cur.execute("""
        INSERT INTO candidates (election_id, name, college_id, department, position_title, photo, manifesto, promoted_from_stage, promotion_status, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
    """, (
        target_election["id"],
        candidate["name"],
        candidate.get("college_id"),
        candidate.get("department") or dept_name,
        candidate.get("position_title") or default_position,
        candidate["photo"] or "default.png",
        candidate["manifesto"] or f"Promoted winner from {dept_name}",
        candidate["stage"],
        promotion_tag
    ))
    conn.commit()
    conn.close()

    flash(f"🎉 Success! '{candidate['name']}' has been officially promoted to '{target_election['name']}'!", "success")
    return redirect(url_for("dept_dashboard"))


@app.route("/department/create_election", methods=["POST"])
def dept_create_election():
    if "dept_coordinator" not in session:
        return redirect(url_for("department_login"))

    dept_name = session["dept_coordinator"]["name"]
    dept_code = session["dept_coordinator"]["code"]

    try:
        duration = int(request.form.get("duration", 2880))
        stage = int(request.form.get("stage", 1))
    except (ValueError, TypeError):
        duration = 2880
        stage = 1

    election_name = request.form.get("election_name", "").strip()
    description = request.form.get("description", "").strip()
    category_name = STAGE_CONFIG.get(stage, {}).get("title", f"Stage {stage}")

    if not election_name:
        election_name = f"{dept_code} {category_name}"

    start = datetime.now()
    end = start + timedelta(minutes=duration)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time, department)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?)
    """, (election_name, stage, category_name, description, start.isoformat(), end.isoformat(), dept_name))
    conn.commit()
    conn.close()

    flash(f"Department Election '{election_name}' launched for {dept_name}!", "success")
    return redirect(url_for("dept_dashboard"))


# ================================================================
# CANDIDATE SELF-REGISTRATION MODULE (with 75% attendance + OTP)
# ================================================================

@app.route("/candidates")
def candidates():
    """Public directory of all approved candidates across election stages, grouped by department."""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.*, e.name as election_name, e.stage, e.category_name,
               COUNT(v.id) as votes
        FROM candidates c
        LEFT JOIN elections e ON c.election_id = e.id
        LEFT JOIN votes v ON c.id = v.candidate_id
        WHERE c.status = 'approved' OR c.status IS NULL
        GROUP BY c.id
        ORDER BY e.stage ASC, votes DESC, c.id ASC
    """)
    candidate_list = rows_to_dicts(cur.fetchall())

    # Fetch all departments for filter tabs
    cur.execute("SELECT name, code FROM departments ORDER BY name ASC")
    all_depts = rows_to_dicts(cur.fetchall())

    # Compute rank per (election_id) group and enrich
    from collections import defaultdict
    election_rank_counter = defaultdict(int)
    for c in candidate_list:
        c["stage_info"] = STAGE_CONFIG.get(c.get("stage", 1), {})
        election_rank_counter[c["election_id"]] += 1
        c["rank"] = election_rank_counter[c["election_id"]]

    # Group candidates by department
    dept_candidates = defaultdict(list)
    for c in candidate_list:
        dept = c.get("department") or "General"
        dept_candidates[dept].append(c)

    conn.close()
    return render_template(
        "candidates.html",
        candidates=candidate_list,
        dept_candidates=dict(dept_candidates),
        all_depts=all_depts,
        stages=STAGE_CONFIG
    )


@app.route("/candidate/register", methods=["GET", "POST"])
def candidate_register():
    """Step 1: Student checks eligibility & requests OTP."""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM elections WHERE is_active=1 ORDER BY stage ASC, id ASC")
    elections = rows_to_dicts(cur.fetchall())
    for e in elections:
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})
        e["position_titles"] = POSITION_TITLES.get(e["stage"], [])
    cur.execute("SELECT name FROM departments ORDER BY name ASC")
    departments = [r[0] for r in cur.fetchall()]
    conn.close()

    if request.method == "POST":
        college_id = request.form.get("college_id", "").strip().upper()
        election_id = request.form.get("election_id", "")

        if not college_id or not election_id:
            flash("College ID and Election selection are required.", "danger")
            return redirect(url_for("candidate_register"))

        # Check student exists
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE college_id=?", (college_id,))
        student = row_to_dict(cur.fetchone())

        if not student:
            conn.close()
            flash("College ID not found. Please register as a voter first.", "danger")
            return redirect(url_for("candidate_register"))

        attendance = student.get("attendance_pct", 0.0) or 0.0
        student_dept = student.get("department", "")
        selected_dept = request.form.get("department", "").strip()

        # Department match check
        if selected_dept and student_dept and selected_dept.lower() != student_dept.lower():
            conn.close()
            flash(
                f"❌ Department Mismatch: Your student profile is registered under '{student_dept}', but you selected '{selected_dept}'. "
                f"Candidates can only register under their corresponding department ({student_dept}).",
                "danger"
            )
            return redirect(url_for("candidate_register"))

        # Election restriction check
        cur.execute("SELECT * FROM elections WHERE id=?", (election_id,))
        election = row_to_dict(cur.fetchone())
        cur.execute("SELECT * FROM departments ORDER BY name ASC")
        all_depts = rows_to_dicts(cur.fetchall())
        if election:
            election_dept = get_election_department(election, all_depts)
            if election_dept and student_dept and election_dept.lower() != student_dept.lower():
                conn.close()
                flash(
                    f"❌ Election Restriction: The election '{election['name']}' is designated for '{election_dept}'. "
                    f"Your registered department is '{student_dept}'. Candidates can only contest in their corresponding department's election.",
                    "danger"
                )
                return redirect(url_for("candidate_register"))

        # 75% attendance check
        if attendance < 75.0:
            conn.close()
            flash(
                f"❌ Attendance Requirement Not Met! Your attendance is {attendance:.1f}%. "
                f"A minimum of 75% attendance is required to register as a candidate.",
                "danger"
            )
            return redirect(url_for("candidate_register"))

        # Check if already registered as candidate in this election
        cur.execute(
            "SELECT id FROM candidates WHERE election_id=? AND college_id=?",
            (election_id, college_id)
        )
        if cur.fetchone():
            conn.close()
            flash("You are already registered as a candidate in this election.", "warning")
            return redirect(url_for("candidate_register"))

        # Generate OTP
        otp = generate_otp(6)
        cur.execute(
            "INSERT INTO candidate_otp (college_id, otp, election_id) VALUES (?, ?, ?)",
            (college_id, otp, election_id)
        )
        conn.commit()
        conn.close()

        # Store in session for OTP verification step
        session["cand_reg"] = {
            "college_id": college_id,
            "election_id": election_id,
            "attendance": attendance,
            "student_name": college_id,
            "department": student.get("department", "")
        }

        flash(
            f"✅ Attendance verified ({attendance:.1f}%). Your OTP is: "
            f"<strong style='font-size:1.4em;letter-spacing:4px;color:#6366f1'>{otp}</strong> "
            f"(In a real system this would be sent via email/SMS)",
            "success"
        )
        return redirect(url_for("candidate_verify_otp"))

    return render_template(
        "candidate_register.html",
        elections=elections,
        departments=departments,
        position_titles=POSITION_TITLES
    )


@app.route("/candidate/verify-otp", methods=["GET", "POST"])
def candidate_verify_otp():
    """Step 2: Verify OTP and complete candidate profile."""
    if "cand_reg" not in session:
        return redirect(url_for("candidate_register"))

    cand_info = session["cand_reg"]

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM elections WHERE id=?", (cand_info["election_id"],))
    election = row_to_dict(cur.fetchone())
    if election:
        election["stage_info"] = STAGE_CONFIG.get(election["stage"], {})
        election["position_titles"] = POSITION_TITLES.get(election["stage"], [])
    conn.close()

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        if not entered_otp:
            # Check individual box inputs if posted
            entered_otp = "".join([request.form.get(f"otp_{i}", "") for i in range(1, 7)]).strip()

        candidate_name = request.form.get("candidate_name", "").strip() or cand_info.get("student_name") or cand_info["college_id"]
        position_title = request.form.get("position_title", "").strip() or request.form.get("custom_position", "").strip() or "Candidate"
        manifesto = request.form.get("manifesto", "").strip()
        photo = request.files.get("photo")

        # Validate OTP
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM candidate_otp
               WHERE college_id=? AND election_id=? AND used=0
               ORDER BY created_at DESC LIMIT 1""",
            (cand_info["college_id"], cand_info["election_id"])
        )
        otp_row = row_to_dict(cur.fetchone())

        if not otp_row or otp_row["otp"] != entered_otp:
            conn.close()
            flash("❌ Invalid OTP. Please check the code and try again.", "danger")
            return redirect(url_for("candidate_verify_otp"))

        # Mark OTP as used
        cur.execute("UPDATE candidate_otp SET used=1 WHERE id=?", (otp_row["id"],))

        # Handle photo upload
        unique_name = "default.png"
        if photo and photo.filename != "":
            if allowed_file(photo.filename):
                try:
                    image = Image.open(photo)
                    image.verify()
                    photo.seek(0)
                    filename = secure_filename(photo.filename)
                    base, ext = os.path.splitext(filename)
                    unique_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
                    photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
                except Exception:
                    unique_name = "default.png"

        # Get student's class_section
        cur.execute("SELECT class_section FROM students WHERE college_id=?", (cand_info["college_id"],))
        stu_row = cur.fetchone()
        cand_class_section = stu_row["class_section"] if stu_row and stu_row["class_section"] else None

        cgpa_str = request.form.get("cgpa", "").strip()
        cgpa = None
        if cgpa_str:
            try:
                cgpa = float(cgpa_str)
                if cgpa < 0 or cgpa > 10:
                    cgpa = None
            except ValueError:
                cgpa = None

        # Insert candidate
        cur.execute(
            """INSERT INTO candidates
               (election_id, name, college_id, department, position_title, photo, manifesto, cgpa, class_section, is_self_registered, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pending')""",
            (
                cand_info["election_id"],
                candidate_name,
                cand_info["college_id"],
                cand_info.get("department", ""),
                position_title,
                unique_name,
                manifesto,
                cgpa,
                cand_class_section
            )
        )
        conn.commit()
        conn.close()

        session.pop("cand_reg", None)
        flash(
            "🎉 Candidate registration submitted successfully! "
            "Your application has been forwarded to your Department Coordinator for verification and approval.",
            "success"
        )
        return redirect(url_for("candidate_register"))

    return render_template(
        "candidate_verify_otp.html",
        cand_info=cand_info,
        election=election
    )


@app.route("/admin/approve_candidate/<int:cid>", methods=["POST"])
def approve_candidate(cid):
    if "admin" not in session:
        return redirect(url_for("admin"))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE candidates SET status='approved' WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    flash("Candidate approved and is now visible to voters.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reject_candidate/<int:cid>", methods=["POST"])
def reject_candidate(cid):
    if "admin" not in session:
        return redirect(url_for("admin"))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE candidates SET status='rejected' WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    flash("Candidate application rejected.", "warning")
    return redirect(url_for("admin"))


# ---------------- MULTI-STAGE VOTING HUB ----------------
@app.route("/vote")
def vote_hub():
    if "student" not in session:
        return redirect(url_for("login"))

    expire_elections()
    sid = session["student"]["id"]

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM elections WHERE is_active=1 ORDER BY stage ASC, id ASC")
    elections = rows_to_dicts(cur.fetchall())

    cur.execute("SELECT election_id FROM votes WHERE student_id=?", (sid,))
    voted_elections = {r[0] for r in cur.fetchall()}
    conn.close()

    for e in elections:
        e["has_voted"] = e["id"] in voted_elections
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})

    return render_template(
        "vote_hub.html",
        elections=elections,
        stages=STAGE_CONFIG,
        student=session["student"]
    )


# ---------------- VOTE IN SPECIFIC ELECTION ----------------
@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    if "student" not in session:
        return redirect(url_for("login"))

    expire_elections()
    sid = session["student"]["id"]
    now = datetime.now().isoformat()

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM elections WHERE id=? AND is_active=1 AND start_time<=? AND end_time>=?",
                (election_id, now, now))
    election = row_to_dict(cur.fetchone())

    if not election:
        conn.close()
        flash("Election is not currently active or does not exist.", "warning")
        return redirect(url_for("vote_hub"))

    cur.execute("SELECT 1 FROM votes WHERE election_id=? AND student_id=?", (election["id"], sid))
    has_voted = cur.fetchone() is not None

    if request.method == "POST":
        if has_voted:
            conn.close()
            return redirect(url_for("thanks", election_id=election["id"]))

        candidate_id = request.form.get("candidate")
        if not candidate_id:
            conn.close()
            flash("Please select a candidate before casting your ballot.", "danger")
            return redirect(url_for("vote", election_id=election_id))

        try:
            cur.execute(
                "INSERT INTO votes (election_id, student_id, candidate_id) VALUES (?, ?, ?)",
                (election["id"], sid, candidate_id)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return redirect(url_for("thanks", election_id=election["id"]))

        conn.close()
        return redirect(url_for("thanks", election_id=election["id"]))

    # Only show approved candidates
    cur.execute(
        "SELECT * FROM candidates WHERE election_id=? AND (status='approved' OR status IS NULL OR is_self_registered=0)",
        (election["id"],)
    )
    candidates = rows_to_dicts(cur.fetchall())
    conn.close()

    election["stage_info"] = STAGE_CONFIG.get(election["stage"], {})

    return render_template(
        "vote.html",
        election=election,
        candidates=candidates,
        has_voted=has_voted,
        student=session["student"]
    )


# ---------------- THANKS ----------------
@app.route("/thanks")
@app.route("/thanks/<int:election_id>")
def thanks(election_id=None):
    return render_template("thanks.html", election_id=election_id)


# ---------------- RESULTS ----------------
@app.route("/results")
@app.route("/results/<int:election_id>")
def results(election_id=None):
    expire_elections()

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM elections ORDER BY stage ASC, id DESC")
    all_elections = rows_to_dicts(cur.fetchall())

    if not all_elections:
        conn.close()
        return render_template("results.html", status="no_election")

    if election_id:
        cur.execute("SELECT * FROM elections WHERE id=?", (election_id,))
        election = row_to_dict(cur.fetchone())
    else:
        concluded = [e for e in all_elections if e["is_active"] == 0]
        election = concluded[0] if concluded else all_elections[0]

    if not election:
        conn.close()
        return render_template("results.html", status="no_election")

    election["stage_info"] = STAGE_CONFIG.get(election["stage"], {})

    if election["is_active"] == 1:
        conn.close()
        return render_template(
            "results.html",
            status="running",
            election=election,
            all_elections=all_elections,
            stages=STAGE_CONFIG
        )

    cur.execute("""
        SELECT c.id, c.name, c.photo, c.manifesto, c.promoted_from_stage, c.promotion_status,
               c.position_title, c.department,
               COUNT(v.id) AS votes
        FROM candidates c
        LEFT JOIN votes v ON c.id=v.candidate_id
        WHERE c.election_id=?
        GROUP BY c.id
        ORDER BY votes DESC
    """, (election["id"],))
    results_list = rows_to_dicts(cur.fetchall())

    total_votes = sum(c["votes"] for c in results_list)
    for c in results_list:
        c["percent"] = round((c["votes"] / total_votes * 100), 1) if total_votes > 0 else 0

    winner = results_list[0] if results_list and results_list[0]["votes"] > 0 else (results_list[0] if results_list else None)

    conn.close()

    return render_template(
        "results.html",
        status="ended",
        election=election,
        all_elections=all_elections,
        results=results_list,
        winner=winner,
        total_votes=total_votes,
        stages=STAGE_CONFIG
    )


# ---------------- ADMIN DASHBOARD ----------------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST" and "admin_login" in request.form:
        if request.form.get("password") == ADMIN_PASS:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Incorrect admin password", "danger")

    if "admin" not in session:
        return render_template("admin.html", logged_in=False, position_titles=POSITION_TITLES)

    expire_elections()

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Create Election Handler
    if request.method == "POST" and "create_election" in request.form:
        try:
            duration = int(request.form.get("duration", 0))
            stage = int(request.form.get("stage", 1))
        except (ValueError, TypeError):
            flash("Invalid duration or stage", "danger")
            duration = None
            stage = 1

        election_name = request.form.get("election_name", "").strip()
        description = request.form.get("description", "").strip()
        dept = request.form.get("department", "").strip() or None
        category_name = STAGE_CONFIG.get(stage, {}).get("title", f"Stage {stage}")

        if election_name and duration and duration > 0:
            start = datetime.now()
            end = start + timedelta(minutes=duration)

            cur.execute("""
                INSERT INTO elections (name, stage, category_name, description, is_active, start_time, end_time, department)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """, (election_name, stage, category_name, description, start.isoformat(), end.isoformat(), dept))
            conn.commit()
            flash(f"Election '{election_name}' created in [{category_name}]!", "success")
            return redirect(url_for("admin"))

    # Add Candidate Handler
    if request.method == "POST" and "add_candidate" in request.form:
        election_id = request.form.get("election_id")
        candidate_name = request.form.get("candidate_name", "").strip()
        manifesto = request.form.get("manifesto", "").strip()
        position_title = request.form.get("position_title", "").strip()
        dept = request.form.get("candidate_dept", "").strip()
        photo = request.files.get("photo")

        if not election_id or not candidate_name:
            flash("Election and Candidate Name are required", "danger")
        else:
            cur.execute("SELECT * FROM elections WHERE id=?", (election_id,))
            election = row_to_dict(cur.fetchone())
            cur.execute("SELECT * FROM departments ORDER BY name ASC")
            all_depts = rows_to_dicts(cur.fetchall())
            if election and dept:
                election_dept = get_election_department(election, all_depts)
                if election_dept and election_dept.lower() != dept.lower():
                    flash(f"❌ Error: Election '{election['name']}' is restricted to '{election_dept}'. Candidate belongs to '{dept}'. Candidates can only be added to their corresponding department's election.", "danger")
                    conn.close()
                    return redirect(url_for("admin"))
            unique_name = "default.png"
            if photo and photo.filename != "":
                if allowed_file(photo.filename):
                    try:
                        image = Image.open(photo)
                        image.verify()
                        photo.seek(0)
                        filename = secure_filename(photo.filename)
                        base, ext = os.path.splitext(filename)
                        unique_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
                        photo.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
                    except Exception:
                        unique_name = "default.png"

            cur.execute(
                "INSERT INTO candidates (election_id, name, photo, manifesto, position_title, department, status) VALUES (?, ?, ?, ?, ?, ?, 'approved')",
                (election_id, candidate_name, unique_name, manifesto, position_title, dept)
            )
            conn.commit()
            flash(f"Candidate '{candidate_name}' added successfully!", "success")
            return redirect(url_for("admin"))

    # End Election Handler
    if request.method == "POST" and "end_election_id" in request.form:
        eid = request.form.get("end_election_id")
        cur.execute("UPDATE elections SET is_active=0 WHERE id=?", (eid,))
        conn.commit()
        flash("Election concluded and final tallies published!", "info")
        return redirect(url_for("admin"))

    # Fetch elections list
    cur.execute("SELECT * FROM elections ORDER BY stage ASC, id DESC")
    all_elections = rows_to_dicts(cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM students")
    total_students = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT student_id) FROM votes")
    unique_voters = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM votes")
    total_votes_cast = cur.fetchone()[0]

    # Enhance elections with candidate counts and tallies
    for e in all_elections:
        e["stage_info"] = STAGE_CONFIG.get(e["stage"], {})
        e["position_titles"] = POSITION_TITLES.get(e["stage"], [])
        cur.execute("""
            SELECT c.id, c.name, c.photo, c.manifesto, c.promoted_from_stage, c.promotion_status,
                   c.position_title, c.department, c.college_id, c.is_self_registered, c.status,
                   COUNT(v.id) AS votes
            FROM candidates c
            LEFT JOIN votes v ON c.id=v.candidate_id
            WHERE c.election_id=?
            GROUP BY c.id
            ORDER BY votes DESC
        """, (e["id"],))
        e["candidates"] = rows_to_dicts(cur.fetchall())
        e["winner"] = e["candidates"][0] if e["candidates"] else None
        cur.execute("SELECT COUNT(*) FROM votes WHERE election_id=?", (e["id"],))
        e["votes_count"] = cur.fetchone()[0]

    # Pending self-registered candidates
    cur.execute("""
        SELECT c.*, e.name as election_name, e.stage, e.category_name
        FROM candidates c
        JOIN elections e ON c.election_id = e.id
        WHERE c.is_self_registered=1 AND c.status='pending'
        ORDER BY c.id DESC
    """)
    pending_candidates = rows_to_dicts(cur.fetchall())

    # Fetch enrolled students
    cur.execute("SELECT * FROM students ORDER BY college_id ASC")
    students = rows_to_dicts(cur.fetchall())

    # Fetch departments
    cur.execute("SELECT * FROM departments ORDER BY name ASC")
    departments = rows_to_dicts(cur.fetchall())
    for d in departments:
        cur.execute("SELECT COUNT(*) FROM students WHERE department=?", (d["name"],))
        d["enrolled_students"] = cur.fetchone()[0]

    conn.close()

    turnout_pct = round((unique_voters / total_students * 100), 1) if total_students > 0 else 0

    return render_template(
        "admin.html",
        logged_in=True,
        elections=all_elections,
        students=students,
        departments=departments,
        pending_candidates=pending_candidates,
        total_students=total_students,
        unique_voters=unique_voters,
        total_votes_cast=total_votes_cast,
        turnout_pct=turnout_pct,
        stages=STAGE_CONFIG,
        position_titles=POSITION_TITLES
    )


# ---------------- PROMOTION TO NEXT ROUND (CR -> DEPT -> COUNCIL) ----------------
@app.route("/admin/promote_candidate", methods=["POST"])
def promote_candidate():
    if "admin" not in session:
        return redirect(url_for("admin"))

    source_candidate_id = request.form.get("candidate_id")
    target_election_id = request.form.get("target_election_id")

    if not source_candidate_id or not target_election_id:
        flash("Source candidate and target election must be specified.", "danger")
        return redirect(url_for("admin"))

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT c.*, e.stage, e.name as election_name
        FROM candidates c
        JOIN elections e ON c.election_id = e.id
        WHERE c.id=?
    """, (source_candidate_id,))
    candidate = row_to_dict(cur.fetchone())

    cur.execute("SELECT * FROM elections WHERE id=?", (target_election_id,))
    target_election = row_to_dict(cur.fetchone())

    if not candidate or not target_election:
        conn.close()
        flash("Candidate or target round not found.", "danger")
        return redirect(url_for("admin"))

    cur.execute("SELECT id FROM candidates WHERE election_id=? AND name=?",
                (target_election["id"], candidate["name"]))
    if cur.fetchone():
        conn.close()
        flash(f"Candidate '{candidate['name']}' is already competing in '{target_election['name']}'.", "warning")
        return redirect(url_for("admin"))

    promotion_tag = f"Promoted Winner from {candidate['election_name']}"
    target_stage = target_election.get("stage", 1)
    default_position = POSITION_TITLES.get(target_stage, ["Representative"])[0]

    cur.execute("""
        INSERT INTO candidates (election_id, name, college_id, department, position_title, photo, manifesto, promoted_from_stage, promotion_status, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
    """, (
        target_election["id"],
        candidate["name"],
        candidate.get("college_id"),
        candidate.get("department"),
        candidate.get("position_title") or default_position,
        candidate["photo"] or "default.png",
        candidate["manifesto"] or "Promoted to next round.",
        candidate["stage"],
        promotion_tag
    ))
    conn.commit()
    conn.close()

    flash(f"🎉 Success! '{candidate['name']}' has been officially promoted to '{target_election['name']}'!", "success")
    return redirect(url_for("admin"))


# ---------------- DELETE CANDIDATE ----------------
@app.route("/delete_candidate/<int:cid>", methods=["POST"])
def delete_candidate(cid):
    if "admin" not in session:
        return redirect(url_for("admin"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM votes WHERE candidate_id=?", (cid,))
    cur.execute("DELETE FROM candidates WHERE id=?", (cid,))
    conn.commit()
    conn.close()

    flash("Candidate removed successfully.", "success")
    return redirect(url_for("admin"))


# ---------------- DELETE STUDENT ----------------
@app.route("/delete_student/<college_id>", methods=["POST"])
def delete_student(college_id):
    if "admin" not in session:
        return redirect(url_for("admin"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM students WHERE college_id=?", (college_id,))
    row = cur.fetchone()

    if row:
        cur.execute("DELETE FROM votes WHERE student_id=?", (row[0],))
        cur.execute("DELETE FROM students WHERE id=?", (row[0],))
        conn.commit()
        flash(f"Voter record '{college_id}' deleted.", "success")

    conn.close()
    return redirect(url_for("admin"))


# ---------------- API: Get position titles for a stage ----------------
@app.route("/api/position_titles/<int:stage>")
def api_position_titles(stage):
    return jsonify(POSITION_TITLES.get(stage, []))


# ---------------- MAIN ----------------
if __name__ == "__main__":
    init_db()
    seed_data()
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("=====================================================")
    print(" VoteEase Multi-Stage College Voting System")
    print(" Live Server: http://127.0.0.1:5000")
    print(" Admin Portal: http://127.0.0.1:5000/admin (Key: admin123)")
    print(" Demo Student: STUDENT01 (Pass: vote123)")
    print(" Candidate Register: http://127.0.0.1:5000/candidate/register")
    print(" Departments: http://127.0.0.1:5000/departments")
    print("=====================================================")
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)

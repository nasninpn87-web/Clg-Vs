# 🗳️ VoteEase — College Digital Voting System

An institutional, secure, and intuitive web-based voting platform designed for college and university student council elections. Modeled after [VoteEase](https://aman5.pythonanywhere.com/) ([GitHub Repository](https://github.com/aman-matoliya-dev/VoteEase.git)).

---

## 🌟 Key Features

### 🏛️ Multi-Stage Governance Hierarchy & Round Progression
- **4 Progressive Democratic Tiers (as shown in interface)**:
  1. `🎓 Step 1: Class Election (CR)` — Elect section & class representatives.
  2. `📑 Step 2: Department Election` — Department-wide representatives.
  3. `🏛️ Step 3: University Council` — Executive student body government.
  4. `📋 Step 4: Campus Policies & Committees` — Policy referendums & committees.
- **Winner Round Promotion**:
  - When an election (e.g., Class CR) concludes, the administrator can promote the winning student candidate directly into the next round (**Step 2: Department Election**), and from Department to **Step 3: University Council** with a single click!
  - Promoted candidates carry forward their verified photo, manifesto, and qualification badge (`⭐ Promoted Winner from Class Election (CR)`).
- **Independent Ballot Privacy**: Students can cast one ballot per active election round (Class CR, Department, Council, Policies) without ballot conflict.

### 🧑‍🎓 Student Portal
- **Voting Hub (`/vote`)**: Displays all 4 active governance tiers, student eligibility, and individual voting statuses (`✅ Voted` vs `👉 Cast Ballot in Step 1/2/3/4`).
- **Interactive Voting Booth (`/vote/<id>`)**:
  - Live animated countdown timer with progress bar.
  - Candidate cards highlighting qualification tags and manifestos.
  - Glowing card selection with subtle background blur for non-selected candidates.
- **One-Vote Enforcement**: Enforced both at application controller level and at database level via `UNIQUE(election_id, student_id)`.

### 🛡️ Admin Dashboard (`/admin`)
- **Protected Access**: Admin panel guarded by environment-backed security key (default: `admin123`).
- **Real-Time Analytics**:
  - Total Enrolled Students
  - Votes Cast & Votes Remaining
  - Real-Time Voter Turnout Percentage (`%`)
  - Dynamic Front-Runner / Leading Candidate Banner
- **Election Lifecycle Management**:
  - Create new time-bounded elections with customizable duration (minutes or hours).
  - Launch or manually end elections to unveil public results.
- **Candidate Roster Management**:
  - Register new candidates with profile photo upload (supports PNG, JPG, JPEG, WEBP) and name.
  - Delete candidate records and cascade associated ballots.
- **Student Voter Directory**:
  - Real-time client-side search filter across all enrolled students.
  - Status indicators (`Voted` vs `Not Voted`).
  - Option to remove student accounts.

### 📊 Transparent Results (`/results`)
- **Live State**: Displays a real-time countdown timer while voting is active, preserving ballot confidentiality.
- **Concluded State**:
  - Animated trophy and winner banner highlighting the winning candidate and photo.
  - Complete transparent score breakdown with progress bars and percentage of total votes cast.

### 🔐 Security Architecture
- **Password Protection**: Passwords securely hashed with cryptographic PBKDF2 via `werkzeug.security`.
- **CSRF Tokens**: All POST actions validated using Flask-WTF CSRF protection.
- **SQL Injection Prevention**: 100% parameterized queries via Python `sqlite3`.
- **Cache-Control**: `no-store, no-cache, must-revalidate` response headers prevent back-button tampering on shared campus computers.
- **Safe File Uploads**: Image MIME validation, Pillow image verification, extension whitelisting, and timestamped sanitized filenames.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
Ensure you have **Python 3.9+** installed:
```bash
python --version
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Application
```bash
python run.py
```
*(Or alternatively: `python app.py`)*

The server will initialize the SQLite database (`database/voting.db`), seed the initial student batch and demo election, and start at:
👉 **http://127.0.0.1:5000**

---

## 🔑 Default Credentials

| Portal | Role | Identifier | Password |
|---|---|---|---|
| `/login` | **Test Student** | `STUDENT01` | `vote123` |
| `/login` | **Enrolled Student Batch** | `0905CS241001` to `0905CS241064` | `0905CS241001#`, `0905CS241002#`, etc. |
| `/admin` | **Administrator** | *(Security Key)* | `admin123` |

> *Note: Admin password can be customized via the `ADMIN_PASS` variable in the `.env` file.*

---

## 📁 Project Structure

```
CAMPUS VOTE/
├── app.py                  # Main Flask application & routing logic
├── run.py                  # Entrypoint startup script with banner
├── requirements.txt        # Python dependency manifest
├── .env                    # Active environment variables
├── .env.example            # Environment template
├── .gitignore              # Git ignore rules
├── database/
│   └── voting.db           # SQLite database file (auto-generated)
├── utils/
│   ├── db.py               # SQLite connection factory
│   └── security.py         # Password hash & check functions
├── templates/
│   ├── base.html           # Base layout template
│   ├── home.html           # Modern landing page (VoteEase replica)
│   ├── login.html          # Student authentication screen
│   ├── register.html       # Student self-registration screen
│   ├── vote.html           # Interactive voting booth with countdown
│   ├── thanks.html         # Post-vote confirmation screen
│   ├── results.html        # Public election results & winner podium
│   ├── admin.html          # Admin dashboard & management portal
│   └── election.html       # Fallback screen when no election is active
└── static/
    ├── css/
    │   ├── style.css       # Core styles
    │   ├── admin.css       # Admin dashboard styles
    │   └── login.css       # Login styles
    ├── photos/             # Uploaded candidate photos & avatars
    └── favicon.png         # Site favicon
```

---

## 🗄️ Database Schema

```sql
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE elections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL
);

CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    election_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    photo TEXT,
    FOREIGN KEY (election_id) REFERENCES elections (id) ON DELETE CASCADE
);

CREATE TABLE votes (
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
```

---

## 🧪 Testing the Voting Flow

1. Visit **`http://127.0.0.1:5000/`** — browse the hero landing page and active election cards.
2. Click **"Voter Login"** and sign in using `STUDENT01` / `vote123`.
3. You will be redirected to **`/vote`** with the multi-stage ballot booths.
4. Select a candidate card and click **"Cast Vote"**.
5. You are taken to **`/thanks`** confirming your vote is securely logged. Attempting to vote again in that tier is prevented.
6. Visit **`/department/login`** (choose e.g. "Computer Science (CSE)", password: `dept123`) to approve candidates, register new voters, and run department elections!
7. Visit **`/admin`** (password: `admin123`) to manage all academic departments, coordinator keys, and global election tiers.
8. Visit **`/results`** to see official winners and transparent vote tallies!

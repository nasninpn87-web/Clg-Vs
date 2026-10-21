"""
VoteEase - College Digital Voting System Startup Script
Usage:
    python run.py
"""
import sys

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app import app, init_db, seed_data

if __name__ == "__main__":
    init_db()
    seed_data()
    print("\n" + "=" * 65)
    print(" VoteEase -- Multi-Stage College Digital Voting System")
    print("=" * 65)
    print(" Live Home:       http://127.0.0.1:5000")
    print(" Voter Login:     http://127.0.0.1:5000/login (Demo: STUDENT01 / vote123)")
    print(" Dept Portal:     http://127.0.0.1:5000/department/login (Key: dept123)")
    print(" Admin Portal:    http://127.0.0.1:5000/admin (Key: admin123)")
    print(" Public Results:  http://127.0.0.1:5000/results")
    print("=" * 65 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=True)

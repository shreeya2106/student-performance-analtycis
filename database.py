import os
import sqlite3
import logging
from datetime import datetime
from seed_data import DEMO_STUDENTS, calculate_academic_status

logger = logging.getLogger(__name__)

DATABASE_PATH = os.environ.get("DATABASE_PATH", "edutrack.db")


def get_db_connection():
    """Create and return a database connection with dict-like row access."""
    # Ensure directory exists if path contains directory
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed_if_empty=True):
    """Initialize database schema and optionally populate sample demonstration data."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            semester INTEGER NOT NULL,
            cgpa REAL NOT NULL,
            attendance REAL NOT NULL,
            email TEXT,
            academic_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subject_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            marks REAL NOT NULL,
            max_marks REAL DEFAULT 100.0,
            FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE,
            UNIQUE(student_id, subject_name)
        )
    """)

    conn.commit()

    if seed_if_empty:
        cursor.execute("SELECT COUNT(*) AS count FROM students")
        row = cursor.fetchone()
        if row and row["count"] == 0:
            logger.info("Empty database detected. Seeding sample demonstration data...")
            seed_database(conn)

    conn.close()


def seed_database(conn=None):
    """Populates initial demonstration student dataset."""
    close_at_end = False
    if conn is None:
        conn = get_db_connection()
        close_at_end = True

    cursor = conn.cursor()
    cursor.execute("DELETE FROM subject_marks")
    cursor.execute("DELETE FROM students")

    now = datetime.utcnow().isoformat()

    for item in DEMO_STUDENTS:
        status = calculate_academic_status(item["cgpa"], item["attendance"])
        cursor.execute("""
            INSERT INTO students (student_id, name, department, semester, cgpa, attendance, email, academic_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["student_id"],
            item["name"],
            item["department"],
            item["semester"],
            float(item["cgpa"]),
            float(item["attendance"]),
            item.get("email", f"{item['student_id'].lower()}@demo.edutrack.edu"),
            status,
            now,
            now
        ))

        marks_dict = item.get("marks", {})
        for sub_name, mark_val in marks_dict.items():
            cursor.execute("""
                INSERT INTO subject_marks (student_id, subject_name, marks, max_marks)
                VALUES (?, ?, ?, 100.0)
            """, (item["student_id"], sub_name, float(mark_val)))

    conn.commit()
    if close_at_end:
        conn.close()


def check_db_health():
    """Verify database read/write capability."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return True, "Connected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False, str(e)


def get_all_students(department=None, semester=None, search=None, status=None):
    """Fetch students with optional filtering."""
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM students WHERE 1=1"
    params = []

    if department and department != "All":
        query += " AND department = ?"
        params.append(department)

    if semester and semester != "All":
        try:
            query += " AND semester = ?"
            params.append(int(semester))
        except ValueError:
            pass

    if status and status != "All":
        query += " AND academic_status = ?"
        params.append(status)

    if search:
        search_pattern = f"%{search}%"
        query += " AND (name LIKE ? OR student_id LIKE ?)"
        params.extend([search_pattern, search_pattern])

    query += " ORDER BY student_id ASC"

    cursor.execute(query, params)
    students = [dict(row) for row in cursor.fetchall()]

    # Fetch marks for each student
    for s in students:
        cursor.execute("SELECT subject_name, marks FROM subject_marks WHERE student_id = ?", (s["student_id"],))
        marks_rows = cursor.fetchall()
        s["marks"] = {r["subject_name"]: r["marks"] for r in marks_rows}

    conn.close()
    return students


def get_student_by_id(student_id):
    """Retrieve single student and their subject marks."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    student = dict(row)
    cursor.execute("SELECT subject_name, marks, max_marks FROM subject_marks WHERE student_id = ?", (student_id,))
    student["marks"] = {r["subject_name"]: r["marks"] for r in cursor.fetchall()}

    conn.close()
    return student


def create_student(data):
    """Create a new student record and associated marks."""
    conn = get_db_connection()
    cursor = conn.cursor()

    student_id = data["student_id"].strip().upper()
    name = data["name"].strip()
    department = data["department"].strip()
    semester = int(data["semester"])
    cgpa = round(float(data["cgpa"]), 2)
    attendance = round(float(data["attendance"]), 1)
    email = data.get("email", f"{student_id.lower()}@demo.edutrack.edu").strip()
    status = data.get("academic_status") or calculate_academic_status(cgpa, attendance)
    now = datetime.utcnow().isoformat()

    cursor.execute("""
        INSERT INTO students (student_id, name, department, semester, cgpa, attendance, email, academic_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, name, department, semester, cgpa, attendance, email, status, now, now))

    # Add subject marks if provided or create default 5 subjects
    subjects = data.get("marks", {
        "Cloud Computing": round(cgpa * 9.5, 1),
        "Database Systems": round(cgpa * 9.2, 1),
        "Operating Systems": round(cgpa * 9.0, 1),
        "Computer Networks": round(cgpa * 9.3, 1),
        "Data Structures": round(cgpa * 9.4, 1)
    })

    for sub_name, mark_val in subjects.items():
        val = min(100.0, max(0.0, float(mark_val)))
        cursor.execute("""
            INSERT INTO subject_marks (student_id, subject_name, marks, max_marks)
            VALUES (?, ?, ?, 100.0)
        """, (student_id, sub_name, val))

    conn.commit()
    conn.close()
    return get_student_by_id(student_id)


def update_student(student_id, data):
    """Update existing student record."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    if not cursor.fetchone():
        conn.close()
        return None

    name = data.get("name")
    department = data.get("department")
    semester = int(data["semester"]) if "semester" in data else None
    cgpa = round(float(data["cgpa"]), 2) if "cgpa" in data else None
    attendance = round(float(data["attendance"]), 1) if "attendance" in data else None
    email = data.get("email")

    # Fetch current to recompute status if needed
    cursor.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    current = dict(cursor.fetchone())

    new_cgpa = cgpa if cgpa is not None else current["cgpa"]
    new_attendance = attendance if attendance is not None else current["attendance"]
    status = data.get("academic_status") or calculate_academic_status(new_cgpa, new_attendance)
    now = datetime.utcnow().isoformat()

    cursor.execute("""
        UPDATE students
        SET name = COALESCE(?, name),
            department = COALESCE(?, department),
            semester = COALESCE(?, semester),
            cgpa = COALESCE(?, cgpa),
            attendance = COALESCE(?, attendance),
            email = COALESCE(?, email),
            academic_status = ?,
            updated_at = ?
        WHERE student_id = ?
    """, (name, department, semester, cgpa, attendance, email, status, now, student_id))

    if "marks" in data and isinstance(data["marks"], dict):
        for sub_name, mark_val in data["marks"].items():
            val = min(100.0, max(0.0, float(mark_val)))
            cursor.execute("""
                INSERT INTO subject_marks (student_id, subject_name, marks, max_marks)
                VALUES (?, ?, ?, 100.0)
                ON CONFLICT(student_id, subject_name) DO UPDATE SET marks = excluded.marks
            """, (student_id, sub_name, val))

    conn.commit()
    conn.close()
    return get_student_by_id(student_id)


def delete_student(student_id):
    """Delete a student and cascade marks."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    if not cursor.fetchone():
        conn.close()
        return False

    cursor.execute("DELETE FROM subject_marks WHERE student_id = ?", (student_id,))
    cursor.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    conn.commit()
    conn.close()
    return True


def get_analytics_overview():
    """Compute complete aggregated metrics for Dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total, AVG(cgpa) as avg_cgpa, AVG(attendance) as avg_attendance FROM students")
    base = dict(cursor.fetchone())
    total_students = base["total"] or 0
    avg_cgpa = round(base["avg_cgpa"] or 0.0, 2)
    avg_attendance = round(base["avg_attendance"] or 0.0, 1)

    # Attention count: CGPA < 6.5 OR Attendance < 75.0
    cursor.execute("SELECT COUNT(*) as attention_count FROM students WHERE cgpa < 6.5 OR attendance < 75.0")
    attention_count = cursor.fetchone()["attention_count"] or 0

    # CGPA Distribution
    cursor.execute("""
        SELECT
            SUM(CASE WHEN cgpa < 6.0 THEN 1 ELSE 0 END) as range_below_6,
            SUM(CASE WHEN cgpa >= 6.0 AND cgpa < 7.0 THEN 1 ELSE 0 END) as range_6_7,
            SUM(CASE WHEN cgpa >= 7.0 AND cgpa < 8.0 THEN 1 ELSE 0 END) as range_7_8,
            SUM(CASE WHEN cgpa >= 8.0 AND cgpa < 9.0 THEN 1 ELSE 0 END) as range_8_9,
            SUM(CASE WHEN cgpa >= 9.0 THEN 1 ELSE 0 END) as range_9_10
        FROM students
    """)
    cgpa_dist_row = cursor.fetchone()
    cgpa_distribution = [
        {"range": "< 6.0 (At Risk)", "count": cgpa_dist_row["range_below_6"] or 0},
        {"range": "6.0 - 6.9 (Average)", "count": cgpa_dist_row["range_6_7"] or 0},
        {"range": "7.0 - 7.9 (Good)", "count": cgpa_dist_row["range_7_8"] or 0},
        {"range": "8.0 - 8.9 (Very Good)", "count": cgpa_dist_row["range_8_9"] or 0},
        {"range": "9.0 - 10.0 (Distinction)", "count": cgpa_dist_row["range_9_10"] or 0},
    ]

    # Attendance Distribution
    cursor.execute("""
        SELECT
            SUM(CASE WHEN attendance < 65.0 THEN 1 ELSE 0 END) as range_below_65,
            SUM(CASE WHEN attendance >= 65.0 AND attendance < 75.0 THEN 1 ELSE 0 END) as range_65_75,
            SUM(CASE WHEN attendance >= 75.0 AND attendance < 85.0 THEN 1 ELSE 0 END) as range_75_85,
            SUM(CASE WHEN attendance >= 85.0 THEN 1 ELSE 0 END) as range_85_plus
        FROM students
    """)
    att_row = cursor.fetchone()
    attendance_distribution = [
        {"range": "< 65% (Critical)", "count": att_row["range_below_65"] or 0},
        {"range": "65% - 74% (Warning)", "count": att_row["range_65_75"] or 0},
        {"range": "75% - 84% (Adequate)", "count": att_row["range_75_85"] or 0},
        {"range": "85% - 100% (Excellent)", "count": att_row["range_85_plus"] or 0},
    ]

    # Subject-wise Average Marks
    cursor.execute("""
        SELECT subject_name, ROUND(AVG(marks), 1) as avg_marks, COUNT(marks) as student_count
        FROM subject_marks
        GROUP BY subject_name
        ORDER BY subject_name ASC
    """)
    subject_marks = [dict(r) for r in cursor.fetchall()]

    # Academic Performance by Semester
    cursor.execute("""
        SELECT semester, ROUND(AVG(cgpa), 2) as avg_cgpa, ROUND(AVG(attendance), 1) as avg_attendance, COUNT(*) as student_count
        FROM students
        GROUP BY semester
        ORDER BY semester ASC
    """)
    semester_performance = [
        {"semester": f"Sem {r['semester']}", "avg_cgpa": r["avg_cgpa"], "avg_attendance": r["avg_attendance"], "count": r["student_count"]}
        for r in cursor.fetchall()
    ]

    # Monthly performance trends (demonstration data trend based on cohort baseline)
    monthly_trends = [
        {"month": "Nov", "avg_cgpa": max(5.0, round(avg_cgpa - 0.28, 2)), "attendance": max(50.0, round(avg_attendance - 3.5, 1))},
        {"month": "Dec", "avg_cgpa": max(5.0, round(avg_cgpa - 0.15, 2)), "attendance": max(50.0, round(avg_attendance - 1.8, 1))},
        {"month": "Jan", "avg_cgpa": max(5.0, round(avg_cgpa - 0.08, 2)), "attendance": max(50.0, round(avg_attendance + 0.4, 1))},
        {"month": "Feb", "avg_cgpa": avg_cgpa, "attendance": avg_attendance},
        {"month": "Mar", "avg_cgpa": min(10.0, round(avg_cgpa + 0.12, 2)), "attendance": min(100.0, round(avg_attendance + 1.2, 1))},
        {"month": "Apr", "avg_cgpa": min(10.0, round(avg_cgpa + 0.18, 2)), "attendance": min(100.0, round(avg_attendance + 2.0, 1))},
    ]

    # Department-wise breakdown
    cursor.execute("""
        SELECT department, COUNT(*) as student_count, ROUND(AVG(cgpa), 2) as avg_cgpa, ROUND(AVG(attendance), 1) as avg_attendance
        FROM students
        GROUP BY department
        ORDER BY department ASC
    """)
    dept_performance = [dict(r) for r in cursor.fetchall()]

    # Recent student records for dashboard table (top 8)
    cursor.execute("SELECT student_id, name, department, semester, cgpa, attendance, academic_status FROM students ORDER BY student_id ASC LIMIT 10")
    recent_students = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "summary": {
            "total_students": total_students,
            "average_cgpa": avg_cgpa,
            "average_attendance": avg_attendance,
            "students_requiring_attention": attention_count
        },
        "cgpa_distribution": cgpa_distribution,
        "attendance_distribution": attendance_distribution,
        "subject_marks": subject_marks,
        "semester_performance": semester_performance,
        "monthly_trends": monthly_trends,
        "department_performance": dept_performance,
        "students": recent_students,
        "is_demo_data": True
    }


def get_analytics_performance():
    """Academic Performance detailed stats."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Subject performance
    cursor.execute("""
        SELECT subject_name,
               ROUND(AVG(marks), 1) as average_mark,
               MAX(marks) as max_mark,
               MIN(marks) as min_mark,
               COUNT(*) as total_students
        FROM subject_marks
        GROUP BY subject_name
    """)
    subject_stats = [dict(r) for r in cursor.fetchall()]

    # Semester performance
    cursor.execute("""
        SELECT semester,
               ROUND(AVG(cgpa), 2) as average_cgpa,
               ROUND(AVG(attendance), 1) as average_attendance,
               COUNT(*) as student_count
        FROM students
        GROUP BY semester
        ORDER BY semester ASC
    """)
    semester_stats = [
        {"semester": f"Semester {r['semester']}", "semester_num": r["semester"], "average_cgpa": r["average_cgpa"], "average_attendance": r["average_attendance"], "students": r["student_count"]}
        for r in cursor.fetchall()
    ]

    # Department performance comparison
    cursor.execute("""
        SELECT department,
               ROUND(AVG(cgpa), 2) as average_cgpa,
               ROUND(AVG(attendance), 1) as average_attendance,
               COUNT(*) as total_students
        FROM students
        GROUP BY department
    """)
    dept_stats = [dict(r) for r in cursor.fetchall()]

    # Students requiring academic support (CGPA < 6.5 or Attendance < 75.0)
    cursor.execute("""
        SELECT student_id, name, department, semester, cgpa, attendance, academic_status
        FROM students
        WHERE cgpa < 6.5 OR attendance < 75.0
        ORDER BY cgpa ASC
    """)
    at_risk_students = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "subject_statistics": subject_stats,
        "semester_statistics": semester_stats,
        "department_statistics": dept_stats,
        "students_requiring_support": at_risk_students,
        "support_count": len(at_risk_students),
        "is_demo_data": True
    }


def get_analytics_attendance():
    """Attendance page detailed stats."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT AVG(attendance) as avg_att, COUNT(*) as total FROM students")
    overview = dict(cursor.fetchone())

    # Department attendance
    cursor.execute("""
        SELECT department,
               ROUND(AVG(attendance), 1) as average_attendance,
               SUM(CASE WHEN attendance < 75.0 THEN 1 ELSE 0 END) as below_75_count,
               COUNT(*) as total_students
        FROM students
        GROUP BY department
    """)
    department_attendance = [dict(r) for r in cursor.fetchall()]

    # Attendance distribution
    cursor.execute("""
        SELECT
            SUM(CASE WHEN attendance < 65.0 THEN 1 ELSE 0 END) as critical,
            SUM(CASE WHEN attendance >= 65.0 AND attendance < 75.0 THEN 1 ELSE 0 END) as warning,
            SUM(CASE WHEN attendance >= 75.0 AND attendance < 85.0 THEN 1 ELSE 0 END) as adequate,
            SUM(CASE WHEN attendance >= 85.0 THEN 1 ELSE 0 END) as excellent
        FROM students
    """)
    dist = dict(cursor.fetchone())

    # Students below threshold (< 75%)
    cursor.execute("""
        SELECT student_id, name, department, semester, attendance, cgpa, academic_status
        FROM students
        WHERE attendance < 75.0
        ORDER BY attendance ASC
    """)
    low_attendance_students = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "average_attendance": round(overview["avg_att"] or 0.0, 1),
        "total_students": overview["total"] or 0,
        "low_attendance_count": len(low_attendance_students),
        "department_attendance": department_attendance,
        "distribution": [
            {"tier": "< 65% (Critical)", "count": dist["critical"] or 0, "color": "#ef4444"},
            {"tier": "65% - 74% (Warning)", "count": dist["warning"] or 0, "color": "#f59e0b"},
            {"tier": "75% - 84% (Adequate)", "count": dist["adequate"] or 0, "color": "#3b82f6"},
            {"tier": "85% - 100% (Excellent)", "count": dist["excellent"] or 0, "color": "#10b981"},
        ],
        "low_attendance_students": low_attendance_students,
        "is_demo_data": True
    }

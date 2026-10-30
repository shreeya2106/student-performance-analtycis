import os
import time
import json
import socket
import logging
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import redis

from database import (
    init_db,
    check_db_health,
    get_all_students,
    get_student_by_id,
    create_student,
    update_student,
    delete_student,
    get_analytics_overview,
    get_analytics_performance,
    get_analytics_attendance,
    seed_database
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("edutrack-api")

# Application Start Time for Uptime tracking
APP_START_TIME = time.time()

# In-memory metrics tracker
METRICS = {
    "total_requests": 0,
    "endpoints": {},
    "status_codes": {"2xx": 0, "4xx": 0, "5xx": 0},
    "latencies_ms": [],
    "cache_hits": 0,
    "cache_misses": 0
}


def get_redis_client():
    """Connect to Redis with resilient error handling and timeout."""
    redis_url = os.environ.get("REDIS_URL")
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    redis_password = os.environ.get("REDIS_PASSWORD", None)

    try:
        if redis_url:
            r = redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
        else:
            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True
            )
        r.ping()
        logger.info(f"Connected to Redis cache at {redis_host}:{redis_port}")
        return r
    except Exception as e:
        logger.warning(f"Redis cache is not available ({e}). Fallback to SQLite direct queries without caching.")
        return None


def invalidate_cache(r_client):
    """Purge cached analytics keys after student record changes."""
    if not r_client:
        return
    try:
        keys = r_client.keys("edutrack:analytics:*")
        if keys:
            r_client.delete(*keys)
            logger.info(f"Invalidated {len(keys)} Redis cache keys.")
    except Exception as e:
        logger.warning(f"Error invalidating Redis cache: {e}")


def create_app():
    """Application factory for EduTrack Flask Backend."""
    app = Flask(__name__)

    # Environment configuration
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "edutrack-college-lab-secret-key-2026")
    app.config["ENV_NAME"] = os.environ.get("APP_ENV", "local")
    app.config["VERSION"] = "1.0.0"

    # Configure CORS for frontend access
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Initialize SQLite database schema and sample data
    try:
        init_db(seed_if_empty=True)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

    # Initialize Redis connection
    redis_conn = get_redis_client()

    # Request timing & metrics middleware
    @app.before_request
    def record_start_time():
        g.start_time = time.time()

    @app.after_request
    def record_metrics_and_timing(response):
        if hasattr(g, "start_time"):
            duration_ms = round((time.time() - g.start_time) * 1000, 2)
            response.headers["X-Response-Time-Ms"] = str(duration_ms)

            # Update metrics safely
            METRICS["total_requests"] += 1
            endpoint = request.endpoint or request.path
            METRICS["endpoints"][endpoint] = METRICS["endpoints"].get(endpoint, 0) + 1

            code_group = f"{response.status_code // 100}xx"
            if code_group in METRICS["status_codes"]:
                METRICS["status_codes"][code_group] += 1

            # Keep sliding window of last 200 latencies for P95 calculation
            if len(METRICS["latencies_ms"]) >= 200:
                METRICS["latencies_ms"].pop(0)
            METRICS["latencies_ms"].append(duration_ms)

        return response

    # -------------------------------------------------------------
    # HEALTH & READY PROBES (Required for Kubernetes EKS)
    # -------------------------------------------------------------

    @app.route("/api/health", methods=["GET"])
    def health():
        """Liveness probe: verifies process is alive and internal state."""
        db_ok, db_msg = check_db_health()

        redis_status = "disabled"
        if redis_conn:
            try:
                redis_conn.ping()
                redis_status = "connected"
            except Exception:
                redis_status = "unreachable"

        status_code = 200 if db_ok else 500
        return jsonify({
            "status": "healthy" if db_ok else "unhealthy",
            "service": "edutrack-backend",
            "version": app.config["VERSION"],
            "environment": app.config["ENV_NAME"],
            "database": {"type": "SQLite", "status": "connected" if db_ok else "error", "message": db_msg},
            "redis_cache": {"status": redis_status},
            "hostname": socket.gethostname(),
            "uptime_seconds": int(time.time() - APP_START_TIME),
            "timestamp": datetime.utcnow().isoformat()
        }), status_code

    @app.route("/api/ready", methods=["GET"])
    def readiness():
        """Readiness probe: verifies backend is ready to accept user traffic."""
        db_ok, db_msg = check_db_health()
        if db_ok:
            return jsonify({
                "status": "ready",
                "ready_for_traffic": True,
                "timestamp": datetime.utcnow().isoformat()
            }), 200
        return jsonify({
            "status": "not_ready",
            "ready_for_traffic": False,
            "error": db_msg,
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    # -------------------------------------------------------------
    # STUDENT RECORD MANAGEMENT (CRUD)
    # -------------------------------------------------------------

    @app.route("/api/students", methods=["GET"])
    def list_students():
        """List students with search and filtering."""
        department = request.args.get("department")
        semester = request.args.get("semester")
        status = request.args.get("status")
        search = request.args.get("search")

        students = get_all_students(
            department=department,
            semester=semester,
            search=search,
            status=status
        )
        return jsonify({
            "count": len(students),
            "students": students,
            "is_demo_data": True
        }), 200

    @app.route("/api/students/<student_id>", methods=["GET"])
    def get_student(student_id):
        """Retrieve single student record."""
        student = get_student_by_id(student_id)
        if not student:
            return jsonify({"error": f"Student with ID '{student_id}' not found."}), 404
        return jsonify({"student": student, "is_demo_data": True}), 200

    @app.route("/api/students", methods=["POST"])
    def add_student():
        """Add new student with strict validation."""
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Invalid request payload. Expected JSON."}), 400

        # Field validation
        required_fields = ["student_id", "name", "department", "semester", "cgpa", "attendance"]
        missing = [f for f in required_fields if f not in data or str(data[f]).strip() == ""]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        student_id = str(data["student_id"]).strip().upper()

        # Check uniqueness
        if get_student_by_id(student_id):
            return jsonify({"error": f"Student ID '{student_id}' already exists."}), 409

        try:
            semester = int(data["semester"])
            if semester < 1 or semester > 8:
                return jsonify({"error": "Semester must be an integer between 1 and 8."}), 400
        except ValueError:
            return jsonify({"error": "Semester must be an integer."}), 400

        try:
            cgpa = float(data["cgpa"])
            if cgpa < 0.0 or cgpa > 10.0:
                return jsonify({"error": "CGPA must be a decimal number between 0.00 and 10.00."}), 400
        except ValueError:
            return jsonify({"error": "CGPA must be a valid number."}), 400

        try:
            attendance = float(data["attendance"])
            if attendance < 0.0 or attendance > 100.0:
                return jsonify({"error": "Attendance must be a percentage between 0.0 and 100.0."}), 400
        except ValueError:
            return jsonify({"error": "Attendance must be a valid number."}), 400

        created = create_student(data)
        invalidate_cache(redis_conn)

        return jsonify({
            "message": "Student record created successfully.",
            "student": created,
            "is_demo_data": True
        }), 201

    @app.route("/api/students/<student_id>", methods=["PUT"])
    def modify_student(student_id):
        """Update an existing student record."""
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Invalid request payload. Expected JSON."}), 400

        existing = get_student_by_id(student_id)
        if not existing:
            return jsonify({"error": f"Student '{student_id}' not found."}), 404

        # Validation on fields if present
        if "semester" in data:
            try:
                sem = int(data["semester"])
                if sem < 1 or sem > 8:
                    return jsonify({"error": "Semester must be between 1 and 8."}), 400
            except ValueError:
                return jsonify({"error": "Semester must be an integer."}), 400

        if "cgpa" in data:
            try:
                cgpa = float(data["cgpa"])
                if cgpa < 0.0 or cgpa > 10.0:
                    return jsonify({"error": "CGPA must be between 0.00 and 10.00."}), 400
            except ValueError:
                return jsonify({"error": "CGPA must be a valid number."}), 400

        if "attendance" in data:
            try:
                att = float(data["attendance"])
                if att < 0.0 or att > 100.0:
                    return jsonify({"error": "Attendance must be between 0.0 and 100.0."}), 400
            except ValueError:
                return jsonify({"error": "Attendance must be a valid number."}), 400

        updated = update_student(student_id, data)
        invalidate_cache(redis_conn)

        return jsonify({
            "message": "Student record updated successfully.",
            "student": updated,
            "is_demo_data": True
        }), 200

    @app.route("/api/students/<student_id>", methods=["DELETE"])
    def remove_student(student_id):
        """Delete a student record."""
        success = delete_student(student_id)
        if not success:
            return jsonify({"error": f"Student '{student_id}' not found."}), 404

        invalidate_cache(redis_conn)
        return jsonify({
            "message": f"Student '{student_id}' deleted successfully.",
            "deleted_id": student_id
        }), 200

    # -------------------------------------------------------------
    # ANALYTICS ENDPOINTS (Cached with Redis)
    # -------------------------------------------------------------

    @app.route("/api/analytics/overview", methods=["GET"])
    def analytics_overview():
        """Dashboard overview statistics with Redis caching."""
        cache_key = "edutrack:analytics:overview"
        if redis_conn:
            try:
                cached = redis_conn.get(cache_key)
                if cached:
                    METRICS["cache_hits"] += 1
                    resp = jsonify(json.loads(cached))
                    resp.headers["X-Cache"] = "HIT"
                    return resp, 200
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        METRICS["cache_misses"] += 1
        data = get_analytics_overview()

        if redis_conn:
            try:
                redis_conn.setex(cache_key, 60, json.dumps(data))
            except Exception as e:
                logger.warning(f"Redis setex failed: {e}")

        resp = jsonify(data)
        resp.headers["X-Cache"] = "MISS"
        return resp, 200

    @app.route("/api/analytics/performance", methods=["GET"])
    def analytics_performance():
        """Academic performance statistics."""
        cache_key = "edutrack:analytics:performance"
        if redis_conn:
            try:
                cached = redis_conn.get(cache_key)
                if cached:
                    METRICS["cache_hits"] += 1
                    resp = jsonify(json.loads(cached))
                    resp.headers["X-Cache"] = "HIT"
                    return resp, 200
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        METRICS["cache_misses"] += 1
        data = get_analytics_performance()

        if redis_conn:
            try:
                redis_conn.setex(cache_key, 60, json.dumps(data))
            except Exception as e:
                logger.warning(f"Redis setex failed: {e}")

        resp = jsonify(data)
        resp.headers["X-Cache"] = "MISS"
        return resp, 200

    @app.route("/api/analytics/attendance", methods=["GET"])
    def analytics_attendance():
        """Attendance management statistics."""
        cache_key = "edutrack:analytics:attendance"
        if redis_conn:
            try:
                cached = redis_conn.get(cache_key)
                if cached:
                    METRICS["cache_hits"] += 1
                    resp = jsonify(json.loads(cached))
                    resp.headers["X-Cache"] = "HIT"
                    return resp, 200
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        METRICS["cache_misses"] += 1
        data = get_analytics_attendance()

        if redis_conn:
            try:
                redis_conn.setex(cache_key, 60, json.dumps(data))
            except Exception as e:
                logger.warning(f"Redis setex failed: {e}")

        resp = jsonify(data)
        resp.headers["X-Cache"] = "MISS"
        return resp, 200

    # -------------------------------------------------------------
    # METRICS & SYSTEM INFO
    # -------------------------------------------------------------

    @app.route("/api/metrics", methods=["GET"])
    def metrics():
        """Return application-level metrics in JSON format."""
        latencies = METRICS["latencies_ms"]
        p95 = 0.0
        avg_lat = 0.0
        if latencies:
            sorted_lat = sorted(latencies)
            p95_idx = int(len(sorted_lat) * 0.95)
            p95 = round(sorted_lat[min(p95_idx, len(sorted_lat) - 1)], 2)
            avg_lat = round(sum(latencies) / len(latencies), 2)

        return jsonify({
            "service": "edutrack-backend",
            "uptime_seconds": int(time.time() - APP_START_TIME),
            "total_requests": METRICS["total_requests"],
            "endpoints": METRICS["endpoints"],
            "status_codes": METRICS["status_codes"],
            "latency": {
                "avg_ms": avg_lat,
                "p95_ms": p95,
                "min_ms": round(min(latencies), 2) if latencies else 0.0,
                "max_ms": round(max(latencies), 2) if latencies else 0.0,
                "sample_count": len(latencies)
            },
            "cache": {
                "hits": METRICS["cache_hits"],
                "misses": METRICS["cache_misses"],
                "hit_ratio": round(METRICS["cache_hits"] / max(1, (METRICS["cache_hits"] + METRICS["cache_misses"])), 2)
            },
            "timestamp": datetime.utcnow().isoformat()
        }), 200

    @app.route("/api/info", methods=["GET"])
    def info():
        """Application version and environment metadata."""
        redis_status = "disabled"
        if redis_conn:
            try:
                redis_conn.ping()
                redis_status = "connected"
            except Exception:
                redis_status = "error"

        return jsonify({
            "application": "EduTrack — Student Performance Analytics",
            "course": "Fundamentals of Cloud Computing Lab #2",
            "version": app.config["VERSION"],
            "environment": app.config["ENV_NAME"],
            "hostname": socket.gethostname(),
            "database": {
                "type": "SQLite",
                "storage": "Local container / volume (Local Demo)",
                "kubernetes_production_note": "For multi-replica EKS, use Amazon RDS (PostgreSQL/MySQL) or Aurora with shared connection pooling instead of SQLite."
            },
            "cache": {
                "type": "Redis",
                "status": redis_status
            },
            "demonstration_notice": "All student records and metrics are sample demonstration data. No real student personal information is processed."
        }), 200

    # -------------------------------------------------------------
    # DEMO RESET & CACHE MANAGEMENT
    # -------------------------------------------------------------

    @app.route("/api/reset-demo", methods=["POST"])
    def reset_demo():
        """Helper to reseed sample records for testing/demonstration."""
        seed_database()
        invalidate_cache(redis_conn)
        return jsonify({
            "message": "Database successfully re-seeded with 25 demonstration student records.",
            "is_demo_data": True
        }), 200

    @app.route("/api/cache/clear", methods=["POST"])
    def clear_cache():
        """Clear Redis cache manually."""
        invalidate_cache(redis_conn)
        return jsonify({"message": "Redis analytics cache cleared."}), 200

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

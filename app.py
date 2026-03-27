import os
from functools import wraps

import pymysql
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, session,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]  # intentional KeyError if missing — fail fast

CATEGORIES = [
    "Home Repairs", "Moving", "Assembly",
    "Cleaning", "Delivery", "Gardening", "Other",
]


# -----------------------------------------------------------------
# Database helper
# -----------------------------------------------------------------
def get_db():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
    )


# -----------------------------------------------------------------
# Auth decorator
# -----------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# -----------------------------------------------------------------
# Health check — ALB hits this every 30s
# -----------------------------------------------------------------
@app.route("/health")
def health():
    try:
        conn = get_db()
        conn.ping()
        conn.close()
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# -----------------------------------------------------------------
# Global error handlers
# -----------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    flash("Page not found.", "danger")
    return redirect(url_for("home"))


@app.errorhandler(500)
def internal_error(e):
    flash("A server error occurred. Please try again shortly.", "danger")
    return redirect(url_for("home"))


# -----------------------------------------------------------------
# Home — landing page
# -----------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html")


# -----------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")

        if len(password) < 4:
            flash("Password must be at least 4 characters.", "danger")
            return render_template("register.html")

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    flash("Username already taken.", "danger")
                    return render_template("register.html")

                cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, generate_password_hash(password)),
                )
                conn.commit()
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                user = cur.fetchone()
        finally:
            conn.close()

        session["user_id"] = user["id"]
        session["username"] = username
        flash("Account created! You're now logged in.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("login.html")

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                user = cur.fetchone()
        finally:
            conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        flash("Logged in successfully.", "success")
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("home"))


# -----------------------------------------------------------------
# Browse jobs — search & filter
# -----------------------------------------------------------------
@app.route("/jobs")
def jobs():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM jobs WHERE 1=1"
            params = []

            if q:
                sql += " AND (title LIKE %s OR description LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%"])

            if category:
                sql += " AND category = %s"
                params.append(category)

            sql += " ORDER BY created_at DESC"
            cur.execute(sql, params)
            job_list = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "jobs.html",
        jobs=job_list,
        q=q,
        category=category,
        categories=CATEGORIES,
    )


# -----------------------------------------------------------------
# Job detail + offers
# -----------------------------------------------------------------
@app.route("/jobs/<int:id>")
def job_detail(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (id,))
            job = cur.fetchone()

            if not job:
                flash("Job not found.", "danger")
                return redirect(url_for("jobs"))

            cur.execute(
                "SELECT o.*, u.username FROM offers o "
                "JOIN users u ON o.user_id = u.id "
                "WHERE o.job_id = %s ORDER BY o.created_at DESC",
                (id,),
            )
            offer_list = cur.fetchall()
    finally:
        conn.close()

    return render_template("job_detail.html", job=job, offers=offer_list)


@app.route("/jobs/<int:id>/offer", methods=["POST"])
@login_required
def make_offer(id):
    amount = request.form.get("amount", "").strip()
    message = request.form.get("message", "").strip()

    if not amount or not message:
        flash("Amount and message are required.", "danger")
        return redirect(url_for("job_detail", id=id))

    try:
        amount_val = float(amount)
    except ValueError:
        flash("Amount must be a number.", "danger")
        return redirect(url_for("job_detail", id=id))

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO offers (job_id, user_id, amount, message) "
                "VALUES (%s, %s, %s, %s)",
                (id, session["user_id"], amount_val, message),
            )
            conn.commit()
    finally:
        conn.close()

    flash("Offer submitted!", "success")
    return redirect(url_for("job_detail", id=id))


# -----------------------------------------------------------------
# Post a job
# -----------------------------------------------------------------
@app.route("/post", methods=["GET", "POST"])
@login_required
def post_job():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        budget = request.form.get("budget", "").strip()
        category = request.form.get("category", "").strip()

        if not all([title, description, budget, category]):
            flash("All fields are required.", "danger")
            return render_template("post.html", categories=CATEGORIES)

        try:
            budget_val = float(budget)
        except ValueError:
            flash("Budget must be a number.", "danger")
            return render_template("post.html", categories=CATEGORIES)

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO jobs (title, description, budget, category, posted_by, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (title, description, budget_val, category,
                     session["username"], session["user_id"]),
                )
                conn.commit()
        finally:
            conn.close()

        flash("Job posted successfully!", "success")
        return redirect(url_for("jobs"))

    return render_template("post.html", categories=CATEGORIES)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

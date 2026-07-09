from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

from database import open_connection, build_schema

app = Flask(__name__)
app.secret_key = "swap-this-for-a-real-secret-in-production"


def login_needed(view_func):
    @wraps(view_func)
    def guarded(*args, **kwargs):
        if "account_id" not in session:
            flash("Please sign in first.", "warning")
            return redirect(url_for("auth.login"))
        return view_func(*args, **kwargs)
    return guarded


def roles_only(*allowed_roles):
    def outer(view_func):
        @wraps(view_func)
        def guarded(*args, **kwargs):
            if "account_id" not in session:
                flash("Please sign in first.", "warning")
                return redirect(url_for("auth.login"))
            if session.get("role") not in allowed_roles:
                flash("That page isn't available for your account type.", "danger")
                return redirect(url_for("auth.landing"))
            return view_func(*args, **kwargs)
        return guarded
    return outer


@app.route("/", endpoint="auth.landing")
def landing():
    if "account_id" not in session:
        return redirect(url_for("auth.login"))
    role = session.get("role")
    if role == "admin":
        return redirect(url_for("admin.overview"))
    if role == "staff":
        return redirect(url_for("staff.my_expeditions"))
    return redirect(url_for("trekker.overview"))


@app.route("/register", methods=["GET", "POST"], endpoint="auth.register")
def register():
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip().lower()
        raw_password = request.form["password"]
        phone_no = request.form.get("phone_no", "").strip()
        role = request.form["role"]

        if role not in ("staff", "user"):
            flash("That account type isn't available for self-registration.", "danger")
            return redirect(url_for("auth.register"))

        conn = open_connection()
        clash = conn.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
        if clash:
            conn.close()
            flash("That email is already registered.", "danger")
            return redirect(url_for("auth.register"))

        starting_status = "pending" if role == "staff" else "active"
        conn.execute(
            """INSERT INTO accounts (full_name, email, password_hash, role, phone_no, account_status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (full_name, email, generate_password_hash(raw_password), role, phone_no, starting_status),
        )
        conn.commit()
        conn.close()

        if role == "staff":
            flash("Account created. A staff account needs admin sign-off before you can log in.", "info")
        else:
            flash("Account created - you can log in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"], endpoint="auth.login")
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        raw_password = request.form["password"]

        conn = open_connection()
        person = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        conn.close()

        if person is None or not check_password_hash(person["password_hash"], raw_password):
            flash("Email or password is incorrect.", "danger")
            return redirect(url_for("auth.login"))

        if person["account_status"] == "blacklisted":
            flash("This account has been blacklisted. Reach out to the admin.", "danger")
            return redirect(url_for("auth.login"))

        if person["role"] == "staff" and person["account_status"] == "pending":
            flash("Your staff account is still waiting on admin approval.", "warning")
            return redirect(url_for("auth.login"))

        session["account_id"] = person["id"]
        session["full_name"] = person["full_name"]
        session["role"] = person["role"]

        flash(f"Signed in as {person['full_name']}.", "success")
        return redirect(url_for("auth.landing"))

    return render_template("auth/login.html")


@app.route("/logout", endpoint="auth.logout")
def logout():
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))


@app.route("/admin/overview", endpoint="admin.overview")
@roles_only("admin")
def admin_overview():
    conn = open_connection()
    counts = {
        "expeditions": conn.execute("SELECT COUNT(*) c FROM expeditions").fetchone()["c"],
        "trekkers": conn.execute("SELECT COUNT(*) c FROM accounts WHERE role='user'").fetchone()["c"],
        "guides": conn.execute("SELECT COUNT(*) c FROM accounts WHERE role='staff'").fetchone()["c"],
        "live_reservations": conn.execute(
            "SELECT COUNT(*) c FROM reservations WHERE reservation_state='Booked'"
        ).fetchone()["c"],
        "pending_guides": conn.execute(
            "SELECT COUNT(*) c FROM accounts WHERE role='staff' AND account_status='pending'"
        ).fetchone()["c"],
    }
    newest_expeditions = conn.execute(
        "SELECT * FROM expeditions ORDER BY added_on DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return render_template("admin/dashboard.html", counts=counts, newest_expeditions=newest_expeditions)


@app.route("/admin/expeditions", endpoint="admin.list_expeditions")
@roles_only("admin")
def admin_list_expeditions():
    search_term = request.args.get("q", "").strip()
    conn = open_connection()
    base_sql = """SELECT expeditions.*, accounts.full_name AS guide_name
                  FROM expeditions LEFT JOIN accounts ON expeditions.guide_id = accounts.id"""
    if search_term:
        rows = conn.execute(
            base_sql + " WHERE expeditions.title LIKE ? OR expeditions.region LIKE ? ORDER BY expeditions.added_on DESC",
            (f"%{search_term}%", f"%{search_term}%"),
        ).fetchall()
    else:
        rows = conn.execute(base_sql + " ORDER BY expeditions.added_on DESC").fetchall()
    conn.close()
    return render_template("admin/expeditions.html", expeditions=rows, q=search_term)


@app.route("/admin/expeditions/new", methods=["GET", "POST"], endpoint="admin.new_expedition")
@roles_only("admin")
def admin_new_expedition():
    if request.method == "POST":
        title = request.form["title"].strip()
        region = request.form["region"].strip()
        difficulty_level = request.form["difficulty_level"]
        num_days = int(request.form["num_days"])
        capacity = int(request.form["capacity"])
        begin_date = request.form.get("begin_date", "").strip()
        finish_date = request.form.get("finish_date", "").strip()
        notes = request.form.get("notes", "").strip()

        if begin_date and finish_date and finish_date < begin_date:
            flash("End date cannot be before start date.", "danger")
            return render_template("admin/expedition_form.html", expedition=None, form_data=request.form)

        conn = open_connection()
        conn.execute(
            """INSERT INTO expeditions
               (title, region, difficulty_level, num_days, capacity, seats_left,
                current_state, begin_date, finish_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)""",
            (title, region, difficulty_level, num_days, capacity, capacity, begin_date, finish_date, notes),
        )
        conn.commit()
        conn.close()
        flash("Expedition created.", "success")
        return redirect(url_for("admin.list_expeditions"))

    return render_template("admin/expedition_form.html", expedition=None, form_data={})


@app.route("/admin/expeditions/<int:expedition_id>/edit", methods=["GET", "POST"], endpoint="admin.edit_expedition")
@roles_only("admin")
def admin_edit_expedition(expedition_id):
    conn = open_connection()
    expedition = conn.execute("SELECT * FROM expeditions WHERE id = ?", (expedition_id,)).fetchone()
    if expedition is None:
        conn.close()
        flash("That expedition no longer exists.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    if request.method == "POST":
        title = request.form["title"].strip()
        region = request.form["region"].strip()
        difficulty_level = request.form["difficulty_level"]
        num_days = int(request.form["num_days"])
        capacity = int(request.form["capacity"])
        begin_date = request.form.get("begin_date", "").strip()
        finish_date = request.form.get("finish_date", "").strip()
        notes = request.form.get("notes", "").strip()

        if begin_date and finish_date and finish_date < begin_date:
            conn.close()
            flash("End date cannot be before start date.", "danger")
            return render_template("admin/expedition_form.html", expedition=expedition, form_data=request.form)

        capacity_shift = capacity - expedition["capacity"]
        updated_seats_left = max(0, expedition["seats_left"] + capacity_shift)

        conn.execute(
            """UPDATE expeditions SET title=?, region=?, difficulty_level=?, num_days=?,
               capacity=?, seats_left=?, begin_date=?, finish_date=?, notes=? WHERE id=?""",
            (title, region, difficulty_level, num_days, capacity, updated_seats_left,
             begin_date, finish_date, notes, expedition_id),
        )
        conn.commit()
        conn.close()
        flash("Expedition updated.", "success")
        return redirect(url_for("admin.list_expeditions"))

    conn.close()
    return render_template("admin/expedition_form.html", expedition=expedition, form_data={})


@app.route("/admin/expeditions/<int:expedition_id>/delete", methods=["POST"], endpoint="admin.delete_expedition")
@roles_only("admin")
def admin_delete_expedition(expedition_id):
    conn = open_connection()
    conn.execute("DELETE FROM reservations WHERE expedition_id = ?", (expedition_id,))
    conn.execute("DELETE FROM expeditions WHERE id = ?", (expedition_id,))
    conn.commit()
    conn.close()
    flash("Expedition removed.", "info")
    return redirect(url_for("admin.list_expeditions"))


@app.route("/admin/expeditions/<int:expedition_id>/assign", methods=["GET", "POST"], endpoint="admin.assign_guide")
@roles_only("admin")
def admin_assign_guide(expedition_id):
    conn = open_connection()
    expedition = conn.execute("SELECT * FROM expeditions WHERE id = ?", (expedition_id,)).fetchone()
    if expedition is None:
        conn.close()
        flash("That expedition no longer exists.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    if request.method == "POST":
        chosen_guide_id = request.form.get("guide_id")
        if not chosen_guide_id:
            conn.close()
            flash("Please select a guide.", "warning")
            return redirect(url_for("admin.assign_guide", expedition_id=expedition_id))

        guide = conn.execute(
            "SELECT id FROM accounts WHERE id=? AND role='staff' AND account_status='approved'",
            (chosen_guide_id,)
        ).fetchone()
        if not guide:
            conn.close()
            flash("Selected guide is not available.", "danger")
            return redirect(url_for("admin.assign_guide", expedition_id=expedition_id))

        conn.execute(
            "UPDATE expeditions SET guide_id = ?, current_state = 'Approved' WHERE id = ?",
            (chosen_guide_id, expedition_id),
        )
        conn.commit()
        conn.close()
        flash("Guide assigned to expedition.", "success")
        return redirect(url_for("admin.list_expeditions"))

    eligible_guides = conn.execute(
        "SELECT * FROM accounts WHERE role='staff' AND account_status='approved'"
    ).fetchall()
    conn.close()
    return render_template("admin/assign_guide.html", expedition=expedition, eligible_guides=eligible_guides)


@app.route("/admin/expeditions/<int:expedition_id>/set-state", methods=["POST"], endpoint="admin.set_expedition_state")
@roles_only("admin")
def admin_set_expedition_state(expedition_id):
    conn = open_connection()
    expedition = conn.execute("SELECT * FROM expeditions WHERE id = ?", (expedition_id,)).fetchone()
    if expedition is None:
        conn.close()
        flash("Expedition not found.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    new_state = request.form.get("current_state", "")
    valid_states = ("Pending", "Approved", "Open", "Closed", "Completed")
    if new_state not in valid_states:
        conn.close()
        flash("Invalid state.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    conn.execute("UPDATE expeditions SET current_state=? WHERE id=?", (new_state, expedition_id))
    if new_state == "Completed":
        conn.execute(
            "UPDATE reservations SET reservation_state='Completed' WHERE expedition_id=? AND reservation_state='Booked'",
            (expedition_id,),
        )
    conn.commit()
    conn.close()
    flash(f"Expedition state set to {new_state}.", "success")
    return redirect(url_for("admin.list_expeditions"))


@app.route("/admin/guides", endpoint="admin.list_guides")
@roles_only("admin")
def admin_list_guides():
    search_term = request.args.get("q", "").strip()
    conn = open_connection()
    if search_term:
        guides = conn.execute(
            """SELECT * FROM accounts WHERE role='staff'
               AND (full_name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) = ?)
               ORDER BY joined_on DESC""",
            (f"%{search_term}%", f"%{search_term}%", search_term),
        ).fetchall()
    else:
        guides = conn.execute("SELECT * FROM accounts WHERE role='staff' ORDER BY joined_on DESC").fetchall()
    conn.close()
    return render_template("admin/guides.html", guides=guides, q=search_term)


@app.route("/admin/guides/<int:account_id>/approve", methods=["POST"], endpoint="admin.approve_guide")
@roles_only("admin")
def admin_approve_guide(account_id):
    conn = open_connection()
    result = conn.execute(
        "UPDATE accounts SET account_status='approved' WHERE id=? AND role='staff'", (account_id,)
    )
    conn.commit()
    conn.close()
    if result.rowcount:
        flash("Guide approved.", "success")
    else:
        flash("Guide not found.", "danger")
    return redirect(url_for("admin.list_guides"))


@app.route("/admin/guides/<int:account_id>/blacklist", methods=["POST"], endpoint="admin.blacklist_guide")
@roles_only("admin")
def admin_blacklist_guide(account_id):
    conn = open_connection()
    conn.execute("UPDATE accounts SET account_status='blacklisted' WHERE id=? AND role='staff'", (account_id,))
    conn.commit()
    conn.close()
    flash("Guide blacklisted.", "warning")
    return redirect(url_for("admin.list_guides"))


@app.route("/admin/guides/<int:account_id>/reinstate", methods=["POST"], endpoint="admin.reinstate_guide")
@roles_only("admin")
def admin_reinstate_guide(account_id):
    conn = open_connection()
    conn.execute("UPDATE accounts SET account_status='approved' WHERE id=? AND role='staff'", (account_id,))
    conn.commit()
    conn.close()
    flash("Guide reinstated.", "success")
    return redirect(url_for("admin.list_guides"))


@app.route("/admin/trekkers", endpoint="admin.list_trekkers")
@roles_only("admin")
def admin_list_trekkers():
    search_term = request.args.get("q", "").strip()
    conn = open_connection()
    if search_term:
        trekkers = conn.execute(
            """SELECT * FROM accounts WHERE role='user'
               AND (full_name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) = ?)
               ORDER BY joined_on DESC""",
            (f"%{search_term}%", f"%{search_term}%", search_term),
        ).fetchall()
    else:
        trekkers = conn.execute("SELECT * FROM accounts WHERE role='user' ORDER BY joined_on DESC").fetchall()
    conn.close()
    return render_template("admin/trekkers.html", trekkers=trekkers, q=search_term)


@app.route("/admin/trekkers/<int:account_id>/blacklist", methods=["POST"], endpoint="admin.blacklist_trekker")
@roles_only("admin")
def admin_blacklist_trekker(account_id):
    conn = open_connection()
    conn.execute("UPDATE accounts SET account_status='blacklisted' WHERE id=? AND role='user'", (account_id,))
    conn.commit()
    conn.close()
    flash("Trekker blacklisted.", "warning")
    return redirect(url_for("admin.list_trekkers"))


@app.route("/admin/trekkers/<int:account_id>/reinstate", methods=["POST"], endpoint="admin.reinstate_trekker")
@roles_only("admin")
def admin_reinstate_trekker(account_id):
    conn = open_connection()
    conn.execute("UPDATE accounts SET account_status='active' WHERE id=? AND role='user'", (account_id,))
    conn.commit()
    conn.close()
    flash("Trekker reinstated.", "success")
    return redirect(url_for("admin.list_trekkers"))


@app.route("/admin/reservations", endpoint="admin.all_reservations")
@roles_only("admin")
def admin_all_reservations():
    conn = open_connection()
    rows = conn.execute(
        """SELECT reservations.*, accounts.full_name AS trekker_name, expeditions.title AS expedition_title
           FROM reservations
           JOIN accounts ON reservations.account_id = accounts.id
           JOIN expeditions ON reservations.expedition_id = expeditions.id
           ORDER BY reservations.reserved_on DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin/reservations.html", reservations=rows)


@app.route("/staff/my-expeditions", endpoint="staff.my_expeditions")
@roles_only("staff")
def staff_my_expeditions():
    conn = open_connection()
    guide_id = session["account_id"]
    rows = conn.execute(
        "SELECT * FROM expeditions WHERE guide_id = ? ORDER BY added_on DESC", (guide_id,)
    ).fetchall()

    signup_counts = {}
    for exp in rows:
        signup_counts[exp["id"]] = conn.execute(
            "SELECT COUNT(*) c FROM reservations WHERE expedition_id=? AND reservation_state='Booked'",
            (exp["id"],),
        ).fetchone()["c"]

    conn.close()
    return render_template("staff/dashboard.html", expeditions=rows, signup_counts=signup_counts)


@app.route("/staff/expedition/<int:expedition_id>", methods=["GET", "POST"], endpoint="staff.expedition_detail")
@roles_only("staff")
def staff_expedition_detail(expedition_id):
    conn = open_connection()
    expedition = conn.execute("SELECT * FROM expeditions WHERE id = ?", (expedition_id,)).fetchone()

    if expedition is None or expedition["guide_id"] != session["account_id"]:
        conn.close()
        flash("This expedition isn't assigned to you.", "danger")
        return redirect(url_for("staff.my_expeditions"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_seats":
            new_seats_left = int(request.form["seats_left"])
            new_seats_left = max(0, min(new_seats_left, expedition["capacity"]))
            conn.execute("UPDATE expeditions SET seats_left=? WHERE id=?", (new_seats_left, expedition_id))
            conn.commit()
            flash("Seat count updated.", "success")

        elif action == "change_state":
            new_state = request.form["current_state"]
            if new_state in ("Open", "Closed", "Completed"):
                conn.execute("UPDATE expeditions SET current_state=? WHERE id=?", (new_state, expedition_id))
                conn.commit()
                if new_state == "Completed":
                    conn.execute(
                        "UPDATE reservations SET reservation_state='Completed' WHERE expedition_id=? AND reservation_state='Booked'",
                        (expedition_id,),
                    )
                    conn.commit()
                flash(f"Expedition marked {new_state}.", "success")

        conn.close()
        return redirect(url_for("staff.expedition_detail", expedition_id=expedition_id))

    signed_up = conn.execute(
        """SELECT reservations.*, accounts.full_name, accounts.email, accounts.phone_no
           FROM reservations JOIN accounts ON reservations.account_id = accounts.id
           WHERE reservations.expedition_id = ? ORDER BY reservations.reserved_on DESC""",
        (expedition_id,),
    ).fetchall()
    conn.close()
    return render_template("staff/expedition_detail.html", expedition=expedition, signed_up=signed_up)


@app.route("/staff/profile", methods=["GET", "POST"], endpoint="staff.profile")
@roles_only("staff")
def staff_profile():
    conn = open_connection()
    account = conn.execute("SELECT * FROM accounts WHERE id=?", (session["account_id"],)).fetchone()

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        phone_no = request.form.get("phone_no", "").strip()
        new_password = request.form.get("password", "").strip()

        if new_password:
            conn.execute(
                "UPDATE accounts SET full_name=?, phone_no=?, password_hash=? WHERE id=?",
                (full_name, phone_no, generate_password_hash(new_password), session["account_id"]),
            )
        else:
            conn.execute(
                "UPDATE accounts SET full_name=?, phone_no=? WHERE id=?",
                (full_name, phone_no, session["account_id"]),
            )
        conn.commit()
        conn.close()
        session["full_name"] = full_name
        flash("Profile saved.", "success")
        return redirect(url_for("staff.profile"))

    conn.close()
    return render_template("staff/profile.html", account=account)


@app.route("/trekker/overview", endpoint="trekker.overview")
@roles_only("user")
def trekker_overview():
    conn = open_connection()
    account_id = session["account_id"]

    open_count = conn.execute("SELECT COUNT(*) c FROM expeditions WHERE current_state='Open'").fetchone()["c"]
    active_reservations = conn.execute(
        "SELECT COUNT(*) c FROM reservations WHERE account_id=? AND reservation_state='Booked'",
        (account_id,),
    ).fetchone()["c"]

    latest = conn.execute(
        """SELECT reservations.*, expeditions.title AS expedition_title, expeditions.current_state
           FROM reservations JOIN expeditions ON reservations.expedition_id = expeditions.id
           WHERE reservations.account_id = ? ORDER BY reservations.reserved_on DESC LIMIT 5""",
        (account_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "trekker/dashboard.html", open_count=open_count,
        active_reservations=active_reservations, latest=latest,
    )


@app.route("/trekker/browse", endpoint="trekker.browse")
@roles_only("user")
def trekker_browse():
    search_term = request.args.get("q", "").strip()
    difficulty_level = request.args.get("difficulty_level", "").strip()
    region_filter = request.args.get("region", "").strip()

    sql = """SELECT expeditions.*, accounts.full_name AS guide_name FROM expeditions
             LEFT JOIN accounts ON expeditions.guide_id = accounts.id
             WHERE expeditions.current_state IN ('Approved', 'Open')"""
    params = []

    if search_term:
        sql += " AND (expeditions.title LIKE ? OR expeditions.region LIKE ?)"
        params += [f"%{search_term}%", f"%{search_term}%"]
    if difficulty_level:
        sql += " AND expeditions.difficulty_level = ?"
        params.append(difficulty_level)
    if region_filter:
        sql += " AND expeditions.region LIKE ?"
        params.append(f"%{region_filter}%")
    sql += " ORDER BY expeditions.added_on DESC"

    conn = open_connection()
    expeditions = conn.execute(sql, params).fetchall()

    already_booked = {
        row["expedition_id"] for row in conn.execute(
            "SELECT expedition_id FROM reservations WHERE account_id=? AND reservation_state='Booked'",
            (session["account_id"],),
        ).fetchall()
    }
    conn.close()
    return render_template(
        "trekker/browse.html", expeditions=expeditions, q=search_term,
        difficulty_level=difficulty_level, region=region_filter, already_booked=already_booked,
    )


@app.route("/trekker/book/<int:expedition_id>", methods=["POST"], endpoint="trekker.book")
@roles_only("user")
def trekker_book(expedition_id):
    conn = open_connection()
    expedition = conn.execute("SELECT * FROM expeditions WHERE id = ?", (expedition_id,)).fetchone()

    if expedition is None:
        conn.close()
        flash("That expedition no longer exists.", "danger")
        return redirect(url_for("trekker.browse"))

    if expedition["current_state"] != "Open":
        conn.close()
        flash("This expedition isn't open for booking right now.", "warning")
        return redirect(url_for("trekker.browse"))

    if expedition["seats_left"] <= 0:
        conn.close()
        flash("No seats left on this expedition.", "warning")
        return redirect(url_for("trekker.browse"))

    account_id = session["account_id"]
    dupe = conn.execute(
        "SELECT id FROM reservations WHERE account_id=? AND expedition_id=? AND reservation_state='Booked'",
        (account_id, expedition_id),
    ).fetchone()
    if dupe:
        conn.close()
        flash("You've already booked this expedition.", "info")
        return redirect(url_for("trekker.browse"))

    conn.execute(
        "INSERT INTO reservations (account_id, expedition_id, reservation_state) VALUES (?, ?, 'Booked')",
        (account_id, expedition_id),
    )
    conn.execute("UPDATE expeditions SET seats_left = seats_left - 1 WHERE id = ?", (expedition_id,))
    conn.commit()
    conn.close()
    flash("Seat booked!", "success")
    return redirect(url_for("trekker.my_reservations"))


@app.route("/trekker/my-reservations", endpoint="trekker.my_reservations")
@roles_only("user")
def trekker_my_reservations():
    conn = open_connection()
    rows = conn.execute(
        """SELECT reservations.*, expeditions.title AS expedition_title, expeditions.region,
                  expeditions.begin_date, expeditions.finish_date, expeditions.current_state
           FROM reservations JOIN expeditions ON reservations.expedition_id = expeditions.id
           WHERE reservations.account_id = ? ORDER BY reservations.reserved_on DESC""",
        (session["account_id"],),
    ).fetchall()
    conn.close()
    return render_template("trekker/reservations.html", reservations=rows)


@app.route("/trekker/cancel/<int:reservation_id>", methods=["POST"], endpoint="trekker.cancel")
@roles_only("user")
def trekker_cancel(reservation_id):
    conn = open_connection()
    reservation = conn.execute(
        "SELECT * FROM reservations WHERE id=? AND account_id=?",
        (reservation_id, session["account_id"]),
    ).fetchone()

    if reservation is None:
        conn.close()
        flash("Reservation not found.", "danger")
        return redirect(url_for("trekker.my_reservations"))

    if reservation["reservation_state"] != "Booked":
        conn.close()
        flash("Only an active reservation can be cancelled.", "warning")
        return redirect(url_for("trekker.my_reservations"))

    conn.execute("UPDATE reservations SET reservation_state='Cancelled' WHERE id=?", (reservation_id,))
    conn.execute(
        "UPDATE expeditions SET seats_left = seats_left + 1 WHERE id=?", (reservation["expedition_id"],)
    )
    conn.commit()
    conn.close()
    flash("Reservation cancelled.", "info")
    return redirect(url_for("trekker.my_reservations"))


@app.route("/trekker/profile", methods=["GET", "POST"], endpoint="trekker.profile")
@roles_only("user")
def trekker_profile():
    conn = open_connection()
    account = conn.execute("SELECT * FROM accounts WHERE id=?", (session["account_id"],)).fetchone()

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        phone_no = request.form.get("phone_no", "").strip()
        new_password = request.form.get("password", "").strip()

        if new_password:
            conn.execute(
                "UPDATE accounts SET full_name=?, phone_no=?, password_hash=? WHERE id=?",
                (full_name, phone_no, generate_password_hash(new_password), session["account_id"]),
            )
        else:
            conn.execute(
                "UPDATE accounts SET full_name=?, phone_no=? WHERE id=?",
                (full_name, phone_no, session["account_id"]),
            )
        conn.commit()
        conn.close()
        session["full_name"] = full_name
        flash("Profile saved.", "success")
        return redirect(url_for("trekker.profile"))

    conn.close()
    return render_template("trekker/profile.html", account=account)


build_schema()

if __name__ == "__main__":
    app.run(debug=True)
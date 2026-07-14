from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, Account, Expedition, Reservation, seed_admin

app = Flask(__name__)
# to secure the session (login) cookies.
app.secret_key = "swap-this-for-a-real-secret-in-production"

# DataBase setup
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///trekking.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

#this will create table and an admin account if it does not exist
with app.app_context():
    db.create_all()
    seed_admin()


# Decorators
def login_needed(view_func):
    @wraps(view_func)
    def guarded(*args, **kwargs):
        if "account_id" not in session:
            flash("Please sign in first.", "warning")
            return redirect(url_for("auth.login"))
        return view_func(*args, **kwargs)
    return guarded


# Decorator that check if the user has one of the permitted roles. If DNE then it sends to login page with flash message.
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


# Auth routes/ login page that check for the login and send to the correct dashboard based on the user's role.
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

 
#it takes the data from register form and creates the account based on role.
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
        
        #same email can not register twice.
        clash = Account.query.filter_by(email=email).first()
        if clash:
            flash("That email is already registered.", "danger")
            return redirect(url_for("auth.register"))
        
        #the staff status will be pending untill the admin approves. 
        starting_status = "pending" if role == "staff" else "active"

        new_account = Account(
            full_name=full_name,
            email=email,
            password_hash=generate_password_hash(raw_password),
            role=role,
            phone_no=phone_no,
            account_status=starting_status,
        )
        db.session.add(new_account)
        db.session.commit()

        if role == "staff":
            flash("Account created. A staff account needs admin approval before you can log in.", "info")
        else:
            flash("Account created - you can log in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")

#sends to the correct dashboard based on user's role after login.
@app.route("/login", methods=["GET", "POST"], endpoint="auth.login")
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        raw_password = request.form["password"]

        person = Account.query.filter_by(email=email).first()

        if person is None or not check_password_hash(person.password_hash, raw_password):
            flash("Email or password is incorrect.", "danger")
            return redirect(url_for("auth.login"))

        if person.account_status == "blacklisted":
            flash("This account has been blacklisted. Reach out to the admin.", "danger")
            return redirect(url_for("auth.login"))

        if person.role == "staff" and person.account_status == "pending":
            flash("Your staff account is still waiting on admin approval.", "warning")
            return redirect(url_for("auth.login"))

        session["account_id"] = person.id
        session["full_name"] = person.full_name
        session["role"] = person.role

        flash(f"Signed in as {person.full_name}.", "success")
        return redirect(url_for("auth.landing"))

    return render_template("auth/login.html")

#logout route that clears the session and sends to login page.
@app.route("/logout", endpoint="auth.logout")
def logout():
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))





# Admin routes

#Admin dashboard that shows the counts of the expeditions, trekkers, guides, live reservations and pending guides. it also shows the latest 5 expeditions.
@app.route("/admin/overview", endpoint="admin.overview")
@roles_only("admin")
def admin_overview():
    counts = {
        "expeditions": Expedition.query.count(),
        "trekkers": Account.query.filter_by(role="user").count(),
        "guides": Account.query.filter_by(role="staff").count(),
        "live_reservations": Reservation.query.filter_by(reservation_state="Booked").count(),
        "pending_guides": Account.query.filter_by(role="staff", account_status="pending").count(),
    }
    newest_expeditions = Expedition.query.order_by(Expedition.added_on.desc()).limit(5).all()
    return render_template("admin/dashboard.html", counts=counts, newest_expeditions=newest_expeditions)


#It shows the list of expiditions with search function.
@app.route("/admin/expeditions", endpoint="admin.list_expeditions")
@roles_only("admin")
def admin_list_expeditions():
    search_term = request.args.get("q", "").strip()

    query = Expedition.query
    if search_term:
        like = f"%{search_term}%"
        query = query.filter(db.or_(Expedition.title.like(like), Expedition.region.like(like)))
    rows = query.order_by(Expedition.added_on.desc()).all()

    for exp in rows:
        exp.guide_name = exp.guide.full_name if exp.guide else None

    return render_template("admin/expeditions.html", expeditions=rows, q=search_term)

# new trekking expedition making route. It takes data from form and creates a new expidition.
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
        
        #ensures that the start date can not be before the end date
        if begin_date and finish_date and finish_date < begin_date:
            flash("End date cannot be before start date.", "danger")
            return render_template("admin/expedition_form.html", expedition=None, form_data=request.form)

        new_expedition = Expedition(
            title=title,
            region=region,
            difficulty_level=difficulty_level,
            num_days=num_days,
            capacity=capacity,
            seats_left=capacity,
            current_state="Pending",
            begin_date=begin_date,
            finish_date=finish_date,
            notes=notes,
        )
        db.session.add(new_expedition)
        db.session.commit()
        flash("Expedition created.", "success")
        return redirect(url_for("admin.list_expeditions"))

    return render_template("admin/expedition_form.html", expedition=None, form_data={})

#search for expedition to edit.
@app.route("/admin/expeditions/<int:expedition_id>/edit", methods=["GET", "POST"], endpoint="admin.edit_expedition")
@roles_only("admin")
def admin_edit_expedition(expedition_id):
    expedition = Expedition.query.get(expedition_id)
    if expedition is None:
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
            flash("End date cannot be before start date.", "danger")
            return render_template("admin/expedition_form.html", expedition=expedition, form_data=request.form)

        capacity_shift = capacity - expedition.capacity
        updated_seats_left = max(0, expedition.seats_left + capacity_shift)

        expedition.title = title
        expedition.region = region
        expedition.difficulty_level = difficulty_level
        expedition.num_days = num_days
        expedition.capacity = capacity
        expedition.seats_left = updated_seats_left
        expedition.begin_date = begin_date
        expedition.finish_date = finish_date
        expedition.notes = notes
        db.session.commit()

        flash("Expedition updated.", "success")
        return redirect(url_for("admin.list_expeditions"))

    return render_template("admin/expedition_form.html", expedition=expedition, form_data={})

#delete the expedition and all the reservations associated  with it.
@app.route("/admin/expeditions/<int:expedition_id>/delete", methods=["POST"], endpoint="admin.delete_expedition")
@roles_only("admin")
def admin_delete_expedition(expedition_id):
    Reservation.query.filter_by(expedition_id=expedition_id).delete()
    Expedition.query.filter_by(id=expedition_id).delete()
    db.session.commit()
    flash("Expedition removed.", "info")
    return redirect(url_for("admin.list_expeditions"))

#assign treek guide to the journey, if the guide is available(approved and not blacklisted)
@app.route("/admin/expeditions/<int:expedition_id>/assign", methods=["GET", "POST"], endpoint="admin.assign_guide")
@roles_only("admin")
def admin_assign_guide(expedition_id):
    expedition = Expedition.query.get(expedition_id)
    if expedition is None:
        flash("That expedition no longer exists.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    if request.method == "POST":
        chosen_guide_id = request.form.get("guide_id")
        if not chosen_guide_id:
            flash("Please select a guide.", "warning")
            return redirect(url_for("admin.assign_guide", expedition_id=expedition_id))

        guide = Account.query.filter_by(id=chosen_guide_id, role="staff", account_status="approved").first()
        if not guide:
            flash("Selected guide is not available.", "danger")
            return redirect(url_for("admin.assign_guide", expedition_id=expedition_id))

        expedition.guide_id = chosen_guide_id
        expedition.current_state = "Approved"
        db.session.commit()
        flash("Guide assigned to expedition.", "success")
        return redirect(url_for("admin.list_expeditions"))

    eligible_guides = Account.query.filter_by(role="staff", account_status="approved").all()
    return render_template("admin/assign_guide.html", expedition=expedition, eligible_guides=eligible_guides)

#admin can set the trek status
@app.route("/admin/expeditions/<int:expedition_id>/set-state", methods=["POST"], endpoint="admin.set_expedition_state")
@roles_only("admin")
def admin_set_expedition_state(expedition_id):
    expedition = Expedition.query.get(expedition_id)
    if expedition is None:
        flash("Expedition not found.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    new_state = request.form.get("current_state", "")
    valid_states = ("Pending", "Approved", "Open", "Closed", "Completed")
    if new_state not in valid_states:
        flash("Invalid state.", "danger")
        return redirect(url_for("admin.list_expeditions"))

    expedition.current_state = new_state
    if new_state == "Completed":
        Reservation.query.filter_by(expedition_id=expedition_id, reservation_state="Booked").update(
            {"reservation_state": "Completed"}
        )
    db.session.commit()
    flash(f"Expedition state set to {new_state}.", "success")
    return redirect(url_for("admin.list_expeditions"))


#it shows the list of guides with function to approve. Can search with name, email-id or number
@app.route("/admin/guides", endpoint="admin.list_guides")
@roles_only("admin")
def admin_list_guides():
    search_term = request.args.get("q", "").strip()

    query = Account.query.filter_by(role="staff")
    if search_term:
        like = f"%{search_term}%"
        query = query.filter(
            db.or_(
                Account.full_name.like(like),
                Account.email.like(like),
                db.cast(Account.id, db.String) == search_term,
            )
        )
    guides = query.order_by(Account.joined_on.desc()).all()
    return render_template("admin/guides.html", guides=guides, q=search_term)


#it approve the pending staffs- after approval he can login
@app.route("/admin/guides/<int:account_id>/approve", methods=["POST"], endpoint="admin.approve_guide")
@roles_only("admin")
def admin_approve_guide(account_id):
    guide = Account.query.filter_by(id=account_id, role="staff").first()
    if guide:
        guide.account_status = "approved"
        db.session.commit()
        flash("Guide approved.", "success")
    else:
        flash("Guide not found.", "danger")
    return redirect(url_for("admin.list_guides"))



#it blacklist the approved guides- after blacklisting he can not login.
@app.route("/admin/guides/<int:account_id>/blacklist", methods=["POST"], endpoint="admin.blacklist_guide")
@roles_only("admin")
def admin_blacklist_guide(account_id):
    guide = Account.query.filter_by(id=account_id, role="staff").first()
    if guide:
        guide.account_status = "blacklisted"
        db.session.commit()
    flash("Guide blacklisted.", "warning")
    return redirect(url_for("admin.list_guides"))


#blacklisted guide ko wapas normal kar deta hai.
@app.route("/admin/guides/<int:account_id>/reinstate", methods=["POST"], endpoint="admin.reinstate_guide")
@roles_only("admin")
def admin_reinstate_guide(account_id):
    guide = Account.query.filter_by(id=account_id, role="staff").first()
    if guide:
        guide.account_status = "approved"
        db.session.commit()
    flash("Guide reinstated.", "success")
    return redirect(url_for("admin.list_guides"))


#trekkers can serach with name, email-id or number.
@app.route("/admin/trekkers", endpoint="admin.list_trekkers")
@roles_only("admin")
def admin_list_trekkers():
    search_term = request.args.get("q", "").strip()

    query = Account.query.filter_by(role="user")
    if search_term:
        like = f"%{search_term}%"
        query = query.filter(
            db.or_(
                Account.full_name.like(like),
                Account.email.like(like),
                db.cast(Account.id, db.String) == search_term,
            )
        )
    trekkers = query.order_by(Account.joined_on.desc()).all()
    return render_template("admin/trekkers.html", trekkers=trekkers, q=search_term)


# it let the admin to blacklist the trekkers- after blacklisting he can not login.
@app.route("/admin/trekkers/<int:account_id>/blacklist", methods=["POST"], endpoint="admin.blacklist_trekker")
@roles_only("admin")
def admin_blacklist_trekker(account_id):
    trekker = Account.query.filter_by(id=account_id, role="user").first()
    if trekker:
        trekker.account_status = "blacklisted"
        db.session.commit()
    flash("Trekker blacklisted.", "warning")
    return redirect(url_for("admin.list_trekkers"))


#trekkers can be blacklisted and reinstated by the admin.
@app.route("/admin/trekkers/<int:account_id>/reinstate", methods=["POST"], endpoint="admin.reinstate_trekker")
@roles_only("admin")
def admin_reinstate_trekker(account_id):
    trekker = Account.query.filter_by(id=account_id, role="user").first()
    if trekker:
        trekker.account_status = "active"
        db.session.commit()
    flash("Trekker reinstated.", "success")
    return redirect(url_for("admin.list_trekkers"))


#admin can seee all the reservations made by trekkers and details of trek and trekkers.
@app.route("/admin/reservations", endpoint="admin.all_reservations")
@roles_only("admin")
def admin_all_reservations():
    rows = Reservation.query.order_by(Reservation.reserved_on.desc()).all()

    for r in rows:
        r.trekker_name = r.account.full_name
        r.expedition_title = r.expedition.title

    return render_template("admin/reservations.html", reservations=rows)








# Staff/guide's routes

#guide can see the assigned treks and the number of trekkers signed-up for each treks
@app.route("/staff/my-expeditions", endpoint="staff.my_expeditions")
@roles_only("staff")
def staff_my_expeditions():
    guide_id = session["account_id"]
    rows = Expedition.query.filter_by(guide_id=guide_id).order_by(Expedition.added_on.desc()).all()

    signup_counts = {}
    for exp in rows:
        signup_counts[exp.id] = Reservation.query.filter_by(
            expedition_id=exp.id, reservation_state="Booked"
        ).count()

    return render_template("staff/dashboard.html", expeditions=rows, signup_counts=signup_counts)


#guide can see its own assigned trek only(security purpose))
@app.route("/staff/expedition/<int:expedition_id>", methods=["GET", "POST"], endpoint="staff.expedition_detail")
@roles_only("staff")
def staff_expedition_detail(expedition_id):
    expedition = Expedition.query.get(expedition_id)

    if expedition is None or expedition.guide_id != session["account_id"]:
        flash("This expedition isn't assigned to you.", "danger")
        return redirect(url_for("staff.my_expeditions"))

    if request.method == "POST":
        action = request.form.get("action")
        

        #guide can open/close/complete the trek and also update the number of seats left.
        if action == "update_seats":
            new_seats_left = int(request.form["seats_left"])
            new_seats_left = max(0, min(new_seats_left, expedition.capacity))
            expedition.seats_left = new_seats_left
            db.session.commit()
            flash("Seat count updated.", "success")

        elif action == "change_state":
            new_state = request.form["current_state"]
            if new_state in ("Open", "Closed", "Completed"):
                expedition.current_state = new_state
                if new_state == "Completed":
                    Reservation.query.filter_by(
                        expedition_id=expedition_id, reservation_state="Booked"
                    ).update({"reservation_state": "Completed"})
                db.session.commit()
                flash(f"Expedition marked {new_state}.", "success")

        return redirect(url_for("staff.expedition_detail", expedition_id=expedition_id))
    
    #guide can see the details of trekkers signed up for the trek with their details.
    signed_up = Reservation.query.filter_by(expedition_id=expedition_id).order_by(
        Reservation.reserved_on.desc()
    ).all()

    for p in signed_up:
        p.full_name = p.account.full_name
        p.email = p.account.email
        p.phone_no = p.account.phone_no

    return render_template("staff/expedition_detail.html", expedition=expedition, signed_up=signed_up)


# guide can update his details.
@app.route("/staff/profile", methods=["GET", "POST"], endpoint="staff.profile")
@roles_only("staff")
def staff_profile():
    account = Account.query.get(session["account_id"])

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        phone_no = request.form.get("phone_no", "").strip()
        new_password = request.form.get("password", "").strip()

        account.full_name = full_name
        account.phone_no = phone_no
        if new_password:
            account.password_hash = generate_password_hash(new_password)
        db.session.commit()

        session["full_name"] = full_name
        flash("Profile saved.", "success")
        return redirect(url_for("staff.profile"))

    return render_template("staff/profile.html", account=account)










# Trekker routes

#trekkers home page that shows no of open expeditions, no of active reservations aand the latest 5 reservations made by the trekkers.
@app.route("/trekker/overview", endpoint="trekker.overview")
@roles_only("user")
def trekker_overview():
    account_id = session["account_id"]

    open_count = Expedition.query.filter_by(current_state="Open").count()
    active_reservations = Reservation.query.filter_by(
        account_id=account_id, reservation_state="Booked"
    ).count()

    latest = Reservation.query.filter_by(account_id=account_id).order_by(
        Reservation.reserved_on.desc()
    ).limit(5).all()

    for r in latest:
        r.expedition_title = r.expedition.title
        r.current_state = r.expedition.current_state

    return render_template(
        "trekker/dashboard.html", open_count=open_count,
        active_reservations=active_reservations, latest=latest,
    )


#trekkers can browse the open expenditions with search and filter function.
@app.route("/trekker/browse", endpoint="trekker.browse")
@roles_only("user")
def trekker_browse():
    search_term = request.args.get("q", "").strip()
    difficulty_level = request.args.get("difficulty_level", "").strip()
    region_filter = request.args.get("region", "").strip()

    query = Expedition.query.filter(Expedition.current_state.in_(["Approved", "Open"]))
    

    #search box, difficulty, region filter for trekkers.
    if search_term:
        like = f"%{search_term}%"
        query = query.filter(db.or_(Expedition.title.like(like), Expedition.region.like(like)))
    if difficulty_level:
        query = query.filter(Expedition.difficulty_level == difficulty_level)
    if region_filter:
        query = query.filter(Expedition.region.like(f"%{region_filter}%"))

    expeditions = query.order_by(Expedition.added_on.desc()).all()
    for exp in expeditions:
        exp.guide_name = exp.guide.full_name if exp.guide else None

    already_booked = {
        row.expedition_id
        for row in Reservation.query.filter_by(
            account_id=session["account_id"], reservation_state="Booked"
        ).all()
    }

    return render_template(
        "trekker/browse.html", expeditions=expeditions, q=search_term,
        difficulty_level=difficulty_level, region=region_filter, already_booked=already_booked,
    )


#3 checks before boooking thr trek- 1 if the trek is open, 2 if the trek has seats left, 3 if the trek is already booked by same trekker.
@app.route("/trekker/book/<int:expedition_id>", methods=["POST"], endpoint="trekker.book")
@roles_only("user")
def trekker_book(expedition_id):
    expedition = Expedition.query.get(expedition_id)

    if expedition is None:
        flash("That expedition no longer exists.", "danger")
        return redirect(url_for("trekker.browse"))

    if expedition.current_state != "Open":
        flash("This expedition isn't open for booking right now.", "warning")
        return redirect(url_for("trekker.browse"))

    if expedition.seats_left <= 0:
        flash("No seats left on this expedition.", "warning")
        return redirect(url_for("trekker.browse"))

    account_id = session["account_id"]
    dupe = Reservation.query.filter_by(
        account_id=account_id, expedition_id=expedition_id, reservation_state="Booked"
    ).first()
    if dupe:
        flash("You've already booked this expedition.", "info")
        return redirect(url_for("trekker.browse"))

    new_reservation = Reservation(account_id=account_id, expedition_id=expedition_id, reservation_state="Booked")
    db.session.add(new_reservation)
    expedition.seats_left -= 1
    db.session.commit()

    flash("Seat booked!", "success")
    return redirect(url_for("trekker.my_reservations"))

#to see all trekker (his) booking.
@app.route("/trekker/my-reservations", endpoint="trekker.my_reservations")
@roles_only("user")
def trekker_my_reservations():
    rows = Reservation.query.filter_by(account_id=session["account_id"]).order_by(
        Reservation.reserved_on.desc()
    ).all()

    for r in rows:
        r.expedition_title = r.expedition.title
        r.region = r.expedition.region
        r.begin_date = r.expedition.begin_date
        r.finish_date = r.expedition.finish_date
        r.current_state = r.expedition.current_state

    return render_template("trekker/reservations.html", reservations=rows)


#trekker can cancel the booking if the trek is still open and if trekker has booked the trek.
@app.route("/trekker/cancel/<int:reservation_id>", methods=["POST"], endpoint="trekker.cancel")
@roles_only("user")
def trekker_cancel(reservation_id):
    reservation = Reservation.query.filter_by(id=reservation_id, account_id=session["account_id"]).first()

    if reservation is None:
        flash("Reservation not found.", "danger")
        return redirect(url_for("trekker.my_reservations"))

    if reservation.reservation_state != "Booked":
        flash("Only an active reservation can be cancelled.", "warning")
        return redirect(url_for("trekker.my_reservations"))

    reservation.reservation_state = "Cancelled"
    reservation.expedition.seats_left += 1
    db.session.commit()

    flash("Reservation cancelled.", "info")
    return redirect(url_for("trekker.my_reservations"))

#trekker can edit his profile and chng the password
@app.route("/trekker/profile", methods=["GET", "POST"], endpoint="trekker.profile")
@roles_only("user")
def trekker_profile():
    account = Account.query.get(session["account_id"])

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        phone_no = request.form.get("phone_no", "").strip()
        new_password = request.form.get("password", "").strip()

        account.full_name = full_name
        account.phone_no = phone_no
        if new_password:
            account.password_hash = generate_password_hash(new_password)
        db.session.commit()

        session["full_name"] = full_name
        flash("Profile saved.", "success")
        return redirect(url_for("trekker.profile"))

    return render_template("trekker/profile.html", account=account)

#to start the flask app in debug mode.
if __name__ == "__main__":
    app.run(debug=True)
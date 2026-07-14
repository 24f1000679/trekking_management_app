from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash

db = SQLAlchemy()


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    phone_no = db.Column(db.String(20))
    account_status = db.Column(db.String(20), nullable=False, default="active")
    joined_on = db.Column(db.DateTime, default=datetime.utcnow)

    guided_expeditions = db.relationship("Expedition", backref="guide", lazy=True)
    reservations = db.relationship("Reservation", backref="account", lazy=True)


class Expedition(db.Model):
    __tablename__ = "expeditions"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    region = db.Column(db.String(100), nullable=False)
    difficulty_level = db.Column(db.String(20), nullable=False)
    num_days = db.Column(db.Integer, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    seats_left = db.Column(db.Integer, nullable=False)
    guide_id = db.Column(db.Integer, db.ForeignKey("accounts.id"))
    current_state = db.Column(db.String(20), nullable=False, default="Pending")
    begin_date = db.Column(db.String(20))
    finish_date = db.Column(db.String(20))
    notes = db.Column(db.Text)
    added_on = db.Column(db.DateTime, default=datetime.utcnow)

    reservations = db.relationship("Reservation", backref="expedition", lazy=True)


class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    expedition_id = db.Column(db.Integer, db.ForeignKey("expeditions.id"), nullable=False)
    reserved_on = db.Column(db.DateTime, default=datetime.utcnow)
    reservation_state = db.Column(db.String(20), nullable=False, default="Booked")


#seeds admin into database if not already present.
def seed_admin():
    if Account.query.filter_by(role="admin").first() is None:
        admin = Account(
            full_name="System Admin",
            email="admin@trek.com",
            password_hash=generate_password_hash("admin123"),
            role="admin",
            phone_no="9999999999",
            account_status="active",
        )
        db.session.add(admin)
        db.session.commit()
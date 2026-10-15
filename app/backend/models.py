from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)   # user-vabb
    station_code = db.Column(db.String(10), nullable=False)           # vabb
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")                   # user/admin/super_admin
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to activity logs
    activities = db.relationship("UserActivity", backref="user", lazy=True, cascade="all, delete-orphan")


class UserActivity(db.Model):
    __tablename__ = "user_activities"
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)  # login/logout/access
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    page_or_route = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)  # supports IPv6
    tab_id = db.Column(db.String(100), nullable=True)
    details = db.Column(db.Text, nullable=True)  # for additional info (JSON)
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username,
            "activity_type": self.activity_type,
            "timestamp": self.timestamp.isoformat(),
            "page_or_route": self.page_or_route,
            "ip_address": self.ip_address,
            "tab_id": self.tab_id,
            "details": self.details
        }


class VerificationParameter(db.Model):
    __tablename__ = "verification_parameters"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    param_key = db.Column(db.String(50), nullable=False)
    param_value = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=True)
    description = db.Column(db.String(200), nullable=True)
    is_enabled = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("category", "param_key", name="uq_verification_parameters_category_key"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "param_key": self.param_key,
            "param_value": self.param_value,
            "unit": self.unit,
            "description": self.description,
            "is_enabled": self.is_enabled,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

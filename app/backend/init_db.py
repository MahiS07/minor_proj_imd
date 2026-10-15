from app.backend import create_app
from app.backend.models import db
from app.backend.auth import create_super_admin

app = create_app()

with app.app_context():
    db.create_all()
    create_super_admin()
    print("✅ Database initialized")


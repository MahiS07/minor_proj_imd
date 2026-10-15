# app/backend/__init__.py

from flask import Flask
import os
from flask_cors import CORS
from .config import Config, initialize_data_directories
from .models import db
from .auth import create_super_admin, seed_verification_parameters
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')

_initialized = False

def create_app():
    global _initialized

    app = Flask(__name__,template_folder=FRONTEND_DIR,
        static_folder=FRONTEND_DIR, 
        static_url_path='/')
    
    app.config.from_object(Config)

    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PREFERRED_URL_SCHEME"] = "http"

    CORS(app, supports_credentials=True)
    db.init_app(app)
    
    from .routes.api import api_bp
    from .routes.web import web
    from .auth import auth_bp

    app.register_blueprint(api_bp, url_prefix='/api')
    # app.register_blueprint(web, url_prefix='/web')
    app.register_blueprint(web)
    app.register_blueprint(auth_bp, url_prefix='/auth')

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    # Ensure persistent data directories and database tables exist
    if not _initialized:
        try:
            with app.app_context():

                from .models import User,UserActivity
                print("[INIT] Initializing data directories...")
                initialize_data_directories()
                db.create_all()
                create_super_admin()
                seed_verification_parameters()
                print("[INIT] ✓ Initialization complete")
                _initialized = True
        except Exception as e:
            print(f"[ERROR] Startup initialization error: {e}")
            import traceback
            traceback.print_exc()

    return app



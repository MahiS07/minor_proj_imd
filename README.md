# METAR GUI v2 - Forecast Dashboard

A comprehensive Flask-based web application for analyzing Meteorological Aerodrome Report (METAR) data and comparing meteorological observations with forecast data. Designed for the India Meteorological Department (IMD) to verify forecast accuracy across weather stations.

## Overview

Forecast dashboard fetches real-time meteorological observations from the OGIMET service, compares them with forecast data, and generates verification reports for forecast accuracy analysis. The system includes role-based authentication and support for managing multiple airports.

### Key Features
- **Dual Data Processing**: Combines actual observations (METAR) with forecast data
- **Accuracy Verification**: Automatically calculates verification metrics for forecasts
- **Multi-Airport Support**: Manage and process data for multiple ICAO airport codes
- **Role-Based Access Control**: User, Admin, and Super Admin roles with granular permissions
- **CSV Report Generation**: Automated export of verification results
- **Upper Air Data**: Integrates upper-level atmospheric observations
- **Responsive Dashboard**: Interactive web interface with Plotly charts
- **Secure Authentication**: JWT-based token authentication with 8-hour expiry

---

## Architecture

### Component Structure
```
forecast_dashboard/
├── app/
│   ├── backend/                    # Flask API & Processing Logic
│   │   ├── app.py                 # Flask app factory & initialization
│   │   ├── auth.py                # JWT-based authentication
│   │   ├── models.py              # SQLAlchemy database models
│   │   ├── config.py              # Configuration & data directory paths
│   │   ├── requirements.txt        # Python dependencies
│   │   ├── routes/
│   │   │   ├── api.py             # Core API endpoints (/api/*)
│   │   │   └── web.py             # Web routes & page serving
│   │   └── utils/                 # Data processing utilities
│   │       ├── metar.py           # METAR parsing & CSV decoding
│   │       ├── ogimet.py          # OGIMET API calls
│   │       ├── validation.py      # ICAO code & date validation
│   │       ├── extract_metar_features.py  # Feature extraction
│   │       ├── fetch_metar.py     # METAR fetching utilities
│   │       ├── generate_warning_report.py # Report generation
│   │       └── upper_data_fetch.py        # Upper air data processing
│   │
│   ├── frontend/                   # Web Interface
│   │   ├── index.html             # Main dashboard
│   │   ├── login.html             # Login page
│   │   ├── signup.html            # Registration page
│   │   ├── admin.html             # Admin panel
│   │   ├── superadmin.html        # Super admin panel
│   │   ├── js/                    # JavaScript application logic
│   │   │   ├── app.js             # Core UI logic
│   │   │   ├── auth.js            # Authentication handling
│   │   │   └── admin.js           # Admin-specific scripts
│   │   └── css/                   # Tailwind CSS styling
│   │
│   └── data/                       # Data Storage
│       ├── metar_data/            # METAR observations
│       ├── upper_air_data/        # Upper air sounding data
│       └── ad_warn_data/          # Aerodrome warning workspace
│
├── instance/                       # Database & Instance Data
│   └── auth_test.db               # SQLite database
│
├── nginx/                          # Reverse Proxy Configuration
│   └── default.conf               # Nginx routing rules
│
├── docker-compose.yml             # Docker orchestration
├── Dockerfile                     # Container image definition
└── .env                          # Environment configuration
```

### Data Flow Pipeline

```
User Upload (METAR + Forecast Files)
        ↓
Parse & Validate Timestamps
        ↓
Extract METAR Features (wind, temp, pressure)
        ↓
Compare with Forecast Data
        ↓
Calculate Accuracy Metrics
        ↓
Generate CSV Report & visual graphs
        ↓
User Download
```

---

## Getting Started

### Prerequisites
- Python 3.8+
- pip or conda (package manager)
- Git (optional, for version control)

### Local Installation

1. **Clone or navigate to project directory**
   ```bash
   cd forecast_dashboard
   ```

2. **Create a virtual environment** (recommended)
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r app/backend/requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   # Create .env file in project root (see .env.example)
   # Required variables:
   JWT_SECRET=your-secret-key-here
   SUPER_ADMIN_PASSWORD=your-secure-password
   FLASK_ENV=development
   ```

5. **Initialize the database**
   ```bash
   python app/backend/init_db.py
   ```

6. **Run the Flask Development Server**
   ```bash
   python app/backend/app.py
   ```
   The application will start at `http://localhost:5000`

### Docker Deployment

For production or containerized environments:

```bash
# Build and run with Docker Compose
docker-compose up -d --build

# Access the application
# Frontend: http://localhost:8080
# Backend API: http://localhost:5000 (internal)
```

The Docker setup includes:
- Flask backend with Gunicorn WSGI server
- Nginx reverse proxy for static file serving
- Persistent SQLite database volume

---

## Usage

### Authentication Flow

1. **Sign Up**: Create a new user account
   - Username must be uppercase
   - Associate with airport ICAO code
   - Assigned default "user" role

2. **Login**: Authenticate and receive JWT token
   - Token stored in secure HTTP-only cookie
   - Expires after 8 hours
   - Automatic validation on API requests

3. **Role Hierarchy**:
   - **User (Level 1)**: Dashboard access, process own data
   - **Admin (Level 2)**: Manage users
   - **Super Admin (Level 3)**: Full system access, database management

### Processing METAR Data

1. **Navigate to Dashboard** (authenticated users)
2. **Upload Files**:
   - METAR observations file (text format)
   - Forecast/Warning file (PDF or text)
3. **Select Airport**: Choose ICAO code (e.g., VABB, VAJJ)
4. **Submit for Processing**
5. **Download Results**: CSV report with accuracy metrics

### Key Endpoints

**Authentication APIs:**
- `POST /auth/signup` - Register new user
- `POST /auth/login` - Authenticate and receive token
- `GET /auth/me` - Get current user info

**Processing APIs:**
- `POST /api/process_metar` - Process METAR + forecast files
- `GET /api/get_metar` - Fetch METAR data from OGIMET service
- `GET /api/download/<file_token>` - Download processed results

**Admin APIs:**
- `GET /api/users` - List all users (admin only)
- `POST /api/users` - Create new user (admin only)

---

## Core Processing Details

### METAR Data Pipeline

The system extracts the following features from METAR data:
- **Wind Direction** (degrees)
- **Wind Speed** (knots)
- **Temperature** (°C)
- **Dew Point** (°C)
- **QNH / Pressure** (hPa)
- **Visibility** (meters)
- **Cloud Coverage** (feet AGL)

**Processing Steps:**
1. Decode ICAO METAR strings using the `metar` library
2. Parse 12-digit timestamps (YYYYMMDDHHMM format)
3. Extract numerical values from observation codes
4. Validate timestamps fall within warning validity period
5. Match with forecast data by timestamp proximity
6. Calculate accuracy metrics (MAE, RMSE, etc.)

### External Data Sources

**OGIMET Service** (`https://www.ogimet.com/`)
- Fetches actual meteorological observations
- Parameters: ICAO code, date range
- Timeout: 60 seconds
- Fallback: Returns empty file on network failure

---

## Technologies & Dependencies

### Backend Stack
- **Flask 3.1.0** - Web micro-framework
- **SQLAlchemy** - ORM for database models
- **JWT (PyJWT)** - Token-based authentication
- **Pandas** - Data manipulation & CSV processing
- **Requests** - HTTP client for external APIs
- **metar 1.11.0** - METAR parsing library
- **PyPDF2** - PDF document handling

### Frontend Stack
- **HTML5** - Semantic markup
- **Vanilla JavaScript** - No framework dependencies
- **Tailwind CSS** - Utility-first styling
- **Plotly.js** - Interactive charts & graphs

### DevOps
- **Docker** - Container runtime
- **Docker Compose** - Multi-container orchestration
- **Nginx** - Reverse proxy & load balancer
- **Gunicorn** - Python WSGI application server

---

## Project Structure Details

### Data Directories

All paths are configured through `config.py` for cross-platform compatibility:

| Directory | Purpose |
|-----------|---------|
| `app/data/metar_data/` | Uploaded METAR observations (uploads/downloads subdirs) |
| `app/data/upper_air_data/` | Upper air sounding & atmospheric profile data |
| `app/data/ad_warn_data/` | Aerodrome warning processing workspace |
| `instance/` | Application instance data (database, logs) |

### Configuration

**Environment Detection** (Automatic):
- Docker mode: Uses `/docker_volume_mount_point`
- Local development: Uses relative paths
- Configurable via `config.py`

---

## Troubleshooting

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| **METAR parsing fails** | Invalid ICAO code or format | Check console logs; validate METAR string format |
| **File not found after processing** | Files stored in unexpected subdirs | Check `downloads/` or `uploads/` subdirectories |
| **Date range validation error** | METAR timestamps outside warning validity | Ensure observation times fall within warning period |
| **OGIMET timeout** | Slow network or large date range requested | Reduce date range; increase timeout in `ogimet.py` |
| **JWT token expired** | User session over 8 hours | Require re-login; tokens auto-refresh on valid requests |
| **Database locked error** | SQLite access conflict | Ensure single Flask instance; use Gunicorn in production |

### Debug Logging

Enable detailed logging:
```python
# In app.py
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check console output for:
- METAR decode errors (printed but not raised)
- OGIMET API response codes
- File path resolution issues
- Timestamp parsing failures

---

## Security Considerations

### Authentication
- JWT tokens stored in HTTP-only secure cookies
- Passwords hashed with Werkzeug security utilities
- Role-based access control enforced at route level

### File Handling
- File downloads use base64-encoded tokens (prevent directory traversal)
- All file paths validated through `config.py` constants
- UTF-8 encoding enforced for METAR text files

### Environment Variables
- Never commit `.env` to version control
- Use `.env.example` as template
- Change `JWT_SECRET` and `SUPER_ADMIN_PASSWORD` in production

---

## Development Workflow

### Adding New Features

1. **Backend Logic**:
   - Add processing functions to `utils/` modules
   - Create API endpoints in `routes/api.py`
   - Add database models to `models.py` if needed

2. **Frontend**:
   - Update HTML templates in `frontend/`
   - Add JavaScript handlers to `js/app.js`
   - Style with Tailwind CSS classes

3. **Testing**:
   - Test API endpoints with curl or Postman
   - Check browser console for JavaScript errors
   - Verify file outputs in data directories

### Code Conventions

- **Python**: Flask conventions, snake_case naming, type hints encouraged
- **JavaScript**: Vanilla JS, camelCase naming, event-driven patterns
- **File Paths**: Always use `config.py` constants, never hardcode paths
- **Error Handling**: Log to console, return sensible defaults on failure
- **Encoding**: Always UTF-8 for text files

---

## Support & Contributing

### Known Limitations
- OGIMET timeout: 60 seconds (large date ranges may fail)
- SQLite not recommended for concurrent heavy load (use PostgreSQL for scaling)
- METAR parsing depends on external `metar` library (non-standard formats may fail)

### Future Enhancements
- Real-time data streaming integration
- Machine learning-based forecast accuracy prediction
- Advanced visualization dashboard with D3.js
- RESTful API documentation (Swagger/OpenAPI)
- PostgreSQL database support for scaling

---

## License & Attribution

**IMD Forecast Dashboard** - India Meteorological Department

For questions or issues, contact the development team.

---

**Last Updated**: March 2026 

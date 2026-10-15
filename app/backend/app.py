import sys
from pathlib import Path

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import sys
from pathlib import Path

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.backend import create_app

app = create_app()


if __name__ == '__main__':
    # Listen on all interfaces (0.0.0.0) and port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)



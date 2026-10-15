import requests
from datetime import datetime, timedelta
import re
import sys
import os
import json
import time
from pathlib import Path
from app.backend.config import AD_WARN_DIR, METAR_DATA_DIR
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class RobustMetarFetcher:
    """
    Robust METAR fetcher with retry logic, caching, and error handling.
    Addresses OGIMET website issues with multiple fallback strategies.
    """
    
    OGIMET_URLS = [
        "https://www.ogimet.com/display_metars2.php",
        "http://www.ogimet.com/cgi-bin/getmetar",
    ]
    
    CACHE_DIR = os.path.join(METAR_DATA_DIR, ".cache")
    CACHE_TTL = 3600  # 1 hour cache
    
    def __init__(self, timeout=120, retries=3):
        """Initialize fetcher with retry configuration."""
        self.timeout = timeout
        self.retries = retries
        self.session = self._create_session()
        self._ensure_cache_dir()
    
    def _create_session(self):
        """Create requests session with retry strategy."""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=self.retries,
            backoff_factor=1,  # 1, 2, 4 seconds between retries
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set reasonable headers to avoid blocking
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        
        return session
    
    def _ensure_cache_dir(self):
        """Create cache directory if it doesn't exist."""
        os.makedirs(self.CACHE_DIR, exist_ok=True)
    
    def _get_cache_key(self, icao, start_dt, end_dt):
        """Generate cache key for METAR query."""
        key = f"{icao}_{start_dt.strftime('%Y%m%d%H%M')}_{end_dt.strftime('%Y%m%d%H%M')}"
        return key
    
    def _get_cached_metar(self, icao, start_dt, end_dt):
        """Retrieve cached METAR data if available and not expired."""
        cache_key = self._get_cache_key(icao, start_dt, end_dt)
        cache_file = os.path.join(self.CACHE_DIR, f"{cache_key}.json")
        
        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < self.CACHE_TTL:
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    print(f"[✓] Using cached METAR data for {icao}")
                    return cache_data
                except (json.JSONDecodeError, IOError) as e:
                    print(f"[!] Cache read error: {e}")
        
        return None
    
    def _save_cache(self, icao, start_dt, end_dt, data):
        """Save METAR data to cache."""
        cache_key = self._get_cache_key(icao, start_dt, end_dt)
        cache_file = os.path.join(self.CACHE_DIR, f"{cache_key}.json")
        
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({'data': data, 'timestamp': time.time()}, f)
        except IOError as e:
            print(f"[!] Cache write error: {e}")
    
    def fetch_metar_with_retry(self, icao, start_dt, end_dt):
        """
        Fetch METAR data with retry logic and timeout handling.
        
        Args:
            icao: Airport ICAO code
            start_dt: Start datetime
            end_dt: End datetime
            
        Returns:
            List of METAR lines or empty list if failed
        """
        
        # Try cache first
        cached_data = self._get_cached_metar(icao, start_dt, end_dt)
        if cached_data:
            return cached_data.get('metar_lines', [])
        
        # Build request parameters
        params = {
            'lang': 'en',
            'lugar': icao,
            'tipo': 'ALL',
            'ord': 'DIR',
            'nil': 'NO',
            'fmt': 'txt',
            'ano': start_dt.year,
            'mes': f'{start_dt.month:02}',
            'day': f'{start_dt.day:02}',
            'hora': f'{start_dt.hour:02}',
            'anof': end_dt.year,
            'mesf': f'{end_dt.month:02}',
            'dayf': f'{end_dt.day:02}',
            'horaf': f'{end_dt.hour:02}',
            'min': '00',
            'minf': '59'
        }
        
        metar_lines = []
        
        # Try each OGIMET URL
        for attempt, url in enumerate(self.OGIMET_URLS):
            try:
                print(f"[→] Attempt {attempt + 1}/{len(self.OGIMET_URLS)}: Fetching from {url}")
                
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout
                )
                
                response.raise_for_status()
                
                lines = response.text.strip().split("\n")
                
                # Process lines
                for line in lines:
                    line = line.strip()
                    # Exclude comment lines
                    if line.startswith('#'):
                        continue
                    # Keep METAR lines
                    if line and ('METAR' in line or line.startswith(icao)):
                        metar_lines.append(line)
                
                if metar_lines:
                    print(f"[✓] Successfully fetched {len(metar_lines)} METAR lines for {icao}")
                    self._save_cache(icao, start_dt, end_dt, {'metar_lines': metar_lines})
                    return metar_lines
                else:
                    print(f"[!] No METAR data found for {icao} in response")
                    
            except requests.Timeout:
                print(f"[✘] Timeout on attempt {attempt + 1}: Request exceeded {self.timeout}s")
                
            except requests.ConnectionError as e:
                print(f"[✘] Connection error on attempt {attempt + 1}: {str(e)}")
                
            except requests.HTTPError as e:
                print(f"[✘] HTTP error on attempt {attempt + 1}: {e.response.status_code} - {e}")
                
            except Exception as e:
                print(f"[✘] Unexpected error on attempt {attempt + 1}: {str(e)}")
                
            # Wait before retry (exponential backoff)
            if attempt < len(self.OGIMET_URLS) - 1:
                wait_time = 2 ** attempt
                print(f"[⏱] Waiting {wait_time}s before next attempt...")
                time.sleep(wait_time)
        
        print(f"[✘] Failed to fetch METAR data for {icao} after all attempts")
        return metar_lines

def fetch_all_metar(icao, start_dt, end_dt, output_file="metar.txt"):
    """
    Main function to fetch and save METAR data.
    
    Args:
        icao: Airport ICAO code
        start_dt: Start datetime
        end_dt: End datetime
        output_file: Output file path
    """
    
    # Ensure output directory exists
    os.makedirs(METAR_DATA_DIR, exist_ok=True)
    
    # If output_file doesn't have a path, save in METAR_DATA_DIR
    if not os.path.dirname(output_file):
        output_file = os.path.join(METAR_DATA_DIR, output_file)
    
    # Create fetcher with environment-based configuration
    timeout = int(os.getenv('OGIMET_TIMEOUT', '120'))
    retries = int(os.getenv('OGIMET_RETRIES', '3'))
    
    fetcher = RobustMetarFetcher(timeout=timeout, retries=retries)
    
    print(f"[+] Fetching METAR for {icao} from {start_dt} to {end_dt}")
    
    metar_lines = fetcher.fetch_metar_with_retry(icao, start_dt, end_dt)
    
    # Save to file
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            for line in metar_lines:
                f.write(line + "\n")
        
        print(f"[✓] Saved {len(metar_lines)} METAR lines to {output_file}")
        return output_file
        
    except IOError as e:
        print(f"[✘] Failed to write output file: {e}")
        return None

if __name__ == "__main__":
    icao = sys.argv[1] if len(sys.argv) > 1 else "VABB"
    start_dt = sys.argv[2] if len(sys.argv) > 2 else (datetime.now() - timedelta(days=1))
    end_dt = sys.argv[3] if len(sys.argv) > 3 else datetime.now()
    
    if isinstance(start_dt, str):
        start_dt = datetime.strptime(start_dt, "%Y%m%d%H%M")
    if isinstance(end_dt, str):
        end_dt = datetime.strptime(end_dt, "%Y%m%d%H%M")
    
    fetch_all_metar(icao, start_dt, end_dt)

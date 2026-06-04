# broker/web_token.py â€” Web-based Fyers token generator (auto OAuth capture)
import os
import sys
import webbrowser
import threading
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

load_dotenv()

ENV_PATH   = os.path.join(os.path.dirname(__file__), '..', '.env')
CLIENT_ID  = os.getenv("CLIENT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")
PORT       = 8085
REDIRECT_URI = f"http://127.0.0.1:{PORT}"

_server_done  = threading.Event()
_auth_code    = None


def _atomic_update_access_token(env_path: str, full_token: str):
    """Atomically replace ACCESS_TOKEN in .env to avoid partial writes."""
    env_content = ""
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            env_content = f.read()

    lines = [line for line in env_content.splitlines() if not line.startswith('ACCESS_TOKEN=')]
    lines.append(f"ACCESS_TOKEN={full_token}")
    payload = '\n'.join(lines) + '\n'

    env_dir = os.path.dirname(os.path.abspath(env_path))
    os.makedirs(env_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.env.', suffix='.tmp', dir=env_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmpf:
            tmpf.write(payload)
            tmpf.flush()
            os.fsync(tmpf.fileno())
        os.replace(tmp_path, env_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "auth_code" in params:
            _auth_code = params["auth_code"][0]
            self._send_success()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            _server_done.set()
        else:
            self._send_waiting()

    def _send_success(self):
        html = (
            "<html><head><style>"
            "body{font-family:Segoe UI,sans-serif;background:#0d1117;color:#e6edf3;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}"
            ".box{text-align:center;padding:40px;background:#161b22;border-radius:12px;"
            "border:1px solid #30363d;}"
            "h2{color:#00B050;font-size:28px;margin-bottom:10px;}"
            "p{color:#8b949e;font-size:15px;}"
            "</style></head><body>"
            "<div class='box'>"
            "<h2>Token Captured!</h2>"
            "<p>Auth code received. Generating access token...</p>"
            "<p style='margin-top:20px;color:#58a6ff;'>You can close this tab.</p>"
            "</div></body></html>"
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def _send_waiting(self):
        html = (
            "<html><head><style>"
            "body{font-family:Segoe UI,sans-serif;background:#0d1117;color:#e6edf3;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}"
            ".box{text-align:center;padding:40px;background:#161b22;border-radius:12px;"
            "border:1px solid #30363d;}"
            "h2{color:#58a6ff;}p{color:#8b949e;}"
            "</style></head><body>"
            "<div class='box'>"
            "<h2>CB6 QUANTUM - Waiting for Fyers login...</h2>"
            "<p>Complete the login in the Fyers window.</p>"
            "</div></body></html>"
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    print("=" * 52)
    print("      CB6 QUANTUM - FYERS WEB TOKEN GENERATOR")
    print("=" * 52)

    if not CLIENT_ID or not SECRET_KEY:
        print("ERROR: CLIENT_ID / SECRET_KEY missing in .env")
        sys.exit(1)

    # NOTE: REDIRECT_URI used here is http://127.0.0.1:8085
    # Add this URI once in your Fyers API app settings at
    # myapi.fyers.in > App Details > Redirect URL
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )

    login_url = session.generate_authcode()

    server = HTTPServer(("127.0.0.1", PORT), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"\nLocal callback server started on port {PORT}")
    print(f"Opening Fyers login in your browser...\n")

    webbrowser.open(login_url)

    print("Waiting for Fyers to redirect back... (login in the browser)")
    _server_done.wait(timeout=120)

    if not _auth_code:
        print("\nERROR: Timed out waiting for auth code (120s). Try again.")
        sys.exit(1)

    print("Auth code captured. Generating access token...")
    session.set_token(_auth_code)
    response = session.generate_token()

    if response.get("code") == 200:
        access_token = response["access_token"]
        full_token   = f"{CLIENT_ID}:{access_token}"
        try:
            _atomic_update_access_token(ENV_PATH, full_token)
            print("\nToken saved to .env successfully!")
            print(f"Token preview: {full_token[:40]}...")
            print("\nRun the bot:  python main.py")
        except Exception as e:
            print(f"\nERROR: Failed to save token: {e}")
            sys.exit(1)
    else:
        print(f"\nERROR: Token generation failed: {response}")
        sys.exit(1)


if __name__ == "__main__":
    main()


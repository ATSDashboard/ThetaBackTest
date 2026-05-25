"""
Daily Kite Connect login flow.

Run once a day (typically before 9:00 AM IST). It:
  1. Loads API key + secret from ~/.config/kite_credentials.json
  2. Opens the Kite login URL in your default browser
  3. Waits for you to paste back the redirected URL (or just the request_token)
  4. Exchanges that for an access_token via API
  5. Saves access_token to ~/.config/kite_session.json (mode 600)

After this runs successfully, lib/kite_live.py functions work for the rest of
the trading day (token expires 6 AM next day).

⚠ Note about your existing Google-Sheets app: Kite Connect typically allows
only ONE active access_token per app at a time. Generating a new session here
may invalidate the Google Sheets app's token; you'd then need to re-login that
one too. If that's a problem, create a SECOND Kite Connect app for this Python
adapter (separate API key + secret + ₹2K/mo each — Zerodha charges per app).
"""
import json
import sys
import webbrowser
from pathlib import Path

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("ERROR: pip install kiteconnect"); sys.exit(1)

CRED = Path.home() / ".config" / "kite_credentials.json"
SESS = Path.home() / ".config" / "kite_session.json"

if not CRED.exists():
    print(f"Missing {CRED}"); sys.exit(1)

creds = json.loads(CRED.read_text())
kite = KiteConnect(api_key=creds["api_key"])

login_url = kite.login_url()
print(f"\n→ Opening login URL in browser:\n  {login_url}\n")
print("After Zerodha login, your browser will redirect to a URL like:")
print("  http://127.0.0.1:5000/callback?action=login&type=login&status=success&request_token=XXXXXXXXX&...")
print("\nCopy & paste the FULL redirect URL (or just the request_token value):")
try:
    webbrowser.open(login_url)
except Exception:
    pass

inp = input("\nPaste here: ").strip()
if "request_token=" in inp:
    request_token = inp.split("request_token=", 1)[1].split("&")[0]
elif "&" not in inp and len(inp) >= 16:
    request_token = inp
else:
    print(f"Couldn't parse request_token from: {inp[:80]}..."); sys.exit(1)

print(f"\n→ Exchanging request_token for access_token...")
try:
    sess = kite.generate_session(request_token, api_secret=creds["api_secret"])
except Exception as e:
    print(f"FAILED: {e}"); sys.exit(1)

out = {
    "access_token": sess["access_token"],
    "user_id": sess["user_id"],
    "user_name": sess.get("user_name", ""),
    "login_at": sess.get("login_time", "").isoformat() if hasattr(sess.get("login_time", ""), "isoformat") else str(sess.get("login_time", "")),
}
SESS.write_text(json.dumps(out, indent=2))
SESS.chmod(0o600)
print(f"\n✓ Session saved: user_id={out['user_id']} ({out['user_name']})")
print(f"  Token valid until ~6 AM tomorrow.")
print(f"  File: {SESS}")

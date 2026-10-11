"""Run locally once. Requires client_secret.json from Google Cloud Console."""
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN = Path("token.json")
CLIENT = Path("client_secret.json")

def main():
    if not CLIENT.exists():
        raise SystemExit("Missing client_secret.json. Download OAuth Desktop client JSON from Google Cloud Console.")
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print("Saved token.json. Keep it private; encode it into GitHub Secret YOUTUBE_TOKEN_JSON_B64.")

if __name__ == "__main__":
    main()

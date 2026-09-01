from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "../credentials.json",
    SCOPES,
)

credentials = flow.run_local_server(port=0)

print("REFRESH TOKEN:")
print(credentials.refresh_token)

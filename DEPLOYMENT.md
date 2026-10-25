# Deploying Chronos

Deploy this repository with `app.py` as the Streamlit entry point. Install dependencies from `requirements.txt`.

In Streamlit Community Cloud, open the app settings and add this TOP LEVEL entry under Secrets:

```toml
TIMESCALE_SERVICE_URL = "YOUR_FULL_POSTGRES_CONNECTION_URL"
```

Use the same TigerData service as local development to access existing database accounts. Keep the TLS parameters from your service URL. Never commit the actual value, `.env.local`, `.streamlit/secrets.toml`, private keys, or `users_db.json`.

Other hosts can set TIMESCALE_SERVICE_URL as an environment variable. DATABASE_URL is accepted as an alternative. Restart/redeploy after changing secrets. The database account must be allowed to create the app's missing tables and read/write its account/profile tables. Creating an account uses hashed passwords; usernames have 3–40 letters/numbers/dots/underscores/hyphens, passwords have 8–128 characters.

Test by signing up a new account, signing out, then signing in again. Restart the app and confirm the account still works. Accounts that exist only in a local users_db.json need password-verified migration while running locally against the same database; do not publish that file.

If login fails, use the displayed safe error and hosting logs to distinguish missing configuration, network failure, database permissions, and invalid credentials. Never paste connection strings into support messages.

This configuration covers account storage. Remote Presage camera processing needs its own hosted service and browser-camera setup.

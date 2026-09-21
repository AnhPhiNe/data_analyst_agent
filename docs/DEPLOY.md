# Deploying the demo on Streamlit Community Cloud

This app runs on [Streamlit Community Cloud](https://share.streamlit.io) for free. The repository is
prepared for it: `requirements.txt` installs the package with pinned versions, and the app reads its
API key from Streamlit **Secrets** as well as from a local `.env`.

## Before you deploy — the one real caveat

The app calls the Gemini API for every analysis. On a public deployment, **anyone who visits spends
your key's quota** (the free tier allows about 500 requests per day). A crawler or a burst of
visitors can exhaust it, and then the demo is broken for the next person who opens it.

To reduce the risk:

- Use a **separate, throwaway API key** for the demo, not your main key.
- On the demo, encourage visitors to use the sample data, not real data — uploaded data's field
  metadata and verified values still reach the model provider (Spec Section 14.5).
- In your CV, label it honestly, e.g. *"Live demo (may be rate-limited)"*, and keep a short demo
  video as the reliable fallback.

## Steps

1. Push the repository to GitHub (already done: `AnhPhiNe/data_analyst_agent`).
2. Sign in to <https://share.streamlit.io> with GitHub.
3. **New app** → pick the repo `data_analyst_agent`, branch `main`, main file `streamlit_app.py`.
4. Open **Advanced settings** and choose **Python 3.12** (the project requires it).
5. In **Secrets**, paste:

   ```toml
   GOOGLE_API_KEY = "your-throwaway-gemini-key"
   ```

   Optionally also set the model or a comma-separated list of keys that rotate:

   ```toml
   GOOGLE_API_KEY = "key-one,key-two"
   TABULAR_AGENT_MODEL = "gemini-3.5-flash-lite"
   ```

6. **Deploy.** The first build installs the pinned dependencies and can take a few minutes.

The key never goes in the repository — it lives only in Streamlit's Secrets store, and the app
copies it into its environment at startup.

## Notes and limits

- **Ephemeral storage.** Streamlit Cloud's filesystem resets when the app sleeps or reboots, so
  saved sessions do not persist between restarts. That is fine for a demo.
- **Memory.** The free tier is limited (about 1 GB). Keep demo files small; very large uploads may
  exceed it.
- **First load** after the app sleeps takes a few seconds to wake.

## After it is live

Add the URL to the README's Screenshots section and to your CV. Keep the demo video as the version
that always works, in case the live demo is rate-limited when someone opens it.

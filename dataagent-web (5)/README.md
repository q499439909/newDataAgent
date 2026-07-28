<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# DataAgent Web

This is the React web cockpit for the DataAgent Python control plane.

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Start the Python DataAgent API, usually on `http://127.0.0.1:8000`
3. Optionally set `DATAAGENT_API_BASE` in `.env.local` if the backend uses another URL
4. Run the app:
   `npm run dev`

The web server proxies `/api/*` to the Python backend, so the browser stays same-origin.

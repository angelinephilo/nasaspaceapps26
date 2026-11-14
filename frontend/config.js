// Edit these before running. This file is public (served to browsers), so restrict
// the Maps key to your site's address in Google Cloud > APIs & Services > Credentials.
window.APP_CONFIG = {
  GOOGLE_MAPS_API_KEY: "AIzaSyDsM0ItSPV7m6j6bu-TMs7WEpgQWDRLwnY",
  // Map ID from Google Cloud > Google Maps Platform > Map management. "DEMO_MAP_ID" works for testing.
  MAP_ID: "DEMO_MAP_ID",
  // Leave empty when the API is on the same site (local uvicorn, or Firebase Hosting rewrite).
  // Otherwise e.g. "https://agriclimate-api-xxxxx.a.run.app"
  API_BASE: "",
  // Shown when the browser can't give the farmer's location.
  FALLBACK_CENTER: { lat: 1.3521, lng: 103.8198 },
  FALLBACK_ZOOM: 6,
};
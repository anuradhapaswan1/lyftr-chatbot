#!/bin/sh
# Writes the backend URL into config.js at container start, so the image is configured by env only.
set -e
echo "window.__CONFIG__ = { API_URL: \"${API_URL:-http://localhost:8000}\" };" > /usr/share/nginx/html/config.js

#!/usr/bin/env bash
# Deploy NornPulse to Cloud Run.
#
# Prerequisites (already done once for project norn-labs):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#       artifactregistry.googleapis.com secretmanager.googleapis.com
#   and the three secrets below, created from .env.
#
# Secrets are mounted from Secret Manager rather than passed as env vars, so
# their values never appear in the service config, the deploy command, or
# shell history, and rotating a key doesn't require a redeploy.
#
# NORNPULSE_DEMO_MODE=1 is not optional on a public URL. The submission
# requires --allow-unauthenticated, and without demo mode an anonymous
# visitor can run the SQL console (user SQL, write access enabled,
# remoteSecure available) and spend unmetered Gemini/Lyria/Imagen credit
# from the generate buttons. Demo mode leaves every page, chart and
# grounded decision fully live and stands down only the actions that
# write, spend, or publish.
#
# Region is europe-west1, not europe-west2 where ClickHouse lives, because
# Cloud Run refuses to create domain mappings in europe-west2:
#   "Creating domain mappings is not allowed in europe-west2" (501)
# Belgium is ~10ms from London, which is negligible against the ~0.35s a
# ClickHouse query costs through the persistent MCP session, and it is the
# nearest region that can host nornpulse.nornlabs.ai without a load
# balancer. Check with:
#   gcloud beta run domain-mappings list --region=REGION
#
# Usage:  ./deploy_cloud_run.sh
set -euo pipefail

PROJECT=norn-labs
REGION=europe-west1
SERVICE=nornpulse

# concurrency is deliberately low and memory high: a render shells out to
# ffmpeg, which is CPU- and memory-hungry, and several concurrent renders in
# one instance would contend. timeout is the 60-minute maximum because a
# batch generation legitimately runs for many minutes.
gcloud run deploy "$SERVICE" \
  --source . \
  --project="$PROJECT" \
  --region="$REGION" \
  --allow-unauthenticated \
  --memory=4Gi \
  --cpu=2 \
  --timeout=3600 \
  --concurrency=4 \
  --max-instances=3 \
  --set-env-vars="CLICKHOUSE_HOST=sd8qeu9ilt.europe-west2.gcp.clickhouse.cloud" \
  --set-env-vars="CLICKHOUSE_USER=default" \
  --set-env-vars="CLICKHOUSE_SECURE=true" \
  --set-env-vars="CLICKHOUSE_DATABASE=default" \
  --set-env-vars="CLICKHOUSE_MCP_QUERY_TIMEOUT=180" \
  --set-env-vars="NORNPULSE_DEMO_MODE=1" \
  --set-env-vars="CAPTION_FONT=Roboto Black" \
  --set-env-vars="GMAIL_USER=franeppotrc@gmail.com" \
  --set-env-vars="NOTIFY_EMAIL=franeppotrc@gmail.com" \
  --set-secrets="GEMINI_API_KEY=nornpulse-gemini-api-key:latest" \
  --set-secrets="CLICKHOUSE_PASSWORD=nornpulse-clickhouse-password:latest" \
  --set-secrets="GMAIL_APP_PASSWORD=nornpulse-gmail-app-password:latest"

echo
echo "Service URL:"
gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)'

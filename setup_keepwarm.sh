#!/usr/bin/env bash
# Keep nornpulse.ai's Cloud Run instance warm without paying for
# --min-instances=1 (~$150/month at this service's 2 vCPU / 4Gi, running
# 24/7 whether or not anyone visits). A Cloud Scheduler job hitting the
# live URL periodically costs a handful of HTTP requests -- comfortably
# inside Cloud Scheduler's free tier (3 jobs/month free) -- and only
# "spends" anything when it actually prevents a real cold start.
#
# Every 5 minutes, not every 1: DEMO_SCRIPT.md's "hit it once a minute"
# was a short pre-recording burst for one afternoon, not a standing
# schedule. A permanent job at 1/minute is 1,440 requests/day forever for
# no measured benefit over 5 -- Cloud Run's scale-to-zero window is
# comfortably longer than 5 minutes of inactivity in practice. Tighten
# SCHEDULE below if cold starts are still observed at this interval.
#
# Idempotent: safe to re-run after a redeploy changes the service URL.
#
# Usage: ./setup_keepwarm.sh

set -euo pipefail

PROJECT=norn-labs
REGION=europe-west1
SERVICE=nornpulse
JOB=nornpulse-keepwarm
SCHEDULE="*/5 * * * *"

URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)')"
if [ -z "$URL" ]; then
  echo "❌ Could not resolve the ${SERVICE} service URL — is it deployed?" >&2
  exit 1
fi

echo "📡 Pinging ${URL}/ every 5 minutes to keep one instance warm."

if gcloud scheduler jobs describe "$JOB" --project="$PROJECT" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" \
    --project="$PROJECT" --location="$REGION" \
    --schedule="$SCHEDULE" --uri="$URL/" --http-method=GET \
    --attempt-deadline=30s
  echo "✅ Updated existing keep-warm job."
else
  gcloud scheduler jobs create http "$JOB" \
    --project="$PROJECT" --location="$REGION" \
    --schedule="$SCHEDULE" --uri="$URL/" --http-method=GET \
    --attempt-deadline=30s \
    --description="Keeps nornpulse.ai's Cloud Run instance warm without min-instances."
  echo "✅ Created keep-warm job."
fi

echo
echo "To remove it later:"
echo "  gcloud scheduler jobs delete ${JOB} --project=${PROJECT} --location=${REGION}"

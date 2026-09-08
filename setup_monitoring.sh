#!/usr/bin/env bash
# Alert when the live demo degrades, and keep it warm at the same time.
#
# Two real outages happened in one evening (a ClickHouse client library
# renamed a field under us; the Gemini API key's prepaid credit ran out)
# and both were caught only because someone happened to load the page and
# see the error banner. This closes that gap for the one that's visible on
# page load: a Cloud Monitoring uptime check hits the live URL every 5
# minutes and fails if the response contains "ClickHouse is NOT connected"
# (see app.py's `demo_banner()` / `_urdr_health` block -- that banner is
# deliberately global, shown on every page, for exactly this reason). An
# alerting policy on top emails the owner when it does.
#
# This replaced a separate Cloud Scheduler keep-warm job: an uptime check
# already pings the URL on a schedule from Google's own monitoring infra,
# so running a second, unmonitored ping alongside it was duplicate
# infrastructure doing half the job each. One thing now does both.
#
# NOT covered: the other outage tonight (Gemini/Vertex billing exhaustion)
# has no page-load-time banner -- it only surfaces when a generation
# actually runs, mid-action, with a graceful fallback rather than a global
# banner. A real check for that needs either a dedicated health-check
# surface in the app or a scheduled synthetic generation call; neither
# exists yet. This script only closes the ClickHouse half.
#
# Usage: ./setup_monitoring.sh

set -euo pipefail

PROJECT=norn-labs
REGION=europe-west1
SERVICE=nornpulse
ALERT_EMAIL=franeppotrc@gmail.com
CHANNEL_NAME="NornPulse owner (email)"
CHECK_NAME="NornPulse - ClickHouse health"
POLICY_NAME="NornPulse - ClickHouse degraded or site down"

URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)')"
HOST="$(echo "$URL" | sed -E 's#^https?://##')"
# Prefer the custom domain if it's mapped -- same service, but it's the
# address a real visitor (and a judge) actually uses.
if gcloud beta run domain-mappings describe --domain=nornpulse.nornlabs.ai \
    --project="$PROJECT" --region="$REGION" >/dev/null 2>&1; then
  HOST="nornpulse.nornlabs.ai"
fi
echo "📡 Checking https://${HOST}/ every 5 minutes."

# --- notification channel ---------------------------------------------
CHANNEL_ID="$(gcloud beta monitoring channels list --project="$PROJECT" \
  --filter="displayName=\"${CHANNEL_NAME}\"" --format="value(name)" | head -1)"
if [ -z "$CHANNEL_ID" ]; then
  CHANNEL_ID="$(gcloud beta monitoring channels create --project="$PROJECT" \
    --display-name="$CHANNEL_NAME" \
    --description="Alerts when the live NornPulse demo degrades." \
    --type=email --channel-labels=email_address="$ALERT_EMAIL" \
    --format="value(name)")"
  echo "✅ Created notification channel."
else
  echo "✅ Notification channel already exists."
fi

# --- uptime check --------------------------------------------------------
CHECK_RESOURCE="$(gcloud monitoring uptime list-configs --project="$PROJECT" \
  --filter="displayName=\"${CHECK_NAME}\"" --format="value(name)" | head -1)"
if [ -z "$CHECK_RESOURCE" ]; then
  CHECK_RESOURCE="$(gcloud monitoring uptime create "$CHECK_NAME" \
    --project="$PROJECT" \
    --resource-type=uptime-url \
    --resource-labels=host="${HOST}",project_id="${PROJECT}" \
    --protocol=https --path=/ --port=443 \
    --matcher-content="ClickHouse is NOT connected" \
    --matcher-type=not-contains-string \
    --period=5 --timeout=30 \
    --format="value(name)")"
  echo "✅ Created uptime check."
else
  echo "✅ Uptime check already exists."
fi
CHECK_ID="${CHECK_RESOURCE##*/}"

# --- alerting policy -------------------------------------------------
POLICY_ID="$(gcloud alpha monitoring policies list --project="$PROJECT" \
  --filter="displayName=\"${POLICY_NAME}\"" --format="value(name)" | head -1)"
if [ -z "$POLICY_ID" ]; then
  POLICY_YAML="$(mktemp)"
  trap 'rm -f "$POLICY_YAML"' EXIT
  cat > "$POLICY_YAML" <<EOF
combiner: OR
displayName: "${POLICY_NAME}"
documentation:
  content: >
    The live demo (https://${HOST}/) either failed to respond, or responded
    but the page shows "ClickHouse is NOT connected" -- Urðr has fallen back
    to in-memory benchmarks, so nothing being generated right now is
    grounded in real data. Check Cloud Run logs for the connection error.
  mimeType: text/markdown
conditions:
  - displayName: Uptime check failed
    conditionThreshold:
      filter: >
        resource.type = "uptime_url" AND
        metric.type = "monitoring.googleapis.com/uptime_check/check_passed" AND
        metric.label.check_id = "${CHECK_ID}"
      comparison: COMPARISON_GT
      thresholdValue: 1
      duration: 0s
      trigger:
        count: 1
      aggregations:
        - alignmentPeriod: 300s
          crossSeriesReducer: REDUCE_COUNT_FALSE
          groupByFields:
            - resource.label.host
            - resource.label.project_id
          perSeriesAligner: ALIGN_NEXT_OLDER
notificationChannels:
  - ${CHANNEL_ID}
EOF
  gcloud alpha monitoring policies create --project="$PROJECT" \
    --policy-from-file="$POLICY_YAML" --format="value(name)"
  echo "✅ Created alerting policy."
else
  echo "✅ Alerting policy already exists."
fi

echo
echo "Alerts go to: ${ALERT_EMAIL}"

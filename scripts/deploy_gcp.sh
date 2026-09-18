#!/bin/sh
set -eu

REPOSITORY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPOSITORY_DIR"

if command -v gcloud >/dev/null 2>&1; then
  GCLOUD_COMMAND=$(command -v gcloud)
elif [ -x "$REPOSITORY_DIR/.tools/google-cloud-sdk/bin/gcloud" ]; then
  GCLOUD_COMMAND="$REPOSITORY_DIR/.tools/google-cloud-sdk/bin/gcloud"
  export CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-"$REPOSITORY_DIR/.tools/gcloud-config"}
else
  echo "Google Cloud CLI is not installed." >&2
  exit 1
fi

gcloud() {
  "$GCLOUD_COMMAND" "$@"
}

PROJECT_ID=${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to the Google Cloud project ID}
REGION=${GCP_REGION:-us-west1}
FIRESTORE_LOCATION=${GCP_FIRESTORE_LOCATION:-us-west1}
JOB_NAME=${HOST_OPS_JOB_NAME:-host-ops-cleaner-reminders}
SCHEDULER_NAME=${HOST_OPS_SCHEDULER_NAME:-host-ops-every-six-hours}
REPOSITORY_NAME=${HOST_OPS_ARTIFACT_REPOSITORY:-host-ops}
SERVICE_ACCOUNT_NAME=${HOST_OPS_SERVICE_ACCOUNT:-host-ops-runner}
SERVICE_ACCOUNT="$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY_NAME/worker:latest"

if [ ! -f .env ] || [ ! -f config/property.json ]; then
  echo "Private .env and config/property.json are required." >&2
  exit 1
fi

set -a
. ./.env
set +a

for required_name in AIRBNB_ICAL_URL CLEANER_NAME CLEANER_PHONE_NUMBER \
  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM_NUMBER; do
  eval "required_value=\${$required_name-}"
  if [ -z "$required_value" ]; then
    echo "$required_name is missing from .env" >&2
    exit 1
  fi
done

if [ -z "${CLEANER_RECIPIENTS_JSON-}" ]; then
  CLEANER_RECIPIENTS_JSON=$(python3 -c 'import json, os; print(json.dumps([{"name": os.environ["CLEANER_NAME"], "phone": os.environ["CLEANER_PHONE_NUMBER"], "approved": True}]))')
fi

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  firestore.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com \
  cloudresourcemanager.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY_NAME" \
  --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY_NAME" \
    --repository-format docker --location "$REGION" \
    --description "Host Ops Cloud Run images"
fi

if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --display-name "Host Ops reminder worker"
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/datastore.user >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/secretmanager.secretAccessor >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/run.invoker >/dev/null

if ! gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  gcloud firestore databases create --database='(default)' \
    --location "$FIRESTORE_LOCATION" --type firestore-native
fi

put_secret() {
  secret_name=$1
  secret_value=$2
  if ! gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
    gcloud secrets create "$secret_name" --replication-policy automatic
  else
    current_value=$(gcloud secrets versions access latest \
      --secret "$secret_name" 2>/dev/null || true)
    if [ "$current_value" = "$secret_value" ]; then
      return
    fi
  fi
  printf '%s' "$secret_value" | \
    gcloud secrets versions add "$secret_name" --data-file=- >/dev/null
}

put_secret host-ops-airbnb-ical-url "$AIRBNB_ICAL_URL"
put_secret host-ops-cleaner-name "$CLEANER_NAME"
put_secret host-ops-cleaner-phone "$CLEANER_PHONE_NUMBER"
put_secret host-ops-cleaner-recipients "$CLEANER_RECIPIENTS_JSON"
put_secret host-ops-twilio-auth-token "$TWILIO_AUTH_TOKEN"
if ! gcloud secrets describe host-ops-property-config >/dev/null 2>&1; then
  gcloud secrets create host-ops-property-config --replication-policy automatic
fi
property_config=$(cat config/property.json)
current_property_config=$(gcloud secrets versions access latest \
  --secret host-ops-property-config 2>/dev/null || true)
if [ "$current_property_config" != "$property_config" ]; then
  printf '%s' "$property_config" | gcloud secrets versions add \
    host-ops-property-config --data-file=- >/dev/null
fi

gcloud builds submit --tag "$IMAGE" .
gcloud run jobs deploy "$JOB_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --tasks 1 \
  --max-retries 0 \
  --task-timeout 5m \
  --cpu 1 \
  --memory 512Mi \
  --set-env-vars "TWILIO_ACCOUNT_SID=$TWILIO_ACCOUNT_SID,TWILIO_FROM_NUMBER=$TWILIO_FROM_NUMBER" \
  --set-secrets "/secrets/property.json=host-ops-property-config:latest,AIRBNB_ICAL_URL=host-ops-airbnb-ical-url:latest,CLEANER_NAME=host-ops-cleaner-name:latest,CLEANER_PHONE_NUMBER=host-ops-cleaner-phone:latest,CLEANER_RECIPIENTS_JSON=host-ops-cleaner-recipients:latest,TWILIO_AUTH_TOKEN=host-ops-twilio-auth-token:latest"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
JOB_URI="https://run.googleapis.com/v2/projects/$PROJECT_NUMBER/locations/$REGION/jobs/$JOB_NAME:run"
if gcloud scheduler jobs describe "$SCHEDULER_NAME" \
  --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_NAME" \
    --location "$REGION" \
    --schedule "0 3,9,15,21 * * *" \
    --time-zone "America/Los_Angeles" \
    --uri "$JOB_URI" \
    --http-method POST \
    --oauth-service-account-email "$SERVICE_ACCOUNT"
else
  gcloud scheduler jobs create http "$SCHEDULER_NAME" \
    --location "$REGION" \
    --schedule "0 3,9,15,21 * * *" \
    --time-zone "America/Los_Angeles" \
    --uri "$JOB_URI" \
    --http-method POST \
    --oauth-service-account-email "$SERVICE_ACCOUNT"
fi

echo "Deployed $JOB_NAME with a six-hour schedule."
echo "Run an end-to-end test with:"
echo "gcloud run jobs execute $JOB_NAME --region $REGION --wait"

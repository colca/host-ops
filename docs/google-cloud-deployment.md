# Google Cloud deployment

This deployment removes the requirement for a host Mac to remain online. A
Cloud Scheduler job starts a Cloud Run Job every six hours. The disposable
container polls the private Airbnb iCal feed, stores reservation and reminder
state in Firestore, and submits eligible cleaner messages through Twilio.

## Cost-oriented architecture

- Cloud Run Job: one task, 1 vCPU, 512 MiB, maximum five-minute execution.
- Cloud Scheduler: every six hours at 3:00, 9:00, 15:00, and 21:00 in
  `America/Los_Angeles`, preserving the configured 9:00 AM reminder time.
- Firestore: durable reservation, action, idempotency, and SMS outbox records.
- Secret Manager: private calendar URL, Twilio credentials, cleaner contact,
  and private property configuration.
- Artifact Registry: the worker container.

At four executions per day, normal one-property usage should remain well within
the relevant free quotas. Google Cloud billing must still be enabled, and users
should create a small budget alert before deployment.

## Optional mobile dashboard

A separate read-only Cloud Run service can present sanitized poll summaries,
upcoming cleaning dates, and SMS delivery states in a phone-friendly page. The
rendered page excludes names, phone numbers, message bodies, provider IDs,
credentials, and the private calendar URL. Keep the service behind
Identity-Aware Proxy and grant access only to approved Google accounts.

## Safety properties

- Firestore document IDs enforce stable per-reminder idempotency.
- The SMS outbox changes a message to `sending` before contacting Twilio. A
  process crash therefore favors manual review over an automatic duplicate.
- Cloud Run task retries are disabled. The next scheduled poll reconciles
  calendar state.
- Only the currently configured cleaner phone number may receive queued SMS.
- Cleaner payments and pricing changes remain approval-gated.
- No secret is copied into the image or Git repository.

## Deployment

Install and authenticate the Google Cloud CLI, create a billing-enabled project,
and then run:

```bash
export GCP_PROJECT_ID='your-project-id'
scripts/deploy_gcp.sh
```

The script enables the required APIs, creates a least-purpose worker service
account, creates the default Firestore database when needed, stores the private
values as secrets, builds the image, deploys the job, and creates or updates the
six-hour schedule.

After deployment, execute the job once manually and inspect its logs before
disabling the Mac LaunchAgent:

```bash
gcloud run jobs execute host-ops-cleaner-reminders --region us-west1 --wait
gcloud run jobs executions list --job host-ops-cleaner-reminders --region us-west1
```

To send one deliberate end-to-end test containing the current confirmed
cleaning dates, temporarily override the job arguments. This command requires
the explicit live-delivery flag and does not add a reminder to the automatic
outbox:

```bash
gcloud run jobs execute host-ops-cleaner-reminders --region us-west1 --wait \
  --args=send-schedule-test-sms,--confirm-live-delivery
```

Keep the Mac worker enabled until this test succeeds. Never operate both workers
long-term, even though idempotency protection exists.

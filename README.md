# Host Ops

Host Ops is an approval-aware automation engine for short-term-rental hosts.
It turns reservation, cleaning, guest-message, and local-event events into
auditable proposed actions.

This first milestone is intentionally local and provider-neutral. It does not
scrape Airbnb, send real messages, change prices, or initiate bank transfers.

## Included in the MVP

- Reservation workflows for check-in, post-check-in, checkout, and cleaner SMS
- Approval gates for cleaner payments and price changes
- Confidence-based approval for guest answers
- SQLite event and action log
- Idempotency keys that prevent duplicate actions
- Synthetic demo data and a dependency-free test suite
- Adapter interfaces for messaging, pricing, and payment providers
- Airbnb `.ics` calendar-file ingestion for cleaner scheduling
- Itemized cleaner extras on top of a flat turnover fee
- A safe local message outbox that never sends a real text
- Editable base and add-on cleaner fee schedule
- Last-minute booking protection for cleaner notifications
- Explainable nightly pricing recommendations from market comparables
- Distance-weighted premiums for nearby sports, concerts, and conferences
- Configurable price floors, ceilings, and maximum recommendation changes

## Run locally

Python 3.11 or later is required.

```bash
cd host-ops
PYTHONPATH=src python3 -m host_ops.cli --db var/demo.db demo
PYTHONPATH=src python3 -m host_ops.cli --db var/demo.db actions
```

Import a downloaded Airbnb calendar export:

```bash
PYTHONPATH=src python3 -m host_ops.cli --db var/demo.db import-ical path/to/calendar.ics
```

Poll the private Airbnb calendar without storing its URL in configuration:

```bash
export AIRBNB_ICAL_URL='https://www.airbnb.com/calendar/ical/...'
PYTHONPATH=src python3 -m host_ops.cli --db var/host-ops.db poll-ical
```

The command is one-shot and idempotent, so run it from a local scheduler at the
desired interval. Keep the environment variable in the scheduler's private
secret configuration, never in a tracked script or service file.

Poll and queue due cleaner work orders in the safe, non-delivering outbox:

```bash
scripts/run_host_ops.sh
```

The outbox is written to `var/cleaner-outbox.jsonl`, which is ignored by Git.
Records remain `not_sent`; this command does not contact a cleaner or an SMS
provider. Only eligible `ready` or explicitly `approved` actions can run.
Set `CLEANER_NAME` and `CLEANER_PHONE_NUMBER` (in E.164 format) only in the
ignored `.env`; tracked configuration stores only those variable names.
For multiple consented recipients, set `CLEANER_RECIPIENTS_JSON` to a JSON list
such as `[{"name":"Host","phone":"+15555550100","approved":true}]`.
Every entry must explicitly set `approved` to `true`; the legacy single contact
is used only when this list is absent. Each recipient receives a separate copy
and has independent delivery and idempotency state.

Cleaner reminders are queued 14 days and five days before each confirmed
stay's check-in, and again the day before its checkout cleaning. Every reminder includes all
confirmed checkout/cleaning dates in the next 60 days. Stable per-stay keys
prevent duplicates when polling repeatedly. By default, reminders use 9:00 AM
in the property's IANA timezone.
When a newly detected reservation checks in within five days, the next poll
sends a one-time last-minute reminder immediately instead of waiting for the
normal reminder hour. A booking detected exactly five days before check-in uses
the regular five-day idempotency key so it cannot produce two alerts.

## Optional live SMS delivery

Live delivery is disabled while `cleaner_messaging.provider` is `file_outbox`.
To prepare Twilio later, set the provider to `twilio` only in the private
`config/property.json` and store `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and
`TWILIO_FROM_NUMBER` in `.env`. Submission still requires an explicit command:

```bash
PYTHONPATH=src python3 -m host_ops.cli deliver-outbox --confirm-live-delivery
```

Set `automatic_delivery_enabled` to `true` only in the private property
configuration when the recipient has consented and the provider is ready. The
15-minute polling script then delivers newly due reminders automatically. It
refuses to send any queued record whose recipient differs from the currently
configured cleaner. A provider-accepted message is marked `submitted`; failed
requests return it to `not_sent` without exposing credentials or message
contents.

On macOS, install the private runtime copy and 15-minute LaunchAgent:

```bash
scripts/install_launchd.sh
```

The installer copies the application and private configuration to
`~/Library/Application Support/HostOps`, avoiding macOS background-access
restrictions on Documents. It stores no credentials in the LaunchAgent. Run the
installer again after code, private configuration, or credentials change.

For operation without an always-online Mac, deploy the Firestore-backed Cloud
Run Job on a six-hour schedule. See
[Google Cloud deployment](docs/google-cloud-deployment.md). The cloud worker
uses durable reminder and SMS idempotency state and keeps all private values in
Secret Manager.

Approve an action using the ID shown by `actions`:

```bash
PYTHONPATH=src python3 -m host_ops.cli --db var/demo.db approve ACTION_ID
```

Generate price recommendations from a normalized market/event snapshot:

```bash
PYTHONPATH=src python3 -m host_ops.cli \
  --db var/demo.db recommend-prices config/pricing-snapshot.example.json
```

The regular-night recommendation starts from the median comparable rate.
Nearby events and conferences add a configurable premium that decreases with
distance. Every result records its evidence and remains `pending_approval`;
this command never changes a live listing. See
[pricing recommendations](docs/pricing-recommendations.md).

Run the tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Safety boundary

Payments, refunds, reservation exceptions, safety responses, and rate changes
always require approval. The eventual Zelle integration should prepare a
payment request and open a confirmation flow; it should never store online
banking credentials or submit a transfer without a human confirmation.

Airbnb access must use native Airbnb features or an authorized PMS/channel
manager connection. An unofficial browser bot or scraper is outside the design.

## Configuration

Copy `config/property.example.json` to `config/property.json`. The private file
is ignored by Git. Do not put guest data, door codes, phone numbers, payment
identifiers, access tokens, or real property photos in the public repository.

## Next milestone

1. Add a small web dashboard for the action queue.
2. Install the private iCal poll command in the host's local scheduler.
3. Replace the local cleaner outbox with SMS delivery and acknowledgement tracking.
4. Add labeled cleaner-photo upload and checklist validation.
5. Connect authorized comparable-rate, event, conference, and listing-rate adapters.

See [the one-property deployment profile](docs/one-property-deployment.md) for
the current Airbnb-only design and the remaining private configuration.

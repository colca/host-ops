# One-property deployment profile

This deployment assumes one Airbnb listing, Airbnb-native guest messaging, a
single cleaner, and a host whose personal phone service is AT&T.

## Data path

1. Airbnb scheduled quick replies send standard guest messages.
2. The Airbnb calendar export supplies booked date ranges to Host Ops.
3. Host Ops proposes a cleaner work order for each detected checkout.
4. During development, cleaner texts go to a local outbox and are not sent.
5. In production, a programmable SMS adapter sends the work order and records
   acknowledgement and delivery status.
6. The cleaner reports completion, labeled photos, and optional extra tasks.
7. Host Ops calculates the flat turnover fee plus itemized extras.
8. The host approves the payment request and completes Zelle separately.

## Why guest messages stay in Airbnb

The Airbnb calendar export is suitable for detecting occupied dates, but it is
not a general reservation or messaging API. Native Airbnb scheduled quick
replies are the reliable first implementation for check-in and checkout
messages. The adapter boundary allows an authorized PMS connection later.

## Cleaner compensation

Every work report contains:

- One configured flat turnover fee
- Zero or more itemized extra tasks
- A description and proposed amount for each extra
- Photo-review status

The resulting payment action always requires host approval. An extra task can
never silently change the base fee.

Current private deployment values:

- Flat turnover fee: $160
- Move trash cans out and back in: $10
- Restock supplies: $10
- Change lock battery: $10
- Cleaner language: English
- Notification: 24 hours after booking detection, or immediately when check-in
  occurs before that delayed notification

## SMS boundary

The mobile carrier used by the host does not provide application credentials in
this design. Production texting requires a programmable messaging service with
delivery receipts and inbound webhooks. The current file outbox lets us finish
the workflow and message wording before creating that external account.

## Private calendar polling

`poll-ical` reads the private export URL from the environment variable named in
`config/property.json`, fetches it over HTTPS, and generates an idempotent
cleaner work order for each stay. The URL is never written to the database or
printed in errors. Run the one-shot command from a private cron or launchd
configuration; polling every 15 minutes is a reasonable starting interval.

```bash
AIRBNB_ICAL_URL='private URL from Airbnb' \
  PYTHONPATH=src python3 -m host_ops.cli --db var/host-ops.db poll-ical
```

The work order records the $160 base fee and the available $10 add-ons, but it
does not authorize payment. Any later cleaner payment action remains behind the
host approval gate.

`scripts/run_host_ops.sh` performs one complete safe cycle: poll the calendar,
deduplicate stays, and queue due cleaner work orders in the local file outbox.
Use `scripts/render_launchd_plist.py` to create an ignored 15-minute macOS
LaunchAgent definition. The definition contains repository paths but no private
calendar URL or cleaner contact information.

The cleaner outbox queues a reminder five days before each confirmed stay's
check-in and another reminder the day before its checkout cleaning. Every text
contains all confirmed checkout/cleaning dates in the next 60 days. Stable
per-stay keys prevent duplicates when the scheduler runs every 15 minutes. The
default send time is 9:00 AM in the property timezone.

## Private values still needed

Store these only in `config/property.json`, environment variables, or a secret
manager:

- Airbnb calendar export URL
- Cleaner phone number and preferred name
- Exact base turnover fee
- Agreed fees or approval rules for common extras
- Property timezone, check-in time, and checkout time

## Local-demand pricing

The reusable pricing engine uses the median of comparable nightly accommodation
rates for regular and event nights. Nearby sports, concerts, conferences, and
other demand signals identify dates that need a fresh market sample; event
premiums default to zero and may be configured only from observed
event-versus-regular market evidence. The one-property deployment can configure
nearby venues privately, while the normalized input and calculation remain
generic.

Recommendations never change Airbnb rates directly. Each nightly proposal is
stored as a high-risk `rate_change` action and requires host approval. Live
updates require a separate authorized PMS or channel-manager adapter.

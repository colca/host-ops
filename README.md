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

Approve an action using the ID shown by `actions`:

```bash
PYTHONPATH=src python3 -m host_ops.cli --db var/demo.db approve ACTION_ID
```

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
2. Poll the private Airbnb iCal URL on a schedule.
3. Replace the local cleaner outbox with SMS delivery and acknowledgement tracking.
4. Add labeled cleaner-photo upload and checklist validation.
5. Add event discovery and bounded pricing recommendations.

See [the one-property deployment profile](docs/one-property-deployment.md) for
the current Airbnb-only design and the remaining private configuration.

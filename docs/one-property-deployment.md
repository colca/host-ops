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

## Private values still needed

Store these only in `config/property.json`, environment variables, or a secret
manager:

- Airbnb calendar export URL
- Cleaner phone number and preferred name
- Exact base turnover fee
- Agreed fees or approval rules for common extras
- Property timezone, check-in time, and checkout time

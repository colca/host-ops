# Pricing recommendations

Host Ops can turn normalized market and local-demand data into explainable,
approval-gated nightly rate recommendations. The core is intentionally generic:
it does not depend on one stadium, convention center, booking channel, or data
vendor.

## Recommendation model

For each night:

1. Use the median of valid comparable nightly rates as the regular-night market
   baseline.
2. Use nearby demand signals to choose dates that require fresh market samples.
   Default event premiums are zero; an operator may configure an uplift only
   when it was derived from observed event-versus-regular market data.
3. Weight each contribution by event importance and distance from the listing.
4. Cap the combined event premium.
5. When `as_of_date` places the stay inside the configured last-minute window,
   propose the configured native last-minute discount without reducing the base
   nightly rate.
6. Apply the configured nightly floor, ceiling, and maximum change from the
   listing's current rate.
7. Create a `rate_change` action with the inputs and calculation attached.

All `rate_change` and `discount_change` actions require host approval. Approval changes only the local
action status; a future authorized channel adapter must still present the exact
dates and prices before writing them to a live listing.

## Normalized input

`config/pricing-snapshot.example.json` shows the provider-neutral contract. A
snapshot identifies a listing and contains one or more nights. Each night has:

- the current listing rate;
- a non-empty set of comparable nightly rates;
- zero or more local demand signals;
- an optional `as_of_date` used for deterministic last-minute pricing;
- for each signal, a stable ID, name, category, distance, and importance from
  zero to one.

Real adapters should collect comparables that match the subject property's
location, property type, capacity, bedrooms, amenities, quality, and booking
horizon. Taxes and one-time fees should not be mixed into nightly rates.
Adapters should also deduplicate events that appear in multiple feeds.

## Configuration

The `pricing` section controls:

- minimum and maximum nightly rate;
- minimum number of comparable observations required for a recommendation;
- optional venue-specific minimum rates;
- maximum recommendation change relative to the current rate;
- maximum combined nearby-event premium;
- event influence radius;
- last-minute window and discount percentage;
- premium percentages for sports, concerts, conferences, and other categories.

Host Ops prefers five same-size comparables and accepts three or four with a
`limited` confidence label. If fewer than three same-size homes are available,
it can use at least three one-bedroom comparables only when the snapshot also
provides a market-derived one-to-two-bedroom premium backed by at least three
observations. It refuses to invent that adjustment.

A venue floor is applied only when a normalized demand signal carries the
matching `venue_id`. The market sample is still required and remains attached
to the recommendation for review.

## Provider boundaries

The open-source core accepts normalized snapshots through a JSON file today.
Future adapters may use authorized sources for:

- comparable listing rates and availability;
- venue and ticketed-event calendars;
- convention-center and conference calendars;
- an authorized PMS or channel manager for listing-rate writes.

Credentials belong in environment variables or a secret manager and must never
be stored in Git. Scraping private booking-channel pages is outside this design.

# Task 6 Extension - Frequency-Based Departure Time Booking

## Motivation

The original mock national rail schedule data includes `first_train_time`,
`last_train_time`, and `frequency_min`. Task 6 extends the relational schema
and seed process to preserve these fields in the database.

National rail schedules are stored as frequency-based service patterns instead of fully materialized timetable rows. The booking flow previously risked assuming an arbitrary default departure time. This extension makes bookings explicitly tied to inferred valid departure times.

## Files Modified

- `databases/relational/queries.py`
  - `_generate_departure_times`
  - `query_national_rail_availability`
  - `execute_booking`
  - `execute_cancellation`
- `skeleton/agent.py`
  - `execute_booking` tool description and departure-time parameter forwarding

## Database Objects Involved

- `national_rail_schedules`
- `national_rail_schedule_stops`
- `national_rail_seats`
- `national_rail_bookings`
- `payments`

## Implementation Summary

- Departure times are generated from first train time, last train time, frequency, and origin station offset.
- Departure times are generated from:
  - `first_train_time`
  - `last_train_time`
  - `frequency_min`
  - `travel_time_from_origin_min` at the selected origin station
- A fallback service window of `06:00` to `23:00` is kept only as defensive behavior when the time-window fields or values are unavailable.
- The agent is instructed to obtain valid departure times before booking.
- `execute_booking` rejects missing departure times instead of defaulting to `07:00`.
- Booking validation uses the same generated departure times as the availability query.
- If the user provides a departure time that is not available for that schedule, the booking fails gracefully and no booking or payment is created.
- Seat conflicts are checked using travel date, departure time, seat code, booking status, and overlapping journey segments.
- Booking and payment inserts are committed together in one atomic transaction.

## Testing Evidence

Run direct function checks such as:

```python
query_national_rail_availability("NR01", "NR05", "2026-06-15")
execute_booking("RU01", "NR_SCH01", "NR01", "NR05", "2026-06-15", "06:00", "standard", "any")
execute_cancellation("<returned booking_ref>", "RU01")
```

Expected evidence:

- `departure_times` is non-empty.
- Booking without departure time fails gracefully.
- Booking succeeds only when a departure time is provided.
- Booking and payment are created together.
- Double booking the same seat/time/overlapping segment fails.
- Cancellation updates status and refund amount.

## Section 7 - Task 6 Extension: Frequency-Based Departure Time Booking

### Motivation

The mock national rail data describes services through first train time, last train time, and frequency instead of storing every departure as a separate timetable row. To make the booking flow realistic, the system infers valid departure times and requires bookings to use one of those times.

### Schema Changes

No new timetable table is required. The implementation extends and uses the existing schedule and schedule stop tables:

- `national_rail_schedules`
- `national_rail_schedule_stops`
- `national_rail_bookings`

The `national_rail_schedules` table stores `first_train_time` and `last_train_time` from the mock schedule JSON. Fallback service hours from `06:00` to `23:00` remain only as defensive behavior if those values are unavailable.

### Query and Transaction Changes

`query_national_rail_availability` now returns inferred `departure_times`.

`execute_booking` no longer assumes a default departure time. It requires an explicit departure time and continues to use an atomic transaction for booking and payment creation.

### Testing Evidence

Add screenshots or console output showing:

1. Availability query returns valid departure times.
2. Booking without departure time fails gracefully.
3. Booking with a valid departure time succeeds.
4. Duplicate overlapping seat booking fails.
5. Cancellation calculates refund and updates booking status.

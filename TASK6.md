### Task 6 Extension - Frequency-Based Departure Time Booking

#### Motivation
The original mock national rail schedule data includes `first_train_time`, `last_train_time`, and `frequency_min`. Task 6 extends the relational schema and seed process to preserve these fields in the database.
National rail schedules are stored as frequency-based service patterns instead of fully materialized timetable rows. The booking flow previously risked assuming an arbitrary default departure time. This extension makes bookings explicitly tied to inferred valid departure times.

#### Files Modified
* `databases/relational/queries.py`
    * `_generate_departure_times`
    * `query_national_rail_availability`
    * `execute_booking`
    * `execute_cancellation`
* `skeleton/agent.py`
    * `execute_booking` tool description and departure-time parameter forwarding

#### Database Objects Involved
* `national_rail_schedules`
* `national_rail_schedule_stops`
* `national_rail_seats`
* `national_rail_bookings`
* `payments`

#### Implementation Summary
* Departure times are generated dynamically using:
  * `first_train_time`
  * `last_train_time`
  * `frequency_min`
  * `travel_time_from_origin_min` (to account for origin station offset)
* A fallback service window of 06:00 to 23:00 is kept only as defensive behavior when the time-window fields or values are unavailable.
* The agent is instructed to obtain valid departure times before booking.
* `execute_booking` rejects missing departure times instead of defaulting to 07:00.
* Booking validation uses the same generated departure times as the availability query.
* If the user provides a departure time that is not available for that schedule, the booking fails gracefully and no booking or payment is created.
* Seat conflicts are checked using travel date, departure time, seat code, booking status, and overlapping journey segments.
* Booking and payment inserts are committed together in one atomic transaction.
* Implemented row-level locking (`FOR UPDATE`) and advisory locks (`pg_advisory_xact_lock`) in `execute_booking` to prevent race conditions and ensure strict data consistency during concurrent seat selections.

#### Testing Evidence
Run direct function checks such as:
Expected evidence:
* `departure_times` is non-empty.
* Booking without departure time fails gracefully.
* Booking succeeds only when a departure time is provided.
* Booking and payment are created together.
* Double booking the same seat/time/overlapping segment fails.
* Cancellation updates status and refund amount.
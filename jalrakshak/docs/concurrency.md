# Concurrency: why two people can never hand out the same water twice

## The problem

Applying a plan (`POST /allocate/commit`) does four things: read how much water is available and which
requests are pending, run the scheduler, save the allocations, and lower the reservoir level. If two
operators click **Apply** at the same moment, both could read the same "available" figure and the same
pending requests before either has saved anything. Each would then allocate the same water to the same
requests: a classic **lost update / race condition**, like two processes entering a critical section at once.

## The solution (operating-system idea: a lock around the critical section)

`commit_allocation` in `backend/app/service.py` runs as **one database transaction**:

```sql
BEGIN;
  SELECT available_litres FROM reservoirs WHERE reservoir_id = 1 FOR UPDATE;   -- lock the reservoir row
  SELECT ... FROM water_requests ... WHERE status = 'pending' FOR UPDATE OF r;  -- lock the pending requests
  -- run the C++ scheduler on exactly these values
  INSERT INTO scheduler_runs ...;  INSERT INTO allocations ...;
  UPDATE water_requests SET status = ...;
  UPDATE reservoirs SET available_litres = available_litres - total;
COMMIT;                                                                         -- locks released
```

`SELECT ... FOR UPDATE` is a row lock (a mutex held by the database). A second transaction that wants the
same row **waits** until the first commits. When it continues, it reads the *new* values: the water is lower
and the requests are no longer pending, so it gets a clean "409: no pending requests" instead of a duplicate
plan. If anything fails half way (for example the scheduler crashes), the whole transaction is rolled back
and nothing is saved.

A second safety net is in the schema: `CHECK (available_litres >= 0)` on `reservoirs`. Even with no locks,
the database refuses to store a negative water level.

## The experiment

`backend/tests/concurrency_demo.py` starts **4 clients at exactly the same moment** (a thread barrier),
each trying to apply the Water-aware plan. The reservoir is filled to 200,000 L so water is not the limit;
the 8 sample requests ask for 80,000 L. It is run twice: with the real code, and with the two `FOR UPDATE`
clauses removed.

Run it yourself (needs a **test** database, which it wipes):

    cd backend
    python -m tests.concurrency_demo        # with SCHEDULER_BIN and JAL_TEST_DSN set, see tests/pgtest.py

### Result (PostgreSQL 16, recorded for this report; repeated 4 times, same outcome every time)

```
WITH row locks (the real code)
    committed
    conflict (no pending requests)
    conflict (no pending requests)
    conflict (no pending requests)
    runs stored: 1   litres allocated: 80000.00   (requests total 80,000 L)

WITHOUT row locks (for comparison)
    committed
    committed
    database error 23514
    database error 23514
    runs stored: 2   litres allocated: 160000.00   (requests total 80,000 L)
```

### What it shows

- **With locks:** exactly one plan is saved and exactly the 80,000 L requested is allocated. The other three
  clients waited, saw that nothing was pending any more, and stopped cleanly.
- **Without locks:** two clients saved a plan for the *same* eight requests, so **160,000 L** was recorded
  against requests for 80,000 L: every request was served twice. The other two were stopped only by the
  `CHECK` constraint (error 23514, check violation) because the level would have gone negative.
- The `CHECK` constraint alone is **not enough**: it prevented a negative level, but it could not prevent the
  double allocation while there was still water left. The row lock is what makes the operation correct.

The automated test `test_concurrent_commits_allocate_the_water_exactly_once` in `tests/test_postgres.py`
checks the "with locks" result on every test run.

## OS concepts this maps to

| Operating systems | JalRakshak |
|---|---|
| Shared resource | reservoir water (one row in `reservoirs`) |
| Critical section | read level, run scheduler, write allocations, lower level |
| Mutex / lock | `SELECT ... FOR UPDATE` row lock |
| Atomicity | one transaction: all of it is saved, or none of it |
| Race condition / lost update | the "without locks" run above |
| Blocking wait | the second transaction waits for the first to commit |

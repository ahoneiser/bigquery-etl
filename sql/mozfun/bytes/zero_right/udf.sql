CREATE OR REPLACE FUNCTION bytes.zero_right(b BYTES, length INT64)
RETURNS BYTES AS (
  IF(
    length > LENGTH(b) * 8,
    ERROR("Cannot zero out more bits than are present"),
    b >> length << length
  )
);

-- Tests
SELECT
  assert.equals(b'\xF0', bytes.zero_right(b'\xFF', 4)),
  assert.equals(b'\xFF', bytes.zero_right(b'\xFF', 0)),
  assert.equals(b'\x00', bytes.zero_right(b'\xFF', 8)),

# Tuning used on Bob's machine (27 Nov 2026)

These values live in `software/machine.toml`, which is not in git. They took
throughput from ~450 to ~700-800 pieces/hour (best 5 min: 816/h) with 2-6 %
double drops. The code in this branch makes the `[chute]` and `[c4]` values
live-tunable (read at runtime, no restart).

## General settings (most parts)

```toml
[chute]
settle_ms = 1000                       # default was 2000

[feeder_pulse_perception]
ch1_move_speed_usteps_per_s = 2005
ch2_move_speed_usteps_per_s = 1085
ch3_move_speed_usteps_per_s = 4000
ch2_greedy_enabled = true
ch3_greedy_enabled = true
greedy_pulse_output_deg = 90           # big steps only while far from the precise zone
greedy_pulse_pause_ms = 159
exit_move_margin_deg = 12
drop_zone_persistence_ms = 500
ch2_max_pieces = 4
ch3_max_pieces = 5
ch2_fast_to_exit_standoff_deg = 0
ch3_fast_to_exit_standoff_deg = 0

[classification_channel_rev01]
discharge_speed_usteps_per_s = 6000
rotate_speed_usteps_per_s = 7000

[c4]
overlap_chute = true                   # advance the classified piece while the chute turns
overlap_standoff_deg = 10              # ...to 10 deg short of the fall-off
head_alone_after_s = 6.0               # eject a lone piece after 6 s (was a fixed 12 s)
eject_past_straddler = false
```

Zones (set in the UI, stored in the local state DB): C3 drop zone 241-354 deg
(112 deg wide), C3 precise zone 173-183 deg (10 deg).

## Rolling parts (wheels, round parts)

Wheels roll on the rings, so use gentler settings for such a batch:

```toml
ch3_move_speed_usteps_per_s = 3000
ch3_max_pieces = 4
[c4]
overlap_chute = false
head_alone_after_s = 6.0
eject_past_straddler = false
```

Why: with a short head-alone wait (1 s) or `eject_past_straddler = true`, C4
rotated while C3 was still delivering a piece. That piece landed on a moving
ring, was never photographed and went to misc as a "stray" (up to 36 % of
pieces). With these settings strays went to 0-4 %.

Lowering C4 `rotate_speed_usteps_per_s` to 5500 did not help.

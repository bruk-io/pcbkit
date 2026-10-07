# Routing recipes

Freerouting routes most of a board on its own. These recipes cover what it should not
decide alone: wide power paths, a sensitive net you want short, solid ground under a
switching regulator, pours and stitching. Each is a few lines in your project's
`routing.py`; the hooks and the `api` helpers are listed in
[the project interface](project-interface.md#routingpy).

Coordinates are millimetres from the board's top-left corner, y pointing down, as in
`layout.py`. Net names have no leading `/`, except in `NETCLASSES`. After any change, run
`pcbkit route` (or `pcbkit route --eco golden`, below) and read its DRC summary.

## Wide tracks for power nets

Give power nets their own net class. Each entry is
`(track, clearance, via diameter, via drill, nets)` in millimetres, and the net names
carry KiCad's leading `/`:

```python
NETCLASSES = {
    "Power": (0.8, 0.25, 0.9, 0.5, ["/VIN", "/5V"]),
    "Motor": (1.5, 0.3, 1.0, 0.6, ["/VMOTOR"]),
}
```

Freerouting uses the class's width for every track it lays on those nets. For currents
above an amp or two, size the width with a calculator (IPC-2221 or IPC-2152) for your
copper weight (`[stackup] copper_mm`), or use a pour (below).

## Hand-route a net that must stay short

A gate-drive loop, a crystal or a current-sense pair should be short and direct, not
wherever the autorouter finds room. Route it yourself in `prerouted`; tracks and vias
made with `api.track` and `api.via` are locked, so Freerouting routes around them:

```python
def prerouted(board, api):
    """Keep the gate loop short: driver U1 -> gate resistor R5 -> MOSFET Q1's gate."""
    drive = api.ppos(board, "U1", 5)       # pad centres, in layout mm
    r_in = api.ppos(board, "R5", 1)
    r_out = api.ppos(board, "R5", 2)
    gate = api.ppos(board, "Q1", 1)
    api.track(board, [drive, (r_in[0], drive[1]), r_in], 0.3, "GATE_DRV")
    api.track(board, [r_out, (gate[0], r_out[1]), gate], 0.3, "GATE")
```

A track runs through the listed points, so an L-shape is three points. Keep the parts
close in `layout.py` first: a pre-route is only as good as the placement under it. To
change layer, end the track on a via (`api.via(board, x, y, "GATE")`) and continue with
`layer=pcbnew.B_Cu` (`import pcbnew` inside the hook).

## Keep solid ground under a switching regulator

A switching regulator's switch node and its input loop want an unbroken ground plane
underneath. A rule area on the bottom layer that bans tracks, but still lets the ground
pour and stitching vias in, keeps signals from cutting it:

```python
def keepouts(board, api):
    """No signal tracks on the bottom under the regulator (U3, L1 and their capacitors)."""
    api.keepout(board, 40.0, 18.0, 52.0, 30.0,
                tracks=True, vias=False, pours=False, layers=("B.Cu",))
```

`tracks`, `vias` and `pours` say what the area bans. The same call with the defaults
(everything banned, both layers) keeps a region empty: under an antenna, for example.

Add a denser stitching grid over the same box in `pcbkit.toml`, so the ground on both
layers is tied together closely there:

```toml
[stitch]
dense = [[40.0, 18.0, 52.0, 30.0, 1.5]]   # x0, y0, x1, y1, pitch in mm
```

## Ground and power pours

Pours are made in the `zones` hook. Ground goes on both layers (stitching needs both);
a power pour sits on top of it with a higher priority, and `full=True` connects its pads
solid instead of through thermal spokes:

```python
import layout


def zones(board, api):
    """Ground on both layers, and a solid VIN pour around the input connector."""
    import pcbnew

    outline = api.rect(0.3, 0.3, layout.W - 0.3, layout.H - 0.3)
    api.zone(board, "GND", pcbnew.B_Cu, outline)
    api.zone(board, "GND", pcbnew.F_Cu, outline)
    api.zone(board, "VIN", pcbnew.F_Cu, api.rect(2.0, 2.0, 14.0, 9.0),
             priority=3, full=True)


solid_pad_refs = {"J1"}   # J1's ground pads join the ground pour solid
```

A pour has to reach at least one pad of its own net; one that does not is an island of
copper, and DRC reports it as `isolated_copper`. Draw its rectangle over the pads it
feeds.

`solid_pad_refs` names parts whose ground pads should have no thermal spokes: a
high-current connector, a regulator's ground pad. Leave it out for parts you will hand
solder, where spokes help.

## Stitching

With a ground pour on each layer, pcbkit adds stitching vias by itself after the pours
are filled. Three settings in `pcbkit.toml` control it:

```toml
[stitch]
pitch_mm = 5.0                            # grid of stitching vias
dense = [[40.0, 18.0, 52.0, 30.0, 1.5]]   # finer grids in boxes
gap_limit_mm = 3.4                        # no point further than this from a ground via
```

pcbkit fills gaps until no spot with ground on both layers is further than
`gap_limit_mm` from a ground via or a plated ground pad. A twentieth of a wavelength at
your fastest edge or radio frequency is a common choice. The ground net must be named
`GND`.

## A small change: re-route only what moved

Once a route is in `golden/`, a small change does not need a whole new route:

```sh
pcbkit build                  # after editing design.py or layout.py
pcbkit route --eco golden     # keep golden's copper, route only what changed
pcbkit promote                # when DRC is clean
pcbkit finalize
```

Copper that clashes with the change is dropped, and copper within
`route.eco_unlock_reach_mm` of a changed part stays free for Freerouting to rework. If
the change is large (a block moved across the board), route from scratch with
`pcbkit route`: every full route is a new layout to review.

## When a net will not route

`pcbkit route` prints each try's problems by category. If `unconnected_items` stays
above zero:

- Look at the board in KiCad (`kicad/<stem>.kicad_pcb`) and find the net: usually a pin
  boxed in by other parts or by a keep-out.
- Move parts apart in `layout.py`, or turn a part so its pins face their nets.
- Pre-route the stubborn net yourself in `prerouted`.
- More passes (`route.freerouting_passes`) help a little; more tries
  (`route.tries`) rarely do, because Freerouting tends to give the same result for the
  same input.

If Freerouting stops before it starts routing, see
[Troubleshooting](troubleshooting.md).

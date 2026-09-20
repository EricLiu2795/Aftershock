# Simulated drone verification imagery

## Real satellite reference images for W9–W20

`w9_post_event_satellite.jpg` through `w20_post_event_satellite.jpg` are **not**
AI renders or drone photos. They are unmodified, georeferenced OpenAerialMap XYZ
tiles containing the corresponding canonical OSM building centroid and nearby
buildings. W9 and W10 share a tile because they are adjacent.

- Provider/credit: Planet Labs PBC; Source Cooperative; HOTOSM/OpenAerialMap.
- Acquisition: 2026-06-27 11:26:21 UTC, post-earthquake SkySat scene.
- License: [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — attribution and noncommercial use required.
- Source: [HOT Caraballeda dataset](https://huggingface.co/datasets/hotosm/venezuela_eq_2026/blob/d21e93ae667f639db83eba71366b4ba02169ac41/caraballeda/README.md).
- Per-file original URLs and XYZ coordinates: `../../verification_assets.json`.

These images provide historical visual context only. Current scan findings and
occupant counts in the demo are simulated. Positive simulated scans reveal the
image with a satellite credit; negative scans keep the existing green/no-photo behavior.

## Original W1–W8 simulated imagery

These files are AI-generated simulated drone verification renders. They are
simulated renders tied to real OSM sites for demo visualization. They are not
historical photographs, not real post-earthquake images, and not simulator
ground truth.

Operational interpretation still comes from StructuredObservation / IncidentFusion.

| Worksite | Event | Asset ID | File |
| --- | --- | --- | --- |
| W1 Residencias Caribe A | tracking-demo scan only | `va-w1-drone-oblique` | `w1_residencias_caribe_a_oblique.jpg` |
| W2 Residencias Caribe B | E05 | `va-w2-drone-oblique` | `w2_drone_oblique.png` |
| W3 Costa Brava | tracking-demo scan only | `va-w3-drone-oblique` | `w3_costa_brava_oblique.jpg` |
| W4 Parque Caraballeda | tracking-demo scan only | `va-w4-drone-oblique` | `w4_parque_caraballeda_oblique.jpg` |
| W5 La Gabarra | tracking-demo scan only | `va-w5-drone-oblique` | `w5_la_gabarra_oblique.jpg` |
| W7 Colegio Tanaguarenas | E12 | `va-w7-drone-topdown` | `w7_drone_topdown.png` |
| W8 Rita Luna Palaca | tracking-demo scan only | `va-w8-drone-oblique` | `w8_rita_luna_palaca_oblique.jpg` |

The dashboard shows a building's drone image only after a scan finds trapped
people (the building is marked red). Green (clear) buildings never display imagery.

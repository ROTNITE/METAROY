# City 2.3 road visual repair (candidate)

The 27 November revision disabled rigid ModularRoadSectionV1 overlays. Those
must not be re-enabled over the graded, physically destructible surface.
This change restores ordinary street centre lines, avenue edge paint and a
bevelled, visual-only curb on trimmed sidewalks. The original asphalt
support, road graph, cavity geometry, hydrology and mission collision rules
are not replaced. Sidewalks and building paths are clipped against the
actual oriented road rectangles and existing junction/base patches, not an
axis-aligned approximation or a centre-point check. The decoration uses
one procedural mesh per edge, follows the terrain, and is cut/restored by
FMRUrbanTerrain's existing overlay mechanism. Access drives remain unmarked.
Legacy city formats and disaster_revision=0 retain their original rendering.

Portable check (no UE headers):

~~~sh
c++ -std=c++17 -O2 -Wall -Wextra -pedantic -Iunreal/MetaRescue/Source/MetaRescue tools/street_geometry_probe.cpp -o /tmp/street_geometry_probe
/tmp/street_geometry_probe
~~~

Native acceptance still required: build UE 5.8.4; capture the same city at
model time 0 from overview and street level; verify crossing/curb continuity,
centre paint and material appearance; execute the existing road damage,
flood, mud and reset probes; verify no change in route support, visibility
and collision. Portable geometry tests do not certify rendered output.

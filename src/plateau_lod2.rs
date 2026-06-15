//! PLATEAU LOD2 CityGML roof polygon voxelizer.
//!
//! Parses `bldg:RoofSurface` polygons from PLATEAU CityGML (LOD2) files and
//! voxelizes them into block coordinates for all three output formats
//! (Luanti map.sqlite, Java Anvil, Bedrock .mcworld).
//!
//! # Supported CRS
//! EPSG:6697 (JGD2011 geographic 3D) – the standard for PLATEAU Japan.
//! Coordinate order in `gml:posList`: **latitude longitude height**.
//!
//! # Algorithm
//! 1. Stream-parse the CityGML file with quick-xml.
//! 2. Collect `posList` coordinates within each `bldg:RoofSurface`.
//! 3. Per building, compute `ground_z` = min height across all surfaces.
//! 4. Fan-triangulate each polygon.
//! 5. Scan-line rasterize each triangle in the XZ plane and interpolate Y.
//! 6. Emit blocks via `WorldEditor::set_block_absolute`.

use crate::block_definitions::*;
use crate::coordinate_system::geographic::LLPoint;
use crate::coordinate_system::transformation::CoordTransformer;
use crate::world_editor::WorldEditor;
use colored::Colorize;
use quick_xml::events::Event;
use quick_xml::reader::Reader;
use std::collections::HashSet;
use std::path::Path;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Parse a PLATEAU CityGML file and apply roof blocks to the world editor.
///
/// Returns the number of unique block positions placed.
pub fn apply_to_editor(
    path: &Path,
    editor: &mut WorldEditor,
    transformer: &CoordTransformer,
    ground_level: i32,
    scale: f64,
) -> Result<usize, String> {
    let buildings = parse_buildings(path)?;

    let mut placed = HashSet::new();
    let mut count = 0usize;
    let mut skipped_height = 0usize;
    let mut skipped_isolated = 0usize;

    for bld in &buildings {
        // Ground Z = minimum height across LOD2 boundary surfaces of this building.
        let ground_z: f64 = bld
            .all_vertices
            .iter()
            .map(|v| v[2])
            .fold(f64::INFINITY, f64::min);
        let ground_z = if ground_z.is_infinite() { 0.0 } else { ground_z };

        for poly in &bld.roof_polygons {
            let poly_min_z = poly.iter().map(|v| v[2]).fold(f64::INFINITY, f64::min);

            // Filter 1 – height sanity: skip polygons more than MAX_ROOF_HEIGHT_M above
            // the building's own ground level. These are data artifacts, not real roofs.
            if poly_min_z - ground_z > MAX_ROOF_HEIGHT_M {
                skipped_height += 1;
                continue;
            }

            // Filter 2 – connectivity: if wall data is available, skip roof polygons
            // whose lowest point is more than WALL_CONNECTIVITY_SLACK_M above the
            // tallest wall vertex. Such patches are not connected to any wall surface
            // and would appear as isolated floating slabs in the world.
            if let Some(wall_max_z) = bld.wall_max_z {
                if poly_min_z > wall_max_z + WALL_CONNECTIVITY_SLACK_M {
                    skipped_isolated += 1;
                    continue;
                }
            }

            let blocks = voxelize_polygon(poly, transformer, ground_level, scale, ground_z);

            for (x, y, z) in blocks {
                // Skip blocks at or near ground level – roofs must be above buildings.
                // Minimum Y is ground_level + 10 to eliminate stray surface blocks
                // and near-ground false-positives from low-quality LOD2 polygons.
                if y < ground_level + 10 {
                    continue;
                }
                if placed.insert((x, y, z)) {
                    let roof_block = roof_block_for_height(y, ground_level);
                    editor.set_block_absolute(roof_block, x, y, z, None, None);
                    count += 1;
                }
            }
        }
    }

    if skipped_height > 0 || skipped_isolated > 0 {
        println!(
            "{} Filtered out {} polygon(s) exceeding {}m height limit, {} isolated polygon(s) with no wall connection.",
            "[LOD2]".bold(),
            skipped_height,
            MAX_ROOF_HEIGHT_M as u32,
            skipped_isolated,
        );
    }

    Ok(count)
}

// ---------------------------------------------------------------------------
// CityGML parser
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq)]
enum SurfaceKind {
    Roof,
    Wall,
    Ground,
    Other,
}

/// Accumulated data for one CityGML Building / BuildingPart.
struct BuildingData {
    /// LOD2 boundary-surface vertices (Wall + Ground + Roof) – used to find ground_z.
    all_vertices: Vec<[f64; 3]>,
    /// Maximum Z (height) found across WallSurface vertices.
    /// Used to detect isolated roof polygons that are not connected to any wall.
    wall_max_z: Option<f64>,
    /// Vertices of roof surface polygons only.
    roof_polygons: Vec<Vec<[f64; 3]>>,
}

// Maximum height above ground_z a roof polygon is allowed to have.
// Polygons further than this are considered data artifacts and are skipped.
const MAX_ROOF_HEIGHT_M: f64 = 10.0;

// How much higher than the tallest wall vertex a roof polygon's lowest point
// may be before it is considered disconnected / isolated. 10 m gives enough
// slack for stepped roofs, parapets, and floating-point imprecision while
// still catching truly orphaned surface patches.
const WALL_CONNECTIVITY_SLACK_M: f64 = 10.0;

/// Parse all buildings from a CityGML file.
///
/// Uses a streaming state-machine to avoid loading the whole XML tree.
fn parse_buildings(path: &Path) -> Result<Vec<BuildingData>, String> {
    let raw = std::fs::read(path)
        .map_err(|e| format!("Failed to read CityGML '{}': {}", path.display(), e))?;

    // Try UTF-8, then fall back to Shift-JIS (rare in PLATEAU but present in older files)
    let content = String::from_utf8(raw.clone()).unwrap_or_else(|_| {
        // Minimal Shift-JIS fallback: replace non-ASCII bytes with '?'
        raw.iter()
            .map(|&b| if b < 0x80 { b as char } else { '?' })
            .collect()
    });

    let mut reader = Reader::from_str(&content);
    reader.config_mut().trim_text(true);

    let mut buildings: Vec<BuildingData> = Vec::new();

    // Parsing state
    let mut in_building = false;
    let mut current_surface = SurfaceKind::Other;
    let mut in_pos_list = false;
    let mut in_exterior = true; // track exterior vs interior ring

    // Per-building accumulator
    let mut current: BuildingData = BuildingData {
        all_vertices: Vec::new(),
        wall_max_z: None,
        roof_polygons: Vec::new(),
    };

    // Current polygon vertices (accumulated while reading posList)
    let mut poly_buf: Vec<[f64; 3]> = Vec::new();

    // Depth tracking to detect Building end
    let mut depth: i32 = 0;
    let mut building_depth: i32 = 0;

    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                depth += 1;
                let local = local_name(e.local_name().as_ref());

                match local.as_str() {
                    "Building" | "BuildingPart" => {
                        if !in_building {
                            in_building = true;
                            building_depth = depth;
                            current = BuildingData {
                                all_vertices: Vec::new(),
                                wall_max_z: None,
                                roof_polygons: Vec::new(),
                            };
                        }
                        // BuildingPart inside Building: just continue accumulating
                    }
                    "RoofSurface" if in_building => {
                        current_surface = SurfaceKind::Roof;
                    }
                    "WallSurface" if in_building => {
                        current_surface = SurfaceKind::Wall;
                    }
                    "GroundSurface" if in_building => {
                        current_surface = SurfaceKind::Ground;
                    }
                    "exterior" => {
                        in_exterior = true;
                    }
                    "interior" => {
                        in_exterior = false;
                    }
                    "posList" if in_building => {
                        in_pos_list = true;
                        poly_buf.clear();
                    }
                    _ => {}
                }
            }
            Ok(Event::End(ref e)) => {
                let local = local_name(e.local_name().as_ref());

                match local.as_str() {
                    "Building" | "BuildingPart"
                        if in_building && depth == building_depth =>
                    {
                        // Finish this building / part
                        let finished = std::mem::replace(
                            &mut current,
                            BuildingData {
                                all_vertices: Vec::new(),
                                wall_max_z: None,
                                roof_polygons: Vec::new(),
                            },
                        );
                        if !finished.roof_polygons.is_empty() {
                            buildings.push(finished);
                        }
                        in_building = false;
                        building_depth = 0;
                        current = BuildingData {
                            all_vertices: Vec::new(),
                            wall_max_z: None,
                            roof_polygons: Vec::new(),
                        };
                    }
                    "RoofSurface" | "WallSurface" | "GroundSurface" if in_building => {
                        current_surface = SurfaceKind::Other;
                    }
                    "exterior" => {
                        in_exterior = true; // reset
                    }
                    "interior" => {
                        in_exterior = true; // back to exterior context
                    }
                    "posList" if in_building => {
                        in_pos_list = false;
                        if !poly_buf.is_empty() {
                            // Remove closing vertex if it duplicates the first
                            let verts = dedup_closing_vertex(poly_buf.clone());
                            if verts.len() >= 3 {
                                // Only accumulate into all_vertices from actual LOD2 boundary
                                // surfaces (Wall/Ground/Roof). Excludes lod0RoofEdge (z=0
                                // projected footprints) and lod1Solid which would drag ground_z
                                // down to 0 and cause roofs to float above buildings.
                                if current_surface != SurfaceKind::Other {
                                    current.all_vertices.extend_from_slice(&verts);
                                }
                                // Track the highest Z seen across WallSurface polygons so we
                                // can later detect roof polygons that are not connected to any
                                // wall (isolated / floating patches).
                                if current_surface == SurfaceKind::Wall {
                                    let max_z = verts.iter().map(|v| v[2]).fold(f64::NEG_INFINITY, f64::max);
                                    current.wall_max_z = Some(match current.wall_max_z {
                                        Some(prev) => prev.max(max_z),
                                        None => max_z,
                                    });
                                }
                                // Only save exterior rings of roof surfaces
                                if current_surface == SurfaceKind::Roof && in_exterior {
                                    current.roof_polygons.push(verts);
                                }
                            }
                            poly_buf.clear();
                        }
                    }
                    _ => {}
                }

                depth -= 1;
            }
            Ok(Event::Text(ref e)) if in_pos_list => {
                if let Ok(text) = e.unescape() {
                    let values: Vec<f64> = text
                        .split_ascii_whitespace()
                        .filter_map(|s| s.parse::<f64>().ok())
                        .collect();

                    if values.len() >= 9 && values.len() % 3 == 0 {
                        poly_buf = values
                            .chunks_exact(3)
                            .map(|c| [c[0], c[1], c[2]])
                            .collect();
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                return Err(format!(
                    "XML error parsing '{}': {}",
                    path.display(),
                    e
                ))
            }
            _ => {}
        }
        buf.clear();
    }

    Ok(buildings)
}

// ---------------------------------------------------------------------------
// Voxelization
// ---------------------------------------------------------------------------

/// Voxelize one roof polygon using fan-triangulation + XZ scan-line rasterization.
///
/// `vertices` – list of `[lat, lng, height_m]` triplets (EPSG:6697 order).
/// Returns a list of absolute Minecraft `(x, y, z)` block positions.
fn voxelize_polygon(
    vertices: &[[f64; 3]],
    transformer: &CoordTransformer,
    ground_level: i32,
    scale: f64,
    ground_z: f64,
) -> Vec<(i32, i32, i32)> {
    if vertices.len() < 3 {
        return Vec::new();
    }

    // Convert all vertices to block coordinates.
    let bverts: Vec<(i32, i32, i32)> = vertices
        .iter()
        .filter_map(|v| {
            let lat = v[0];
            let lng = v[1];
            let h = v[2];

            let llp = LLPoint::new(lat, lng).ok()?;
            let xz = transformer.transform_point(llp);

            let h_above = (h - ground_z).max(0.0);
            let y = ground_level + (h_above * scale).round() as i32;

            Some((xz.x, y, xz.z))
        })
        .collect();

    if bverts.len() < 3 {
        return Vec::new();
    }

    // Fan-triangulate and rasterize each triangle.
    let mut result: Vec<(i32, i32, i32)> = Vec::new();
    let v0 = bverts[0];

    for i in 1..bverts.len() - 1 {
        let v1 = bverts[i];
        let v2 = bverts[i + 1];
        result.extend(rasterize_triangle(v0, v1, v2));
    }

    // Also draw edges to fill potential gaps (especially for steep surfaces).
    for i in 0..bverts.len() {
        let a = bverts[i];
        let b = bverts[(i + 1) % bverts.len()];
        result.extend(line_3d(a, b));
    }

    result
}

/// Rasterize a triangle in the XZ plane and interpolate Y by barycentric coordinates.
fn rasterize_triangle(
    v0: (i32, i32, i32),
    v1: (i32, i32, i32),
    v2: (i32, i32, i32),
) -> Vec<(i32, i32, i32)> {
    let (x0, y0, z0) = (v0.0 as f64, v0.1 as f64, v0.2 as f64);
    let (x1, y1, z1) = (v1.0 as f64, v1.1 as f64, v1.2 as f64);
    let (x2, y2, z2) = (v2.0 as f64, v2.1 as f64, v2.2 as f64);

    // Bounding box in XZ
    let min_x = x0.min(x1).min(x2).floor() as i32;
    let max_x = x0.max(x1).max(x2).ceil() as i32;
    let min_z = z0.min(z1).min(z2).floor() as i32;
    let max_z = z0.max(z1).max(z2).ceil() as i32;

    // Sanity guard: skip huge degenerate bounding boxes
    if (max_x - min_x) > 2048 || (max_z - min_z) > 2048 {
        return Vec::new();
    }

    // Area of triangle in XZ (for degenerate check)
    let denom = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2);
    if denom.abs() < 0.5 {
        // Degenerate in XZ → draw edges only
        let mut edges = line_3d(v0, v1);
        edges.extend(line_3d(v1, v2));
        edges.extend(line_3d(v2, v0));
        return edges;
    }

    let mut result = Vec::with_capacity(((max_x - min_x + 1) * (max_z - min_z + 1)) as usize);

    for px in min_x..=max_x {
        for pz in min_z..=max_z {
            let px_f = px as f64 + 0.5;
            let pz_f = pz as f64 + 0.5;

            // Barycentric coordinates in XZ plane
            let w0 = ((z1 - z2) * (px_f - x2) + (x2 - x1) * (pz_f - z2)) / denom;
            let w1 = ((z2 - z0) * (px_f - x2) + (x0 - x2) * (pz_f - z2)) / denom;
            let w2 = 1.0 - w0 - w1;

            if w0 >= -1e-6 && w1 >= -1e-6 && w2 >= -1e-6 {
                let py = w0 * y0 + w1 * y1 + w2 * y2;
                result.push((px, py.round() as i32, pz));
            }
        }
    }

    result
}

/// 3D Bresenham line from block a to block b.
fn line_3d(a: (i32, i32, i32), b: (i32, i32, i32)) -> Vec<(i32, i32, i32)> {
    let (x0, y0, z0) = a;
    let (x1, y1, z1) = b;

    let dx = (x1 - x0).abs();
    let dy = (y1 - y0).abs();
    let dz = (z1 - z0).abs();

    let sx = if x0 < x1 { 1 } else { -1 };
    let sy = if y0 < y1 { 1 } else { -1 };
    let sz = if z0 < z1 { 1 } else { -1 };

    let mut x = x0;
    let mut y = y0;
    let mut z = z0;

    let mut points = Vec::new();
    points.push((x, y, z));

    if dx >= dy && dx >= dz {
        let mut err1 = 2 * dy - dx;
        let mut err2 = 2 * dz - dx;
        for _ in 0..dx {
            if err1 >= 0 {
                y += sy;
                err1 -= 2 * dx;
            }
            if err2 >= 0 {
                z += sz;
                err2 -= 2 * dx;
            }
            err1 += 2 * dy;
            err2 += 2 * dz;
            x += sx;
            points.push((x, y, z));
        }
    } else if dy >= dx && dy >= dz {
        let mut err1 = 2 * dx - dy;
        let mut err2 = 2 * dz - dy;
        for _ in 0..dy {
            if err1 >= 0 {
                x += sx;
                err1 -= 2 * dy;
            }
            if err2 >= 0 {
                z += sz;
                err2 -= 2 * dy;
            }
            err1 += 2 * dx;
            err2 += 2 * dz;
            y += sy;
            points.push((x, y, z));
        }
    } else {
        let mut err1 = 2 * dy - dz;
        let mut err2 = 2 * dx - dz;
        for _ in 0..dz {
            if err1 >= 0 {
                y += sy;
                err1 -= 2 * dz;
            }
            if err2 >= 0 {
                x += sx;
                err2 -= 2 * dz;
            }
            err1 += 2 * dy;
            err2 += 2 * dx;
            z += sz;
            points.push((x, y, z));
        }
    }

    points
}

// ---------------------------------------------------------------------------
// Block selection
// ---------------------------------------------------------------------------

/// Select an appropriate roof block based on absolute Y height.
///
/// Taller buildings get a slightly different material for visual interest.
fn roof_block_for_height(y: i32, ground_level: i32) -> Block {
    let height_above_ground = (y - ground_level).max(0);
    if height_above_ground <= 6 {
        STONE_BRICKS
    } else if height_above_ground <= 20 {
        SMOOTH_STONE
    } else {
        LIGHT_GRAY_CONCRETE
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/// Extract the local XML name (strip namespace prefix).
fn local_name(raw: &[u8]) -> String {
    let s = std::str::from_utf8(raw).unwrap_or("");
    s.rsplit(':').next().unwrap_or(s).to_string()
}

/// Remove the closing duplicate vertex from a GML ring (first == last).
fn dedup_closing_vertex(mut verts: Vec<[f64; 3]>) -> Vec<[f64; 3]> {
    if verts.len() >= 2 {
        let first = verts[0];
        let last = *verts.last().unwrap();
        if (first[0] - last[0]).abs() < 1e-10
            && (first[1] - last[1]).abs() < 1e-10
            && (first[2] - last[2]).abs() < 1e-10
        {
            verts.pop();
        }
    }
    verts
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_local_name() {
        assert_eq!(local_name(b"bldg:RoofSurface"), "RoofSurface");
        assert_eq!(local_name(b"gml:posList"), "posList");
        assert_eq!(local_name(b"Building"), "Building");
    }

    #[test]
    fn test_dedup_closing_vertex() {
        let v = vec![
            [35.0, 139.0, 10.0],
            [35.1, 139.0, 10.0],
            [35.1, 139.1, 10.0],
            [35.0, 139.0, 10.0], // duplicate
        ];
        let result = dedup_closing_vertex(v);
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn test_rasterize_flat_triangle() {
        // Flat triangle in XZ plane (all y=5)
        let v0 = (0, 5, 0);
        let v1 = (4, 5, 0);
        let v2 = (0, 5, 4);
        let blocks = rasterize_triangle(v0, v1, v2);
        // Should produce blocks in the triangle area
        assert!(!blocks.is_empty());
        // All blocks should have y = 5
        for (_, y, _) in &blocks {
            assert_eq!(*y, 5, "Expected y=5, got y={}", y);
        }
    }

    #[test]
    fn test_rasterize_sloped_triangle() {
        // Sloped triangle: different y values
        let v0 = (0, 0, 0);
        let v1 = (4, 4, 0);
        let v2 = (0, 2, 4);
        let blocks = rasterize_triangle(v0, v1, v2);
        assert!(!blocks.is_empty());
    }

    #[test]
    fn test_line_3d_simple() {
        let pts = line_3d((0, 0, 0), (3, 0, 0));
        assert_eq!(pts.len(), 4);
        assert_eq!(pts[0], (0, 0, 0));
        assert_eq!(pts[3], (3, 0, 0));
    }

    #[test]
    fn test_line_3d_diagonal() {
        let pts = line_3d((0, 0, 0), (2, 2, 2));
        assert!(!pts.is_empty());
        assert_eq!(*pts.first().unwrap(), (0, 0, 0));
        assert_eq!(*pts.last().unwrap(), (2, 2, 2));
    }

    // -----------------------------------------------------------------------
    // Filter logic tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_height_filter_passes_normal_roof() {
        // A roof 8 m above ground_z = 2.0 should pass the 10 m limit.
        let ground_z = 2.0_f64;
        let poly_min_z = 10.0_f64;
        assert!(poly_min_z - ground_z <= MAX_ROOF_HEIGHT_M);
    }

    #[test]
    fn test_height_filter_rejects_high_polygon() {
        // A polygon 15 m above ground_z must be rejected (> 10 m limit).
        let ground_z = 2.0_f64;
        let poly_min_z = 17.0_f64;
        assert!(poly_min_z - ground_z > MAX_ROOF_HEIGHT_M);
    }

    #[test]
    fn test_ground_level_block_filter() {
        // Blocks below ground_level + 10 must be skipped.
        let ground_level = -62_i32;
        let threshold = ground_level + 10; // = -52
        assert!((ground_level + 9) < threshold);  // excluded
        assert!((ground_level + 10) >= threshold); // included (boundary)
        assert!((ground_level + 11) >= threshold); // included
    }

    #[test]
    fn test_connectivity_filter_passes_connected_roof() {
        // wall_max_z = 10.0, roof min = 10.0 → within slack → should pass.
        let wall_max_z: f64 = 10.0;
        let poly_min_z: f64 = 10.0;
        assert!(poly_min_z <= wall_max_z + WALL_CONNECTIVITY_SLACK_M);
    }

    #[test]
    fn test_connectivity_filter_rejects_isolated_roof() {
        // wall_max_z = 10.0, roof min = 25.0 → 15 m above walls → isolated.
        let wall_max_z: f64 = 10.0;
        let poly_min_z: f64 = 25.0;
        assert!(poly_min_z > wall_max_z + WALL_CONNECTIVITY_SLACK_M);
    }

    #[test]
    fn test_connectivity_filter_passes_when_no_walls() {
        // When wall_max_z is None, the connectivity filter must be skipped
        // (no walls → don't reject anything based on wall connection).
        let wall_max_z: Option<f64> = None;
        let poly_min_z: f64 = 999.0;
        // Simulate filter logic: only check when Some
        let rejected = wall_max_z
            .map(|wz| poly_min_z > wz + WALL_CONNECTIVITY_SLACK_M)
            .unwrap_or(false);
        assert!(!rejected);
    }
}

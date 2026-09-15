import s2sphere


def cell_of(lat_degrees: float, lng_degrees: float, level: int) -> s2sphere.CellId:
    """Return S2 cell at specified level that contains the specified point."""
    return s2sphere.CellId.from_lat_lng(s2sphere.LatLng.from_degrees(lat_degrees, lng_degrees))  # TODO: fix


def offset_cell(cell: s2sphere.CellId, east: int, north: int) -> s2sphere.CellID:
    """Return S2 cell at the same level that is `east` cells to the east and `north` cells to the north.
    
    For instance, east=1,north=0 retrieves the neighboring S2 cell to the east.
    Negative `east` or `north` retrieves the cell to the west or south, respectively."""
    raise NotImplementedError()


def interior_latlng_rect(cell: s2sphere.CellId) -> tuple[float, float, float, float]:
    """Return a large inscribed lat-lng rectangle fully contained within the specified S2 cell.
    
    No points in the lat-lng rectangle may lie within any other S2 cell at the specified level
    (i.e., they may not be on the edge between two S2 cells).
    
    Returns:
      * Minimum latitude (degrees)
      * Minimum longitude (degrees)
      * Maximum latitude (degrees)
      * Maximum longitude (degrees)
    """
    raise NotImplementedError()

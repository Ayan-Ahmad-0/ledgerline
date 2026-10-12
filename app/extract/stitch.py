import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class XInterval:
    x0: float
    x1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def midpoint(self) -> float:
        return (self.x0 + self.x1) / 2.0

    def overlap(self, other: "XInterval") -> float:
        """Returns horizontal intersection width."""
        intersect_x0 = max(self.x0, other.x0)
        intersect_x1 = min(self.x1, other.x1)
        return max(0.0, intersect_x1 - intersect_x0)

    def overlap_ratio(self, other: "XInterval") -> float:
        """Overlap fraction relative to the smaller interval."""
        intersect = self.overlap(other)
        min_w = min(self.width, other.width)
        return intersect / min_w if min_w > 0 else 0.0


@dataclass
class TableCell:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    col_idx: Optional[int] = None

    @property
    def interval(self) -> XInterval:
        return XInterval(self.x0, self.x1)


@dataclass
class TableRow:
    cells: list[TableCell]
    page_number: int = 1
    is_header: bool = False

    @property
    def text_tokens(self) -> list[str]:
        tokens = []
        for c in self.cells:
            cleaned = re.sub(r"[^\w\s]", "", c.text.lower()).strip()
            if cleaned:
                tokens.append(cleaned)
        return tokens


@dataclass
class PageTable:
    page_number: int
    rows: list[TableRow]


@dataclass
class ColumnCluster:
    col_idx: int
    interval: XInterval
    name: Optional[str] = None


@dataclass
class StitchedTable:
    columns: list[ColumnCluster]
    header_tokens: list[str]
    rows: list[TableRow]
    dropped_headers_count: int
    page_count: int


# ---------------------------------------------------------------------------
# Header Token Fingerprinting
# ---------------------------------------------------------------------------

def compute_header_fingerprint(tokens: list[str]) -> str:
    """
    Hash normalized tokens of a row to generate a reproducible fingerprint.
    Used to detect and drop repeated headers on subsequent pages.
    """
    normalized = "|".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_matching_header(row_tokens: list[str], target_fingerprint: str, target_tokens: list[str]) -> bool:
    """Check exact fingerprint match or high token Jaccard overlap (>= 80%)."""
    if not row_tokens:
        return False
    if compute_header_fingerprint(row_tokens) == target_fingerprint:
        return True

    set_a = set(row_tokens)
    set_b = set(target_tokens)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    jaccard = intersection / union if union > 0 else 0.0
    return jaccard >= 0.80


# ---------------------------------------------------------------------------
# X-Interval Clustering for Column Alignment
# ---------------------------------------------------------------------------

def cluster_column_intervals(header_row: TableRow, min_overlap: float = 0.3) -> list[ColumnCluster]:
    """
    Derive canonical column clusters from the first table header row intervals.
    Sorts intervals horizontally left-to-right.
    """
    sorted_cells = sorted(header_row.cells, key=lambda c: c.x0)
    clusters: list[ColumnCluster] = []

    for idx, cell in enumerate(sorted_cells):
        cluster = ColumnCluster(
            col_idx=idx,
            interval=cell.interval,
            name=cell.text.strip()
        )
        clusters.append(cluster)

    return clusters


def assign_cells_to_columns(row: TableRow, column_clusters: list[ColumnCluster]) -> None:
    """
    Assign cells in a data row to column clusters based on maximum horizontal overlap.
    Carries column alignments forward across multi-page boundaries.
    """
    for cell in row.cells:
        best_col = None
        best_overlap = -1.0
        c_interval = cell.interval

        for col in column_clusters:
            overlap = c_interval.overlap_ratio(col.interval)
            if overlap > best_overlap and overlap > 0.15:
                best_overlap = overlap
                best_col = col.col_idx

        # If no strict overlap, pick nearest midpoint
        if best_col is None and column_clusters:
            mid = c_interval.midpoint
            best_col = min(column_clusters, key=lambda col: abs(col.interval.midpoint - mid)).col_idx

        cell.col_idx = best_col


# ---------------------------------------------------------------------------
# Multi-Page Table Stitcher
# ---------------------------------------------------------------------------

def stitch_multi_page_tables(page_tables: list[PageTable]) -> StitchedTable:
    """
    Stitch tables across pages:
    1. Extract column clusters and header fingerprint from Page 1.
    2. On Page 2+, fingerprint rows to drop repeated running headers.
    3. Carry column x-interval alignment forward to all data cells.
    """
    if not page_tables:
        return StitchedTable(
            columns=[],
            header_tokens=[],
            rows=[],
            dropped_headers_count=0,
            page_count=0
        )

    first_page = page_tables[0]
    if not first_page.rows:
        return StitchedTable(
            columns=[],
            header_tokens=[],
            rows=[],
            dropped_headers_count=0,
            page_count=len(page_tables)
        )

    # First row of first page is the primary table header
    header_row = first_page.rows[0]
    header_row.is_header = True
    column_clusters = cluster_column_intervals(header_row)
    header_tokens = header_row.text_tokens
    header_fingerprint = compute_header_fingerprint(header_tokens)

    stitched_rows: list[TableRow] = []
    dropped_headers = 0

    # Process Page 1 data rows
    for row in first_page.rows[1:]:
        assign_cells_to_columns(row, column_clusters)
        stitched_rows.append(row)

    # Process Page 2+
    for page in page_tables[1:]:
        for row in page.rows:
            row_tokens = row.text_tokens
            if is_matching_header(row_tokens, header_fingerprint, header_tokens):
                # Repeated running header detected: drop it!
                dropped_headers += 1
                row.is_header = True
                continue

            # Regular data row on subsequent page
            assign_cells_to_columns(row, column_clusters)
            stitched_rows.append(row)

    return StitchedTable(
        columns=column_clusters,
        header_tokens=header_tokens,
        rows=stitched_rows,
        dropped_headers_count=dropped_headers,
        page_count=len(page_tables)
    )

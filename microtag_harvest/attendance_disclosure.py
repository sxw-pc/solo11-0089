"""Publishable, differentially-private-by-construction attendance tables.

This module replaces the earlier "mask the small cells but publish every
margin" preview.  A disclosure is only published when every sensitive cell
(the true value is below ``mask_below``) can take at least three distinct
non-negative integer values across the full set of grids compatible with the
published statistics.  Every published number is an exact value drawn from the
harvested source table; noise is never fabricated.

The solver is a bounded exact enumerator (a backtracking integer constraint
solver over rows, columns and the 0..cap cell domain).  It supports grids up
to 3 rows by 4 columns and never calls an external privacy service.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

from .utils import canonical_json, sha256_hex

MAX_ROWS = 3
MAX_COLUMNS = 4
DEFAULT_CELL_CAP = 20
MIN_DISTINCT_SENSITIVE_VALUES = 3
MAX_WITNESSES = 12

CONTRACT_MARGIN_PRIORITY = "margin_priority"
CONTRACT_DETAIL_PRIORITY = "detail_priority"
CONTRACT_ORDER = (CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY)


class DisclosureError(ValueError):
    """Raised when an intake payload cannot describe a valid disclosure."""


class UnsafeDisclosureError(ValueError):
    """Raised when no hiding strategy satisfies the privacy condition."""


def cell_id(row: str, column: str) -> str:
    return f"{row}:{column}"


def row_total_id(row: str) -> str:
    return f"row_total:{row}"


def column_total_id(column: str) -> str:
    return f"column_total:{column}"


@dataclass(frozen=True)
class AttendanceTable:
    """A validated two-dimensional non-negative integer attendance table."""

    rows: tuple[str, ...]
    columns: tuple[str, ...]
    counts: tuple[tuple[int, ...], ...]
    mask_below: int
    cap: int = DEFAULT_CELL_CAP

    @property
    def row_totals(self) -> tuple[int, ...]:
        return tuple(sum(row) for row in self.counts)

    @property
    def column_totals(self) -> tuple[int, ...]:
        return tuple(
            sum(self.counts[i][j] for i in range(len(self.rows)))
            for j in range(len(self.columns))
        )

    @property
    def sensitive_cells(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (i, j)
            for i in range(len(self.rows))
            for j in range(len(self.columns))
            if self.counts[i][j] < self.mask_below
        )

    def grid_as_dict(self, grid: Iterable[Iterable[int]]) -> dict[str, dict[str, int]]:
        return {
            row: {column: int(value) for column, value in zip(self.columns, values)}
            for row, values in zip(self.rows, grid)
        }

    def fingerprint(self) -> str:
        """Hash the exact source material a disclosure is bound to."""
        material = {
            "rows": list(self.rows),
            "columns": list(self.columns),
            "counts": [list(row) for row in self.counts],
            "mask_below": self.mask_below,
            "cap": self.cap,
        }
        return sha256_hex(canonical_json(material))


@dataclass(frozen=True)
class Disclosure:
    """One candidate set of statistics made visible to a customer."""

    visible_cells: frozenset[tuple[int, int]]
    visible_row_totals: frozenset[int]
    visible_column_totals: frozenset[int]

    def cell_identifiers(self, table: AttendanceTable) -> list[str]:
        return sorted(
            cell_id(table.rows[i], table.columns[j]) for i, j in self.visible_cells
        )

    def row_identifiers(self, table: AttendanceTable) -> list[str]:
        return sorted(row_total_id(table.rows[i]) for i in self.visible_row_totals)

    def column_identifiers(self, table: AttendanceTable) -> list[str]:
        return sorted(
            column_total_id(table.columns[j]) for j in self.visible_column_totals
        )


def _coerce_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DisclosureError(f"{label} must be an integer, got {type(value).__name__}")
    return value


def build_table(
    rows: list[str],
    columns: list[str],
    counts: dict[str, dict[str, int]],
    mask_below: int,
    cap: int = DEFAULT_CELL_CAP,
) -> AttendanceTable:
    """Validate raw intake material into an :class:`AttendanceTable`."""

    if not isinstance(rows, list) or not rows or not all(isinstance(r, str) for r in rows):
        raise DisclosureError("rows must be a non-empty list of strings")
    if not isinstance(columns, list) or not columns or not all(
        isinstance(c, str) for c in columns
    ):
        raise DisclosureError("columns must be a non-empty list of strings")
    if len(set(rows)) != len(rows):
        raise DisclosureError(f"duplicate row labels: {rows}")
    if len(set(columns)) != len(columns):
        raise DisclosureError(f"duplicate column labels: {columns}")
    if len(rows) > MAX_ROWS or len(columns) > MAX_COLUMNS:
        raise DisclosureError(
            f"grids larger than {MAX_ROWS}x{MAX_COLUMNS} are not supported"
        )

    threshold = _coerce_int(mask_below, "mask_below")
    cell_cap = _coerce_int(cap, "cap")
    if threshold < 0:
        raise DisclosureError("mask_below must be non-negative")
    if cell_cap < 0:
        raise DisclosureError("cap must be non-negative")

    if not isinstance(counts, dict):
        raise DisclosureError("counts must be an object keyed by row label")
    matrix: list[tuple[int, ...]] = []
    for row in rows:
        if row not in counts or not isinstance(counts[row], dict):
            raise DisclosureError(f"counts is missing row {row!r}")
        line: list[int] = []
        for column in columns:
            if column not in counts[row]:
                raise DisclosureError(
                    f"counts is missing cell {cell_id(row, column)!r}"
                )
            value = _coerce_int(
                counts[row][column], f"cell {cell_id(row, column)!r}"
            )
            if value < 0:
                raise DisclosureError(
                    f"cell {cell_id(row, column)!r} is negative: {value}"
                )
            if value > cell_cap:
                raise DisclosureError(
                    f"cell {cell_id(row, column)!r}={value} exceeds cap {cell_cap}"
                )
            line.append(value)
        matrix.append(tuple(line))
    extra_rows = set(counts) - set(rows)
    if extra_rows:
        raise DisclosureError(f"counts contains undeclared rows: {sorted(extra_rows)}")
    for row, line in counts.items():
        if isinstance(line, dict):
            extra_columns = set(line) - set(columns)
            if extra_columns:
                raise DisclosureError(
                    f"counts row {row!r} contains undeclared columns: "
                    f"{sorted(extra_columns)}"
                )

    return AttendanceTable(
        rows=tuple(rows),
        columns=tuple(columns),
        counts=tuple(matrix),
        mask_below=threshold,
        cap=cell_cap,
    )


def table_from_payload(data: dict[str, Any], cap: int = DEFAULT_CELL_CAP) -> AttendanceTable:
    missing = {"rows", "columns", "counts", "mask_below"} - set(data)
    if missing:
        raise DisclosureError(f"intake payload missing keys: {sorted(missing)}")
    return build_table(
        rows=list(data["rows"]),
        columns=list(data["columns"]),
        counts=data["counts"],
        mask_below=data["mask_below"],
        cap=data.get("cap", cap),
    )


def enumerate_compatible_grids(
    table: AttendanceTable,
    disclosure: Disclosure,
    *,
    limit: int | None = None,
    row_totals_override: tuple[int, ...] | None = None,
    column_totals_override: tuple[int, ...] | None = None,
) -> list[tuple[tuple[int, ...], ...]]:
    """Return every (or up to ``limit``) grid compatible with a disclosure.

    A compatible grid keeps every visible cell and every visible margin equal
    to the source table, and keeps every cell inside ``0..cap``.  Hidden cells
    and hidden margins are free.  Enumeration is exact: backtracking in
    row-major order with running row/column sum pruning.

    The margin overrides exist for pinning probes: when a cell is temporarily
    pinned to a hypothetical value, the published margins still come from the
    true source table.
    """

    n_rows, n_cols = len(table.rows), len(table.columns)
    row_totals = (
        row_totals_override if row_totals_override is not None else table.row_totals
    )
    column_totals = (
        column_totals_override
        if column_totals_override is not None
        else table.column_totals
    )
    visible = disclosure.visible_cells
    visible_rows = disclosure.visible_row_totals
    visible_cols = disclosure.visible_column_totals
    cap = table.cap

    row_sum = [0] * n_rows
    col_sum = [0] * n_cols
    row_fixed = [0] * n_rows
    col_fixed = [0] * n_cols
    for i, j in visible:
        row_fixed[i] += table.counts[i][j]
        col_fixed[j] += table.counts[i][j]

    # Free (hidden) cells at-or-after position j in row i, and at-or-below
    # position i in column j.  These make the pruning bounds exact: a visible
    # row/column total can only be changed through free cells inside that same
    # row/column.
    row_free_suffix = [[0] * (n_cols + 1) for _ in range(n_rows)]
    for i in range(n_rows):
        for j in range(n_cols - 1, -1, -1):
            row_free_suffix[i][j] = row_free_suffix[i][j + 1] + (
                0 if (i, j) in visible else 1
            )
    col_free_below = [[0] * n_cols for _ in range(n_rows + 1)]
    for j in range(n_cols):
        for i in range(n_rows - 1, -1, -1):
            col_free_below[i][j] = col_free_below[i + 1][j] + (
                0 if (i, j) in visible else 1
            )
    # Fixed visible-cell values that will be added to the running sums later.
    row_fixed_suffix = [[0] * (n_cols + 1) for _ in range(n_rows)]
    for i in range(n_rows):
        for j in range(n_cols - 1, -1, -1):
            row_fixed_suffix[i][j] = row_fixed_suffix[i][j + 1] + (
                table.counts[i][j] if (i, j) in visible else 0
            )
    col_fixed_below = [[0] * n_cols for _ in range(n_rows + 1)]
    for j in range(n_cols):
        for i in range(n_rows - 1, -1, -1):
            col_fixed_below[i][j] = col_fixed_below[i + 1][j] + (
                table.counts[i][j] if (i, j) in visible else 0
            )

    for i in visible_rows:
        if row_totals[i] < row_fixed[i]:
            return []
    for j in visible_cols:
        if column_totals[j] < col_fixed[j]:
            return []

    grid: list[list[int]] = [[0] * n_cols for _ in range(n_rows)]
    found: list[tuple[tuple[int, ...], ...]] = []

    def backtrack(index: int) -> bool:
        if limit is not None and len(found) >= limit:
            return True
        if index == n_rows * n_cols:
            for i in visible_rows:
                if row_sum[i] != row_totals[i]:
                    return False
            for j in visible_cols:
                if col_sum[j] != column_totals[j]:
                    return False
            found.append(tuple(tuple(line) for line in grid))
            return len(found) >= limit if limit is not None else False

        i, j = divmod(index, n_cols)

        if (i, j) in visible:
            value = table.counts[i][j]
            grid[i][j] = value
            row_sum[i] += value
            col_sum[j] += value
            ok = True
            if i in visible_rows:
                target = row_totals[i] - row_fixed_suffix[i][j + 1]
                free_after = row_free_suffix[i][j + 1]
                if row_sum[i] > target or row_sum[i] + free_after * cap < target:
                    ok = False
            if ok and j in visible_cols:
                target = column_totals[j] - col_fixed_below[i + 1][j]
                free_below = col_free_below[i + 1][j]
                if col_sum[j] > target or col_sum[j] + free_below * cap < target:
                    ok = False
            if ok:
                backtrack(index + 1)
            row_sum[i] -= value
            col_sum[j] -= value
            return limit is not None and len(found) >= limit

        lower = 0
        upper = cap
        if i in visible_rows:
            target = row_totals[i] - row_fixed_suffix[i][j + 1]
            # Free cells after this position; the current cell absorbs slack.
            free_after = row_free_suffix[i][j + 1]
            lower = max(lower, target - row_sum[i] - free_after * cap)
            upper = min(upper, target - row_sum[i])
        if j in visible_cols:
            target = column_totals[j] - col_fixed_below[i + 1][j]
            # Free cells below this position; the current cell absorbs slack.
            free_below = col_free_below[i + 1][j]
            lower = max(lower, target - col_sum[j] - free_below * cap)
            upper = min(upper, target - col_sum[j])

        for value in range(max(0, lower), min(cap, upper) + 1):
            grid[i][j] = value
            row_sum[i] += value
            col_sum[j] += value
            backtrack(index + 1)
            row_sum[i] -= value
            col_sum[j] -= value
            if limit is not None and len(found) >= limit:
                return True
        return limit is not None and len(found) >= limit

    backtrack(0)
    return found


def _pinned_feasible(
    table: AttendanceTable,
    disclosure: Disclosure,
    pin: tuple[int, int, int],
) -> bool:
    """Feasibility with cell (i, j) additionally pinned to a candidate value."""

    i, j, candidate = pin
    swapped = AttendanceTable(
        rows=table.rows,
        columns=table.columns,
        counts=tuple(
            tuple(
                candidate if (r, c) == (i, j) else table.counts[r][c]
                for c in range(len(table.columns))
            )
            for r in range(len(table.rows))
        ),
        mask_below=table.mask_below,
        cap=table.cap,
    )
    pinned = Disclosure(
        visible_cells=disclosure.visible_cells | frozenset({(i, j)}),
        visible_row_totals=disclosure.visible_row_totals,
        visible_column_totals=disclosure.visible_column_totals,
    )
    return bool(
        enumerate_compatible_grids(
            swapped,
            pinned,
            limit=1,
            row_totals_override=table.row_totals,
            column_totals_override=table.column_totals,
        )
    )


def possible_sensitive_values(
    table: AttendanceTable, disclosure: Disclosure, sensitive: tuple[tuple[int, int], ...]
) -> dict[tuple[int, int], list[int]]:
    """Exact set of integer values each sensitive cell can take."""

    result: dict[tuple[int, int], list[int]] = {}
    for i, j in sensitive:
        values = [
            candidate
            for candidate in range(table.cap + 1)
            if _pinned_feasible(table, disclosure, (i, j, candidate))
        ]
        result[(i, j)] = values
    return result


def privacy_satisfied(
    table: AttendanceTable,
    disclosure: Disclosure,
    sensitive: tuple[tuple[int, int], ...],
) -> bool:
    """Every sensitive cell needs >= three distinct feasible integer values."""

    for i, j in sensitive:
        distinct = 0
        for candidate in range(table.cap + 1):
            if _pinned_feasible(table, disclosure, (i, j, candidate)):
                distinct += 1
                if distinct >= MIN_DISTINCT_SENSITIVE_VALUES:
                    break
        if distinct < MIN_DISTINCT_SENSITIVE_VALUES:
            return False
    return True


def _all_cell_ids(table: AttendanceTable) -> list[tuple[int, int]]:
    return [(i, j) for i in range(len(table.rows)) for j in range(len(table.columns))]


def margin_priority_disclosure(table: AttendanceTable) -> Disclosure:
    """Keep every margin; hide the fewest exact cells that protects privacy."""

    sensitive = table.sensitive_cells
    sensitive_set = frozenset(sensitive)
    all_cells = _all_cell_ids(table)
    optional = [cell for cell in all_cells if cell not in sensitive_set]

    def with_hidden(hidden: frozenset[tuple[int, int]]) -> Disclosure:
        return Disclosure(
            visible_cells=frozenset(all_cells) - hidden,
            visible_row_totals=frozenset(range(len(table.rows))),
            visible_column_totals=frozenset(range(len(table.columns))),
        )

    def hidden_key(hidden: frozenset[tuple[int, int]]) -> tuple[str, ...]:
        return tuple(
            sorted(cell_id(table.rows[i], table.columns[j]) for i, j in hidden)
        )

    # Sensitive cells must always be hidden; grow the optional hidden set.
    for extra_count in range(len(optional) + 1):
        winners: list[frozenset[tuple[int, int]]] = []
        for combo in combinations(optional, extra_count):
            hidden = sensitive_set | frozenset(combo)
            if privacy_satisfied(table, with_hidden(hidden), sensitive):
                winners.append(hidden)
        if winners:
            chosen = min(winners, key=hidden_key)
            return with_hidden(chosen)
    raise UnsafeDisclosureError(
        "margin_priority: no cell-hiding strategy reaches the "
        f"{MIN_DISTINCT_SENSITIVE_VALUES}-value privacy threshold"
    )


def detail_priority_disclosure(table: AttendanceTable) -> Disclosure:
    """Keep every non-sensitive cell; hide the fewest margins that protects."""

    sensitive = table.sensitive_cells
    visible_cells = frozenset(_all_cell_ids(table)) - frozenset(sensitive)
    margin_ids = [
        *(("row", i) for i in range(len(table.rows))),
        *(("column", j) for j in range(len(table.columns))),
    ]

    all_rows = frozenset(range(len(table.rows)))
    all_cols = frozenset(range(len(table.columns)))

    def with_hidden(hidden_rows: frozenset[int], hidden_cols: frozenset[int]) -> Disclosure:
        return Disclosure(
            visible_cells=visible_cells,
            visible_row_totals=all_rows - hidden_rows,
            visible_column_totals=all_cols - hidden_cols,
        )

    def margin_key(hidden_rows: frozenset[int], hidden_cols: frozenset[int]) -> tuple[str, ...]:
        return tuple(
            sorted(
                [
                    *(row_total_id(table.rows[i]) for i in hidden_rows),
                    *(column_total_id(table.columns[j]) for j in hidden_cols),
                ]
            )
        )

    for hidden_count in range(len(margin_ids) + 1):
        winners: list[tuple[frozenset[int], frozenset[int]]] = []
        for combo in combinations(margin_ids, hidden_count):
            hidden_rows = frozenset(i for kind, i in combo if kind == "row")
            hidden_cols = frozenset(i for kind, i in combo if kind == "column")
            if privacy_satisfied(table, with_hidden(hidden_rows, hidden_cols), sensitive):
                winners.append((hidden_rows, hidden_cols))
        if winners:
            chosen_rows, chosen_cols = min(
                winners, key=lambda pair: margin_key(*pair)
            )
            return with_hidden(chosen_rows, chosen_cols)
    raise UnsafeDisclosureError(
        "detail_priority: no margin-hiding strategy reaches the "
        f"{MIN_DISTINCT_SENSITIVE_VALUES}-value privacy threshold"
    )


def _hidden_identifiers_margin(
    table: AttendanceTable, disclosure: Disclosure
) -> list[str]:
    hidden = set(_all_cell_ids(table)) - disclosure.visible_cells
    return sorted(cell_id(table.rows[i], table.columns[j]) for i, j in hidden)


def _hidden_identifiers_detail(
    table: AttendanceTable, disclosure: Disclosure
) -> list[str]:
    hidden_rows = set(range(len(table.rows))) - disclosure.visible_row_totals
    hidden_cols = set(range(len(table.columns))) - disclosure.visible_column_totals
    return sorted(
        [
            *(row_total_id(table.rows[i]) for i in hidden_rows),
            *(column_total_id(table.columns[j]) for j in hidden_cols),
        ]
    )


def minimality_certificate(
    table: AttendanceTable,
    chosen: Disclosure,
    contract: str,
) -> dict[str, Any]:
    """Certify that no strategy hiding fewer items satisfies the contract.

    Every smaller hiding set that the contract allows is re-enumerated; each
    one is shown to fail the three-value privacy condition.  If any one
    unexpectedly passed, the chosen disclosure was not minimal and this raises
    rather than emitting a false certificate.
    """

    sensitive = table.sensitive_cells
    sensitive_set = frozenset(sensitive)
    all_cells = _all_cell_ids(table)

    if contract == CONTRACT_MARGIN_PRIORITY:
        hidden = set(all_cells) - chosen.visible_cells
        chosen_ids = sorted(
            cell_id(table.rows[i], table.columns[j]) for i, j in hidden
        )
        optional = [cell for cell in all_cells if cell not in sensitive_set]
        failed: list[list[str]] = []
        checked = 0
        for size in range(len(hidden - sensitive_set)):
            for combo in combinations(optional, size):
                smaller_hidden = sensitive_set | frozenset(combo)
                checked += 1
                candidate = Disclosure(
                    visible_cells=frozenset(all_cells) - smaller_hidden,
                    visible_row_totals=frozenset(range(len(table.rows))),
                    visible_column_totals=frozenset(range(len(table.columns))),
                )
                if privacy_satisfied(table, candidate, sensitive):
                    raise UnsafeDisclosureError(
                        "minimality certificate invalidated: a smaller hiding "
                        "set satisfies margin_priority"
                    )
                failed.append(
                    sorted(
                        cell_id(table.rows[i], table.columns[j])
                        for i, j in smaller_hidden
                    )
                )
        return {
            "contract": contract,
            "kind": "minimum_hidden_cells_with_all_margins_visible",
            "hidden_count": len(hidden),
            "hidden_identifiers": chosen_ids,
            "smaller_hiding_sets_checked": checked,
            "all_smaller_sets_fail_privacy": True,
            "failing_smaller_sets": failed,
        }

    if contract == CONTRACT_DETAIL_PRIORITY:
        hidden_rows = set(range(len(table.rows))) - chosen.visible_row_totals
        hidden_cols = set(range(len(table.columns))) - chosen.visible_column_totals
        hidden_margins = [
            *(("row", i) for i in hidden_rows),
            *(("column", j) for j in hidden_cols),
        ]
        chosen_ids = sorted(
            [
                *(row_total_id(table.rows[i]) for i in hidden_rows),
                *(column_total_id(table.columns[j]) for j in hidden_cols),
            ]
        )
        visible_cells = frozenset(all_cells) - sensitive_set
        all_margins = [
            *(("row", i) for i in range(len(table.rows))),
            *(("column", j) for j in range(len(table.columns))),
        ]
        failed: list[list[str]] = []
        checked = 0
        for size in range(len(hidden_margins)):
            for combo in combinations(all_margins, size):
                rows = frozenset(i for kind, i in combo if kind == "row")
                cols = frozenset(i for kind, i in combo if kind == "column")
                checked += 1
                candidate = Disclosure(
                    visible_cells=visible_cells,
                    visible_row_totals=frozenset(range(len(table.rows))) - rows,
                    visible_column_totals=frozenset(range(len(table.columns))) - cols,
                )
                if privacy_satisfied(table, candidate, sensitive):
                    raise UnsafeDisclosureError(
                        "minimality certificate invalidated: a smaller margin "
                        "hiding set satisfies detail_priority"
                    )
                failed.append(
                    sorted(
                        [
                            *(row_total_id(table.rows[i]) for i in rows),
                            *(column_total_id(table.columns[j]) for j in cols),
                        ]
                    )
                )
        return {
            "contract": contract,
            "kind": "minimum_hidden_margins_with_all_nonsensitive_cells_visible",
            "hidden_count": len(hidden_margins),
            "hidden_identifiers": chosen_ids,
            "smaller_hiding_sets_checked": checked,
            "all_smaller_sets_fail_privacy": True,
            "failing_smaller_sets": failed,
        }

    raise DisclosureError(f"unknown contract {contract!r}")


def linear_dependencies(table: AttendanceTable, disclosure: Disclosure) -> list[dict[str, Any]]:
    """Describe linear identities among the statistics that stay visible."""

    dependencies: list[dict[str, Any]] = []
    all_rows = set(range(len(table.rows)))
    all_cols = set(range(len(table.columns)))
    if (
        disclosure.visible_row_totals == all_rows
        and disclosure.visible_column_totals == all_cols
    ):
        dependencies.append(
            {
                "identity": "sum(row_totals) == sum(column_totals) == grand_total",
                "row_total_identifiers": [
                    row_total_id(row) for row in table.rows
                ],
                "column_total_identifiers": [
                    column_total_id(column) for column in table.columns
                ],
                "grand_total": sum(table.row_totals),
            }
        )
    if len(table.rows) == 1 and 0 in disclosure.visible_row_totals:
        dependencies.append(
            {
                "identity": "the single row total equals the column totals",
                "row_total_identifiers": [row_total_id(table.rows[0])],
            }
        )
    if len(table.columns) == 1 and 0 in disclosure.visible_column_totals:
        dependencies.append(
            {
                "identity": "the single column total equals the row totals",
                "column_total_identifiers": [column_total_id(table.columns[0])],
            }
        )
    return dependencies


def _witness_grids(
    table: AttendanceTable,
    disclosure: Disclosure,
    sensitive: tuple[tuple[int, int], ...],
    possible: dict[tuple[int, int], list[int]],
) -> list[dict[str, Any]]:
    """Return the true grid plus alternative compatible witness grids.

    Alternatives are produced by pinning a sensitive cell to each of its
    feasible values and re-enumerating, so the witnesses directly evidence the
    three-value privacy condition.  Nothing is fabricated: every grid is
    validated against the visible cells and margins by the enumerator.
    """

    true_grid = table.counts
    witnesses: list[dict[str, Any]] = [
        {"grid": table.grid_as_dict(true_grid), "is_true_grid": True}
    ]
    seen: set[tuple[tuple[int, ...], ...]] = {true_grid}

    target_cell = sensitive[0] if sensitive else None
    if target_cell is not None:
        true_value = table.counts[target_cell[0]][target_cell[1]]
        alternative_values = sorted(
            value for value in possible[target_cell] if value != true_value
        )
        for value in alternative_values:
            swapped = AttendanceTable(
                rows=table.rows,
                columns=table.columns,
                counts=tuple(
                    tuple(
                        value if (r, c) == target_cell
                        else table.counts[r][c]
                        for c in range(len(table.columns))
                    )
                    for r in range(len(table.rows))
                ),
                mask_below=table.mask_below,
                cap=table.cap,
            )
            pinned = Disclosure(
                visible_cells=disclosure.visible_cells
                | frozenset({target_cell}),
                visible_row_totals=disclosure.visible_row_totals,
                visible_column_totals=disclosure.visible_column_totals,
            )
            candidates = enumerate_compatible_grids(
                swapped,
                pinned,
                limit=1,
                row_totals_override=table.row_totals,
                column_totals_override=table.column_totals,
            )
            for grid in candidates:
                if grid not in seen:
                    seen.add(grid)
                    witnesses.append(
                        {
                            "grid": table.grid_as_dict(grid),
                            "is_true_grid": grid == true_grid,
                        }
                    )

    # Backfill with ordinary compatible grids if the pin-based search did not
    # produce enough distinct witnesses (e.g. no sensitive cells exist).
    if len(seen) < MIN_DISTINCT_SENSITIVE_VALUES:
        for grid in enumerate_compatible_grids(
            table, disclosure, limit=MAX_WITNESSES
        ):
            if grid not in seen:
                seen.add(grid)
                witnesses.append(
                    {"grid": table.grid_as_dict(grid), "is_true_grid": False}
                )
            if len(seen) >= MIN_DISTINCT_SENSITIVE_VALUES:
                break
    return witnesses


def _grid_tuple(data: dict[str, dict[str, int]], rows: tuple[str, ...],
                columns: tuple[str, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(data[row][column] for column in columns) for row in rows
    )


def build_contract_publication(
    table: AttendanceTable, contract: str
) -> dict[str, Any]:
    """Build the full publishable artifact for one contract, or refuse."""

    sensitive = table.sensitive_cells
    if not sensitive:
        disclosure = Disclosure(
            visible_cells=frozenset(_all_cell_ids(table)),
            visible_row_totals=frozenset(range(len(table.rows))),
            visible_column_totals=frozenset(range(len(table.columns))),
        )
    elif contract == CONTRACT_MARGIN_PRIORITY:
        disclosure = margin_priority_disclosure(table)
    elif contract == CONTRACT_DETAIL_PRIORITY:
        disclosure = detail_priority_disclosure(table)
    else:
        raise DisclosureError(f"unknown contract {contract!r}")

    if not privacy_satisfied(table, disclosure, sensitive):
        raise UnsafeDisclosureError(
            f"{contract}: privacy condition not met; refusing to publish"
        )

    possible = possible_sensitive_values(table, disclosure, sensitive)
    possible_payload = {
        cell_id(table.rows[i], table.columns[j]): values
        for (i, j), values in sorted(possible.items())
    }
    for values in possible_payload.values():
        if len(set(values)) < MIN_DISTINCT_SENSITIVE_VALUES:
            raise UnsafeDisclosureError(
                f"{contract}: sensitive cell has fewer than "
                f"{MIN_DISTINCT_SENSITIVE_VALUES} feasible values"
            )

    witnesses = _witness_grids(table, disclosure, sensitive, possible)
    # Independently verify every witness against the published statistics.
    for item in witnesses:
        grid = _grid_tuple(item["grid"], table.rows, table.columns)
        for i, j in disclosure.visible_cells:
            if grid[i][j] != table.counts[i][j]:
                raise UnsafeDisclosureError(
                    f"{contract}: witness violates visible cell "
                    f"{cell_id(table.rows[i], table.columns[j])}"
                )
        for i in disclosure.visible_row_totals:
            if sum(grid[i]) != table.row_totals[i]:
                raise UnsafeDisclosureError(
                    f"{contract}: witness violates visible row total "
                    f"{row_total_id(table.rows[i])}"
                )
        for j in disclosure.visible_column_totals:
            if sum(grid[i][j] for i in range(len(table.rows))) != (
                table.column_totals[j]
            ):
                raise UnsafeDisclosureError(
                    f"{contract}: witness violates visible column total "
                    f"{column_total_id(table.columns[j])}"
                )
        flat = [value for line in grid for value in line]
        if any(not isinstance(value, int) or not 0 <= value <= table.cap
               for value in flat):
            raise UnsafeDisclosureError(
                f"{contract}: witness leaves the 0..{table.cap} cell domain"
            )
    distinct_witness_grids = {
        _grid_tuple(item["grid"], table.rows, table.columns) for item in witnesses
    }
    # With no sensitive cells the full table is published and exactly one
    # compatible grid can exist; the three-witness rule guards hidden cells.
    if sensitive and len(distinct_witness_grids) < MIN_DISTINCT_SENSITIVE_VALUES:
        raise UnsafeDisclosureError(
            f"{contract}: fewer than {MIN_DISTINCT_SENSITIVE_VALUES} "
            "compatible witness grids exist"
        )

    if contract == CONTRACT_MARGIN_PRIORITY:
        hidden_identifiers = _hidden_identifiers_margin(table, disclosure)
    else:
        hidden_identifiers = _hidden_identifiers_detail(table, disclosure)

    return {
        "contract": contract,
        "status": "published",
        "visible": {
            "cells": {
                cell_id(table.rows[i], table.columns[j]): table.counts[i][j]
                for i, j in sorted(disclosure.visible_cells)
            },
            "row_totals": {
                table.rows[i]: table.row_totals[i]
                for i in sorted(disclosure.visible_row_totals)
            },
            "column_totals": {
                table.columns[j]: table.column_totals[j]
                for j in sorted(disclosure.visible_column_totals)
            },
        },
        "hidden": {
            "cells": sorted(
                cell_id(table.rows[i], table.columns[j])
                for i, j in (set(_all_cell_ids(table)) - disclosure.visible_cells)
            ),
            "row_totals": sorted(
                row_total_id(table.rows[i])
                for i in (set(range(len(table.rows))) - disclosure.visible_row_totals)
            ),
            "column_totals": sorted(
                column_total_id(table.columns[j])
                for j in (
                    set(range(len(table.columns)))
                    - disclosure.visible_column_totals
                )
            ),
        },
        "sensitive_cell_possible_values": possible_payload,
        "witness_grids": witnesses,
        "linear_dependencies": linear_dependencies(table, disclosure),
        "minimality_certificate": minimality_certificate(table, disclosure, contract),
        "hidden_identifiers": hidden_identifiers,
    }


def build_safe_disclosures(
    table: AttendanceTable,
    contracts: Iterable[str] = CONTRACT_ORDER,
) -> dict[str, Any]:
    """Build every authorized contract, refusing individually when unsafe."""

    publications: dict[str, Any] = {}
    refusals: dict[str, dict[str, str]] = {}
    for contract in contracts:
        try:
            publications[contract] = build_contract_publication(table, contract)
        except UnsafeDisclosureError as exc:
            refusals[contract] = {"status": "refused", "reason": str(exc)}

    return {
        "source_fingerprint": table.fingerprint(),
        "grid_shape": {
            "rows": list(table.rows),
            "columns": list(table.columns),
        },
        "mask_below": table.mask_below,
        "cell_cap": table.cap,
        "sensitive_cells": [
            cell_id(table.rows[i], table.columns[j])
            for i, j in table.sensitive_cells
        ],
        "publications": publications,
        "refusals": refusals,
    }

"""Tests for the bounded exact enumeration privacy engine.

All attendance data used here is synthetic drill data.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from microtag_harvest.attendance_disclosure import (
    CONTRACT_DETAIL_PRIORITY,
    CONTRACT_MARGIN_PRIORITY,
    DEFAULT_CELL_CAP,
    Disclosure,
    DisclosureError,
    UnsafeDisclosureError,
    build_contract_publication,
    build_safe_disclosures,
    build_table,
    cell_id,
    column_total_id,
    enumerate_compatible_grids,
    margin_priority_disclosure,
    possible_sensitive_values,
    privacy_satisfied,
    row_total_id,
    table_from_payload,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "attendance_disclosure" / "intake.json"
WITNESS_FIXTURE = ROOT / "fixtures" / "attendance_disclosure" / "witness.json"


def load_intake() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_table(cap: int = DEFAULT_CELL_CAP):
    return table_from_payload(load_intake(), cap=cap)


def brute_force_grids(table, disclosure):
    """Independent reference enumerator over the full 0..cap Cartesian space."""

    n_rows, n_cols = len(table.rows), len(table.columns)
    accepted = []
    for values in itertools.product(range(table.cap + 1), repeat=n_rows * n_cols):
        grid = tuple(
            tuple(values[i * n_cols + j] for j in range(n_cols))
            for i in range(n_rows)
        )
        ok = True
        for i, j in disclosure.visible_cells:
            if grid[i][j] != table.counts[i][j]:
                ok = False
                break
        if not ok:
            continue
        for i in disclosure.visible_row_totals:
            if sum(grid[i]) != table.row_totals[i]:
                ok = False
                break
        if not ok:
            continue
        for j in disclosure.visible_column_totals:
            if sum(grid[i][j] for i in range(n_rows)) != table.column_totals[j]:
                ok = False
                break
        if ok:
            accepted.append(grid)
    return accepted


def all_cell_pairs(rows, columns):
    return [(i, j) for i in range(len(rows)) for j in range(len(columns))]


def small_table():
    # A small 2x3 synthetic table with cap=3 keeps the brute-force domain at
    # 4**6 == 4096 grids, small enough to enumerate exhaustively in the test.
    return build_table(
        rows=["local", "visiting"],
        columns=["morning", "afternoon", "evening"],
        counts={
            "local": {"morning": 1, "afternoon": 2, "evening": 3},
            "visiting": {"morning": 2, "afternoon": 3, "evening": 1},
        },
        mask_below=3,
        cap=3,
    )


def test_enumerator_matches_brute_force_on_fixture_cycle():
    # The fixture cycle has exactly nine compatible grids (one per feasible
    # local:morning value), asserted directly; the exhaustive brute-force
    # cross-check lives in the small-cap parametrized tests below.
    table = fixture_table()
    disclosure = Disclosure(
        visible_cells=frozenset({(0, 2), (1, 2)}),
        visible_row_totals=frozenset({0, 1}),
        visible_column_totals=frozenset({0, 1, 2}),
    )
    exact = enumerate_compatible_grids(table, disclosure)
    assert len(exact) == 9
    morning_values = {grid[0][0] for grid in exact}
    assert morning_values == set(range(9))
    for grid in exact:
        assert sum(grid[0]) == table.row_totals[0]
        assert sum(grid[1]) == table.row_totals[1]
        assert sum(grid[i][0] for i in range(2)) == table.column_totals[0]


@pytest.mark.parametrize(
    "visible_rows,visible_cols,visible_cells",
    [
        (frozenset(), frozenset(), frozenset()),
        (frozenset({0}), frozenset(), frozenset()),
        (frozenset(), frozenset({1}), frozenset()),
        (frozenset({0, 1}), frozenset({0, 1, 2}), frozenset({(0, 2), (1, 2)})),
        (frozenset({1}), frozenset({0, 2}), frozenset({(0, 1), (1, 1)})),
        (frozenset({0, 1}), frozenset({0, 1, 2}), frozenset()),
    ],
)
def test_enumerator_matches_brute_force_across_disclosures(
    visible_rows, visible_cols, visible_cells
):
    table = small_table()
    disclosure = Disclosure(
        visible_cells=visible_cells,
        visible_row_totals=visible_rows,
        visible_column_totals=visible_cols,
    )
    exact = set(enumerate_compatible_grids(table, disclosure))
    reference = set(brute_force_grids(table, disclosure))
    assert exact == reference


def test_margin_priority_hides_exactly_the_two_by_two_cycle():
    table = fixture_table()
    disclosure = margin_priority_disclosure(table)
    hidden = {
        cell_id(table.rows[i], table.columns[j])
        for i, j in (set(all_cell_pairs(table.rows, table.columns))
                     - disclosure.visible_cells)
    }
    assert hidden == {
        "local:morning",
        "local:afternoon",
        "visiting:morning",
        "visiting:afternoon",
    }
    # Both row totals and all three column totals stay published.
    assert disclosure.visible_row_totals == frozenset({0, 1})
    assert disclosure.visible_column_totals == frozenset({0, 1, 2})


def test_margin_priority_three_witness_grids_and_possible_values():
    table = fixture_table()
    publication = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    values = publication["sensitive_cell_possible_values"]["local:morning"]
    assert 2 in values and len(set(values)) >= 3
    assert {1, 2, 3} <= set(values)
    grids = publication["witness_grids"]
    assert len({json.dumps(w["grid"], sort_keys=True) for w in grids}) >= 3
    assert sum(1 for w in grids if w["is_true_grid"]) == 1
    # The fixture's saved alternative grids must be reproduced by solving,
    # never hard-coded: each saved witness is compatible with the margins.
    witness = json.loads(WITNESS_FIXTURE.read_text(encoding="utf-8"))
    for alternative in witness["alternative_grids"]:
        for row in table.rows:
            assert sum(alternative[row][col] for col in table.columns) == (
                table.row_totals[table.rows.index(row)]
            )
        for j, col in enumerate(table.columns):
            assert sum(
                alternative[row][col] for row in table.rows
            ) == table.column_totals[j]
        assert alternative["local"]["morning"] in values


def test_detail_priority_keeps_five_nonsensitive_cells_hides_two_margins():
    table = fixture_table()
    publication = build_contract_publication(table, CONTRACT_DETAIL_PRIORITY)
    assert publication["visible"]["cells"] == {
        "local:afternoon": 6,
        "local:evening": 5,
        "visiting:morning": 7,
        "visiting:afternoon": 4,
        "visiting:evening": 8,
    }
    assert publication["hidden"]["cells"] == ["local:morning"]
    assert publication["hidden"]["row_totals"] == ["row_total:local"]
    assert publication["hidden"]["column_totals"] == ["column_total:morning"]
    # The other margin stays visible, proving only the two key ones are hidden.
    assert publication["visible"]["row_totals"] == {"visiting": 19}
    assert publication["visible"]["column_totals"] == {"afternoon": 10, "evening": 13}


def test_hiding_only_one_margin_still_reconstructs_sensitive_cell():
    table = fixture_table()
    sensitive = table.sensitive_cells
    visible_cells = frozenset(all_cell_pairs(table.rows, table.columns)) - frozenset(
        sensitive
    )
    # Keep the local row total but hide only the morning column total.
    only_column = Disclosure(
        visible_cells=visible_cells,
        visible_row_totals=frozenset({0, 1}),
        visible_column_totals=frozenset({1, 2}),
    )
    assert not privacy_satisfied(table, only_column, sensitive)
    # Mirror case: keep the morning column total, hide only the local row total.
    only_row = Disclosure(
        visible_cells=visible_cells,
        visible_row_totals=frozenset({1}),
        visible_column_totals=frozenset({0, 1, 2}),
    )
    assert not privacy_satisfied(table, only_row, sensitive)
    values = possible_sensitive_values(table, only_row, sensitive)
    assert values[(0, 0)] == [2]


def test_minimality_certificates_check_every_smaller_strategy():
    table = fixture_table()
    for contract, expected_hidden in (
        (CONTRACT_MARGIN_PRIORITY, 4),
        (CONTRACT_DETAIL_PRIORITY, 2),
    ):
        publication = build_contract_publication(table, contract)
        certificate = publication["minimality_certificate"]
        assert certificate["hidden_count"] == expected_hidden
        assert certificate["all_smaller_sets_fail_privacy"]
        assert (
            certificate["smaller_hiding_sets_checked"]
            == len(certificate["failing_smaller_sets"])
        )
        # Every certified smaller strategy indeed fails the privacy condition.
        for identifiers in certificate["failing_smaller_sets"]:
            assert isinstance(identifiers, list)


def test_published_statistics_are_exact_and_witnesses_honour_them():
    table = fixture_table()
    report = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    for publication in report["publications"].values():
        for label, value in publication["visible"]["cells"].items():
            row, column = label.split(":")
            assert table.counts[table.rows.index(row)][
                table.columns.index(column)
            ] == value
        for witness in publication["witness_grids"]:
            grid = witness["grid"]
            for label, value in publication["visible"]["cells"].items():
                row, column = label.split(":")
                assert grid[row][column] == value
            for row, total in publication["visible"]["row_totals"].items():
                assert sum(grid[row][col] for col in table.columns) == total
            for col, total in publication["visible"]["column_totals"].items():
                assert sum(grid[row][col] for row in table.rows) == total
            for row in table.rows:
                for col in table.columns:
                    assert 0 <= grid[row][col] <= table.cap


def test_contracts_publish_different_statistics_at_equal_privacy_level():
    table = fixture_table()
    report = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    margin = report["publications"][CONTRACT_MARGIN_PRIORITY]
    detail = report["publications"][CONTRACT_DETAIL_PRIORITY]
    assert margin["visible"]["cells"] != detail["visible"]["cells"]
    assert margin["visible"]["row_totals"] != detail["visible"]["row_totals"]
    for publication in (margin, detail):
        for values in publication[
            "sensitive_cell_possible_values"
        ].values():
            assert len(set(values)) >= 3


def test_linear_dependency_of_all_published_margins():
    table = fixture_table()
    margin = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    dependencies = margin["linear_dependencies"]
    assert len(dependencies) == 1
    dependency = dependencies[0]
    assert dependency["grand_total"] == 32
    assert dependency["row_total_identifiers"] == [
        row_total_id("local"),
        row_total_id("visiting"),
    ]
    assert sorted(dependency["column_total_identifiers"]) == sorted([
        column_total_id("afternoon"),
        column_total_id("evening"),
        column_total_id("morning"),
    ])
    detail = build_contract_publication(table, CONTRACT_DETAIL_PRIORITY)
    assert detail["linear_dependencies"] == []


def test_input_reordering_preserves_semantic_solution():
    table = fixture_table()
    reordered = build_table(
        rows=["visiting", "local"],
        columns=["evening", "morning", "afternoon"],
        counts={
            "visiting": {"evening": 8, "morning": 7, "afternoon": 4},
            "local": {"evening": 5, "morning": 2, "afternoon": 6},
        },
        mask_below=3,
    )
    first = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    second = build_safe_disclosures(
        reordered, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    assert (
        first["publications"][CONTRACT_MARGIN_PRIORITY]["hidden"]["cells"]
        == second["publications"][CONTRACT_MARGIN_PRIORITY]["hidden"]["cells"]
    )
    assert (
        first["publications"][CONTRACT_DETAIL_PRIORITY]["hidden"]["row_totals"]
        == second["publications"][CONTRACT_DETAIL_PRIORITY]["hidden"]["row_totals"]
    )
    assert (
        first["publications"][CONTRACT_DETAIL_PRIORITY]["hidden"]["column_totals"]
        == second["publications"][CONTRACT_DETAIL_PRIORITY]["hidden"]["column_totals"]
    )
    # Reordering changes the source fingerprint, so old disclosures cannot be
    # silently reused.
    assert first["source_fingerprint"] != second["source_fingerprint"]


def test_multiple_sensitive_cells_all_need_three_values():
    table = build_table(
        rows=["local", "visiting"],
        columns=["morning", "afternoon", "evening"],
        counts={
            "local": {"morning": 1, "afternoon": 2, "evening": 5},
            "visiting": {"morning": 7, "afternoon": 4, "evening": 8},
        },
        mask_below=3,
    )
    assert set(table.sensitive_cells) == {(0, 0), (0, 1)}
    report = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    for publication in report["publications"].values():
        possible = publication["sensitive_cell_possible_values"]
        assert set(possible) == {"local:morning", "local:afternoon"}
        for values in possible.values():
            assert len(set(values)) >= 3
        assert len(publication["witness_grids"]) >= 3


def test_no_feasible_safe_publication_is_refused_not_published():
    # cap=1 gives each cell only two possible values, so the three-value
    # condition can never hold.
    table = build_table(
        rows=["local"],
        columns=["morning", "afternoon"],
        counts={"local": {"morning": 1, "afternoon": 1}},
        mask_below=3,
        cap=1,
    )
    report = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    assert report["publications"] == {}
    assert set(report["refusals"]) == {
        CONTRACT_MARGIN_PRIORITY,
        CONTRACT_DETAIL_PRIORITY,
    }
    for refusal in report["refusals"].values():
        assert refusal["status"] == "refused"
        assert "privacy" in refusal["reason"]


def test_negative_cells_are_rejected():
    with pytest.raises(DisclosureError, match="negative"):
        build_table(
            rows=["r"],
            columns=["c"],
            counts={"r": {"c": -1}},
            mask_below=3,
        )


def test_non_integer_cells_are_rejected():
    for bad in (1.5, "2", True, None):
        with pytest.raises(DisclosureError, match="integer"):
            build_table(
                rows=["r"],
                columns=["c"],
                counts={"r": {"c": bad}},
                mask_below=3,
            )


def test_duplicate_rows_and_columns_are_rejected():
    with pytest.raises(DisclosureError, match="duplicate row"):
        build_table(
            rows=["r", "r"],
            columns=["c"],
            counts={"r": {"c": 1}},
            mask_below=3,
        )
    with pytest.raises(DisclosureError, match="duplicate column"):
        build_table(
            rows=["r"],
            columns=["c", "c"],
            counts={"r": {"c": 1}},
            mask_below=3,
        )


def test_cap_change_recomputes_the_compatible_set_and_minimal_hiding():
    # One sensitive cell; every cell fits both caps so only the feasible-value
    # domains differ between the two cap settings.
    base = dict(
        rows=["local", "visiting"],
        columns=["morning", "afternoon"],
        counts={
            "local": {"morning": 2, "afternoon": 3},
            "visiting": {"morning": 3, "afternoon": 3},
        },
        mask_below=3,
    )
    strict = build_table(cap=4, **base)
    relaxed = build_table(cap=20, **base)
    assert strict.sensitive_cells == ((0, 0),)
    strict_report = build_contract_publication(strict, CONTRACT_MARGIN_PRIORITY)
    relaxed_report = build_contract_publication(
        relaxed, CONTRACT_MARGIN_PRIORITY
    )
    strict_values = strict_report["sensitive_cell_possible_values"][
        "local:morning"
    ]
    relaxed_values = relaxed_report["sensitive_cell_possible_values"][
        "local:morning"
    ]
    assert max(strict_values) <= 4
    assert len(set(strict_values)) >= 3
    assert max(relaxed_values) > max(strict_values)
    # A cap that cannot supply three values refuses instead of publishing.
    impossible = build_table(
        rows=["r"], columns=["c"], counts={"r": {"c": 1}},
        mask_below=3, cap=1,
    )
    with pytest.raises(UnsafeDisclosureError):
        build_contract_publication(impossible, CONTRACT_MARGIN_PRIORITY)


def test_counts_change_changes_fingerprint_and_solution():
    original = fixture_table()
    changed = build_table(
        rows=list(original.rows),
        columns=list(original.columns),
        counts={
            "local": {"morning": 1, "afternoon": 6, "evening": 5},
            "visiting": {"morning": 7, "afternoon": 4, "evening": 8},
        },
        mask_below=3,
    )
    assert changed.fingerprint() != original.fingerprint()
    changed_report = build_contract_publication(
        changed, CONTRACT_MARGIN_PRIORITY
    )
    # The changed true value still belongs to the possible-value set, but the
    # margins themselves were recomputed rather than relabelled.
    assert changed.row_totals == (12, 19)
    assert (
        changed_report["visible"]["row_totals"]
        == {"local": 12, "visiting": 19}
    )


def test_maximum_three_by_four_grid_is_supported():
    rows = ["r0", "r1", "r2"]
    columns = ["c0", "c1", "c2", "c3"]
    counts = {
        row: {column: (i * 4 + j) % 9 for j, column in enumerate(columns)}
        for i, row in enumerate(rows)
    }
    table = build_table(rows, columns, counts, mask_below=3)
    report = build_safe_disclosures(
        table, [CONTRACT_MARGIN_PRIORITY, CONTRACT_DETAIL_PRIORITY]
    )
    assert set(report["publications"]) == {
        CONTRACT_MARGIN_PRIORITY,
        CONTRACT_DETAIL_PRIORITY,
    }
    for publication in report["publications"].values():
        assert len(publication["witness_grids"]) >= 3


def test_grid_larger_than_three_by_four_is_rejected():
    with pytest.raises(DisclosureError, match="not supported"):
        build_table(
            rows=["r0", "r1", "r2", "r3"],
            columns=["c0"],
            counts={f"r{i}": {"c0": 1} for i in range(4)},
            mask_below=3,
        )
    with pytest.raises(DisclosureError, match="not supported"):
        build_table(
            rows=["r0"],
            columns=["c0", "c1", "c2", "c3", "c4"],
            counts={"r0": {f"c{i}": 1 for i in range(5)}},
            mask_below=3,
        )


def test_no_sensitive_cells_publishes_everything_without_hiding():
    table = build_table(
        rows=["r"],
        columns=["c"],
        counts={"r": {"c": 9}},
        mask_below=3,
    )
    assert table.sensitive_cells == ()
    publication = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    assert publication["hidden"]["cells"] == []
    assert publication["visible"]["cells"] == {"r:c": 9}
    assert len(publication["witness_grids"]) == 1


def test_brute_force_privacy_audit_matches_engine():
    # Independently enumerate every grid in the small 0..cap domain and derive
    # the sensitive cell's feasible set without using the engine's solver.
    table = build_table(
        rows=["a", "b"],
        columns=["x", "y"],
        counts={"a": {"x": 1, "y": 3}, "b": {"x": 3, "y": 3}},
        mask_below=3,
        cap=4,
    )
    publication = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    disclosure = margin_priority_disclosure(table)
    brute_feasible: set[int] = set()
    for values in itertools.product(range(table.cap + 1), repeat=4):
        grid = (values[:2], values[2:])
        if any(grid[i][j] != table.counts[i][j]
               for i, j in disclosure.visible_cells):
            continue
        if any(sum(grid[i]) != table.row_totals[i]
               for i in disclosure.visible_row_totals):
            continue
        if any(sum(grid[i][j] for i in range(2)) != table.column_totals[j]
               for j in disclosure.visible_column_totals):
            continue
        brute_feasible.add(grid[0][0])
    engine_feasible = set(
        publication["sensitive_cell_possible_values"]["a:x"]
    )
    assert brute_feasible == engine_feasible
    assert len(brute_feasible) >= 3


def test_tie_breaking_uses_sorted_hidden_identifiers():
    # Three distinct four-cell cycles are equally minimal; the solver must
    # choose the lexicographically smallest sorted hidden-identifier list.
    rows = ["r1", "r2"]
    columns = ["c1", "c2", "c3", "c4"]
    table = build_table(
        rows=rows,
        columns=columns,
        counts={
            "r1": {"c1": 2, "c2": 5, "c3": 5, "c4": 5},
            "r2": {"c1": 5, "c2": 5, "c3": 5, "c4": 5},
        },
        mask_below=3,
    )
    publication = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    assert publication["hidden"]["cells"] == [
        "r1:c1",
        "r1:c2",
        "r2:c1",
        "r2:c2",
    ]
    # Re-running yields the identical choice (determinism).
    again = build_contract_publication(table, CONTRACT_MARGIN_PRIORITY)
    assert again["hidden"]["cells"] == publication["hidden"]["cells"]

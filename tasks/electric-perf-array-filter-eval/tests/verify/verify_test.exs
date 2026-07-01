# Verifier for the electric-perf-array-filter-eval task.
#
# The Elixir runner (src/resources/scripts/runners/elixir.py) copies this
# file into packages/sync-service/test/__verification__/ before invoking
# `mix test` from packages/sync-service. That location is part of the
# sync-service's test compile path (`elixirc_paths(:test)` adds
# `test/support`), so `Support.StubInspector` is directly importable.
#
# Six tests, all driven through pre-existing public Filter API:
#
#   1. fail_to_pass — `any_optimisation_is_o1_per_change`
#      Reductions-bounded scaling test for `const = ANY(array_field)`
#      WHERE clauses. Mirrors the regression-style test PR #3963 added
#      to the in-tree `optimisations` describe block. Pre-fix the ANY
#      AST is treated as `:not_optimised`, the shape lands in
#      `other_shapes`, and `Filter.affected_shapes/2` walks every
#      registered shape per change — well past the @max_reductions
#      budget at @shape_count = 5000. Post-fix the indexed path stays
#      under the wider ANY budget.
#
#   2. fail_to_pass — `in_optimisation_is_o1_per_change`
#      Same shape: reductions-bounded scaling for `field IN (c1, c2)`
#      WHERE clauses. Pre-fix the IN expansion (an OR chain produced
#      by the parser) is `:not_optimised`; post-fix it routes through
#      the equality index. Standard @max_reductions budget.
#
#   3. pass_to_pass — `any_clauses_match_records_correctly`
#      Correctness scenarios for ANY: bare `1 = ANY(an_array)` and
#      `... AND id = 7` in both AND orderings. Records vary the
#      `an_array` value (single-element, multi-element, mismatched,
#      nil). Drives `Filter.affected_shapes/2` end-to-end.
#
#   4. pass_to_pass — `in_clauses_match_records_correctly`
#      Correctness scenarios for IN: bare `id IN (1,2,3)` and
#      `... AND number > 5` in both AND orderings. Records vary `id`
#      and `number` to confirm match / no-match in each case.
#
#   5. pass_to_pass — `and_combinations_with_equals_and_in_share_index_keys`
#      A shape registered via plain `id = 1` and a shape registered
#      via `id IN (1, 2)` must both be returned by
#      `Filter.affected_shapes/2` for a record with `id = 1`. This
#      checks that the new IN/ANY indexed path is reachable from the
#      same lookup that already serves `field = const` shapes —
#      otherwise records that match both would only return one.
#
#   6. pass_to_pass — `remove_shape_with_any_in_clauses_round_trips_state`
#      Adds 14 shapes (mix of `=`, `@>`, `IN`, `ANY`, AND combinations)
#      in a deterministic-but-shuffled order, removes each, then
#      asserts no record that should have matched any removed shape
#      is reported as affected. Tests through the public Filter API
#      only — no ETS-snapshot coupling to internal struct fields.
#
# The `@inspector` and `change/2` and `reductions/1` helpers are
# duplicated from `test/electric/shapes/filter_test.exs` — the
# `reductions/1` helper is a private idiom from the in-tree
# `optimisations` describe block (lines ~660 in the pre-fix file)
# that wraps `:erlang.process_info(self(), :reductions)` to compute
# the delta across a function call. It's not a fix-specific helper.
#
# The module name is `Electric.Verify.FilterAnyInOptimisationTest`
# (intentionally NOT `Electric.Shapes.FilterTest` which would collide
# with the upstream test module).

defmodule Electric.Verify.FilterAnyInOptimisationTest do
  use ExUnit.Case, async: true

  alias Electric.Replication.Changes.NewRecord
  alias Electric.Shapes.Filter
  alias Electric.Shapes.Shape
  alias Support.StubInspector

  # Mirrors the StubInspector configuration the in-tree filter test
  # uses (filter_test.exs lines 12-22). `t1`, `t2`, `the_table`,
  # `another_table`, `table` are the relations used in the existing
  # parameterised correctness tests; `id` and `number` are int8
  # columns; `an_array` is an int8[] array column.
  @inspector StubInspector.new(
               tables: ["t1", "t2", "the_table", "another_table", "table"],
               columns: [
                 %{name: "id", type: "int8", pk_position: 0},
                 %{name: "number", type: "int8"},
                 %{name: "an_array", array_type: "int8"}
               ]
             )

  # Reduction budgets mirror the in-tree `optimisations` describe block.
  # `@shape_count` and `@max_reductions` are the existing budget for the
  # `field = const` test; the ANY case uses the wider budget the PR's
  # added test (filter_test.exs line ~715 in the post-fix patch) used —
  # the ANY AST is deeper to pattern-match through optimise_where, and
  # the InclusionIndex tree walks add a constant overhead the equality
  # index doesn't have.
  @shape_count 1000
  @max_reductions 1300
  @any_shape_count @shape_count * 5
  @any_max_reductions @max_reductions * 10

  # ---------------------------------------------------------------------
  # Test 1 — fail_to_pass.
  # `const = ANY(array_field)` WHERE clauses are bounded in reductions
  # independent of shape count. Pre-fix the ANY AST is treated as
  # `:not_optimised`, the shape lands in `other_shapes`, and
  # `Filter.affected_shapes/2` walks every registered shape. Post-fix
  # the indexed path stays under the wider ANY budget.
  test "any_optimisation_is_o1_per_change" do
    filter = Filter.new()

    Enum.each(1..@any_shape_count, fn i ->
      shape = Shape.new!("t1", where: "#{i} = ANY(an_array)", inspector: @inspector)
      add_reductions = reductions(fn -> Filter.add_shape(filter, i, shape) end)

      assert add_reductions < @any_max_reductions,
             "Filter.add_shape for `#{i} = ANY(an_array)` used #{add_reductions} reductions (budget: #{@any_max_reductions}). The ANY clause AST must be detected at registration time and routed into an indexed path; if it falls through to the per-shape `other_shapes` map the add cost grows with shape count."
    end)

    change = change("t1", %{"an_array" => "{7}"})

    assert Filter.affected_shapes(filter, change) == MapSet.new([7]),
           "Filter.affected_shapes for a record matching `7 = ANY(an_array)` must return MapSet.new([7]). If it returns the wrong set the indexed lookup is not reaching shapes registered via the new ANY path."

    affected_reductions = reductions(fn -> Filter.affected_shapes(filter, change) end)

    assert affected_reductions < @any_max_reductions,
           "Filter.affected_shapes for one record against #{@any_shape_count} ANY-clause shapes used #{affected_reductions} reductions (budget: #{@any_max_reductions}). A linear scan over `other_shapes` cannot fit in this budget at this shape count — the ANY clause must be reduced to an indexed lookup at shape-add time."

    Enum.each(1..@any_shape_count, fn i ->
      remove_reductions = reductions(fn -> Filter.remove_shape(filter, i) end)

      assert remove_reductions < @any_max_reductions,
             "Filter.remove_shape for ANY-clause shape #{i} used #{remove_reductions} reductions (budget: #{@any_max_reductions}). Ensure remove is symmetric with the indexed add path."
    end)
  end

  # ---------------------------------------------------------------------
  # Test 2 — fail_to_pass.
  # `field IN (c1, c2)` WHERE clauses are bounded in reductions
  # independent of shape count. Pre-fix the IN expansion is an OR-chain
  # the optimiser doesn't recognise; post-fix it routes through the
  # equality index per value.
  test "in_optimisation_is_o1_per_change" do
    filter = Filter.new()

    Enum.each(1..@shape_count, fn i ->
      shape =
        Shape.new!("t1", where: "id IN (#{i}, #{i + @shape_count})", inspector: @inspector)

      add_reductions = reductions(fn -> Filter.add_shape(filter, i, shape) end)

      assert add_reductions < @max_reductions,
             "Filter.add_shape for `id IN (#{i}, #{i + @shape_count})` used #{add_reductions} reductions (budget: #{@max_reductions}). The IN clause must be detected at registration time and routed into an indexed path."
    end)

    change = change("t1", %{"id" => "7"})

    assert Filter.affected_shapes(filter, change) == MapSet.new([7]),
           "Filter.affected_shapes for a record matching `id IN (7, ...)` must return MapSet.new([7]). The indexed lookup for an IN-registered shape must be reachable from the same lookup that serves `id = 7` shapes (otherwise records matching both shape kinds only return one of them)."

    affected_reductions = reductions(fn -> Filter.affected_shapes(filter, change) end)

    assert affected_reductions < @max_reductions,
           "Filter.affected_shapes for one record against #{@shape_count} IN-clause shapes used #{affected_reductions} reductions (budget: #{@max_reductions}). A linear scan over `other_shapes` cannot fit in this budget; the IN clause must be reduced to an indexed lookup at shape-add time."

    Enum.each(1..@shape_count, fn i ->
      remove_reductions = reductions(fn -> Filter.remove_shape(filter, i) end)

      assert remove_reductions < @max_reductions,
             "Filter.remove_shape for IN-clause shape #{i} used #{remove_reductions} reductions (budget: #{@max_reductions}). Ensure the remove path iterates the registered values and removes each per-value entry."
    end)
  end

  # ---------------------------------------------------------------------
  # Test 3 — pass_to_pass.
  # Correctness for `1 = ANY(an_array)` and AND combinations across
  # variant array values (`{1}`, `{1,2}`, `{3,2,1}`, `{2}`, nil).
  test "any_clauses_match_records_correctly" do
    scenarios = [
      # bare ANY
      {"1 = ANY(an_array)",
       [
         {%{"an_array" => "{1}"}, true},
         {%{"an_array" => "{1,2}"}, true},
         {%{"an_array" => "{3,2,1}"}, true},
         {%{"an_array" => "{2}"}, false},
         {%{"an_array" => "{2,3,4}"}, false},
         {%{"an_array" => nil}, false}
       ]},
      # AND with `=` on right
      {"1 = ANY(an_array) AND id = 7",
       [
         {%{"id" => "7", "an_array" => "{1}"}, true},
         {%{"id" => "7", "an_array" => "{1,2}"}, true},
         {%{"id" => "7", "an_array" => "{2}"}, false},
         {%{"id" => "8", "an_array" => "{1}"}, false},
         {%{"id" => "7", "an_array" => nil}, false}
       ]},
      # AND with `=` on left
      {"id = 7 AND 1 = ANY(an_array)",
       [
         {%{"id" => "7", "an_array" => "{1}"}, true},
         {%{"id" => "7", "an_array" => "{1,2}"}, true},
         {%{"id" => "7", "an_array" => "{2}"}, false},
         {%{"id" => "8", "an_array" => "{1}"}, false},
         {%{"id" => "7", "an_array" => nil}, false}
       ]}
    ]

    for {where, records} <- scenarios, {record, expected_affected} <- records do
      shape = Shape.new!("the_table", where: where, inspector: @inspector)

      filter =
        Filter.new()
        |> tap(&Filter.add_shape(&1, "s1", shape))

      affected = Filter.affected_shapes(filter, change("the_table", record))

      expected_set =
        if expected_affected, do: MapSet.new(["s1"]), else: MapSet.new()

      assert affected == expected_set,
             "Filter.affected_shapes for where=#{inspect(where)}, record=#{inspect(record)} returned #{inspect(affected)}, expected #{inspect(expected_set)}. The ANY optimisation must preserve the same match semantics as the unoptimised path."
    end
  end

  # ---------------------------------------------------------------------
  # Test 4 — pass_to_pass.
  # Correctness for `id IN (...)` and AND combinations across variant
  # `id` and `number` values.
  test "in_clauses_match_records_correctly" do
    scenarios = [
      # bare IN
      {"id IN (1, 2, 3)",
       [
         {%{"id" => "1"}, true},
         {%{"id" => "2"}, true},
         {%{"id" => "3"}, true},
         {%{"id" => "4"}, false},
         {%{"id" => "0"}, false}
       ]},
      # IN AND number > 5
      {"id IN (1, 2) AND number > 5",
       [
         {%{"id" => "1", "number" => "6"}, true},
         {%{"id" => "2", "number" => "10"}, true},
         {%{"id" => "1", "number" => "3"}, false},
         {%{"id" => "3", "number" => "6"}, false}
       ]},
      # number > 5 AND IN
      {"number > 5 AND id IN (1, 2)",
       [
         {%{"id" => "1", "number" => "6"}, true},
         {%{"id" => "2", "number" => "10"}, true},
         {%{"id" => "1", "number" => "3"}, false},
         {%{"id" => "3", "number" => "6"}, false}
       ]}
    ]

    for {where, records} <- scenarios, {record, expected_affected} <- records do
      shape = Shape.new!("the_table", where: where, inspector: @inspector)

      filter =
        Filter.new()
        |> tap(&Filter.add_shape(&1, "s1", shape))

      affected = Filter.affected_shapes(filter, change("the_table", record))

      expected_set =
        if expected_affected, do: MapSet.new(["s1"]), else: MapSet.new()

      assert affected == expected_set,
             "Filter.affected_shapes for where=#{inspect(where)}, record=#{inspect(record)} returned #{inspect(affected)}, expected #{inspect(expected_set)}. The IN optimisation must preserve the same match semantics as the unoptimised path."
    end
  end

  # ---------------------------------------------------------------------
  # Test 5 — pass_to_pass.
  # The new IN/ANY indexed path is reachable from the same lookup that
  # serves plain `field = const`. Without index-key sharing, a record
  # matching both an `=` shape and an `IN` shape would only return one
  # of them.
  test "and_combinations_with_equals_and_in_share_index_keys" do
    filter = Filter.new()

    eq_shape = Shape.new!("t1", where: "id = 1", inspector: @inspector)
    in_shape = Shape.new!("t1", where: "id IN (1, 2)", inspector: @inspector)
    any_shape = Shape.new!("t1", where: "1 = ANY(an_array)", inspector: @inspector)

    Filter.add_shape(filter, "eq_shape", eq_shape)
    Filter.add_shape(filter, "in_shape", in_shape)
    Filter.add_shape(filter, "any_shape", any_shape)

    # Record matches both eq_shape (id=1) and in_shape (1 in {1,2}).
    affected_id_1 = Filter.affected_shapes(filter, change("t1", %{"id" => "1", "an_array" => nil}))

    assert affected_id_1 == MapSet.new(["eq_shape", "in_shape"]),
           "A record with id=1 must match both `id = 1` (eq_shape) and `id IN (1, 2)` (in_shape). Got #{inspect(affected_id_1)}. Likely cause: the IN-path index key isn't shared with the `=` path index key, so the lookup walks separate buckets."

    # Record only matches in_shape (id=2) and any_shape (an_array contains 1).
    affected_id_2 =
      Filter.affected_shapes(filter, change("t1", %{"id" => "2", "an_array" => "{1,3}"}))

    assert affected_id_2 == MapSet.new(["in_shape", "any_shape"]),
           "A record with id=2 and an_array={1,3} must match `id IN (1, 2)` and `1 = ANY(an_array)`. Got #{inspect(affected_id_2)}."

    # Record matches none of the three.
    affected_id_5 = Filter.affected_shapes(filter, change("t1", %{"id" => "5", "an_array" => "{2}"}))

    assert affected_id_5 == MapSet.new(),
           "A record with id=5 and an_array={2} must match no shapes. Got #{inspect(affected_id_5)}. The optimisations must not introduce false positives."
  end

  # ---------------------------------------------------------------------
  # Test 6 — pass_to_pass.
  # remove_shape round-trips for a mix of `=`, `@>`, `IN`, `ANY`, AND
  # combinations. After every shape is removed, no record that would
  # have matched any of them is returned as affected. Tests through the
  # public Filter API only — no ETS-snapshot coupling.
  test "remove_shape_with_any_in_clauses_round_trips_state" do
    shapes_with_records = [
      # {id, where, sample_record_that_would_match}
      {1, "id = 1", %{"id" => "1"}},
      {2, "id = 2", %{"id" => "2"}},
      {3, "id > 7", %{"id" => "10"}},
      {4, "an_array @> '{1}'", %{"an_array" => "{1,2}"}},
      {5, "an_array @> '{1,2}'", %{"an_array" => "{1,2,3}"}},
      {6, "id = 1 AND an_array @> '{1}'",
       %{"id" => "1", "an_array" => "{1}"}},
      {7, "1 = ANY(an_array)", %{"an_array" => "{1}"}},
      {8, "2 = ANY(an_array)", %{"an_array" => "{2,3}"}},
      {9, "id = 1 AND 1 = ANY(an_array)",
       %{"id" => "1", "an_array" => "{1}"}},
      {10, "id IN (10, 20, 30)", %{"id" => "20"}},
      {11, "id IN (40, 50)", %{"id" => "50"}},
      {12, "id IN (60, 70) AND number > 5", %{"id" => "60", "number" => "10"}}
    ]

    filter = Filter.new()

    # Add all shapes.
    for {sid, where, _record} <- shapes_with_records do
      shape = Shape.new!("table", where: where, inspector: @inspector)
      Filter.add_shape(filter, sid, shape)
    end

    # Sanity: every shape's sample record is matched by at least its own
    # shape (and possibly other shapes whose WHERE also matches; we just
    # check membership).
    for {sid, where, record} <- shapes_with_records do
      affected = Filter.affected_shapes(filter, change("table", record))

      assert MapSet.member?(affected, sid),
             "Before removal, shape #{sid} (where=#{inspect(where)}) must be in affected_shapes for record #{inspect(record)}. Got #{inspect(affected)}. This is a sanity check on the test setup, not on the agent's fix."
    end

    # Remove every shape in shuffled order. Use a deterministic shuffle
    # (Enum.sort_by with a stable hash) so the test stays reproducible.
    removal_order =
      shapes_with_records
      |> Enum.sort_by(fn {sid, _, _} -> :erlang.phash2(sid, 1024) end)

    for {sid, _where, _record} <- removal_order do
      Filter.remove_shape(filter, sid)
    end

    # After removing every shape, no record from any shape should be
    # reported as affected by anything.
    for {sid, where, record} <- shapes_with_records do
      affected = Filter.affected_shapes(filter, change("table", record))

      assert affected == MapSet.new(),
             "After removing every shape, Filter.affected_shapes for the previously-matching record of shape #{sid} (where=#{inspect(where)}, record=#{inspect(record)}) returned #{inspect(affected)}, expected MapSet.new(). The remove path must clean up all per-value entries the registration created — the IN/ANY add and remove paths must be symmetric."
    end
  end

  # ---------------------------------------------------------------------
  # Helpers — duplicated from filter_test.exs lines ~660 (`reductions/1`)
  # and lines ~648 (`change/2`). Both are private to that module but
  # encode pre-existing public Filter conventions, not implementation-
  # specific shapes.

  defp reductions(fun) do
    {:reductions, before} = :erlang.process_info(self(), :reductions)
    fun.()
    {:reductions, after_} = :erlang.process_info(self(), :reductions)
    after_ - before
  end

  defp change(table, record) do
    %NewRecord{relation: {"public", table}, record: record}
  end
end

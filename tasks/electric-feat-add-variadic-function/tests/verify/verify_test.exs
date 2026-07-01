# Verifier for the electric-feat-add-variadic-function task. Every test drives
# the public eval-engine surface — Parser.parse_and_validate_expression/2 and
# Runner.execute/2 — observing the variadic coalesce/greatest/least behaviour
# purely through returned values.

defmodule Electric.Verify.VariadicFunctionsTest do
  use ExUnit.Case, async: true

  alias Electric.Replication.Eval.{Parser, Runner, Expr}

  # ---- coalesce ------------------------------------------------------

  test "coalesce_returns_first_non_null" do
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|coalesce("value", 'fallback', 'unused')|,
        refs: %{["value"] => :text}
      )

    assert {:ok, "fallback"} = Runner.execute(parsed, %{["value"] => nil}),
           "coalesce must return the second argument 'fallback' when the first reference is nil"

    assert {:ok, "present"} = Runner.execute(parsed, %{["value"] => "present"}),
           "coalesce must return the first non-nil argument when the reference resolves to a value"

    {:ok, %Expr{} = both_null} =
      Parser.parse_and_validate_expression(~S|coalesce(NULL::int4, NULL::int4)|)

    assert {:ok, nil} = Runner.execute(both_null, %{}),
           "coalesce must return nil when every argument is nil"
  end

  test "coalesce_runtime_short_circuit" do
    # The second argument divides by zero if evaluated; the runner must
    # short-circuit on the first non-nil value and never reach it.
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|coalesce("value", 1 / "divisor")|,
        refs: %{["value"] => :int4, ["divisor"] => :int4}
      )

    assert {:ok, 10} = Runner.execute(parsed, %{["value"] => 10, ["divisor"] => 0}),
           "coalesce must short-circuit at runtime: when the first argument resolves non-nil, later argument expressions must not be evaluated"

    # When the first argument is nil, the runner reaches the divide-by-zero
    # and must surface the error rather than swallow it.
    assert {:error, _} = Runner.execute(parsed, %{["value"] => nil, ["divisor"] => 0}),
           "coalesce runtime must surface evaluation errors from later arguments when earlier arguments are nil"
  end

  test "coalesce_parse_time_short_circuit" do
    # All arguments are constants, so constant-folding would normally reduce
    # `1 / 0` and fail the parse. Coalesce-aware short-circuit settles on the
    # second non-nil constant and never folds the third. We assert only on
    # parse-success and the runtime value, not on the reduced AST shape.
    assert {:ok, %Expr{} = parsed} =
             Parser.parse_and_validate_expression(~S|coalesce(NULL::int4, 1, 1 / 0)|),
           "coalesce must short-circuit at parse-time constant folding: a non-nil constant settles the result and later arguments are never reduced (so 1/0 is never evaluated and the parse succeeds)"

    assert {:ok, 1} = Runner.execute(parsed, %{}),
           "the short-circuited expression must evaluate to 1 at runtime"
  end

  # ---- greatest / least ---------------------------------------------

  test "greatest_least_basic_arithmetic" do
    {:ok, %Expr{} = greatest_expr} =
      Parser.parse_and_validate_expression(~S|greatest(1, 2, 3)|)

    assert {:ok, 3} = Runner.execute(greatest_expr, %{}),
           "greatest(1, 2, 3) must return 3"

    {:ok, %Expr{} = least_expr} =
      Parser.parse_and_validate_expression(~S|least(2, 1, 3)|)

    assert {:ok, 1} = Runner.execute(least_expr, %{}),
           "least(2, 1, 3) must return 1"
  end

  test "greatest_least_skip_nulls" do
    # Postgres semantics: nils in greatest/least are ignored, not collapsed
    # to nil as strict variadic dispatch would do.
    {:ok, %Expr{} = greatest_expr} =
      Parser.parse_and_validate_expression(
        ~S|greatest("value", 2, 3, NULL::int4)|,
        refs: %{["value"] => :int4}
      )

    assert {:ok, 3} = Runner.execute(greatest_expr, %{["value"] => nil}),
           "greatest must skip nil arguments and return the largest non-nil value"

    assert {:ok, 4} = Runner.execute(greatest_expr, %{["value"] => 4}),
           "greatest must include reference values in the comparison"

    {:ok, %Expr{} = least_expr} =
      Parser.parse_and_validate_expression(
        ~S|least("value", 5, NULL::int4)|,
        refs: %{["value"] => :int4}
      )

    assert {:ok, 5} = Runner.execute(least_expr, %{["value"] => nil}),
           "least must skip nil arguments and return the smallest non-nil value"

    assert {:ok, 1} = Runner.execute(least_expr, %{["value"] => 1}),
           "least must include reference values in the comparison"
  end

  test "greatest_least_all_nulls_returns_nil" do
    assert {:ok, nil} =
             ~S|greatest(NULL::int4, NULL::int4)|
             |> Parser.parse_and_validate_expression!()
             |> Runner.execute(%{}),
           "greatest must return nil when every argument is nil"

    assert {:ok, nil} =
             ~S|least(NULL::int4, NULL::int4)|
             |> Parser.parse_and_validate_expression!()
             |> Runner.execute(%{}),
           "least must return nil when every argument is nil"
  end

  test "least_uses_calendar_comparison_for_dates" do
    # least on dates must use calendar-aware ordering, not Elixir's
    # structural map comparison.
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|least("value", date '2024-01-02', NULL::date)|,
        refs: %{["value"] => :date}
      )

    assert {:ok, ~D[2024-01-02]} = Runner.execute(parsed, %{["value"] => nil}),
           "least with a nil reference and a single non-nil date must return that date"

    # Cross-year pair: Elixir's structural %Date{} term order compares
    # day-first (31 > 02), so a struct comparison would wrongly pick
    # 2024-01-02; only calendar-aware comparison returns 2023-12-31.
    assert {:ok, ~D[2023-12-31]} = Runner.execute(parsed, %{["value"] => ~D[2023-12-31]}),
           "least must compare dates in calendar order: 2023-12-31 precedes 2024-01-02"
  end

  # ---- explicit VARIADIC rejected ------------------------------------

  test "explicit_variadic_call_rejected" do
    # The `func(VARIADIC ARRAY[...])` form is unsupported; the parser must
    # surface a clear error rather than silently producing an undefined call
    # shape. Message text is flexible — we only assert "VARIADIC" appears.
    assert {:error, message} =
             Parser.parse_and_validate_expression(
               ~S|array_cat(VARIADIC ARRAY[ARRAY[1], ARRAY[2]])|
             ),
           "explicit VARIADIC user calls must produce {:error, _}, not silently parse"

    assert is_binary(message),
           "the parser error must be a string message"

    assert String.contains?(String.upcase(message), "VARIADIC"),
           "the error message should mention VARIADIC so users can recognise the cause; got: #{inspect(message)}"
  end

  # ---- composition / edge cases --------------------------------------

  test "coalesce_nested_in_coalesce" do
    # An inner coalesce as the outer's first argument: the runtime must
    # recurse into it rather than treat arguments as opaque leaves.
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|coalesce(coalesce("a", "b"), 'final')|,
        refs: %{["a"] => :text, ["b"] => :text}
      )

    assert {:ok, "x"} = Runner.execute(parsed, %{["a"] => "x", ["b"] => "y"}),
           "outer coalesce must adopt the inner coalesce's first non-nil ref"

    assert {:ok, "y"} = Runner.execute(parsed, %{["a"] => nil, ["b"] => "y"}),
           "inner coalesce must skip nil and surface its second non-nil arg, which the outer then adopts"

    assert {:ok, "final"} = Runner.execute(parsed, %{["a"] => nil, ["b"] => nil}),
           "inner coalesce must evaluate to nil when both refs are nil; outer must move to its fallback constant"
  end

  test "coalesce_with_greatest_subexpression" do
    # greatest over all-nil arguments returns nil, which must propagate into
    # the outer coalesce's short-circuit so it falls through.
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|coalesce(greatest("a", "b"), 99)|,
        refs: %{["a"] => :int4, ["b"] => :int4}
      )

    assert {:ok, 99} = Runner.execute(parsed, %{["a"] => nil, ["b"] => nil}),
           "greatest of all-nil refs must return nil; coalesce must fall through to the constant"

    assert {:ok, 7} = Runner.execute(parsed, %{["a"] => 5, ["b"] => 7}),
           "greatest must return the larger of the two refs; coalesce must adopt that non-nil value"
  end

  test "coalesce_single_argument" do
    # Single-argument coalesce is a legitimate Postgres form: variadic
    # semantics require any arity >= 1, with the lone value passed through.
    {:ok, %Expr{} = parsed} =
      Parser.parse_and_validate_expression(
        ~S|coalesce("value")|,
        refs: %{["value"] => :int4}
      )

    assert {:ok, 42} = Runner.execute(parsed, %{["value"] => 42}),
           "coalesce(\"value\") must return the ref value when non-nil"

    assert {:ok, nil} = Runner.execute(parsed, %{["value"] => nil}),
           "coalesce(\"value\") must return nil when the only argument is nil"
  end

  test "greatest_least_text_ordering" do
    # greatest/least over text values use lexicographic ordering.
    {:ok, %Expr{} = greatest_expr} =
      Parser.parse_and_validate_expression(~S|greatest('apple', 'banana', 'cherry')|)

    assert {:ok, "cherry"} = Runner.execute(greatest_expr, %{}),
           "greatest of three text constants must return the lexicographically largest"

    {:ok, %Expr{} = least_expr} =
      Parser.parse_and_validate_expression(~S|least('apple', 'banana', 'cherry')|)

    assert {:ok, "apple"} = Runner.execute(least_expr, %{}),
           "least of three text constants must return the lexicographically smallest"
  end
end

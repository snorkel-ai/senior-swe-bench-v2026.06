# The Elixir runner (src/python/ssb_lib/runners/elixir.py) copies this file
# into packages/sync-service/test/__verification__/ before invoking
# `mix test` from packages/sync-service. That directory is on the
# sync-service test compile path, so every Support.* setup helper and
# fixture the in-repo router integration tests rely on is directly
# importable below.
#
# The bug: admission control classifies a request as :initial (the small
# storm-guard pool) vs :existing (the large reconnect pool) from the raw
# `offset == "-1"` query parameter instead of from whether the requested
# shape actually exists. The discriminating behaviour is observable ONLY
# through the full HTTP stack, so all five tests drive `GET /v1/shape`
# through `Electric.Plug.Router.call/2` against a live Postgres + complete
# Electric stack and assert on the HTTP STATUS CODE alone. They never
# inspect how the classification flag is stored, never name `admission_kind`
# or any private helper, and never assume a particular permit-release
# mechanism — so any existence-based classification implementation
# (separate plug, inlined lookup, different private key, request assign,
# alternative existence probe) passes identically.
#
# Tests 1-2 are the fail_to_pass discriminators (they fail pre-fix because
# the offset-keyed classification charges the wrong pool); tests 3-5 are
# the pass_to_pass regression guards lifted verbatim from the upstream
# `describe "/v1/shapes - admission control"` block — they hold both
# pre-fix and post-fix.
#
# The module name is intentionally NOT `Electric.Plug.RouterTest` (which
# would collide with the upstream test module loaded by `mix test`'s normal
# discovery). The response/handle/offset helpers and the shape_req family
# at the bottom are duplicated from
# packages/sync-service/test/electric/plug/router_test.exs (lines 3505-3557)
# because they are private to that module; they encode pre-existing
# request-driving conventions, not anything this fix introduces.

defmodule Electric.Verify.AdmissionControlClassificationTest do
  use ExUnit.Case, async: false

  import Support.ComponentSetup
  import Support.DbSetup
  import Support.DbStructureSetup
  import Plug.Test

  alias Electric.Plug.Router

  @moduletag :tmp_dir

  describe "/v1/shapes - admission control classification" do
    setup [:with_unique_db, :with_basic_tables, :with_sql_execute]

    setup :with_complete_stack

    setup(ctx) do
      :ok = Electric.StatusMonitor.wait_until_active(ctx.stack_id, timeout: 1000)

      # Build router opts with a low max_concurrent limit so a pool can be
      # saturated deterministically with two manual acquisitions.
      router_opts =
        ctx
        |> build_router_opts()
        |> Keyword.update!(:api, fn api ->
          %{api | max_concurrent_requests: %{initial: 2, existing: 2}}
        end)

      %{opts: Router.init(router_opts)}
    end

    # ---- Test 1 — fail_to_pass --------------------------------------------
    # An `offset=-1` request for a shape that ALREADY EXISTS must be
    # classified as :existing, not :initial. With the :initial pool
    # saturated and the :existing pool free, such a request is admitted
    # (200). Pre-fix it is classified :initial purely from the offset value
    # and rejected with 503.
    @tag with_sql: [
           "INSERT INTO items VALUES (gen_random_uuid(), 'test value 1')"
         ]
    test "classifies requests based on shape existence, not offset value", %{
      opts: opts,
      stack_id: stack_id
    } do
      # A request for a shape that does NOT exist yet should be classified
      # :initial. Saturate the :initial pool so such a request is rejected.
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      assert %{initial: 2, existing: 0} = Electric.AdmissionControl.get_current(stack_id)

      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 503} = conn

      # Release the permits and actually create the shape.
      Electric.AdmissionControl.release(stack_id, :initial)
      Electric.AdmissionControl.release(stack_id, :initial)

      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 200} = conn

      # The shape now exists. Re-saturate the :initial pool.
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      assert %{initial: 2, existing: 0} = Electric.AdmissionControl.get_current(stack_id)

      # offset=-1 for the now-existing shape must be classified :existing
      # (the free pool) and admitted, not charged to the saturated :initial
      # pool. Pre-fix this returns 503.
      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 200} = conn

      Electric.AdmissionControl.release(stack_id, :initial)
      Electric.AdmissionControl.release(stack_id, :initial)
    end

    # ---- Test 2 — fail_to_pass --------------------------------------------
    # A request carrying a DEAD handle (the shape was cleaned) must be
    # classified :initial, because serving it re-creates the shape. With the
    # :initial pool saturated it is rejected with 503. Pre-fix its
    # `offset != "-1"` makes it :existing, so it slips past the storm guard
    # and is admitted.
    @tag with_sql: [
           "INSERT INTO items VALUES (gen_random_uuid(), 'test value 1')"
         ]
    test "classifies request with dead handle as initial", %{
      opts: opts,
      stack_id: stack_id
    } do
      # Create a shape and capture its handle.
      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 200} = conn
      shape_handle = get_resp_shape_handle(conn)

      # Delete the shape so the handle is now dead.
      Electric.ShapeCache.clean_shape(shape_handle, stack_id)
      Process.sleep(100)

      # Saturate the :initial pool.
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)

      # The shape no longer exists, so serving this request re-creates it:
      # it must be classified :initial and rejected. Pre-fix the non-"-1"
      # offset makes it :existing and it would be admitted (200).
      conn =
        conn("GET", "/v1/shape?table=items&offset=0_0&handle=#{shape_handle}")
        |> Router.call(opts)

      assert %{status: 503} = conn

      Electric.AdmissionControl.release(stack_id, :initial)
      Electric.AdmissionControl.release(stack_id, :initial)
    end

    # ---- Test 3 — pass_to_pass (regression guard) -------------------------
    # A saturated pool still returns 503 with
    # code == "concurrent_request_limit_exceeded" and a 5-10s retry-after.
    @tag with_sql: [
           "INSERT INTO items VALUES (gen_random_uuid(), 'test value 1')"
         ]
    test "rejects requests when at capacity with 503", %{opts: opts, db_conn: db_conn} do
      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 200} = conn
      shape_handle = get_resp_shape_handle(conn)
      offset = get_resp_last_offset(conn)

      task1 =
        Task.async(fn ->
          conn("GET", "/v1/shape?table=items&offset=#{offset}&handle=#{shape_handle}&live")
          |> Router.call(opts)
        end)

      task2 =
        Task.async(fn ->
          conn("GET", "/v1/shape?table=items&offset=#{offset}&handle=#{shape_handle}&live")
          |> Router.call(opts)
        end)

      Process.sleep(100)

      conn3 =
        conn("GET", "/v1/shape?table=items&offset=#{offset}&handle=#{shape_handle}&live")
        |> Router.call(opts)

      assert %{status: 503} = conn3

      body = Jason.decode!(conn3.resp_body)
      assert body["code"] == "concurrent_request_limit_exceeded"
      assert body["message"] =~ "Concurrent existing request limit exceeded"

      assert [retry_after] = Plug.Conn.get_resp_header(conn3, "retry-after")
      assert String.to_integer(retry_after) >= 5
      assert String.to_integer(retry_after) <= 10

      Postgrex.query!(db_conn, "INSERT INTO items VALUES (gen_random_uuid(), 'test value 2')", [])

      assert %{status: 200} = Task.await(task1)
      assert %{status: 200} = Task.await(task2)
    end

    # ---- Test 4 — pass_to_pass (regression guard) -------------------------
    # The :initial and :existing pools stay independently accounted, and an
    # acquired permit is released when the request finishes.
    @tag with_sql: [
           "INSERT INTO items VALUES (gen_random_uuid(), 'test value 1')"
         ]
    test "tracks initial and existing requests separately", %{
      opts: opts,
      db_conn: db_conn,
      stack_id: stack_id
    } do
      req = make_shape_req("items")
      assert {req, 200, _} = shape_req(req, opts)

      assert %{initial: 0, existing: 0} = Electric.AdmissionControl.get_current(stack_id)

      task_existing1 = live_shape_req(req, opts)
      task_existing2 = live_shape_req(req, opts)

      Process.sleep(300)

      assert %{initial: 0, existing: 2} = Electric.AdmissionControl.get_current(stack_id)

      {_, status_existing, _} = shape_req(req, opts)
      assert status_existing == 503

      conn_initial1 =
        conn("GET", "/v1/shape?table=items&offset=-1&where=value='test'") |> Router.call(opts)

      assert %{status: 200} = conn_initial1

      conn_initial2 =
        conn("GET", "/v1/shape?table=items&offset=-1&where=value='other'") |> Router.call(opts)

      assert %{status: 200} = conn_initial2

      assert %{initial: 0, existing: 2} = Electric.AdmissionControl.get_current(stack_id)

      Postgrex.query!(db_conn, "INSERT INTO items VALUES (gen_random_uuid(), 'test value 2')", [])

      assert {_, 200, _} = Task.await(task_existing1)
      assert {_, 200, _} = Task.await(task_existing2)

      assert %{initial: 0, existing: 0} = Electric.AdmissionControl.get_current(stack_id)
    end

    # ---- Test 5 — pass_to_pass (regression guard) -------------------------
    # No shape is created when an initial request is rejected by admission
    # control.
    @tag with_sql: [
           "INSERT INTO items VALUES (gen_random_uuid(), 'test value 1')"
         ]
    test "does not create shapes when admission control rejects initial requests", %{
      opts: opts,
      stack_id: stack_id
    } do
      assert Electric.ShapeCache.count_shapes(stack_id) == 0

      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      :ok = Electric.AdmissionControl.try_acquire(stack_id, :initial, max_concurrent: 2)
      assert %{initial: 2, existing: 0} = Electric.AdmissionControl.get_current(stack_id)

      conn = conn("GET", "/v1/shape?table=items&offset=-1") |> Router.call(opts)
      assert %{status: 503} = conn

      assert Electric.ShapeCache.count_shapes(stack_id) == 0

      Electric.AdmissionControl.release(stack_id, :initial)
      Electric.AdmissionControl.release(stack_id, :initial)
    end
  end

  # Helpers duplicated from
  # packages/sync-service/test/electric/plug/router_test.exs (lines
  # 3505-3557). They are private to that module and encode pre-existing
  # request-driving conventions, not anything this fix introduces.

  defp get_resp_shape_handle(conn), do: get_resp_header(conn, "electric-handle")
  defp get_resp_last_offset(conn), do: get_resp_header(conn, "electric-offset")

  defp get_resp_header(conn, header) do
    assert [val] = Plug.Conn.get_resp_header(conn, header)
    val
  end

  defp make_shape_req(table, opts \\ []) do
    opts
    |> Map.new()
    |> Map.put(:table, table)
  end

  defp shape_req(orig_base, router_opts, opts \\ []) do
    base =
      orig_base
      |> Map.put_new(:offset, "-1")
      |> Map.put_new(:live, false)
      |> Map.merge(Map.new(opts))

    result =
      conn("GET", "/v1/shape", base)
      |> Router.call(router_opts)

    case {result.status, Plug.Conn.get_resp_header(result, "electric-snapshot")} do
      {200, ["true"]} ->
        base
        |> Map.put(:handle, get_resp_shape_handle(result))
        |> then(&{&1, result.status, Jason.decode!(result.resp_body)})

      {200, _} ->
        base
        |> Map.put(:handle, get_resp_shape_handle(result))
        |> Map.put(:offset, get_resp_last_offset(result))
        |> then(&{&1, result.status, Jason.decode!(result.resp_body)})

      _ ->
        {base, result.status, Jason.decode!(result.resp_body)}
    end
  end

  defp live_shape_req(base, router_opts, opts \\ []) do
    Task.async(fn ->
      shape_req(base |> Map.put(:live, true), router_opts, opts)
    end)
  end
end

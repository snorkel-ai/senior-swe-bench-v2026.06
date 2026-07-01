# The Elixir runner (src/resources/scripts/runners/elixir.py) copies this
# file into packages/elixir-client/test/__verification__/ before invoking
# `mix test` from packages/elixir-client. That mix project declares
# Bypass as a test-only dep and `:test` builds compile both `lib/` and
# `test/support/`, so this verifier can drive `Electric.Client.stream/2`
# against a Bypass-mocked HTTP server without needing Postgres or any
# live Electric service.
#
# Two tests, both driven through the pre-existing public
# `Electric.Client.stream/2` API and observed via `Bypass.expect/2`:
#
#   1. fail_to_pass — `expired_handle_param_after_must_refetch`. After
#      the server returns 409 (must-refetch) for a shape, every
#      subsequent request the client issues for that shape MUST include
#      an `expired_handle` query parameter set to the just-expired
#      handle. This is the protocol contract that the Electric server
#      and the production CDN configuration use to invalidate stale
#      cached responses; the TypeScript client at
#      packages/typescript-client/ uses the same parameter name (see
#      `src/constants.ts` `EXPIRED_HANDLE_QUERY_PARAM`). The pre-fix
#      Elixir client never emits this parameter; any port of the
#      cache-busting feature must.
#
#   2. pass_to_pass — `streams_data_through_normal_protocol_flow`.
#      Drives a basic insert + up-to-date sequence followed by a live
#      poll that returns another insert + up-to-date. Asserts the
#      stream emits two `%ChangeMessage{}` and two `%ControlMessage{}`
#      values in order. Guards against breaking the basic protocol
#      while implementing the cache-busting fix.
#
# The Bypass-based helper functions (`bypass_resp/3`, `put_optional_header/3`)
# are duplicated from packages/elixir-client/test/electric/client_test.exs
# (lines 601-625). They use only public Electric.Client APIs and the Plug
# convention for setting response headers — they encode pre-existing
# Electric protocol response shape, not implementation-specific helpers.
# Inlining (rather than importing from `test/electric/client_test.exs`)
# keeps the verifier independent of the agent's editable test/ tree.
#
# The module name is intentionally `Electric.Verify.CacheBustingTest`,
# distinct from any pre-existing test module loaded by `mix test`'s
# normal discovery.

defmodule Electric.Verify.CacheBustingTest do
  use ExUnit.Case, async: false

  alias Electric.Client
  alias Electric.Client.Fetch
  alias Electric.Client.ShapeDefinition
  alias Electric.Client.Message.{ChangeMessage, ControlMessage}

  setup do
    bypass = Bypass.open()

    {:ok, client} =
      Client.new(
        base_url: "http://localhost:#{bypass.port}",
        fetch:
          {Fetch.HTTP,
           [
             request: [
               connect_options: [timeout: 100, protocols: [:http1]],
               retry_delay: fn _n -> 50 end,
               retry_log_level: false,
               max_retries: 10
             ]
           ]}
      )

    shape = ShapeDefinition.new!("my_table")

    [bypass: bypass, client: client, shape: shape]
  end

  # Test 1 — fail_to_pass.
  #
  # Bypass state machine (keyed off the request's `handle` query param):
  #   * no handle (initial sync): respond 200 with handle "A", offset
  #     "1_0", schema, body containing one insert + an `up-to-date`.
  #   * handle == "A" (live re-poll after stage 1's up-to-date): respond
  #     409 (must-refetch). The Elixir client's pre-fix and post-fix
  #     `handle_must_refetch/3` both fall back to handle "A-next" because
  #     the 409 response carries no `electric-handle` header.
  #   * any other handle (post-must-refetch retry): capture the request's
  #     query params via `send(parent, {:post_409_request, params})` so
  #     the test can assert on them, then respond 200 with the request's
  #     handle and a fresh insert + up-to-date so the stream keeps
  #     making progress.
  #
  # The test wraps `Client.stream/2` in a Task with a hard timeout so a
  # buggy implementation that infinite-loops surfaces as a captured
  # `:post_409_request` message AND/OR a Task.shutdown rather than
  # hanging the verifier process.
  test "expired_handle_param_after_must_refetch", ctx do
    parent = self()
    schema = Jason.encode!(%{"id" => %{type: "text"}})

    body_initial =
      Jason.encode!([
        %{
          "headers" => %{"operation" => "insert"},
          "offset" => "1_0",
          "value" => %{"id" => "1"}
        },
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 100}}
      ])

    body_post_409 =
      Jason.encode!([
        %{
          "headers" => %{"operation" => "insert"},
          "offset" => "1_0",
          "value" => %{"id" => "2"}
        },
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 200}}
      ])

    body_409 = Jason.encode!([%{"headers" => %{"control" => "must-refetch"}}])

    Bypass.expect(ctx.bypass, fn conn ->
      handle = conn.query_params["handle"]

      cond do
        is_nil(handle) ->
          # Stage 1: initial sync. No expired handle is known yet so the
          # post-fix client does not include `expired_handle` here — that
          # is correct, expected behaviour. The pre-fix client also omits
          # it (it never sets the parameter). This stage's request is not
          # the one we are asserting on.
          bypass_resp(conn, body_initial,
            shape_handle: "A",
            last_offset: "1_0",
            schema: schema
          )

        handle == "A" ->
          # Stage 2: live request after stage 1's up-to-date. Server
          # returns 409 (must-refetch) with no new handle header — both
          # pre-fix and post-fix `handle_must_refetch/3` then fall back
          # to handle "A-next". The post-fix client also marks "A" as
          # expired in its cache here.
          bypass_resp(conn, body_409, status: 409)

        true ->
          # Stage 3+: the request the test asserts on. Send the captured
          # query params to the test process for inspection; respond 200
          # so the stream can continue (and the Task.async eventually
          # completes).
          send(parent, {:post_409_request, conn.query_params})

          bypass_resp(conn, body_post_409,
            shape_handle: handle,
            last_offset: "1_0",
            schema: schema
          )
      end
    end)

    # Drive the stream up to and through stage 3. Take/5 is enough:
    #   stage-1 insert, stage-1 up_to_date, stage-2 must_refetch,
    #   stage-3 insert, stage-3 up_to_date.
    # Wrap in a Task with a timeout so a buggy infinite-loop
    # implementation does not hang the verifier process.
    task =
      Task.async(fn ->
        try do
          Client.stream(ctx.client, ctx.shape) |> Enum.take(5)
        rescue
          _ -> :stream_raised
        catch
          :exit, _ -> :stream_exited
        end
      end)

    # Wait up to 5 s for the post-must-refetch request to arrive at
    # Bypass. A pre-fix client makes this request without
    # `expired_handle`; a post-fix client includes it.
    assert_receive {:post_409_request, params}, 5_000

    Task.shutdown(task, :brutal_kill)

    assert params["expired_handle"] == "A",
           "post-must-refetch request must include `expired_handle=A` query parameter — " <>
             "this is the protocol contract the Electric server and CDN configuration use to " <>
             "invalidate stale cached responses for the shape (matches the TypeScript client's " <>
             "EXPIRED_HANDLE_QUERY_PARAM in packages/typescript-client/src/constants.ts). " <>
             "Got params: #{inspect(params)}"
  end

  # Test 2 — pass_to_pass.
  #
  # Normal protocol flow with no must-refetch: stage 1 returns insert +
  # up-to-date at offset "1_0"; stage 2 (live re-poll once up_to_date is
  # observed) returns another insert + up-to-date at offset "2_0". The
  # stream must emit four messages in the documented order. Guards
  # against the cache-busting fix breaking the basic streaming protocol.
  test "streams_data_through_normal_protocol_flow", ctx do
    schema = Jason.encode!(%{"id" => %{type: "text"}})

    body_at_minus_one =
      Jason.encode!([
        %{
          "headers" => %{"operation" => "insert"},
          "offset" => "1_0",
          "value" => %{"id" => "1"}
        },
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 100}}
      ])

    body_at_one_zero =
      Jason.encode!([
        %{
          "headers" => %{"operation" => "insert"},
          "offset" => "2_0",
          "value" => %{"id" => "2"}
        },
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 200}}
      ])

    Bypass.expect(ctx.bypass, fn conn ->
      case conn.query_params["offset"] do
        "-1" ->
          bypass_resp(conn, body_at_minus_one,
            shape_handle: "h1",
            last_offset: "1_0",
            schema: schema
          )

        "1_0" ->
          bypass_resp(conn, body_at_one_zero,
            shape_handle: "h1",
            last_offset: "2_0"
          )
      end
    end)

    messages = Client.stream(ctx.client, ctx.shape) |> Enum.take(4)

    assert [
             %ChangeMessage{value: %{"id" => "1"}},
             %ControlMessage{control: :up_to_date},
             %ChangeMessage{value: %{"id" => "2"}},
             %ControlMessage{control: :up_to_date}
           ] = messages,
           "client must stream insert + up_to_date at offset 1_0 then insert + up_to_date " <>
             "at offset 2_0 from a normal-protocol Bypass server — the cache-busting fix " <>
             "must not break the basic protocol flow. Got: #{inspect(messages)}"
  end

  # Test 3 — fail_to_pass: cache-buster on stale retry.
  #
  # After a must-refetch expires handle "A", a misbehaving CDN replays the
  # stale 200 for the just-expired handle. A correct client detects the stale
  # response (its handle matches the expired one and no valid local handle
  # remains) and adds a `cache-buster` query param to the retry so a URL-keyed
  # cache cannot replay the same stale response forever. `cache-buster` is the
  # protocol-contract param the production CDN and the TS client use
  # (CACHE_BUSTER_QUERY_PARAM); asserting its presence is fair. We assert only
  # that the param appears — its value is implementation-chosen.
  test "cache_buster_param_on_stale_retry", ctx do
    parent = self()
    {:ok, counter} = Agent.start_link(fn -> 0 end)
    schema = Jason.encode!(%{"id" => %{type: "text"}})

    body_initial =
      Jason.encode!([
        %{"headers" => %{"operation" => "insert"}, "offset" => "1_0", "value" => %{"id" => "1"}},
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 100}}
      ])

    body_409 = Jason.encode!([%{"headers" => %{"control" => "must-refetch"}}])

    body_fresh =
      Jason.encode!([
        %{"headers" => %{"operation" => "insert"}, "offset" => "1_0", "value" => %{"id" => "2"}},
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 200}}
      ])

    Bypass.expect(ctx.bypass, fn conn ->
      n = Agent.get_and_update(counter, fn c -> {c + 1, c + 1} end)

      cond do
        n == 1 ->
          bypass_resp(conn, body_initial, shape_handle: "A", last_offset: "1_0", schema: schema)

        n == 2 ->
          # 409 carries the same handle "A": the client keeps "A" as its
          # current handle while marking it expired, so the stale replay below
          # (a 200 for the expired "A" with no newer valid handle) is detected
          # as stale rather than adopted.
          bypass_resp(conn, body_409, status: 409, shape_handle: "A")

        n == 3 ->
          # Stale: replay a 200 for the just-expired handle "A".
          bypass_resp(conn, body_initial, shape_handle: "A", last_offset: "1_0", schema: schema)

        true ->
          # The retry after the stale detection — report its params, then
          # serve a fresh handle so the stream recovers and the task ends.
          send(parent, {:after_stale, conn.query_params})
          bypass_resp(conn, body_fresh, shape_handle: "B", last_offset: "1_0", schema: schema)
      end
    end)

    task =
      Task.async(fn ->
        try do
          Client.stream(ctx.client, ctx.shape) |> Enum.take(6)
        rescue
          _ -> :raised
        catch
          :exit, _ -> :exited
        end
      end)

    assert_receive {:after_stale, params}, 5_000
    Task.shutdown(task, :brutal_kill)

    assert Map.has_key?(params, "cache-buster"),
           "the retry after a stale cached response (a 200 replaying the just-expired " <>
             "handle) must carry a `cache-buster` query param so a URL-keyed CDN cache " <>
             "cannot replay the stale response indefinitely (contract param, matches the " <>
             "TS client's CACHE_BUSTER_QUERY_PARAM). Got params: #{inspect(params)}"
  end

  # Test 4 — fail_to_pass: bounded termination with a Client.Error.
  #
  # A CDN that persistently replays the stale handle (ignoring the cache-buster)
  # must not loop the client forever: after a small bounded number of stale
  # retries the client gives up and raises an Electric.Client.Error. We assert
  # the error STATE (a Client.Error is raised, within a bounded time) — not its
  # exact wording, and not the specific retry count — so any bounded
  # implementation passes. A pre-fix / buggy client loops indefinitely and the
  # task times out (asserted against).
  test "bounded_retry_with_clear_error_on_persistent_stale_cdn", ctx do
    {:ok, counter} = Agent.start_link(fn -> 0 end)
    schema = Jason.encode!(%{"id" => %{type: "text"}})

    body_initial =
      Jason.encode!([
        %{"headers" => %{"operation" => "insert"}, "offset" => "1_0", "value" => %{"id" => "1"}},
        %{"headers" => %{"control" => "up-to-date", "global_last_seen_lsn" => 100}}
      ])

    body_409 = Jason.encode!([%{"headers" => %{"control" => "must-refetch"}}])

    Bypass.expect(ctx.bypass, fn conn ->
      n = Agent.get_and_update(counter, fn c -> {c + 1, c + 1} end)

      cond do
        n == 1 ->
          bypass_resp(conn, body_initial, shape_handle: "A", last_offset: "1_0", schema: schema)

        n == 2 ->
          # 409 carries the same handle "A": the client keeps "A" as its
          # current handle while marking it expired, so the stale replay below
          # (a 200 for the expired "A" with no newer valid handle) is detected
          # as stale rather than adopted.
          bypass_resp(conn, body_409, status: 409, shape_handle: "A")

        true ->
          # Persistently stale: every retry replays the expired handle "A".
          bypass_resp(conn, body_initial, shape_handle: "A", last_offset: "1_0", schema: schema)
      end
    end)

    task =
      Task.async(fn ->
        try do
          Client.stream(ctx.client, ctx.shape) |> Enum.take(20)
          :completed_without_error
        rescue
          e in [Electric.Client.Error] -> {:client_error, Exception.message(e)}
          other -> {:other_raise, inspect(other.__struct__)}
        catch
          :exit, reason -> {:exit, inspect(reason)}
        end
      end)

    result =
      case Task.yield(task, 15_000) || Task.shutdown(task, :brutal_kill) do
        {:ok, r} -> r
        nil -> :timed_out
      end

    assert match?({:client_error, _}, result),
           "a persistently-stale CDN (replaying the expired handle on every retry) must make " <>
             "the client give up after a bounded number of retries and raise an " <>
             "Electric.Client.Error, NOT loop indefinitely (:timed_out means it looped). " <>
             "Got: #{inspect(result)}"
  end

  # Helpers duplicated from
  # packages/elixir-client/test/electric/client_test.exs (lines 601-625).
  # They use only public Electric.Client / Plug.Conn APIs and encode the
  # pre-existing Electric HTTP response shape — not implementation-specific
  # helpers. Inlining keeps the verifier independent of the agent's
  # editable test/ tree.

  defp put_optional_header(conn, _header, nil), do: conn

  defp put_optional_header(conn, header, value) do
    Plug.Conn.put_resp_header(conn, header, value)
  end

  defp bypass_resp(conn, body, opts) do
    status = Keyword.get(opts, :status, 200)

    # Mirrors the small delay used by the in-tree Bypass tests — the
    # quick-responding tests are less flaky with a 5 ms gap because the
    # underlying HTTP client occasionally fails to return on
    # very-fast responses (a known Bypass quirk).
    Process.sleep(5)

    conn
    |> Plug.Conn.put_resp_content_type("application/json")
    |> put_optional_header("electric-handle", opts[:shape_handle])
    |> put_optional_header("electric-offset", opts[:last_offset])
    |> put_optional_header("electric-schema", opts[:schema])
    |> put_optional_header("electric-cursor", opts[:cursor])
    |> Plug.Conn.resp(status, body)
  end
end

# Verifier for the electric-fix-resolve-pending-shapes task.
#
# The Elixir runner (src/resources/scripts/runners/elixir.py) copies this
# file into packages/sync-service/test/__verification__/ before invoking
# `mix test` from packages/sync-service/. That location is part of the
# sync-service's test compile path (`elixirc_paths(:test)` adds
# `test/support`), so the support modules and fixtures the in-repo
# Consumer / ShapeLogCollector tests rely on are directly importable.
#
# All three tests are driven through pre-existing public APIs:
#
#   - `Electric.Replication.ShapeLogCollector.handle_event/2`
#     — entry point for sending a `%TransactionFragment{}` to the SLC
#     pipeline.
#   - `Electric.Replication.ShapeLogCollector.notify_flushed/3`
#     — called by the per-shape Consumer when storage has flushed an
#     offset; observed via `Support.Trace.trace_shape_log_collector_calls/1`.
#   - The replication-client `{:flush_boundary_updated, lsn}` message
#     — pre-existing protocol; the test process registers under the
#     stack's replication-client name in the registry via
#     `Support.TestUtils.register_as_replication_client/1`.
#   - `Electric.ShapeCache.get_or_create_shape_handle/2`,
#     `ShapeCache.await_snapshot_start/2`,
#     `Shapes.Consumer.register_for_changes/2`,
#     `Shapes.Consumer.whereis/2`
#     — pre-existing public APIs the existing consumer regression suite
#     uses.
#
# The setup chain mirrors the `transaction handling with real storage`
# describe block in
# `packages/sync-service/test/electric/shapes/consumer_test.exs` so that
# fragment-streaming with PureFileStorage works end-to-end.
#
# The three tests:
#
#   1. fail_to_pass — Timer-based storage flush fires while a multi-fragment
#      transaction is in flight. The per-shape consumer must NOT report any
#      flush acknowledgement upstream until the commit fragment has been
#      processed; otherwise the upstream FlushTracker can be left in a
#      "stuck" state. After the commit, the flush boundary advances.
#
#   2. fail_to_pass — Buffer-size storage flush triggered by a >=64 KiB
#      record inside a non-commit fragment, where the commit fragment has
#      no relevant changes for the shape. Same invariant as test 1 with a
#      different (and more strictly reproducible) flush trigger. After
#      the commit, the consumer notifies upstream exactly once with the
#      commit fragment's `last_log_offset`, and the flush boundary
#      advances to the commit's `tx_offset`.
#
#   3. pass_to_pass — A single complete fragment (begin + commit in one
#      message) with relevant changes. The consumer continues to notify
#      upstream as before; the flush boundary advances. Guards against a
#      fix that breaks the non-multi-fragment path.

defmodule Electric.Verify.PendingFlushOffsetTest do
  use ExUnit.Case, async: true
  use Repatch.ExUnit, assert_expectations: true

  alias Electric.Postgres.Lsn
  alias Electric.Replication.Changes
  alias Electric.Replication.LogOffset
  alias Electric.Replication.ShapeLogCollector
  alias Electric.ShapeCache
  alias Electric.Shapes
  alias Electric.Shapes.Shape

  alias Support.StubInspector

  import Support.ComponentSetup

  import Support.TestUtils,
    only: [
      patch_snapshotter: 1,
      register_as_replication_client: 1,
      complete_txn_fragment: 3,
      txn_fragment: 4
    ]

  @receive_timeout 1_000

  @base_inspector StubInspector.new(
                    tables: [
                      "test_table",
                      "other_table"
                    ],
                    columns: [
                      %{name: "id", type: "int8", pk_position: 0},
                      %{name: "value", type: "text"}
                    ]
                  )

  @shape1 Shape.new!("public.test_table", inspector: @base_inspector)

  @moduletag :tmp_dir

  setup :with_stack_id_from_test

  setup do
    %{inspector: @base_inspector, pool: nil}
  end

  setup [
    :with_registry,
    :with_pure_file_storage,
    :with_shape_status,
    :with_lsn_tracker,
    :with_log_chunking,
    :with_persistent_kv,
    :with_async_deleter,
    :with_shape_cleaner,
    :with_shape_log_collector,
    :with_noop_publication_manager,
    :with_status_monitor
  ]

  setup(_ctx) do
    patch_snapshotter(fn parent, shape_handle, _shape, %{snapshot_fun: snapshot_fun} ->
      pg_snapshot = {10, 11, [10]}
      GenServer.cast(parent, {:pg_snapshot_known, shape_handle, pg_snapshot})
      GenServer.cast(parent, {:snapshot_started, shape_handle})
      snapshot_fun.([])
    end)

    :ok
  end

  setup(ctx) do
    Electric.StackConfig.put(
      ctx.stack_id,
      :shape_hibernate_after,
      Map.get(ctx, :hibernate_after, 10_000)
    )

    if not Map.get(ctx, :allow_subqueries, true) do
      Electric.StackConfig.put(ctx.stack_id, :feature_flags, [])
    end

    :ok
  end

  setup ctx do
    %{consumer_supervisor: consumer_supervisor, shape_cache: shape_cache} =
      Support.ComponentSetup.with_shape_cache(ctx)

    %{
      consumer_supervisor: consumer_supervisor,
      shape_cache: shape_cache
    }
  end

  # Test 1 — fail_to_pass.
  #
  # Two non-commit fragments containing relevant changes for the shape are
  # sent to the SLC. With `flush_period: 1` the storage backend's flush
  # timer fires within milliseconds, producing a `{Storage, :flushed, _}`
  # message in the per-shape consumer's mailbox while the multi-fragment
  # transaction is still in flight.
  #
  # Pre-fix: the consumer immediately translates that message into a
  # `ShapeLogCollector.notify_flushed/3` call at the storage offset (which
  # predates the commit fragment's offset). The trace observer therefore
  # records at least one such call BEFORE the commit fragment has been
  # processed.
  #
  # Post-fix: the consumer holds onto the flush acknowledgement until the
  # pending transaction completes. No `notify_flushed` call is recorded
  # before the commit fragment is processed.
  @tag allow_subqueries: false, with_pure_file_storage_opts: [flush_period: 1]
  test "timer_flush_during_pending_multi_fragment_txn_does_not_emit_premature_notify",
       %{stack_id: stack_id} do
    {shape_handle, _} = ShapeCache.get_or_create_shape_handle(@shape1, stack_id)
    :started = ShapeCache.await_snapshot_start(shape_handle, stack_id)
    ref = Shapes.Consumer.register_for_changes(stack_id, shape_handle)
    register_as_replication_client(stack_id)

    xid = 11
    lsn = Lsn.from_integer(10)

    fragment1 =
      txn_fragment(
        xid,
        lsn,
        [
          %Changes.NewRecord{
            relation: {"public", "test_table"},
            record: %{"id" => "1"},
            log_offset: LogOffset.new(lsn, 0)
          },
          %Changes.NewRecord{
            relation: {"public", "test_table"},
            record: %{"id" => "2"},
            log_offset: LogOffset.new(lsn, 2)
          }
        ],
        has_begin?: true
      )

    fragment2 =
      txn_fragment(
        xid,
        lsn,
        [
          %Changes.NewRecord{
            relation: {"public", "test_table"},
            record: %{"id" => "3"},
            log_offset: LogOffset.new(lsn, 4)
          }
        ],
        []
      )

    commit_fragment =
      txn_fragment(
        xid,
        lsn,
        [
          %Changes.NewRecord{
            relation: {"public", "other_table"},
            record: %{"id" => "99"},
            log_offset: LogOffset.new(lsn, 6)
          }
        ],
        has_commit?: true
      )

    Support.Trace.trace_shape_log_collector_calls(
      pid: Shapes.Consumer.whereis(stack_id, shape_handle),
      functions: [:notify_flushed]
    )

    assert :ok = ShapeLogCollector.handle_event(fragment1, stack_id)
    assert :ok = ShapeLogCollector.handle_event(fragment2, stack_id)

    # `collect_traced_calls/0` waits up to ExUnit's `assert_receive_timeout`
    # (400 ms by default) for any trace event before returning. Pre-fix
    # code calls `notify_flushed/3` synchronously when the storage flush
    # message arrives, so the timer-driven flush within the timeout
    # produces a recorded trace event. Post-fix code defers the
    # acknowledgement until the commit fragment is processed, so the
    # timeout elapses with an empty mailbox.
    assert [] == Support.Trace.collect_traced_calls(),
           "notify_flushed/3 must not be called while a multi-fragment transaction is still pending — the per-shape consumer must defer the flush acknowledgement until the commit fragment has been processed"

    assert :ok = ShapeLogCollector.handle_event(commit_fragment, stack_id)
    assert_receive {^ref, :new_changes, _}, @receive_timeout

    tx_offset = commit_fragment.last_log_offset.tx_offset

    assert_receive {:flush_boundary_updated, ^tx_offset}, @receive_timeout,
                   "the global flush boundary must advance to the commit fragment's tx_offset (#{tx_offset}) once the commit has been processed"
  end

  # Test 2 — fail_to_pass.
  #
  # A single non-commit fragment carries a record padded to 70 KiB which
  # forces PureFileStorage's buffer-size flush trigger (>= 64 KiB) to
  # fire synchronously while the fragment is being written. The flush
  # offset is the relevant change's `log_offset`, which is strictly less
  # than the fragment's `last_log_offset` because a second non-matching
  # change at a higher offset is included in the same fragment. The
  # commit fragment's only change is for a different table the shape
  # under test does not match.
  #
  # The high `flush_period` (10_000 ms) prevents timer-based flushes so
  # the buffer-size trigger is the only flush path — making the test
  # fully deterministic.
  #
  # Pre-fix: the consumer immediately reports the buffer-size flush
  # upstream at an offset below the upstream tracker's `last_seen_offset`
  # after the commit. The shape ends up with `last_sent` at the commit
  # offset and `last_flushed` at the strictly smaller buffer-size flush
  # offset. No follow-up storage flush will arrive (the data is already
  # on disk), so the global flush boundary never advances.
  #
  # Post-fix: the consumer defers the flush acknowledgement until the
  # commit fragment is processed; `notify_flushed/3` is invoked exactly
  # once with the commit fragment's `last_log_offset`; the flush
  # boundary advances to the commit's `tx_offset`.
  @tag allow_subqueries: false, with_pure_file_storage_opts: [flush_period: 10_000]
  test "buffer_size_flush_during_pending_multi_fragment_txn_advances_boundary_at_commit",
       %{stack_id: stack_id} do
    {shape_handle, _} = ShapeCache.get_or_create_shape_handle(@shape1, stack_id)
    :started = ShapeCache.await_snapshot_start(shape_handle, stack_id)
    ref = Shapes.Consumer.register_for_changes(stack_id, shape_handle)
    register_as_replication_client(stack_id)

    xid = 11
    lsn = Lsn.from_integer(10)
    relevant_change_offset = LogOffset.new(lsn, 0)

    # 70 KiB padding ensures the buffer-size flush trigger fires while
    # the fragment is being written.
    padding = String.duplicate("x", 70_000)

    non_commit_fragment =
      txn_fragment(
        xid,
        lsn,
        [
          %Changes.NewRecord{
            relation: {"public", "test_table"},
            record: %{"id" => "1", "value" => padding},
            log_offset: relevant_change_offset
          },
          # Non-matching change at a higher offset raises the fragment's
          # `last_log_offset` above the shape's actual last written
          # offset — mimicking a production txn that touches multiple
          # tables.
          %Changes.NewRecord{
            relation: {"public", "other_table"},
            record: %{"id" => "2"},
            log_offset: LogOffset.new(lsn, 50)
          }
        ],
        has_begin?: true
      )

    commit_fragment =
      txn_fragment(
        xid,
        lsn,
        [
          %Changes.NewRecord{
            relation: {"public", "other_table"},
            record: %{"id" => "99"},
            log_offset: LogOffset.new(lsn, 100)
          }
        ],
        has_commit?: true
      )

    Support.Trace.trace_shape_log_collector_calls(
      pid: Shapes.Consumer.whereis(stack_id, shape_handle),
      functions: [:notify_flushed]
    )

    assert :ok = ShapeLogCollector.handle_event(non_commit_fragment, stack_id)

    assert [] == Support.Trace.collect_traced_calls(),
           "notify_flushed/3 must not be called while a multi-fragment transaction is still pending — even when a buffer-size flush fires during fragment processing"

    assert :ok = ShapeLogCollector.handle_event(commit_fragment, stack_id)
    assert_receive {^ref, :new_changes, ^relevant_change_offset}, @receive_timeout

    commit_last_log_offset = commit_fragment.last_log_offset

    assert [
             {ShapeLogCollector, :notify_flushed,
              [^stack_id, ^shape_handle, ^commit_last_log_offset]}
           ] = Support.Trace.collect_traced_calls(),
           "after the commit fragment is processed, exactly one notify_flushed/3 call must be observed, at the commit fragment's last_log_offset"

    tx_offset = commit_fragment.last_log_offset.tx_offset

    assert_receive {:flush_boundary_updated, ^tx_offset}, @receive_timeout,
                   "the global flush boundary must advance to the commit fragment's tx_offset (#{tx_offset}) once the commit has been processed — without this the Postgres replication slot lag grows without bound"
  end

  # Test 3 — pass_to_pass.
  #
  # A single complete fragment (begin + commit in one message) carrying a
  # relevant change. Storage flushes promptly (`flush_period: 1`). The
  # consumer must continue to notify upstream and the global flush
  # boundary must advance to the fragment's `tx_offset`. There is no
  # multi-fragment transaction in flight, so any new deferral path must
  # not regress this case.
  @tag allow_subqueries: false, with_pure_file_storage_opts: [flush_period: 1]
  test "single_fragment_complete_txn_advances_flush_boundary",
       %{stack_id: stack_id} do
    {shape_handle, _} = ShapeCache.get_or_create_shape_handle(@shape1, stack_id)
    :started = ShapeCache.await_snapshot_start(shape_handle, stack_id)
    ref = Shapes.Consumer.register_for_changes(stack_id, shape_handle)
    register_as_replication_client(stack_id)

    xid = 21
    lsn = Lsn.from_integer(20)
    last_log_offset = LogOffset.new(lsn, 0)

    fragment =
      complete_txn_fragment(xid, lsn, [
        %Changes.NewRecord{
          relation: {"public", "test_table"},
          record: %{"id" => "100"},
          log_offset: last_log_offset
        }
      ])

    assert :ok = ShapeLogCollector.handle_event(fragment, stack_id)
    assert_receive {^ref, :new_changes, ^last_log_offset}, @receive_timeout

    tx_offset = last_log_offset.tx_offset

    assert_receive {:flush_boundary_updated, ^tx_offset}, @receive_timeout,
                   "single-fragment transactions must continue to advance the flush boundary; the deferral path must not trigger when there is no pending multi-fragment transaction"

    # Sanity: the consumer is still alive and able to process subsequent
    # transactions correctly (the deferred-flush path must not leave any
    # stale state behind).
    next_lsn = Lsn.from_integer(30)
    next_offset = LogOffset.new(next_lsn, 0)

    next_fragment =
      complete_txn_fragment(xid + 1, next_lsn, [
        %Changes.NewRecord{
          relation: {"public", "test_table"},
          record: %{"id" => "200"},
          log_offset: next_offset
        }
      ])

    assert :ok = ShapeLogCollector.handle_event(next_fragment, stack_id)
    assert_receive {^ref, :new_changes, ^next_offset}, @receive_timeout
    next_tx_offset = next_offset.tx_offset
    assert_receive {:flush_boundary_updated, ^next_tx_offset}, @receive_timeout
  end
end

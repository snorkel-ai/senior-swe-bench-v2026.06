defmodule PlausibleWeb.Api.StatsController.PagesMetaVerifyTest do
  # Behavioral verifier for the missing Top-Pages comparison date labels.
  # Drives `GET /api/stats/:domain/pages` and inspects the JSON response's
  # `meta` — the surface the dashboard tooltip consumes.
  #
  # The exact-match (`==`) assertions are deliberate: they reject half-fixes
  # that leak the underlying struct's `:values` field into the response JSON.
  #
  # Test names are single-token snake_case at the module top level so both the
  # ran-parser and fail-parser of the harbor Elixir runner derive the same
  # canonical name (multi-word names can mask failures).
  use PlausibleWeb.ConnCase

  setup [:create_user, :log_in, :create_site]

  test "noncomparison_pages_meta_includes_date_range_label", %{conn: conn, site: site} do
    populate_stats(site, [
      build(:pageview, pathname: "/blog", timestamp: ~N[2021-01-01 00:00:00])
    ])

    conn = get(conn, "/api/stats/#{site.domain}/pages?period=day&date=2021-01-01")

    assert json_response(conn, 200)["meta"] == %{"date_range_label" => "1 Jan 2021"}
  end

  test "comparison_pages_meta_includes_both_date_range_labels", %{conn: conn, site: site} do
    populate_stats(site, [
      build(:pageview, pathname: "/blog", timestamp: ~N[2021-01-01 00:00:00]),
      build(:pageview, pathname: "/blog", timestamp: ~N[2021-01-02 00:00:00])
    ])

    conn =
      get(
        conn,
        "/api/stats/#{site.domain}/pages?period=day&date=2021-01-02&comparison=previous_period"
      )

    assert json_response(conn, 200)["meta"] == %{
             "date_range_label" => "2 Jan 2021",
             "comparison_date_range_label" => "1 Jan 2021"
           }
  end
end

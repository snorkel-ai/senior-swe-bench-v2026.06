defmodule PlausibleWeb.ProvisioningSiteIdInjectionVerifyTest do
  # Drives the stable interfaces `PATCH /api/:domain/segments/:segment_id`
  # (ConnCase) and the `edit-shared-link` LiveView event (LiveViewTest), reading
  # ownership back from the database. Assertions check the security property
  # fix-shape-agnostically: the segment never moves off the acting site
  # (regardless of patch success/rejection); a foreign slug never loads the
  # victim's link into the edit form and the victim row is unchanged. The shared
  # link assertion matches `"Edit shared link"`, the pre-existing form title in
  # shared_link_settings/form.ex, not any flash text a fix might introduce.
  use PlausibleWeb.ConnCase
  import Phoenix.LiveViewTest

  describe "segments PATCH" do
    setup [:create_user, :create_site, :log_in]

    test "segments_patch_ignores_site_id_param", %{conn: conn, user: user, site: site} do
      victim_site = new_site()

      segment =
        insert(:segment,
          site: site,
          owner: user,
          type: :personal,
          name: "test segment"
        )

      patch(conn, "/api/#{site.domain}/segments/#{segment.id}", %{
        "name" => "updated name",
        "site_id" => victim_site.id
      })

      # The security property: the segment never moved off the acting site,
      # independent of the HTTP status returned.
      assert Plausible.Repo.reload!(segment).site_id == site.id
    end
  end

  describe "shared link edit" do
    setup [:create_user, :log_in, :create_site]

    setup %{user: user, site: site} do
      subscribe_to_growth_plan(user)
      {:ok, session: %{"site_id" => site.id, "domain" => site.domain}}
    end

    test "shared_link_edit_rejects_foreign_slug", %{conn: conn, site: site, session: session} do
      _own_link = insert(:shared_link, site: site, name: "Own Link")
      victim_site = insert(:site)
      victim_link = insert(:shared_link, site: victim_site, name: "Victim Link")

      lv = get_liveview(conn, session)
      html = render_click(lv, "edit-shared-link", %{"slug" => victim_link.slug})

      # The foreign link must not be loaded into the edit form.
      refute html =~ "Edit shared link"
      # And the victim's row is untouched in the database.
      assert Plausible.Repo.reload!(victim_link).name == "Victim Link"
    end

    defp get_liveview(conn, session) do
      {:ok, lv, _html} =
        live_isolated(conn, PlausibleWeb.Live.SharedLinkSettings, session: session)

      lv
    end
  end
end

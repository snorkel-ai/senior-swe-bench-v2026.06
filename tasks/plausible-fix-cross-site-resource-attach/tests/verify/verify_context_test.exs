defmodule Plausible.ProvisioningSiteIdInjectionVerifyTest do
  # Drives the stable public interfaces `Plausible.Goals.update/2` and
  # `Plausible.Shields.add_*_rule/2` and reads ownership back from the database,
  # never touching the changeset functions a fix would edit. Assertions check the
  # security property (the resource stays on the acting site / the victim site is
  # untouched), not the {:ok,_}/{:error,_} result shape, so any valid fix shape
  # passes whether it drops :site_id from cast, overrides post-cast, or rejects.
  use Plausible.DataCase

  alias Plausible.Goals
  import Plausible.Shields

  test "goals_update_ignores_site_id_param" do
    site = new_site()
    victim = new_site()

    {:ok, goal} = Goals.create(site, %{"event_name" => "Purchase"})

    # Hostile update: attach the victim site's id to an otherwise-valid update.
    # Tolerate either a successful no-op update or a rejection.
    case Goals.update(goal, %{"event_name" => "Purchase", "site_id" => victim.id}) do
      {:ok, updated} -> assert updated.site_id == site.id
      {:error, _changeset} -> :ok
    end

    # The security property: the goal never moved off the acting site.
    assert Plausible.Repo.reload!(goal).site_id == site.id
  end

  test "shield_country_rule_ignores_site_id_param" do
    site = insert(:site)
    victim = insert(:site)

    case add_country_rule(site, %{"country_code" => "US", "site_id" => victim.id}) do
      {:ok, rule} -> assert rule.site_id == site.id
      {:error, _changeset} -> :ok
    end

    assert count_country_rules(victim) == 0
  end

  test "shield_hostname_rule_ignores_site_id_param" do
    site = insert(:site)
    victim = insert(:site)

    case add_hostname_rule(site, %{"hostname" => "example.com", "site_id" => victim.id}) do
      {:ok, rule} -> assert rule.site_id == site.id
      {:error, _changeset} -> :ok
    end

    assert count_hostname_rules(victim) == 0
  end

  test "shield_ip_rule_ignores_site_id_param" do
    site = insert(:site)
    victim = insert(:site)

    case add_ip_rule(site, %{"inet" => "1.2.3.4", "site_id" => victim.id}) do
      {:ok, rule} -> assert rule.site_id == site.id
      {:error, _changeset} -> :ok
    end

    assert count_ip_rules(victim) == 0
  end

  test "shield_page_rule_ignores_site_id_param" do
    site = insert(:site)
    victim = insert(:site)

    case add_page_rule(site, %{"page_path" => "/test", "site_id" => victim.id}) do
      {:ok, rule} -> assert rule.site_id == site.id
      {:error, _changeset} -> :ok
    end

    assert count_page_rules(victim) == 0
  end
end

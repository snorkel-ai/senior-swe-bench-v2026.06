# Idempotent seed for the deep-linking validation stories.
#
# Inserts a Site (with the team/user infrastructure that
# `Plausible.Teams.Test.new_site` builds for free) plus a
# password-protected SharedLink with a known plaintext password.
# Prints a single line of JSON describing the seeded values so the
# Python harness can read them back.
#
# Run via:
#   MIX_ENV=e2e_test mix run --no-start \
#       /repo/plausible/senior_swe_bench_seed.exs
#
# Tunable via env vars (so each test case can request its own
# unique site/slug pair if desired):
#   SEED_DOMAIN    — default "deeplink-test.example"
#   SEED_SLUG      — default "deeplink-slug-abc"
#   SEED_PASSWORD  — default "correct horse battery"

domain   = System.get_env("SEED_DOMAIN", "deeplink-test.example")
slug     = System.get_env("SEED_SLUG", "deeplink-slug-abc")
password = System.get_env("SEED_PASSWORD", "correct horse battery")

# `mix run --no-start` skipped the application supervisor, so the
# repos are not started yet. Start exactly the ones we need.
{:ok, _} = Application.ensure_all_started(:plausible)

# Re-use the pre-existing test-support helper so the team / membership
# / billing chain is correctly populated.  These modules live under
# `test/support/` which is on the e2e_test compile path
# (mix.exs → elixirc_paths/1).
require Plausible.Teams.Test
import Plausible.Teams.Test, only: [new_user: 0, new_site: 1]

site =
  case Plausible.Repo.get_by(Plausible.Site, domain: domain) do
    nil ->
      user = new_user()
      new_site(domain: domain, owner: user)

    existing ->
      existing
  end

password_hash = Plausible.Auth.Password.hash(password)

# Insert the SharedLink only if a row with this slug doesn't already
# exist (idempotent across re-runs of the seed).
case Plausible.Repo.get_by(Plausible.Site.SharedLink, slug: slug) do
  nil ->
    %Plausible.Site.SharedLink{}
    |> Ecto.Changeset.cast(
      %{
        site_id: site.id,
        slug: slug,
        name: "deeplink-validation-#{:erlang.unique_integer([:positive])}",
        password_hash: password_hash
      },
      [:site_id, :slug, :name, :password_hash]
    )
    |> Plausible.Repo.insert!()

  existing ->
    # Refresh the password hash so a stale seed doesn't fail with
    # "Incorrect password".
    existing
    |> Ecto.Changeset.change(password_hash: password_hash)
    |> Plausible.Repo.update!()
end

IO.puts(
  Jason.encode!(%{
    "domain" => domain,
    "slug" => slug,
    "password" => password
  })
)

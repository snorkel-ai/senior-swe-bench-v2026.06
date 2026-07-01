# Pass-to-pass regression guard: adding the recent-authorizations view must not
# drop the pre-existing default content of the Clients, Policies, and Resources
# :show pages (e.g. defaulting the page to the authorizations view and hiding
# the overview). Exercises only the pre-existing :show render, so it passes on
# the unmodified tree and on any correct solution.
defmodule PortalAuthViews.VerifyDefaultContentTest do
  use PortalWeb.ConnCase, async: true

  import Portal.AccountFixtures
  import Portal.ActorFixtures
  import Portal.DeviceFixtures
  import Portal.ResourceFixtures
  import Portal.GroupFixtures
  import Portal.PolicyFixtures

  test "the Client, Resource and Policy detail pages still render their default content", %{
    conn: conn
  } do
    account = account_fixture()
    admin = admin_actor_fixture(account: account)
    owner = actor_fixture(account: account, name: "ZzVerifyDefaultOwnerEcho")
    client = client_fixture(account: account, actor: owner)
    resource = resource_fixture(account: account, name: "ZzVerifyDefaultResourceEcho")
    group = group_fixture(account: account, name: "ZzVerifyDefaultGroupEcho")
    policy = policy_fixture(account: account, group: group, resource: resource)

    # authorize_conn/2 calls email_otp_provider_fixture/1 (unique per account),
    # so authorize exactly once and reuse the connection.
    authed_conn = conn |> authorize_conn(admin)

    # Client default view (no view/tab switch): the owner is part of the
    # pre-existing default content.
    {:ok, _lv, client_html} = live(authed_conn, ~p"/#{account}/clients/#{client.id}")
    assert client_html =~ "ZzVerifyDefaultOwnerEcho",
           "Client :show default view dropped its pre-existing owner content"

    # Resource default view: the "Grant access" affordance and the group with
    # access are pre-existing default content.
    {:ok, _lv, resource_html} = live(authed_conn, ~p"/#{account}/resources/#{resource.id}")
    assert resource_html =~ "Grant access",
           "Resource :show default view dropped its pre-existing 'Grant access' content"
    assert resource_html =~ "ZzVerifyDefaultGroupEcho",
           "Resource :show default view dropped its pre-existing group-with-access content"

    # Policy default view: the policy's group and resource are pre-existing
    # default content.
    {:ok, _lv, policy_html} = live(authed_conn, ~p"/#{account}/policies/#{policy.id}")
    assert policy_html =~ "ZzVerifyDefaultGroupEcho",
           "Policy :show default view dropped its pre-existing group content"
    assert policy_html =~ "ZzVerifyDefaultResourceEcho",
           "Policy :show default view dropped its pre-existing resource content"
  end
end

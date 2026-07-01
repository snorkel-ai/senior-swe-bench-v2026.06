"""Behavioral verifier for paperless-ngx-perf-document-counts.

Exercises permission-aware ``document_count`` correctness, soft-delete
exclusion, tag-hierarchy descendant counts, custom-fields listability, the
perf budget on /api/tags/ and /api/custom_fields/, and owner CRUD
round-trips, all through the five pre-existing stable list URLs:

  /api/correspondents/   /api/tags/   /api/document_types/
  /api/storage_paths/    /api/custom_fields/
"""

from __future__ import annotations

import os
import sys
import time

# Repo-notes Gotcha #8: PAPERLESS_* env vars MUST be set before Django import.
# Most are set by the Dockerfile ENV; setdefault is defensive for clean envs.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless.settings")
os.environ.setdefault("PAPERLESS_SECRET_KEY", "test-secret-key-benchmark")
os.environ.setdefault("PAPERLESS_DATA_DIR", "/tmp/paperless-data")
os.environ.setdefault("PAPERLESS_MEDIA_ROOT", "/tmp/paperless-media")
os.environ.setdefault("PAPERLESS_CONSUMPTION_DIR", "/tmp/paperless-consume")
os.environ.setdefault("PAPERLESS_REDIS", "redis://localhost:6379")
os.environ.setdefault("PAPERLESS_DISABLE_DBHANDLER", "true")
os.environ.setdefault(
    "PAPERLESS_CACHE_BACKEND",
    "django.core.cache.backends.locmem.LocMemCache",
)
os.environ.setdefault(
    "PAPERLESS_CHANNELS_BACKEND",
    "channels.layers.InMemoryChannelLayer",
)

# Repo-notes § 14: paperless-ngx packages live under src/.
if "/repo/paperless-ngx/src" not in sys.path:
    sys.path.insert(0, "/repo/paperless-ngx/src")

import django  # noqa: E402

django.setup()

import pytest  # noqa: E402
from django.contrib.auth.models import Permission  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from guardian.shortcuts import assign_perm  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from documents.models import Correspondent  # noqa: E402
from documents.models import CustomField  # noqa: E402
from documents.models import CustomFieldInstance  # noqa: E402
from documents.models import Document  # noqa: E402
from documents.models import DocumentType  # noqa: E402
from documents.models import StoragePath  # noqa: E402
from documents.models import Tag  # noqa: E402

# ---------------------------------------------------------------------------
# Page-size guard — keep all records on one page in fixture-scale tests.
# Below ``StandardPagination.max_page_size = 100000``.
# ---------------------------------------------------------------------------
WIDE_PAGE_SIZE = 200

# ---------------------------------------------------------------------------
# Perf fixture sizing — calibrated empirically against the pre-fix and
# post-fix trees inside the built image (cpus=2, SQLite). At this size:
#   /api/tags/  pre-fix: ~4.0s          post-fix: ~0.13s  (≈30x faster)
#   /api/custom_fields/ pre-fix: ~0.37s post-fix: ~0.04s  (≈10x faster)
# Budgets chosen so pre-fix fails clearly (≥1.5x over budget) and post-fix
# has at least ~5x headroom under expected CI jitter.
# ---------------------------------------------------------------------------
PERF_FIXTURE_DOC_COUNT = 8000
PERF_FIXTURE_TAG_COUNT = 100
PERF_FIXTURE_CF_COUNT = 100
PERF_FIXTURE_PERM_COUNT = 2000
TAGS_PERF_BUDGET_SECONDS = 1.5
CUSTOM_FIELDS_PERF_BUDGET_SECONDS = 0.25
TIMING_REPEATS = 3


def _make_user(username, *, is_superuser=False, all_perms=False):
    """Create a user with optional model perms.

    Per repo-notes Gotcha #1, non-superusers need both Django model-level
    perms AND any Guardian object-level perms to see resources they don't
    own. ``all_perms=True`` grants every available Permission row.
    """
    if is_superuser:
        user = User.objects.create_superuser(
            username=username,
            password="testpass123",  # noqa: S106
        )
    else:
        user = User.objects.create_user(
            username=username,
            password="testpass123",  # noqa: S106
        )
        user.is_staff = True
        user.save()

    if all_perms:
        user.user_permissions.add(*Permission.objects.all())
        user = User.objects.get(pk=user.pk)
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _list(client, path):
    """GET ``path`` with a wide page_size, assert 200, return ``results``."""
    sep = "&" if "?" in path else "?"
    full = f"{path}{sep}page_size={WIDE_PAGE_SIZE}"
    response = client.get(full)
    assert response.status_code == 200, (
        f"GET {full} returned {response.status_code}: "
        f"{getattr(response, 'data', response.content)!r}"
    )
    return list(response.data["results"])


def _count_by_name(results):
    return {row["name"]: row["document_count"] for row in results}


# ---------------------------------------------------------------------------
# Small fixture used by T1 (correctness across users), T2 (soft-delete), T7
# (CRUD). Four documents under a single record per matching model so a
# single name lookup tells you what each user sees.
# ---------------------------------------------------------------------------


@pytest.fixture
def small_dataset(db):
    """Create the small correctness/soft-delete fixture and return a namespace.

    Layout:
      Users:
        superuser, owner, recipient, stranger.
      Records (one of each, all named "alpha_" prefixed):
        Correspondent, Tag, DocumentType, StoragePath, CustomField.
      Documents:
        doc_alpha — owned by ``owner``
        doc_beta  — owned by ``stranger`` (recipient holds Guardian
                    view_document on this one)
        doc_gamma — owned by ``stranger``
        doc_delta — no owner (unowned)
      Each document is attached to ALL FIVE matching-model records and
      has a CustomFieldInstance for the alpha custom field.

    Permission semantics:
      - superuser: sees all 4 docs counted everywhere.
      - owner:     sees doc_alpha (owned) + doc_delta (unowned) = 2.
      - recipient: sees doc_beta (Guardian-granted) + doc_delta (unowned)
                   = 2.
      - stranger:  sees doc_beta + doc_gamma (owned) + doc_delta (unowned)
                   = 3.
    """
    superuser = _make_user("verify_superuser", is_superuser=True)
    owner = _make_user("verify_owner", all_perms=True)
    recipient = _make_user("verify_recipient", all_perms=True)
    stranger = _make_user("verify_stranger", all_perms=True)

    c_alpha = Correspondent.objects.create(name="alpha_correspondent")
    t_alpha = Tag.objects.create(name="alpha_tag", color="#aabbcc")
    dt_alpha = DocumentType.objects.create(name="alpha_dt")
    sp_alpha = StoragePath.objects.create(name="alpha_sp", path="alpha/{title}")
    cf_alpha = CustomField.objects.create(
        name="alpha_cf",
        data_type=CustomField.FieldDataType.STRING,
    )

    docs = {}
    for label, doc_owner in (
        ("alpha", owner),
        ("beta", stranger),
        ("gamma", stranger),
        ("delta", None),
    ):
        doc = Document.objects.create(
            title=f"doc_{label}",
            content="content",
            checksum=f"chk-{label}",
            mime_type="application/pdf",
            owner=doc_owner,
            correspondent=c_alpha,
            document_type=dt_alpha,
            storage_path=sp_alpha,
        )
        doc.tags.add(t_alpha)
        CustomFieldInstance.objects.create(
            document=doc,
            field=cf_alpha,
            value_text=f"v_{label}",
        )
        docs[label] = doc

    assign_perm("view_document", recipient, docs["beta"])

    return {
        "users": {
            "superuser": superuser,
            "owner": owner,
            "recipient": recipient,
            "stranger": stranger,
        },
        "docs": docs,
        "alpha_name": "alpha_correspondent",  # all records share the prefix
        "names": {
            "correspondents": "alpha_correspondent",
            "tags": "alpha_tag",
            "document_types": "alpha_dt",
            "storage_paths": "alpha_sp",
            "custom_fields": "alpha_cf",
        },
    }


# ---------------------------------------------------------------------------
# T1 — document_count correctness across all users and all five endpoints.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("actor_role", "expected_count"),
    [
        ("superuser", 4),
        ("owner", 2),
        ("recipient", 2),
        ("stranger", 3),
    ],
)
def test_document_count_per_user_for_matching_models(
    small_dataset,
    actor_role,
    expected_count,
):
    """Every endpoint reflects exactly what each user is allowed to see.

    The same alpha record is created in every endpoint with the same four
    documents linked. Each user's expected count is therefore identical
    across the five endpoints, sourced from the two-layer permission
    model.
    """
    actor = small_dataset["users"][actor_role]
    client = _client(actor)
    names = small_dataset["names"]

    observed = {
        "correspondents": _count_by_name(_list(client, "/api/correspondents/"))[
            names["correspondents"]
        ],
        "tags": _count_by_name(_list(client, "/api/tags/"))[names["tags"]],
        "document_types": _count_by_name(_list(client, "/api/document_types/"))[
            names["document_types"]
        ],
        "storage_paths": _count_by_name(_list(client, "/api/storage_paths/"))[
            names["storage_paths"]
        ],
        "custom_fields": _count_by_name(_list(client, "/api/custom_fields/"))[
            names["custom_fields"]
        ],
    }

    assert observed["correspondents"] == expected_count, (
        f"actor={actor_role} /api/correspondents/ document_count "
        f"observed={observed['correspondents']} expected={expected_count}"
    )
    assert observed["tags"] == expected_count, (
        f"actor={actor_role} /api/tags/ document_count "
        f"observed={observed['tags']} expected={expected_count}"
    )
    assert observed["document_types"] == expected_count, (
        f"actor={actor_role} /api/document_types/ document_count "
        f"observed={observed['document_types']} expected={expected_count}"
    )
    assert observed["storage_paths"] == expected_count, (
        f"actor={actor_role} /api/storage_paths/ document_count "
        f"observed={observed['storage_paths']} expected={expected_count}"
    )
    assert observed["custom_fields"] == expected_count, (
        f"actor={actor_role} /api/custom_fields/ document_count "
        f"observed={observed['custom_fields']} expected={expected_count}"
    )


# ---------------------------------------------------------------------------
# T2 — soft-deleted documents stay excluded from document_count.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_soft_deleted_document_excluded_from_count(small_dataset):
    """Trashing a document decrements its record's count by exactly 1.

    Discriminates implementations that drop the ``deleted_at__isnull=True``
    filter while reshaping the SQL — a real trap because the existing
    ``get_document_count_filter_for_user`` carries that filter implicitly
    via the Q-block.
    """
    superuser = small_dataset["users"]["superuser"]
    client = _client(superuser)
    names = small_dataset["names"]

    before = {
        "correspondents": _count_by_name(_list(client, "/api/correspondents/"))[
            names["correspondents"]
        ],
        "tags": _count_by_name(_list(client, "/api/tags/"))[names["tags"]],
        "document_types": _count_by_name(_list(client, "/api/document_types/"))[
            names["document_types"]
        ],
        "storage_paths": _count_by_name(_list(client, "/api/storage_paths/"))[
            names["storage_paths"]
        ],
        "custom_fields": _count_by_name(_list(client, "/api/custom_fields/"))[
            names["custom_fields"]
        ],
    }
    assert all(v == 4 for v in before.values()), (
        f"pre-delete fixture state inconsistent: {before}"
    )

    # Soft-delete one of the four documents via SoftDeleteModel's default
    # ``.delete()`` (sets ``deleted_at``). The default manager filters it out.
    small_dataset["docs"]["alpha"].delete()

    after = {
        "correspondents": _count_by_name(_list(client, "/api/correspondents/"))[
            names["correspondents"]
        ],
        "tags": _count_by_name(_list(client, "/api/tags/"))[names["tags"]],
        "document_types": _count_by_name(_list(client, "/api/document_types/"))[
            names["document_types"]
        ],
        "storage_paths": _count_by_name(_list(client, "/api/storage_paths/"))[
            names["storage_paths"]
        ],
        "custom_fields": _count_by_name(_list(client, "/api/custom_fields/"))[
            names["custom_fields"]
        ],
    }

    for endpoint, observed in after.items():
        assert observed == 3, (
            f"{endpoint}: expected document_count=3 after soft-delete, "
            f"got {observed} (was {before[endpoint]} before). The fix "
            f"likely dropped the deleted_at IS NULL filter while reshaping "
            f"the SQL."
        )


# ---------------------------------------------------------------------------
# T3 — tag-hierarchy descendant counts are permission-aware.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_tag_hierarchy_descendant_counts_permission_aware(db):
    """Children nested under each top-level tag carry the same permission filter.

    ``TagViewSet.list`` has TWO code paths that build the count
    annotation: the top-level annotation via the mixin AND a separate
    children-listing branch that re-applies the filter to the descendant
    queryset. A correct refactor must update BOTH paths.
    """
    stranger = _make_user("hier_stranger", all_perms=True)
    viewer = _make_user("hier_viewer", all_perms=True)
    superuser = _make_user("hier_super", is_superuser=True)

    parent = Tag.objects.create(name="hier_parent", color="#aabbcc")
    child_visible = Tag.objects.create(
        name="hier_child_visible",
        color="#aabbcc",
        tn_parent=parent,
    )
    child_hidden = Tag.objects.create(
        name="hier_child_hidden",
        color="#aabbcc",
        tn_parent=parent,
    )

    doc_visible = Document.objects.create(
        title="hier_doc_visible",
        content="...",
        checksum="hier-chk-vis",
        mime_type="application/pdf",
        owner=stranger,
    )
    doc_hidden = Document.objects.create(
        title="hier_doc_hidden",
        content="...",
        checksum="hier-chk-hid",
        mime_type="application/pdf",
        owner=stranger,
    )
    doc_visible.tags.add(parent, child_visible)
    doc_hidden.tags.add(parent, child_hidden)

    assign_perm("view_document", viewer, doc_visible)

    # Superuser sees everything: parent=2, both children=1 each.
    super_results = _list(_client(superuser), "/api/tags/")
    super_parent = next(r for r in super_results if r["name"] == "hier_parent")
    assert super_parent["document_count"] == 2
    super_kids = {c["name"]: c["document_count"] for c in super_parent["children"]}
    assert super_kids["hier_child_visible"] == 1
    assert super_kids["hier_child_hidden"] == 1

    # Viewer with Guardian perm only on doc_visible: parent=1, visible=1,
    # hidden=0. The hidden child's count drops to 0 ONLY if the descendant
    # branch in TagViewSet.list re-applies the permission filter.
    viewer_results = _list(_client(viewer), "/api/tags/")
    viewer_parent = next(r for r in viewer_results if r["name"] == "hier_parent")
    viewer_kids = {c["name"]: c["document_count"] for c in viewer_parent["children"]}
    assert viewer_parent["document_count"] == 1, (
        f"viewer parent count={viewer_parent['document_count']}, expected 1"
    )
    assert viewer_kids["hier_child_visible"] == 1, (
        f"viewer visible-child count={viewer_kids['hier_child_visible']}, "
        f"expected 1"
    )
    assert viewer_kids["hier_child_hidden"] == 0, (
        f"viewer hidden-child count={viewer_kids['hier_child_hidden']}, "
        f"expected 0. The descendant code path in TagViewSet.list is not "
        f"using the same permission filter as the top-level annotation."
    )


# ---------------------------------------------------------------------------
# T4 — /api/custom_fields/ remains listable after any mixin rewiring.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("full_perms_qs", ["", "?full_perms=true"])
def test_custom_fields_endpoint_listable_with_full_perms(db, full_perms_qs):
    """``/api/custom_fields/`` returns 200 with and without ``?full_perms=true``.

    Discriminates the ``unexpected keyword argument 'user'`` regression:
    adopting ``PermissionsAwareDocumentCountMixin`` (which inherits
    ``PassUserMixin``) into ``CustomFieldViewSet`` wires ``user`` and
    ``full_perms`` kwargs into ``CustomFieldSerializer.__init__``. The
    serializer must handle them (e.g. by popping them) or every list call
    raises TypeError before the response is built.
    """
    user = _make_user("cf_user", all_perms=True)
    CustomField.objects.create(
        name="cf_listable_alpha",
        data_type=CustomField.FieldDataType.STRING,
    )
    CustomField.objects.create(
        name="cf_listable_beta",
        data_type=CustomField.FieldDataType.INT,
    )

    client = _client(user)
    results = _list(client, f"/api/custom_fields/{full_perms_qs}")
    names = {r["name"] for r in results}
    assert "cf_listable_alpha" in names, (
        f"/api/custom_fields/{full_perms_qs} did not return alpha record. "
        f"results={results!r}"
    )
    assert "cf_listable_beta" in names, (
        f"/api/custom_fields/{full_perms_qs} did not return beta record."
    )


# ---------------------------------------------------------------------------
# T5/T6 — perf budget on /api/tags/ and /api/custom_fields/ for non-superuser.
# ---------------------------------------------------------------------------


@pytest.fixture
def perf_dataset(db):
    """Bulk-seed the perf fixture and return the non-superuser to test as.

    Layout:
      - PERF_FIXTURE_DOC_COUNT documents owned by ``perf_other_owner`` (so
        ``perf_user`` can only see them via Guardian view_document grants
        or the unowned route — never via the owner branch).
      - PERF_FIXTURE_TAG_COUNT tags, every tag attached to half of the
        documents via the Document.tags M2M through table.
      - PERF_FIXTURE_CF_COUNT CustomField rows, with a CustomFieldInstance
        for every {custom_field, doc[:half]} pair.
      - PERF_FIXTURE_PERM_COUNT Guardian view_document grants to ``perf_user``.

    Sized to amplify the pre-fix multi-OR-JOIN cost on the /api/tags/ and
    /api/custom_fields/ count annotations to a measurable ~4s and ~0.4s,
    while the structurally-different post-fix SQL stays at ~0.1s and
    ~0.04s. The setup is bulk-create-driven and completes in ~50s on
    SQLite.
    """
    user = _make_user("perf_user", all_perms=True)
    other_owner = _make_user("perf_other_owner", all_perms=True)

    cfs = [
        CustomField.objects.create(
            name=f"perf_cf_{i:03d}",
            data_type=CustomField.FieldDataType.STRING,
        )
        for i in range(PERF_FIXTURE_CF_COUNT)
    ]

    # Bulk-create documents owned by other_owner so user can only see them
    # via Guardian or unowned routes — exercises the slow filter path.
    Document.objects.bulk_create(
        [
            Document(
                title=f"perf_doc_{i:05d}",
                content="benchmark",
                checksum=f"perf-{i:08d}",
                mime_type="application/pdf",
                owner=other_owner,
            )
            for i in range(PERF_FIXTURE_DOC_COUNT)
        ],
        batch_size=500,
    )
    docs = list(
        Document.objects.filter(checksum__startswith="perf-").order_by("id"),
    )
    assert len(docs) == PERF_FIXTURE_DOC_COUNT

    # Bulk-create tags and attach via through-table for half the docs each.
    tags = [
        Tag.objects.create(name=f"perf_tag_{i:03d}", color="#aabbcc")
        for i in range(PERF_FIXTURE_TAG_COUNT)
    ]
    half = PERF_FIXTURE_DOC_COUNT // 2
    Through = Document.tags.through  # noqa: N806
    Through.objects.bulk_create(
        [
            Through(document_id=docs[i].id, tag_id=tag.id)
            for tag in tags
            for i in range(half)
        ],
        batch_size=2000,
    )

    # Many-to-many CustomFieldInstance rows — each cf has half the docs
    # tagged. With 100 cfs × 4000 docs = 400K rows the pre-fix
    # ``fields__document__id__in=permitted_ids`` JOIN through Document
    # becomes measurably costly.
    CustomFieldInstance.objects.bulk_create(
        [
            CustomFieldInstance(document=docs[i], field=cf, value_text="x")
            for cf in cfs
            for i in range(half)
        ],
        batch_size=2000,
    )

    for i in range(PERF_FIXTURE_PERM_COUNT):
        assign_perm("view_document", user, docs[i])

    return user


def _time_get(client, path):
    start = time.perf_counter()
    resp = client.get(path)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200, (
        f"GET {path} returned {resp.status_code}: "
        f"{getattr(resp, 'data', resp.content)!r}"
    )
    return elapsed


@pytest.mark.django_db(transaction=True)
def test_tags_endpoint_meets_perf_budget_for_non_superuser(perf_dataset):
    """``GET /api/tags/`` median latency under TAGS_PERF_BUDGET_SECONDS.

    Pre-fix shape's multi-OR JOIN through Document blows up the SQLite
    planner; the post-fix code (and any structurally-different plan)
    finishes well under the budget. Empirically: pre-fix ~4.0s, post-fix ~0.13s.
    """
    client = _client(perf_dataset)
    timings = sorted(
        _time_get(client, "/api/tags/") for _ in range(TIMING_REPEATS)
    )
    median = timings[len(timings) // 2]
    assert median < TAGS_PERF_BUDGET_SECONDS, (
        f"GET /api/tags/ median latency {median:.3f}s exceeds budget "
        f"{TAGS_PERF_BUDGET_SECONDS}s. Sorted timings: {timings}. The "
        f"pre-fix multi-OR JOIN through Document is the most common cause; "
        f"switch to a permitted-document-id subquery and/or count directly "
        f"against the tags M2M through table."
    )


@pytest.mark.django_db(transaction=True)
def test_custom_fields_endpoint_meets_perf_budget_for_non_superuser(perf_dataset):
    """``GET /api/custom_fields/`` median latency under CUSTOM_FIELDS_PERF_BUDGET_SECONDS.

    Empirically on this fixture: pre-fix ~0.37s, post-fix ~0.04s. Budget of
    0.25s gives the post-fix code ~6x headroom and reliably fails pre-fix.
    """
    client = _client(perf_dataset)
    timings = sorted(
        _time_get(client, "/api/custom_fields/") for _ in range(TIMING_REPEATS)
    )
    median = timings[len(timings) // 2]
    assert median < CUSTOM_FIELDS_PERF_BUDGET_SECONDS, (
        f"GET /api/custom_fields/ median latency {median:.3f}s exceeds "
        f"budget {CUSTOM_FIELDS_PERF_BUDGET_SECONDS}s. Sorted timings: "
        f"{timings}. The pre-fix multi-OR JOIN through Document is the "
        f"most common cause; switch to a permitted-document-id subquery "
        f"and/or count directly against CustomFieldInstance."
    )


# ---------------------------------------------------------------------------
# T7 — owner-only CRUD round-trips on every affected endpoint (pass_to_pass).
# ---------------------------------------------------------------------------


CRUD_CASES = [
    # (endpoint, create_body, patch_body)
    (
        "/api/correspondents/",
        {"name": "crud_correspondent"},
        {"name": "crud_correspondent_renamed"},
    ),
    (
        "/api/tags/",
        {"name": "crud_tag", "color": "#aabbcc"},
        {"name": "crud_tag_renamed"},
    ),
    (
        "/api/document_types/",
        {"name": "crud_dt"},
        {"name": "crud_dt_renamed"},
    ),
    (
        "/api/storage_paths/",
        {"name": "crud_sp", "path": "x/{title}"},
        {"path": "y/{title}"},
    ),
    (
        "/api/custom_fields/",
        {"name": "crud_cf", "data_type": "string"},
        # CustomField data_type is editable=False; patch the name field.
        {"name": "crud_cf_renamed"},
    ),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("endpoint", "create_body", "patch_body"), CRUD_CASES)
def test_owner_crud_round_trip_unchanged(db, endpoint, create_body, patch_body):
    """POST/GET/PATCH/DELETE round-trip works on every affected endpoint.

    Pure pass_to_pass regression guard. The agent's only behavioral
    change is the document_count query plan — every other interaction
    must continue to behave as before.
    """
    user = _make_user("crud_owner", is_superuser=True)
    client = _client(user)

    create_resp = client.post(endpoint, create_body, format="json")
    assert create_resp.status_code == 201, (
        f"POST {endpoint} {create_body!r} → {create_resp.status_code}: "
        f"{getattr(create_resp, 'data', create_resp.content)!r}"
    )
    record_id = create_resp.data["id"]

    list_resp = client.get(f"{endpoint}?page_size={WIDE_PAGE_SIZE}")
    assert list_resp.status_code == 200
    assert list_resp.data["count"] == 1

    detail_resp = client.get(f"{endpoint}{record_id}/")
    assert detail_resp.status_code == 200
    # Sanity: the record is the one we just created.
    if "name" in create_body:
        assert detail_resp.data["name"] == create_body["name"]

    patch_resp = client.patch(
        f"{endpoint}{record_id}/",
        patch_body,
        format="json",
    )
    assert patch_resp.status_code == 200, (
        f"PATCH {endpoint}{record_id}/ {patch_body!r} → "
        f"{patch_resp.status_code}: "
        f"{getattr(patch_resp, 'data', patch_resp.content)!r}"
    )

    delete_resp = client.delete(f"{endpoint}{record_id}/")
    assert delete_resp.status_code == 204

    final_resp = client.get(f"{endpoint}?page_size={WIDE_PAGE_SIZE}")
    assert final_resp.status_code == 200
    assert final_resp.data["count"] == 0

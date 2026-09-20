"""巡查评价变更与问题记录联动的测试。"""

from datetime import datetime, timedelta

from tests.conftest import full_items


def _abnormal_inspection(client, restroom, low_indexes):
    items = full_items(9)
    for idx in low_indexes:
        items[idx]["score"] = 3
    response = client.post(
        "/api/v1/inspections",
        json={"restroom_id": restroom["id"], "inspector": "联动测试员", "items": items},
    )
    assert response.status_code == 201, response.text
    return response.json(), items


def _list_issues(client, restroom_id, **extra):
    params = {"restroom_id": restroom_id, "page_size": 100, **extra}
    return client.get("/api/v1/issues", params=params).json()


def test_rescore_to_normal_voids_open_issues_and_excludes_from_stats(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0, 3])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()
    assert issue["auto_registered"] is True
    assert issue["voided"] is False

    before = client.get("/api/v1/stats/overview").json()["issue_total"]

    # 改回全部达标：adjust 模式下原问题应作废
    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        params={"issue_mode": "adjust"},
        json={"items": full_items(9)},
    ).json()
    assert updated["result"] == "正常"
    assert updated["issue_sync"]["voided"] == 1
    assert updated["issue_sync"]["created"] == 0
    assert updated["issue_count"] == 0

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["voided"] is True
    assert detail["status"] == "已关闭"
    assert detail["closed_at"] is not None
    assert detail["records"][-1]["action"] == "评价联动作废"

    # 默认问题列表不包含作废问题，看板总数与台账详情都不再计入
    assert _list_issues(client, restroom["id"])["meta"]["total"] == 0
    after = client.get("/api/v1/stats/overview").json()["issue_total"]
    assert after == before - 1
    restroom_detail = client.get(f"/api/v1/restrooms/{restroom['id']}").json()
    assert restroom_detail["total_issue_count"] == 0
    assert restroom_detail["open_issue_count"] == 0

    # 作废问题无法再流转
    blocked = client.post(
        f"/api/v1/issues/{issue['id']}/transitions",
        json={"to_status": "整改中", "operator": "张三"},
    )
    assert blocked.status_code == 400

    # 显式查询仍能看到作废痕迹
    voided = _list_issues(client, restroom["id"], voided_only="true")
    assert voided["meta"]["total"] == 1
    assert voided["items"][0]["id"] == issue["id"]


def test_rescore_adjust_keeps_and_changes_severity_and_category(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()
    # 3 分 -> 一般
    assert issue["severity"] == "一般"
    assert issue["category"] == "保洁不到位"

    # 同一项改到 0 分（仍不达标）：保留问题，严重程度升到紧急
    items = full_items(9)
    items[0]["score"] = 0
    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}", json={"items": items}
    ).json()
    assert updated["issue_sync"]["kept"] == 1
    assert updated["issue_sync"]["voided"] == 0
    assert updated["issue_sync"]["created"] == 0

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["severity"] == "紧急"
    assert detail["status"] == "待整改"
    assert detail["records"][-1]["action"] == "评价联动调整"
    assert "严重程度" in detail["records"][-1]["remark"]
    assert _list_issues(client, restroom["id"])["meta"]["total"] == 1


def test_rescore_creates_issue_for_new_failing_item(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    )

    # 地面改达标，通风（index 3）新出问题：旧问题作废 + 自动登记一条新问题
    items = full_items(9)
    items[3]["score"] = 2
    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}", json={"items": items}
    ).json()
    assert updated["issue_sync"]["voided"] == 1
    assert updated["issue_sync"]["created"] == 1

    rows = _list_issues(client, restroom["id"])["items"]
    assert len(rows) == 1
    new_issue = rows[0]
    assert new_issue["source_item"] == "通风除臭"
    assert new_issue["category"] == "异味扰民"
    assert new_issue["severity"] == "严重"
    assert new_issue["status"] == "待整改"
    assert new_issue["inspection_id"] == inspection["id"]
    assert new_issue["records"][0]["action"] == "评价联动登记"


def test_keep_mode_leaves_issues_untouched(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()

    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        params={"issue_mode": "keep"},
        json={"items": full_items(9)},
    ).json()
    assert updated["issue_sync"] is None

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["voided"] is False
    assert detail["status"] == "待整改"


def test_void_mode_closes_all_managed_issues(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0, 3])
    for item_name in ("地面与台阶清洁", "通风除臭"):
        client.post(
            "/api/v1/issues",
            json={
                "restroom_id": restroom["id"],
                "inspection_id": inspection["id"],
                "source_item": item_name,
                "title": item_name,
                "reporter": "联动测试员",
            },
        )

    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        params={"issue_mode": "void"},
        json={"items": full_items(3)},
    ).json()
    assert updated["issue_sync"]["voided"] == 2
    assert _list_issues(client, restroom["id"])["meta"]["total"] == 0


def test_manual_issue_without_source_item_is_not_reconciled(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    manual = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "title": "群众反映门锁坏了",
            "reporter": "群众",
        },
    ).json()
    assert manual["auto_registered"] is False

    client.patch(
        f"/api/v1/inspections/{inspection['id']}", json={"items": full_items(9)}
    )
    detail = client.get(f"/api/v1/issues/{manual['id']}").json()
    assert detail["voided"] is False
    assert detail["status"] == "待整改"


def test_delete_inspection_default_voids_issues(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()

    result = client.delete(f"/api/v1/inspections/{inspection['id']}")
    assert result.status_code == 200
    assert "1 条问题已作废" in result.json()["message"]
    assert client.get(f"/api/v1/inspections/{inspection['id']}").status_code == 404

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["voided"] is True
    assert detail["status"] == "已关闭"
    assert detail["records"][-1]["action"] == "巡查删除作废"
    assert _list_issues(client, restroom["id"])["meta"]["total"] == 0


def test_delete_inspection_unlink_keeps_issue(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()

    result = client.delete(
        f"/api/v1/inspections/{inspection['id']}", params={"issue_mode": "unlink"}
    )
    assert "解除 1 条问题" in result.json()["message"]

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["inspection_id"] is None
    assert detail["voided"] is False
    assert detail["status"] == "待整改"


def test_delete_inspection_delete_mode_removes_issues(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()

    result = client.delete(
        f"/api/v1/inspections/{inspection['id']}", params={"issue_mode": "delete"}
    )
    assert "1 条问题已删除" in result.json()["message"]
    assert client.get(f"/api/v1/issues/{issue['id']}").status_code == 404


def test_completed_issue_marked_voided_not_reopened(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
            "deadline": (datetime.now() + timedelta(days=3)).isoformat(),
        },
    ).json()
    for target, operator in (("整改中", "班组"), ("待验收", "班组"), ("已完成", "巡查员")):
        client.post(
            f"/api/v1/issues/{issue['id']}/transitions",
            json={"to_status": target, "operator": operator},
        )

    client.patch(
        f"/api/v1/inspections/{inspection['id']}", json={"items": full_items(9)}
    )
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    # 已完成的历史工单不重新打开，仅标记作废、移出统计
    assert detail["voided"] is True
    assert detail["status"] == "已完成"


def test_invalid_issue_mode_rejected(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    bad_patch = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        params={"issue_mode": "bogus"},
        json={"items": full_items(9)},
    )
    assert bad_patch.status_code == 422
    bad_delete = client.delete(
        f"/api/v1/inspections/{inspection['id']}", params={"issue_mode": "bogus"}
    )
    assert bad_delete.status_code == 422


def test_source_item_must_belong_to_linked_inspection(client, restroom):
    inspection, _ = _abnormal_inspection(client, restroom, [0])
    rejected = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "不存在的检查项",
            "title": "错误来源",
        },
    )
    assert rejected.status_code == 400
    assert "不存在检查项" in rejected.json()["detail"]

    no_inspection = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "source_item": "地面与台阶清洁",
            "title": "缺少巡查",
        },
    )
    assert no_inspection.status_code == 400


def test_reconcile_is_atomic_on_midway_failure(client, restroom, monkeypatch):
    """联动登记新问题的过程中失败时，巡查改分与已做的作废都必须整体回滚。"""
    from app.core.database import SessionLocal
    from app.services import inspection_sync

    inspection, _ = _abnormal_inspection(client, restroom, [0])
    original_issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "source_item": "地面与台阶清洁",
            "title": "地面污渍",
            "reporter": "联动测试员",
        },
    ).json()

    # 地面改达标、通风新出问题：流程会先作废旧问题再登记新问题；
    # 在登记环节制造异常，验证整批回滚，不留下“旧问题已作废、新问题没登记”的中间态。
    def boom(db, inspection_obj, item):  # noqa: ANN001
        raise RuntimeError("模拟联动登记失败")

    monkeypatch.setattr(inspection_sync, "_register_issue", boom)

    items = full_items(9)
    items[3]["score"] = 2
    # TestClient 默认把服务端异常直接抛出，这里等价于接口返回 500
    import pytest

    with pytest.raises(RuntimeError, match="模拟联动登记失败"):
        client.patch(
            f"/api/v1/inspections/{inspection['id']}", json={"items": items}
        )

    # 巡查评分仍是旧值
    detail = client.get(f"/api/v1/inspections/{inspection['id']}").json()
    assert detail["result"] == "发现问题"
    assert detail["items"][0]["score"] == 3

    # 旧问题没有被作废，新问题没有产生
    issue = client.get(f"/api/v1/issues/{original_issue['id']}").json()
    assert issue["voided"] is False
    assert issue["status"] == "待整改"
    assert _list_issues(client, restroom["id"])["meta"]["total"] == 1

    # 底层会话仍可正常使用（说明事务已干净回滚），且该巡查名下没有残留的新问题
    with SessionLocal() as db:
        from app.models import Issue
        from sqlalchemy import func, select

        count = db.scalar(
            select(func.count())
            .select_from(Issue)
            .where(Issue.inspection_id == inspection["id"])
        )
    assert count == 1

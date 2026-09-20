"""接口级测试：覆盖台账、巡查、问题整改与统计看板。"""

from datetime import datetime, timedelta

import pytest

from tests.conftest import full_items


def test_health_and_dictionaries(client):
    assert client.get("/health").json()["status"] == "ok"
    payload = client.get("/api/v1/meta/dictionaries").json()
    assert "待整改" in payload["issue_status"]
    assert len(payload["inspection_check_items"]) == 8
    assert payload["issue_transitions"]["待整改"] == ["整改中", "已关闭"]


def test_restroom_crud_and_delete_guard(client, restroom):
    assert restroom["code"].startswith("WC-")

    listed = client.get("/api/v1/restrooms", params={"district": "测试区"}).json()
    assert listed["meta"]["total"] >= 1

    detail = client.get(f"/api/v1/restrooms/{restroom['id']}").json()
    assert detail["inspection_count"] == 0
    assert detail["open_issue_count"] == 0

    updated = client.patch(
        f"/api/v1/restrooms/{restroom['id']}", json={"status": "维修中", "manager": "新责任人"}
    ).json()
    assert updated["status"] == "维修中"
    assert updated["manager"] == "新责任人"

    # 存在关联数据时不允许直接删除
    client.post(
        "/api/v1/inspections",
        json={
            "restroom_id": restroom["id"],
            "inspector": "测试巡查员",
            "shift": "早班",
            "items": full_items(9),
        },
    )
    blocked = client.delete(f"/api/v1/restrooms/{restroom['id']}")
    assert blocked.status_code == 409

    ok = client.delete(f"/api/v1/restrooms/{restroom['id']}", params={"force": "true"})
    assert ok.status_code == 200
    assert client.get(f"/api/v1/restrooms/{restroom['id']}").status_code == 404


def test_inspection_scoring_and_filter(client, restroom):
    good = client.post(
        "/api/v1/inspections",
        json={
            "restroom_id": restroom["id"],
            "inspector": "李巡查",
            "shift": "中班",
            "items": full_items(9),
            "remark": "整体良好",
        },
    ).json()
    assert good["score"] == 90.0
    assert good["grade"] == "优秀"
    assert good["result"] == "正常"

    bad_items = full_items(9)
    bad_items[0]["score"] = 3
    bad_items[0]["remark"] = "地面污渍"
    bad = client.post(
        "/api/v1/inspections",
        json={
            "restroom_id": restroom["id"],
            "inspector": "李巡查",
            "shift": "晚班",
            "items": bad_items,
        },
    ).json()
    assert bad["result"] == "发现问题"
    assert bad["score"] < 90

    filtered = client.get(
        "/api/v1/inspections", params={"result": "发现问题", "restroom_id": restroom["id"]}
    ).json()
    assert filtered["meta"]["total"] == 1
    assert filtered["items"][0]["id"] == bad["id"]
    assert filtered["items"][0]["restroom"]["name"] == restroom["name"]

    today = datetime.now().date().isoformat()
    ranged = client.get(
        "/api/v1/inspections", params={"date_from": today, "date_to": today}
    ).json()
    assert ranged["meta"]["total"] == 2

    duplicate = full_items(5) + [{"name": "地面与台阶清洁", "score": 4}]
    rejected = client.post(
        "/api/v1/inspections",
        json={"restroom_id": restroom["id"], "inspector": "李巡查", "items": duplicate},
    )
    assert rejected.status_code == 400

    empty = client.post(
        "/api/v1/inspections",
        json={"restroom_id": restroom["id"], "inspector": "李巡查", "items": []},
    )
    assert empty.status_code == 422


def test_issue_lifecycle(client, restroom):
    inspection = client.post(
        "/api/v1/inspections",
        json={
            "restroom_id": restroom["id"],
            "inspector": "王巡查",
            "items": full_items(4),
        },
    ).json()

    issue = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "title": "地面污渍未清理",
            "description": "巡查发现地面有明显污渍",
            "category": "保洁不到位",
            "severity": "严重",
            "reporter": "王巡查",
            "assignee": "保洁班组",
            "deadline": (datetime.now() - timedelta(days=1)).isoformat(),
        },
    ).json()
    assert issue["status"] == "待整改"
    assert len(issue["records"]) == 1
    assert issue["records"][0]["action"] == "上报问题"

    # 越级流转被拒绝：待整改 -> 已完成
    invalid = client.post(
        f"/api/v1/issues/{issue['id']}/transitions",
        json={"to_status": "已完成", "operator": "值班长"},
    )
    assert invalid.status_code == 400
    assert "不允许流转" in invalid.json()["detail"]

    options = client.get(f"/api/v1/issues/{issue['id']}/transitions").json()
    assert {option["status"] for option in options} == {"整改中", "已关闭"}

    processing = client.post(
        f"/api/v1/issues/{issue['id']}/transitions",
        json={"to_status": "整改中", "operator": "保洁班组张伟", "remark": "已安排清洗"},
    ).json()
    assert processing["status"] == "整改中"
    assert processing["assignee"] == "保洁班组"

    reviewing = client.post(
        f"/api/v1/issues/{issue['id']}/transitions",
        json={"to_status": "待验收", "operator": "保洁班组张伟", "remark": "整改完成待验收"},
    ).json()
    assert reviewing["status"] == "待验收"

    # 验收驳回回到整改中
    rejected = client.post(
        f"/api/v1/issues/{issue['id']}/transitions",
        json={"to_status": "整改中", "operator": "王巡查", "remark": "角落仍有残留"},
    ).json()
    assert rejected["status"] == "整改中"
    assert rejected["records"][-1]["action"] == "验收驳回"

    for target in ("待验收", "已完成", "已关闭"):
        payload = {"to_status": target, "operator": "值班长", "remark": f"流转到{target}"}
        response = client.post(f"/api/v1/issues/{issue['id']}/transitions", json=payload)
        assert response.status_code == 200, response.text
    final = response.json()
    assert final["status"] == "已关闭"
    assert final["closed_at"] is not None
    assert [record["to_status"] for record in final["records"]][-1] == "已关闭"

    closed_record = client.post(
        f"/api/v1/issues/{issue['id']}/records",
        json={"action": "整改进度", "operator": "值班长", "remark": "补充说明"},
    )
    assert closed_record.status_code == 400

    overdue = client.get("/api/v1/issues", params={"overdue": "true"}).json()
    assert overdue["meta"]["total"] == 0

    # 巡查记录可反查关联问题数量
    detail = client.get(f"/api/v1/inspections/{inspection['id']}").json()
    assert detail["issue_count"] == 1


def test_issue_requires_matching_restroom(client, restroom):
    other = client.post(
        "/api/v1/restrooms",
        json={"name": "另一座公厕", "district": "测试区", "address": "测试路 2 号"},
    ).json()
    inspection = client.post(
        "/api/v1/inspections",
        json={"restroom_id": other["id"], "inspector": "周巡查", "items": full_items(9)},
    ).json()
    mismatch = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "title": "关联错误",
        },
    )
    assert mismatch.status_code == 400
    assert "不一致" in mismatch.json()["detail"]


def test_dashboard_stats(client, restroom):
    payload = client.get("/api/v1/stats/dashboard", params={"trend_days": 7}).json()
    overview = payload["overview"]
    assert overview["restroom_total"] >= 1
    assert overview["inspection_total"] >= 1
    assert len(payload["inspection_trend"]) == 7
    assert {item["name"] for item in payload["issue_by_status"]} == {
        "待整改",
        "整改中",
        "待验收",
        "已完成",
        "已关闭",
    }
    assert payload["top_restrooms"]
    assert "rectification_rate" in overview


def _create_inspection(client, restroom_id: int, items: list[dict]) -> dict:
    response = client.post(
        "/api/v1/inspections",
        json={"restroom_id": restroom_id, "inspector": "李巡查", "items": items},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_issue(client, restroom_id: int, inspection_id: int, **extra) -> dict:
    payload = {
        "restroom_id": restroom_id,
        "inspection_id": inspection_id,
        "title": "巡查发现问题",
        **extra,
    }
    response = client.post("/api/v1/issues", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_inspection_rescore_syncs_issues(client, restroom):
    # 巡查：地面 4 分、蹲位 3 分、通风 5 分，三项不达标
    items = full_items(9)
    items[0]["score"] = 4  # 地面与台阶清洁
    items[1]["score"] = 3  # 便池蹲位清洁
    items[3]["score"] = 5  # 通风除臭
    inspection = _create_inspection(client, restroom["id"], items)
    assert inspection["result"] == "发现问题"

    floor = _create_issue(
        client,
        restroom["id"],
        inspection["id"],
        check_item="地面与台阶清洁",
        title="地面污渍未清理",
        category="保洁不到位",
        severity="严重",
    )
    odor = _create_issue(
        client,
        restroom["id"],
        inspection["id"],
        check_item="通风除臭",
        title="公厕内异味明显",
        category="异味扰民",
        severity="一般",
    )
    # 蹲位得分保持不变，严重程度为人工指定的「紧急」（与按分推算的「严重」不同）
    steady = _create_issue(
        client,
        restroom["id"],
        inspection["id"],
        check_item="便池蹲位清洁",
        title="蹲位清洁不彻底",
        category="保洁不到位",
        severity="紧急",
    )
    manual = _create_issue(client, restroom["id"], inspection["id"], title="标识牌褪色")

    # 改分：地面 4→2（仍不达标，严重程度应升级为紧急）；通风 5→9（达标，问题应作废）；蹲位保持 3 分
    rescored = full_items(9)
    rescored[0]["score"] = 2
    rescored[1]["score"] = 3
    response = client.patch(f"/api/v1/inspections/{inspection['id']}", json={"items": rescored})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["issue_sync"] == {"kept": 2, "adjusted": 1, "voided": 1}

    # 调整：仍不达标且分数变化 -> 保留待整改，严重程度随分数重算，轨迹新增联动记录
    floor_after = client.get(f"/api/v1/issues/{floor['id']}").json()
    assert floor_after["status"] == "待整改"
    assert floor_after["severity"] == "紧急"
    assert floor_after["records"][-1]["action"] == "巡查改分联动"
    assert "紧急" in floor_after["records"][-1]["remark"]

    # 作废：改分后达标 -> 自动作废关闭
    odor_after = client.get(f"/api/v1/issues/{odor['id']}").json()
    assert odor_after["status"] == "已关闭"
    assert odor_after["closed_at"] is not None
    assert odor_after["records"][-1]["action"] == "巡查改分作废"
    assert odor_after["records"][-1]["to_status"] == "已关闭"

    # 保留：来源检查项得分未变 -> 人工指定的严重程度不被改写，不新增流水
    steady_after = client.get(f"/api/v1/issues/{steady['id']}").json()
    assert steady_after["status"] == "待整改"
    assert steady_after["severity"] == "紧急"
    assert len(steady_after["records"]) == 1

    # 保留：手工上报（无来源检查项）的问题不受影响
    manual_after = client.get(f"/api/v1/issues/{manual['id']}").json()
    assert manual_after["status"] == "待整改"
    assert len(manual_after["records"]) == 1

    # 看板与台账口径一致：未闭环问题数 = 待整改的 3 条
    detail = client.get(f"/api/v1/restrooms/{restroom['id']}").json()
    assert detail["open_issue_count"] == 3
    open_list = client.get(
        "/api/v1/issues", params={"restroom_id": restroom["id"], "open_only": "true"}
    ).json()
    assert open_list["meta"]["total"] == 3

    # 未改动检查项时不触发联动
    untouched = client.patch(
        f"/api/v1/inspections/{inspection['id']}", json={"remark": "补充说明"}
    ).json()
    assert untouched["issue_sync"] is None


def test_inspection_item_removed_and_inflight_issue_kept(client, restroom):
    items = full_items(9)
    items[1]["score"] = 3  # 便池蹲位清洁 不达标
    inspection = _create_inspection(client, restroom["id"], items)

    pending = _create_issue(
        client, restroom["id"], inspection["id"], check_item="便池蹲位清洁", title="蹲位残留"
    )
    inflight = _create_issue(
        client, restroom["id"], inspection["id"], check_item="便池蹲位清洁", title="蹲位污渍"
    )
    started = client.post(
        f"/api/v1/issues/{inflight['id']}/transitions",
        json={"to_status": "整改中", "operator": "保洁班组"},
    )
    assert started.status_code == 200

    # 从巡查中删掉「便池蹲位清洁」检查项
    reduced = [item for item in full_items(9) if item["name"] != "便池蹲位清洁"]
    updated = client.patch(f"/api/v1/inspections/{inspection['id']}", json={"items": reduced})
    assert updated.status_code == 200, updated.text
    assert updated.json()["issue_sync"] == {"kept": 1, "adjusted": 0, "voided": 1}

    # 待整改问题：来源检查项被删除 -> 作废关闭
    pending_after = client.get(f"/api/v1/issues/{pending['id']}").json()
    assert pending_after["status"] == "已关闭"
    assert "已从本次巡查中删除" in pending_after["records"][-1]["remark"]

    # 已进入整改流程的问题：保留不动，不重写历史
    inflight_after = client.get(f"/api/v1/issues/{inflight['id']}").json()
    assert inflight_after["status"] == "整改中"
    assert all(record["action"] != "巡查改分作废" for record in inflight_after["records"])


def test_inspection_update_rolls_back_when_sync_fails(client, restroom, monkeypatch):
    items = full_items(9)
    items[0]["score"] = 4
    inspection = _create_inspection(client, restroom["id"], items)
    issue = _create_issue(
        client, restroom["id"], inspection["id"], check_item="地面与台阶清洁", title="地面污渍"
    )

    from app.services import issue_service

    def boom(*args, **kwargs):
        raise RuntimeError("模拟联动中途失败")

    monkeypatch.setattr(issue_service, "sync_issues_for_inspection", boom)

    with pytest.raises(RuntimeError):
        client.patch(
            f"/api/v1/inspections/{inspection['id']}", json={"items": full_items(9)}
        )

    # 中途失败整体回滚：改分未生效，问题集合保持原样
    after = client.get(f"/api/v1/inspections/{inspection['id']}").json()
    assert after["score"] == inspection["score"]
    assert after["result"] == "发现问题"
    issue_after = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert issue_after["status"] == "待整改"
    assert len(issue_after["records"]) == 1


def test_issue_check_item_validation(client, restroom):
    inspection = _create_inspection(client, restroom["id"], full_items(9))

    unknown = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "inspection_id": inspection["id"],
            "check_item": "不存在的检查项",
            "title": "来源检查项有误",
        },
    )
    assert unknown.status_code == 400
    assert "来源检查项" in unknown.json()["detail"]

    no_inspection = client.post(
        "/api/v1/issues",
        json={
            "restroom_id": restroom["id"],
            "check_item": "地面与台阶清洁",
            "title": "未关联巡查",
        },
    )
    assert no_inspection.status_code == 400

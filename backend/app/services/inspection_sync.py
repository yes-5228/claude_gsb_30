"""巡查评价变更与问题记录的联动。

一次巡查的检查项重新打分（或整条巡查删除）后，当次巡查登记出来的问题需要
随之联动。联动以「检查项」为单位对问题集合做一次性对账：

- 改分后仍不达标的检查项：保留其问题，按新分数调整分类与严重程度；
- 改分后达标（或检查项被删除）的：原问题作废，保留闭环痕迹但不再计入统计；
- 新出现的不达标检查项：自动登记新问题。

所有联动都在调用方的事务内完成（本模块不提交事务），配合 service 层的单次
commit / rollback，保证中途失败时不会留下改了一半的问题集合。
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    ACTION_DELETE_VOID,
    ACTION_RECONCILE_ADJUST,
    ACTION_RECONCILE_CREATE,
    ACTION_RECONCILE_VOID,
    CATEGORY_BY_CHECK_ITEM,
    DEFAULT_DEADLINE_DAYS_NORMAL,
    DEFAULT_DEADLINE_DAYS_URGENT,
    INSPECTION_ITEM_PROBLEM_THRESHOLD,
    INSPECTION_ITEM_SERIOUS_SCORE,
    INSPECTION_ITEM_URGENT_SCORE,
    ISSUE_TITLE_BY_CHECK_ITEM,
    OPEN_ISSUE_STATUSES,
    IssueCategory,
    IssueSeverity,
    IssueStatus,
)
from app.models import Inspection, Issue, RectificationRecord
from app.services.issue_service import _next_code


def category_for_item(item_name: str) -> str:
    return CATEGORY_BY_CHECK_ITEM.get(item_name, IssueCategory.OTHER).value


def severity_for_score(score: float) -> str:
    """按检查项得分推导严重程度：0 分紧急，低于 3 分严重，其余一般。"""
    if score <= INSPECTION_ITEM_URGENT_SCORE:
        return IssueSeverity.URGENT.value
    if score < INSPECTION_ITEM_SERIOUS_SCORE:
        return IssueSeverity.SERIOUS.value
    return IssueSeverity.NORMAL.value


def is_problem_score(score: float) -> bool:
    return float(score) < INSPECTION_ITEM_PROBLEM_THRESHOLD


def _deadline_for(severity: str, base_time: datetime) -> datetime:
    days = (
        DEFAULT_DEADLINE_DAYS_URGENT
        if severity == IssueSeverity.URGENT.value
        else DEFAULT_DEADLINE_DAYS_NORMAL
    )
    return base_time + timedelta(days=days)


def _void_issue(issue: Issue, *, action: str, remark: str) -> None:
    """把问题置为作废：关闭状态、打作废标记，并补一条整改流水（不做越级校验）。"""
    from_status = issue.status
    issue.status = IssueStatus.CLOSED.value
    issue.voided = True
    issue.closed_at = datetime.now()
    issue.records.append(
        RectificationRecord(
            action=action,
            from_status=from_status,
            to_status=IssueStatus.CLOSED.value,
            operator="系统",
            remark=remark,
        )
    )


def _adjust_issue(issue: Issue, *, category: str, severity: str, remark: str) -> bool:
    """按新评分调整仍成立的问题；分类/程度有变化时返回 True 并补流水。"""
    changed = False
    changes: list[str] = []
    if issue.category != category:
        changes.append(f"分类 {issue.category} → {category}")
        issue.category = category
        changed = True
    if issue.severity != severity:
        changes.append(f"严重程度 {issue.severity} → {severity}")
        issue.severity = severity
        changed = True
    if changed:
        issue.records.append(
            RectificationRecord(
                action=ACTION_RECONCILE_ADJUST,
                from_status=issue.status,
                to_status=issue.status,
                operator="系统",
                remark=remark + "（" + "、".join(changes) + "）",
            )
        )
    return changed


def _register_issue(db: Session, inspection: Inspection, item: dict) -> Issue:
    """为新出现的不达标检查项登记一条待整改问题。"""
    name = item["name"]
    score = float(item["score"])
    category = category_for_item(name)
    severity = severity_for_score(score)
    now = datetime.now()
    description = (
        f"巡查重新评分后，检查项「{name}」得分 {score:g}（低于 "
        f"{INSPECTION_ITEM_PROBLEM_THRESHOLD} 分合格线），由系统联动登记。"
    )
    issue = Issue(
        code=_next_code(db),
        restroom_id=inspection.restroom_id,
        inspection_id=inspection.id,
        source_item=name,
        auto_registered=True,
        title=ISSUE_TITLE_BY_CHECK_ITEM.get(name, f"检查项「{name}」不达标"),
        description=description,
        category=category,
        severity=severity,
        reporter=inspection.inspector,
        report_time=now,
        deadline=_deadline_for(severity, now),
        status=IssueStatus.PENDING.value,
    )
    issue.records.append(
        RectificationRecord(
            action=ACTION_RECONCILE_CREATE,
            from_status="",
            to_status=IssueStatus.PENDING.value,
            operator="系统",
            remark="巡查评价变更后新增不达标检查项，自动登记问题工单",
        )
    )
    db.add(issue)
    return issue


def _auto_issues(db: Session, inspection_id: int) -> list[Issue]:
    """该巡查名下由巡查评价联动管理、且尚未作废的问题。"""
    return list(
        db.scalars(
            select(Issue).where(
                Issue.inspection_id == inspection_id,
                Issue.auto_registered.is_(True),
                Issue.voided.is_(False),
            )
        )
    )


def reconcile_on_update(db: Session, inspection: Inspection, new_items: list[dict]) -> dict:
    """按改分后的检查项，对该巡查登记的问题做一次性对账。

    返回各类处理数量 {kept, adjusted, voided, created}。
    """
    issues = _auto_issues(db, inspection.id)
    active_by_item: dict[str, Issue] = {}
    duplicate_voided = 0
    for issue in issues:
        name = issue.source_item or ""
        if name not in active_by_item:
            active_by_item[name] = issue
            continue
        # 同一检查项出现多条有效问题属于异常重复：保留最早一条，其余作废，避免数量对不上
        candidate = active_by_item[name]
        keep, drop = sorted((candidate, issue), key=lambda item: item.id)
        active_by_item[name] = keep
        if drop.status in OPEN_ISSUE_STATUSES:
            _void_issue(
                drop,
                action=ACTION_RECONCILE_VOID,
                remark=f"检查项「{name}」存在重复登记的问题，合并保留工单 #{keep.code}，本单作废",
            )
        else:
            drop.voided = True
        duplicate_voided += 1

    problem_items = [
        item for item in new_items if is_problem_score(float(item["score"]))
    ]
    problem_names = {item["name"] for item in problem_items}

    kept = adjusted = created = 0
    voided = duplicate_voided

    # 1) 检查项已达标 / 被删除 -> 原问题作废
    for name, issue in list(active_by_item.items()):
        if name in problem_names:
            continue
        if issue.status in OPEN_ISSUE_STATUSES:
            _void_issue(
                issue,
                action=ACTION_RECONCILE_VOID,
                remark=f"巡查重新评分后检查项「{name}」已达标或被移除，原问题作废",
            )
        else:
            # 已完成/已关闭的历史工单不重新打开，仅标记为评价作废并不再计入统计
            issue.voided = True
        active_by_item.pop(name, None)
        voided += 1

    # 2) 仍不达标 -> 保留并按新分数调整分类/严重程度
    score_by_name = {item["name"]: float(item["score"]) for item in problem_items}
    for name, issue in list(active_by_item.items()):
        score = score_by_name.get(name)
        if score is None:
            continue
        _adjust_issue(
            issue,
            category=category_for_item(name),
            severity=severity_for_score(score),
            remark=f"巡查重新评分后检查项「{name}」得分 {score:g}，联动更新",
        )
        if issue.status in OPEN_ISSUE_STATUSES:
            kept += 1
        adjusted += 1

    # 3) 新出现的不达标检查项 -> 自动登记问题
    existing_names = set(active_by_item.keys())
    for item in problem_items:
        if item["name"] in existing_names:
            continue
        _register_issue(db, inspection, item)
        created += 1

    return {"kept": kept, "adjusted": adjusted, "voided": voided, "created": created}


def reconcile_void_all(db: Session, inspection: Inspection) -> dict:
    """整批评为作废（PATCH 的 void 模式 / DELETE 的 void 模式共用）。"""
    voided = 0
    for issue in _auto_issues(db, inspection.id):
        if issue.status in OPEN_ISSUE_STATUSES:
            _void_issue(
                issue,
                action=ACTION_RECONCILE_VOID,
                remark="巡查评价变更，本次巡查登记的问题整批作废",
            )
        else:
            issue.voided = True
        voided += 1
    return {"kept": 0, "adjusted": 0, "voided": voided, "created": 0}


def handle_inspection_delete(db: Session, inspection: Inspection, mode: str) -> dict:
    """删除整条巡查时处理其名下联动问题。

    - void（默认）：未闭环的问题作废关闭，已闭环的标记作废，全部保留痕迹；
    - delete：物理删除问题及其整改流水；
    - unlink：解除关联（inspection_id 置空），问题作为独立工单继续流转。
    返回 {voided, deleted, unlinked}。
    """
    issues = _auto_issues(db, inspection.id)
    if mode == "delete":
        deleted = 0
        for issue in issues:
            db.delete(issue)
            deleted += 1
        return {"voided": 0, "deleted": deleted, "unlinked": 0}

    if mode == "unlink":
        unlinked = 0
        for issue in issues:
            issue.inspection_id = None
            issue.auto_registered = False
            unlinked += 1
        return {"voided": 0, "deleted": 0, "unlinked": unlinked}

    voided = 0
    for issue in issues:
        if issue.status in OPEN_ISSUE_STATUSES:
            _void_issue(
                issue,
                action=ACTION_DELETE_VOID,
                remark="关联的巡查记录已删除，问题随之作废",
            )
        else:
            issue.voided = True
        voided += 1
    return {"voided": voided, "deleted": 0, "unlinked": 0}

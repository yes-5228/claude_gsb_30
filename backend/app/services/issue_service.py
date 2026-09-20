"""问题上报与整改跟踪业务逻辑。"""

from datetime import date, datetime, time

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.constants import (
    INSPECTION_ITEM_PROBLEM_THRESHOLD,
    INSPECTION_SYNC_ACTION_ADJUST,
    INSPECTION_SYNC_ACTION_VOID,
    INSPECTION_SYNC_OPERATOR,
    ISSUE_CATEGORY_BY_CHECK_ITEM,
    ISSUE_TRANSITIONS,
    OPEN_ISSUE_STATUSES,
    TRANSITION_ACTIONS,
    IssueStatus,
)
from app.core.exceptions import DomainError, NotFoundError
from app.models import Inspection, Issue, RectificationRecord, Restroom
from app.schemas.issue import IssueCreate, IssueOut, IssueStatusUpdate, IssueUpdate
from app.services import restroom_service, scoring

SORTABLE_FIELDS = {
    "report_time": Issue.report_time,
    "deadline": Issue.deadline,
    "severity": Issue.severity,
    "status": Issue.status,
    "code": Issue.code,
    "updated_at": Issue.updated_at,
}


def _next_code(db: Session) -> str:
    prefix = datetime.now().strftime("WT-%Y%m%d")
    seq = (
        db.scalar(
            select(func.count()).select_from(Issue).where(Issue.code.like(f"{prefix}-%"))
        )
        or 0
    ) + 1
    while True:
        code = f"{prefix}-{seq:03d}"
        if not db.scalar(select(Issue.id).where(Issue.code == code)):
            return code
        seq += 1


def _values(data: dict) -> dict:
    return {key: (value.value if hasattr(value, "value") else value) for key, value in data.items()}


def get_issue(db: Session, issue_id: int) -> Issue:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise NotFoundError(f"问题 {issue_id} 不存在")
    return issue


def to_out(issue: Issue) -> IssueOut:
    return IssueOut.model_validate(issue)


def is_overdue(issue: Issue) -> bool:
    return (
        issue.deadline is not None
        and issue.status in OPEN_ISSUE_STATUSES
        and issue.deadline < datetime.now()
    )


def list_issues(
    db: Session,
    *,
    restroom_id: int | None = None,
    inspection_id: int | None = None,
    district: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    category: str | None = None,
    severity: str | None = None,
    keyword: str | None = None,
    overdue: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: str = "report_time",
    order: str = "desc",
) -> tuple[list[Issue], int]:
    stmt = select(Issue)
    if district:
        stmt = stmt.join(Restroom, Restroom.id == Issue.restroom_id).where(
            Restroom.district == district
        )
    if restroom_id:
        stmt = stmt.where(Issue.restroom_id == restroom_id)
    if inspection_id:
        stmt = stmt.where(Issue.inspection_id == inspection_id)
    if status:
        stmt = stmt.where(Issue.status == status)
    if statuses:
        stmt = stmt.where(Issue.status.in_(statuses))
    if category:
        stmt = stmt.where(Issue.category == category)
    if severity:
        stmt = stmt.where(Issue.severity == severity)
    if date_from:
        stmt = stmt.where(Issue.report_time >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Issue.report_time <= datetime.combine(date_to, time.max))
    if overdue is True:
        stmt = stmt.where(
            Issue.deadline.is_not(None),
            Issue.deadline < datetime.now(),
            Issue.status.in_(OPEN_ISSUE_STATUSES),
        )
    elif overdue is False:
        stmt = stmt.where(
            or_(Issue.deadline.is_(None), Issue.deadline >= datetime.now()),
            Issue.status.in_(OPEN_ISSUE_STATUSES),
        )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Issue.title.like(like),
                Issue.description.like(like),
                Issue.code.like(like),
                Issue.assignee.like(like),
                Issue.reporter.like(like),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = SORTABLE_FIELDS.get(sort_by, Issue.report_time)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc(), Issue.id.desc())
    rows = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return rows, total


def create_issue(db: Session, payload: IssueCreate) -> Issue:
    restroom_service.get_restroom(db, payload.restroom_id)
    if payload.inspection_id is not None:
        inspection = db.get(Inspection, payload.inspection_id)
        if inspection is None:
            raise NotFoundError(f"巡查记录 {payload.inspection_id} 不存在")
        if inspection.restroom_id != payload.restroom_id:
            raise DomainError("关联的巡查记录与所选公厕不一致")
        if payload.check_item:
            item_names = {str(item.get("name", "")) for item in inspection.items or []}
            if payload.check_item not in item_names:
                raise DomainError(f"来源检查项「{payload.check_item}」不在该巡查记录的检查项中")
    elif payload.check_item:
        raise DomainError("未关联巡查记录时不能指定来源检查项")

    data = _values(payload.model_dump(exclude={"inspection_id", "report_time", "initial_remark"}))
    issue = Issue(
        code=_next_code(db),
        inspection_id=payload.inspection_id,
        report_time=payload.report_time or datetime.now(),
        status=IssueStatus.PENDING.value,
        **data,
    )
    issue.records.append(
        RectificationRecord(
            action="上报问题",
            from_status="",
            to_status=IssueStatus.PENDING.value,
            operator=payload.reporter or "巡查员",
            remark=payload.initial_remark or "巡查发现，等待派单整改",
        )
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    restroom_service.touch(db, issue.restroom_id)
    return issue


def update_issue(db: Session, issue_id: int, payload: IssueUpdate) -> Issue:
    issue = get_issue(db, issue_id)
    issue_data = payload.model_dump(exclude_unset=True)
    if "images" in issue_data and payload.images is not None:
        issue_data["images"] = list(payload.images)
    for key, value in _values(issue_data).items():
        setattr(issue, key, value)
    db.commit()
    db.refresh(issue)
    return issue


def allowed_transitions(issue: Issue) -> list[dict[str, str]]:
    return [
        {"status": target, "action": TRANSITION_ACTIONS.get((issue.status, target), "状态变更")}
        for target in ISSUE_TRANSITIONS.get(issue.status, [])
    ]


def change_status(db: Session, issue_id: int, payload: IssueStatusUpdate) -> Issue:
    issue = get_issue(db, issue_id)
    target = payload.to_status.value
    if target == issue.status:
        raise DomainError(f"问题已处于「{target}」状态")
    allowed = ISSUE_TRANSITIONS.get(issue.status, [])
    if target not in allowed:
        raise DomainError(
            f"当前状态「{issue.status}」不允许流转到「{target}」，可选："
            + ("、".join(allowed) if allowed else "无（流程已结束）")
        )

    from_status = issue.status
    issue.status = target
    issue.closed_at = datetime.now() if target == IssueStatus.CLOSED.value else None
    if payload.to_status == IssueStatus.PROCESSING and payload.operator:
        issue.assignee = payload.operator if not issue.assignee else issue.assignee
    issue.records.append(
        RectificationRecord(
            action=TRANSITION_ACTIONS.get((from_status, target), "状态变更"),
            from_status=from_status,
            to_status=target,
            operator=payload.operator,
            remark=payload.remark,
        )
    )
    db.commit()
    db.refresh(issue)
    restroom_service.touch(db, issue.restroom_id)
    return issue


def sync_issues_for_inspection(
    db: Session, inspection: Inspection, previous_items: list[dict]
) -> dict[str, int]:
    """巡查改分/删减检查项后，联动处理该巡查登记出的问题记录。

    规则（仅重判「待整改」问题，已进入整改流程的一律保留）：
    - 作废：来源检查项被删除，或改分后由不达标变为达标 -> 自动作废关闭；
    - 调整：来源检查项仍不达标且得分变化 -> 按新分数重算严重程度并校准分类；
    - 保留：来源检查项判断未变（得分未动、一直达标），或问题不是从检查项登记的。

    只修改 ORM 对象并追加整改流水，不提交事务——由调用方与巡查更新一起提交，
    任何一步失败都会整体回滚，不会留下改了一半的问题集合。
    """
    summary = {"kept": 0, "adjusted": 0, "voided": 0}
    previous_scores = {
        str(item.get("name", "")): float(item.get("score", 0)) for item in previous_items
    }
    current_scores = {
        str(item.get("name", "")): float(item.get("score", 0)) for item in inspection.items or []
    }

    issues = list(db.scalars(select(Issue).where(Issue.inspection_id == inspection.id)))
    for issue in issues:
        if issue.status != IssueStatus.PENDING.value:
            summary["kept"] += 1
            continue
        source = (issue.check_item or "").strip()
        if not source:
            summary["kept"] += 1
            continue

        new_score = current_scores.get(source)
        old_score = previous_scores.get(source)
        if new_score is None:
            reason = f"检查项「{source}」已从本次巡查中删除"
            void = True
        elif new_score >= INSPECTION_ITEM_PROBLEM_THRESHOLD:
            if old_score is not None and old_score >= INSPECTION_ITEM_PROBLEM_THRESHOLD:
                # 改分前后都达标，判断未变，保留
                summary["kept"] += 1
                continue
            reason = f"检查项「{source}」改分后为 {new_score:g} 分，已达标"
            void = True
        else:
            void = False
            reason = ""

        if void:
            issue.status = IssueStatus.CLOSED.value
            issue.closed_at = datetime.now()
            issue.records.append(
                RectificationRecord(
                    action=INSPECTION_SYNC_ACTION_VOID,
                    from_status=IssueStatus.PENDING.value,
                    to_status=IssueStatus.CLOSED.value,
                    operator=INSPECTION_SYNC_OPERATOR,
                    remark=f"巡查评价变更，{reason}，问题自动作废",
                )
            )
            summary["voided"] += 1
            continue

        if old_score is not None and old_score == new_score:
            # 来源检查项得分未变，问题判断依据不变，保留人工调整过的分类/程度
            summary["kept"] += 1
            continue
        new_severity = scoring.severity_for_item_score(new_score)
        new_category = ISSUE_CATEGORY_BY_CHECK_ITEM.get(source, issue.category)
        new_category = new_category.value if hasattr(new_category, "value") else new_category
        changes: list[str] = []
        if new_severity != issue.severity:
            changes.append(f"严重程度「{issue.severity}」调整为「{new_severity}」")
            issue.severity = new_severity
        if new_category != issue.category:
            changes.append(f"分类「{issue.category}」校准为「{new_category}」")
            issue.category = new_category
        if not changes:
            summary["kept"] += 1
            continue
        score_note = (
            f"「{source}」得分 {old_score:g} → {new_score:g} 分"
            if old_score is not None
            else f"「{source}」得分 {new_score:g} 分"
        )
        issue.records.append(
            RectificationRecord(
                action=INSPECTION_SYNC_ACTION_ADJUST,
                from_status=issue.status,
                to_status=issue.status,
                operator=INSPECTION_SYNC_OPERATOR,
                remark=f"巡查评价变更，{score_note}，" + "；".join(changes),
            )
        )
        summary["adjusted"] += 1
    return summary


def add_record(db: Session, issue_id: int, *, action: str, operator: str, remark: str | None) -> Issue:
    """在不改变状态的前提下追加跟进记录（如整改进度说明）。"""
    issue = get_issue(db, issue_id)
    if issue.status == IssueStatus.CLOSED.value:
        raise DomainError("问题已关闭，无法追加整改记录")
    issue.records.append(
        RectificationRecord(
            action=action or "整改进度",
            from_status=issue.status,
            to_status=issue.status,
            operator=operator,
            remark=remark,
        )
    )
    db.commit()
    db.refresh(issue)
    return issue


def delete_issue(db: Session, issue_id: int) -> None:
    issue = get_issue(db, issue_id)
    db.delete(issue)
    db.commit()

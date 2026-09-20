"""业务枚举与规则常量。"""

from enum import StrEnum


class RestroomStatus(StrEnum):
    NORMAL = "正常开放"
    MAINTENANCE = "维修中"
    CLOSED = "暂停使用"


class RestroomGrade(StrEnum):
    FIRST = "一类"
    SECOND = "二类"
    THIRD = "三类"


class Shift(StrEnum):
    MORNING = "早班"
    MIDDLE = "中班"
    NIGHT = "晚班"


class InspectionResult(StrEnum):
    NORMAL = "正常"
    ABNORMAL = "发现问题"


class IssueCategory(StrEnum):
    CLEANING = "保洁不到位"
    FACILITY = "设施损坏"
    ODOR = "异味扰民"
    CONSUMABLE = "耗材缺失"
    SAFETY = "安全隐患"
    OTHER = "其他"


class IssueSeverity(StrEnum):
    NORMAL = "一般"
    SERIOUS = "严重"
    URGENT = "紧急"


class IssueStatus(StrEnum):
    PENDING = "待整改"
    PROCESSING = "整改中"
    REVIEWING = "待验收"
    DONE = "已完成"
    CLOSED = "已关闭"


class IssueSyncMode(StrEnum):
    """巡查评价变更时，对当次巡查登记问题的处理方式。"""

    ADJUST = "adjust"  # 按新评分联动：保留调整/作废/新增
    KEEP = "keep"  # 保留原问题，不随评分联动
    VOID = "void"  # 整批评为作废
    UNLINK = "unlink"  # 解除与巡查的关联，问题本身保留
    DELETE = "delete"  # 连同问题记录一起物理删除


# 整改流转规则：当前状态 -> 允许流转到的状态
ISSUE_TRANSITIONS: dict[str, list[str]] = {
    IssueStatus.PENDING: [IssueStatus.PROCESSING, IssueStatus.CLOSED],
    IssueStatus.PROCESSING: [IssueStatus.REVIEWING, IssueStatus.CLOSED],
    IssueStatus.REVIEWING: [IssueStatus.DONE, IssueStatus.PROCESSING],
    IssueStatus.DONE: [IssueStatus.CLOSED],
    IssueStatus.CLOSED: [],
}

# 状态流转对应的动作名称，用于生成整改流水
TRANSITION_ACTIONS: dict[tuple[str, str], str] = {
    (IssueStatus.PENDING, IssueStatus.PROCESSING): "开始整改",
    (IssueStatus.PENDING, IssueStatus.CLOSED): "作废关闭",
    (IssueStatus.PROCESSING, IssueStatus.REVIEWING): "提交验收",
    (IssueStatus.PROCESSING, IssueStatus.CLOSED): "终止关闭",
    (IssueStatus.REVIEWING, IssueStatus.DONE): "验收通过",
    (IssueStatus.REVIEWING, IssueStatus.PROCESSING): "验收驳回",
    (IssueStatus.DONE, IssueStatus.CLOSED): "归档关闭",
}

# 巡查检查项，每项 0-10 分
INSPECTION_CHECK_ITEMS: list[str] = [
    "地面与台阶清洁",
    "便池蹲位清洁",
    "洗手台与镜面",
    "通风除臭",
    "耗材补充",
    "垃圾清运",
    "工具与标识摆放",
    "墙面门窗卫生",
]

INSPECTION_ITEM_MAX_SCORE = 10

GRADE_EXCELLENT = "优秀"
GRADE_GOOD = "良好"
GRADE_PASS = "合格"
GRADE_FAIL = "不合格"

# 仍处于整改闭环中的状态，用于统计未整改问题
OPEN_ISSUE_STATUSES: list[str] = [
    IssueStatus.PENDING,
    IssueStatus.PROCESSING,
    IssueStatus.REVIEWING,
]

# 单检查项低于该分数视为不合格项
INSPECTION_ITEM_PROBLEM_THRESHOLD = 6

# 检查项名称 -> 问题分类，巡查登记问题与评分联动均以此为准
CATEGORY_BY_CHECK_ITEM: dict[str, IssueCategory] = {
    "地面与台阶清洁": IssueCategory.CLEANING,
    "便池蹲位清洁": IssueCategory.CLEANING,
    "洗手台与镜面": IssueCategory.CLEANING,
    "通风除臭": IssueCategory.ODOR,
    "耗材补充": IssueCategory.CONSUMABLE,
    "垃圾清运": IssueCategory.CLEANING,
    "工具与标识摆放": IssueCategory.OTHER,
    "墙面门窗卫生": IssueCategory.CLEANING,
}

# 各检查项不达标时自动登记问题的标题模板
ISSUE_TITLE_BY_CHECK_ITEM: dict[str, str] = {
    "地面与台阶清洁": "地面或台阶清洁不达标",
    "便池蹲位清洁": "便池蹲位清洁不达标",
    "洗手台与镜面": "洗手台与镜面清洁不达标",
    "通风除臭": "通风除臭不到位，存在异味",
    "耗材补充": "耗材补充不及时",
    "垃圾清运": "垃圾清运不及时",
    "工具与标识摆放": "工具与标识摆放不规范",
    "墙面门窗卫生": "墙面门窗卫生不达标",
}

# 系统联动作废/登记问题时整改流水使用的动作名
ACTION_RECONCILE_VOID = "评价联动作废"
ACTION_RECONCILE_ADJUST = "评价联动调整"
ACTION_RECONCILE_CREATE = "评价联动登记"
ACTION_DELETE_VOID = "巡查删除作废"

# 自动登记问题：0 分视为紧急，低于 3 分视为严重，其余一般
INSPECTION_ITEM_URGENT_SCORE = 0
INSPECTION_ITEM_SERIOUS_SCORE = 3

# 自动登记问题的默认整改期限（天），按严重程度区分
DEFAULT_DEADLINE_DAYS_URGENT = 1
DEFAULT_DEADLINE_DAYS_NORMAL = 3

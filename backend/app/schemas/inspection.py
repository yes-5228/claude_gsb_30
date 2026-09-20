"""保洁巡查记录相关数据结构。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import Shift
from app.schemas.restroom import RestroomBrief


class InspectionItem(BaseModel):
    """单个检查项的打分。"""

    name: str = Field(description="检查项名称")
    score: float = Field(ge=0, le=10, description="得分，0-10")
    remark: str | None = Field(default=None, max_length=200, description="单项备注")


class InspectionCreate(BaseModel):
    restroom_id: int
    inspector: str = Field(min_length=1, max_length=60, description="巡查人")
    shift: Shift = Field(default=Shift.MORNING, description="班次")
    inspect_time: datetime | None = Field(default=None, description="巡查时间，留空取当前时间")
    items: list[InspectionItem] = Field(min_length=1, description="检查项打分明细")
    remark: str | None = Field(default=None, max_length=500)


class InspectionUpdate(BaseModel):
    inspector: str | None = Field(default=None, max_length=60)
    shift: Shift | None = None
    inspect_time: datetime | None = None
    items: list[InspectionItem] | None = Field(default=None, min_length=1)
    remark: str | None = Field(default=None, max_length=500)


class InspectionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inspector: str
    inspect_time: datetime
    score: float
    grade: str
    result: str
    shift: str


class InspectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restroom_id: int
    restroom: RestroomBrief | None = None
    inspector: str
    shift: str
    inspect_time: datetime
    items: list[InspectionItem] = Field(default_factory=list)
    score: float
    grade: str
    result: str
    remark: str | None = None
    created_at: datetime
    issue_count: int = 0


class IssueSyncSummary(BaseModel):
    """巡查改分后对已登记问题记录的联动结果。"""

    kept: int = Field(description="保留不动的问题数（已进入整改流程或判断未变）")
    adjusted: int = Field(description="按新评分调整严重程度/分类的问题数")
    voided: int = Field(description="因检查项删除或达标而作废关闭的问题数")


class InspectionUpdateOut(InspectionOut):
    issue_sync: IssueSyncSummary | None = Field(
        default=None, description="本次改分对关联问题记录的联动摘要，未改动检查项时为 null"
    )

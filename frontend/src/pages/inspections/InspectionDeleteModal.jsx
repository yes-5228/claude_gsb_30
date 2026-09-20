import { useState } from 'react';

import { inspectionApi } from '../../api/inspections.js';
import Modal from '../../components/Modal.jsx';
import { useToast } from '../../components/Toast.jsx';

const MODE_OPTIONS = [
  {
    value: 'void',
    label: '问题随巡查作废（推荐）',
    hint: '仍在整改中的问题作废关闭，已闭环的问题标记作废；整改轨迹全部保留，但不再计入看板与台账',
  },
  {
    value: 'unlink',
    label: '解除关联，问题保留',
    hint: '问题与该巡查脱钩，作为独立工单继续整改闭环，统计数量保持不变',
  },
  {
    value: 'delete',
    label: '连同问题一并删除',
    hint: '物理删除本次巡查登记的问题及其全部整改流水，删除后不可恢复',
    danger: true,
  },
];

export default function InspectionDeleteModal({ inspection, onClose, onDeleted }) {
  const toast = useToast();
  const [issueMode, setIssueMode] = useState('void');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const count = inspection.issue_count || 0;

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await inspectionApi.remove(inspection.id, issueMode);
      toast.success(result.message || '删除成功');
      onDeleted();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="删除巡查记录"
      onClose={onClose}
      width={560}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={`btn ${issueMode === 'delete' ? 'btn-danger' : 'btn-primary'}`}
            onClick={submit}
            disabled={saving}
          >
            {saving ? '处理中…' : '确认删除'}
          </button>
        </>
      }
    >
      {error ? <div className="alert alert-error">{error}</div> : null}
      <div className="alert alert-info">
        该巡查当前关联 <strong>{count}</strong> 条有效问题。删除巡查记录时需要明确这些问题的处理方式，
        删除与问题联动一次性完成，中途失败会整体回滚。
      </div>
      <div className="sync-mode-list">
        {MODE_OPTIONS.map((option) => (
          <label
            key={option.value}
            className={`sync-mode${issueMode === option.value ? ' selected' : ''}`}
          >
            <input
              type="radio"
              name="deleteMode"
              value={option.value}
              checked={issueMode === option.value}
              onChange={() => setIssueMode(option.value)}
            />
            <div>
              <div className={`sync-mode-label${option.danger ? ' danger' : ''}`}>{option.label}</div>
              <div className="muted" style={{ fontSize: 12 }}>{option.hint}</div>
            </div>
          </label>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        手工上报、群众反馈等非本巡查登记的问题不受影响。
      </div>
    </Modal>
  );
}

import { useMemo, useState } from 'react';

import { inspectionApi } from '../../api/inspections.js';
import Field from '../../components/Field.jsx';
import Modal from '../../components/Modal.jsx';
import { GradeTag, StatusTag } from '../../components/Tags.jsx';
import { useToast } from '../../components/Toast.jsx';
import { calcScore, gradeOf, resultOf } from '../../utils/scoring.js';
import { toDateTimeInput } from '../../utils/format.js';

const MODE_OPTIONS = [
  {
    value: 'adjust',
    label: '按新评分联动（推荐）',
    hint: '仍不达标项：保留并更新分类/程度；已达标或删除项：原问题作废；新增不达标项：自动登记问题',
  },
  { value: 'keep', label: '保留原问题不动', hint: '只改评分与结论，原登记的问题全部保留，不做联动' },
  { value: 'void', label: '整批作废', hint: '本次巡查登记的、仍在整改中的问题一律作废关闭' },
];

function syncText(sync) {
  if (!sync) return null;
  const parts = [];
  if (sync.kept) parts.push(`保留 ${sync.kept} 条`);
  if (sync.adjusted) parts.push(`调整 ${sync.adjusted} 条`);
  if (sync.voided) parts.push(`作废 ${sync.voided} 条`);
  if (sync.created) parts.push(`新登记 ${sync.created} 条`);
  return parts.length ? parts.join('，') : '问题集合无变化';
}

export default function InspectionEditModal({ inspection, onClose, onSaved }) {
  const toast = useToast();
  const [form, setForm] = useState({
    inspector: inspection.inspector,
    shift: inspection.shift,
    inspect_time: toDateTimeInput(inspection.inspect_time),
    remark: inspection.remark || '',
  });
  const [items, setItems] = useState(
    (inspection.items || []).map((item) => ({ ...item, remark: item.remark || '' })),
  );
  const [issueMode, setIssueMode] = useState('adjust');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [sync, setSync] = useState(null);

  const score = useMemo(() => calcScore(items), [items]);
  const grade = gradeOf(score);
  const result = resultOf(items, score);
  const failingCount = items.filter((item) => Number(item.score) < 6).length;

  const setItemScore = (index, value) => {
    setItems((prev) =>
      prev.map((item, idx) => (idx === index ? { ...item, score: Number(value) } : item)),
    );
  };
  const setItemRemark = (index, value) => {
    setItems((prev) => prev.map((item, idx) => (idx === index ? { ...item, remark: value } : item)));
  };
  const fillAll = (value) => setItems((prev) => prev.map((item) => ({ ...item, score: value })));

  const submit = async (event) => {
    event.preventDefault();
    if (!form.inspector.trim()) {
      setError('请填写巡查人');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await inspectionApi.update(
        inspection.id,
        {
          inspector: form.inspector.trim(),
          shift: form.shift,
          inspect_time: form.inspect_time
            ? new Date(form.inspect_time).toISOString()
            : null,
          remark: form.remark || null,
          items,
        },
        issueMode,
      );
      setSync(updated.issue_sync);
      toast.success('巡查评分已更新，问题记录已同步联动');
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="修改巡查评分"
      onClose={onClose}
      width={880}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="inspection-edit-form" className="btn btn-primary" disabled={saving}>
            {saving ? '保存中…' : '保存并联动问题'}
          </button>
        </>
      }
    >
      {error ? <div className="alert alert-error">{error}</div> : null}
      {sync ? <div className="alert alert-success">联动结果：{syncText(sync)}</div> : null}
      <form id="inspection-edit-form" onSubmit={submit} className="form-grid">
        <Field label="巡查人 *">
          <input
            value={form.inspector}
            onChange={(event) => setForm((prev) => ({ ...prev, inspector: event.target.value }))}
          />
        </Field>
        <Field label="班次">
          <select
            value={form.shift}
            onChange={(event) => setForm((prev) => ({ ...prev, shift: event.target.value }))}
          >
            {['早班', '中班', '晚班'].map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </Field>
        <Field label="巡查时间">
          <input
            type="datetime-local"
            value={form.inspect_time}
            onChange={(event) => setForm((prev) => ({ ...prev, inspect_time: event.target.value }))}
          />
        </Field>
      </form>

      <div className="card-title">
        <div className="inline">
          <h3>检查项评分（每项 0-10 分）</h3>
          <span className="tag tag-primary">当前得分 {score.toFixed(1)}</span>
          <GradeTag grade={grade} />
          <StatusTag status={result} />
          {failingCount ? <span className="tag tag-danger">{failingCount} 项不达标</span> : null}
        </div>
        <div className="inline">
          <button type="button" className="btn btn-sm" onClick={() => fillAll(10)}>
            全部满分
          </button>
          <button type="button" className="btn btn-sm" onClick={() => fillAll(8)}>
            全部良好
          </button>
        </div>
      </div>

      <div className="check-grid">
        {items.map((item, index) => (
          <div className={`check-item${item.score < 6 ? ' is-low' : ''}`} key={item.name}>
            <div className="name">{item.name}</div>
            <div className="score-line">
              <input
                type="range"
                min="0"
                max="10"
                step="1"
                value={item.score}
                onChange={(event) => setItemScore(index, event.target.value)}
              />
              <strong>{item.score}</strong>
            </div>
            <input
              style={{ marginTop: 6, fontSize: 12.5, padding: '4px 8px' }}
              className="field-input"
              placeholder="备注（可选）"
              value={item.remark}
              onChange={(event) => setItemRemark(index, event.target.value)}
            />
          </div>
        ))}
      </div>

      <Field label="巡查备注" full>
        <textarea
          rows="2"
          value={form.remark}
          onChange={(event) => setForm((prev) => ({ ...prev, remark: event.target.value }))}
        />
      </Field>

      <div className="section-title">原登记问题如何处理</div>
      <div className="sync-mode-list">
        {MODE_OPTIONS.map((option) => (
          <label
            key={option.value}
            className={`sync-mode${issueMode === option.value ? ' selected' : ''}`}
          >
            <input
              type="radio"
              name="issueMode"
              value={option.value}
              checked={issueMode === option.value}
              onChange={() => setIssueMode(option.value)}
            />
            <div>
              <div className="sync-mode-label">{option.label}</div>
              <div className="muted" style={{ fontSize: 12 }}>{option.hint}</div>
            </div>
          </label>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        仅由本次巡查登记的问题会参与联动；手工上报、群众反馈的问题不受影响。保存为一次性事务，中途失败会整体回滚。
      </div>
    </Modal>
  );
}
